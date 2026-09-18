"""การยืนยันตัวตนผู้ใช้ของ ERPLPH (ถอดแบบจาก Stock5 และโปรแกรมห้องยา SSB)

ตรวจสอบกับบัญชีผู้ใช้จริงของระบบโรงพยาบาล (SSBHOSPITAL.dbo.SYSCONFIG, CTRLCODE='10000')
โดยใช้ตารางเดียวกันและวิธีถอดรหัสผ่านแบบเดียวกันกับโปรแกรมจ่ายยา (Frm_login.cs)
ทำให้เจ้าหน้าที่สามารถล็อกอินด้วยชื่อผู้ใช้และรหัสผ่านเดิมที่มีอยู่แล้วได้ทันที
ไม่ต้องสร้างบัญชีผู้ใช้ใหม่และไม่ต้องคอยซิงค์รหัสผ่าน

Session จะถูกเก็บไว้ในหน่วยความจำเท่านั้น (ไม่บันทึกรหัสผ่านลงดิสก์)
และมีอายุตามระยะเวลาที่กำหนด (ค่าเริ่มต้น 8 ชั่วโมง)
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import secrets
import threading

import database

SESSION_COOKIE_NAME = "erplph_session"
ALT_COOKIE_NAME = "stock5_session"

# ค่าเริ่มต้น 8 ชั่วโมง (กะทำงานปกติ) ปรับได้ผ่าน env โดยไม่ต้องแก้โค้ด
SESSION_TTL_SECONDS = int(
    os.environ.get("ERPLPH_SESSION_TTL_SECONDS")
    or os.environ.get("STOCK5_SESSION_TTL_SECONDS")
    or str(8 * 60 * 60)
)

_SESSIONS_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}

# บัญชีลัดผู้ดูแลระบบ — อนุญาต "เฉพาะตอนที่ต่อฐานข้อมูลโรงพยาบาลไม่ได้" เท่านั้น
# (เครื่อง dev / ใช้งานออฟไลน์ / ก่อนตั้งค่า DB ครั้งแรก) บนเซิร์ฟเวอร์จริงที่ DB ต่อได้
# บัญชีนี้จะใช้ไม่ได้เลย ต้องล็อกอินด้วยบัญชี SYSCONFIG จริงของโรงพยาบาลเท่านั้น
_OFFLINE_ADMIN_USER = {
    "username": "ADMIN",
    "name": "ผู้ดูแลระบบ (Admin)",
    "access_group": "ADMIN",
    "facility": "PHAR",
}
_OFFLINE_ADMIN_PASSWORDS = tuple(
    p.strip().upper()
    for p in (
        os.environ.get("ERPLPH_ADMIN_PASSWORD")
        or os.environ.get("STOCK5_ADMIN_PASSWORD")
        or "ADMIN,STOCK5,1234,123456"
    ).split(",")
    if p.strip()
)


def _offline_admin_enabled() -> bool:
    """บัญชีลัดเหมาะกับเครื่องเดียวแบบออฟไลน์ ไม่ใช่เว็บที่ทั้งโรงพยาบาลเข้าได้
    บนเซิร์ฟเวอร์จริง สามารถตั้ง ERPLPH_DISABLE_OFFLINE_ADMIN=1 ได้เพื่อความปลอดภัยสูงสุด
    """
    flag = (
        os.environ.get("ERPLPH_DISABLE_OFFLINE_ADMIN")
        or os.environ.get("STOCK5_DISABLE_OFFLINE_ADMIN")
        or ""
    ).strip().lower()
    return flag not in ("1", "true", "yes")


def _is_offline_admin(user: str, password: str) -> bool:
    return _offline_admin_enabled() and user == "ADMIN" and password in _OFFLINE_ADMIN_PASSWORDS


class LoginError(ValueError):
    """ส่งต่อเมื่อล็อกอินไม่สำเร็จ ข้อความปลอดภัยสำหรับแสดงผลให้ผู้ใช้เห็นโดยตรง"""


class LoginUnavailable(LoginError):
    """ติดต่อฐานข้อมูลโรงพยาบาลไม่ได้ ไม่ใช่รหัสผิด จึงไม่นับเป็นการใส่รหัสผิด"""


# จำกัดการใส่รหัสผิดรายบัญชี ป้องกันการสุ่มเดารหัสผ่าน
LOGIN_FAILURE_LIMIT = int(
    os.environ.get("ERPLPH_LOGIN_FAILURE_LIMIT")
    or os.environ.get("STOCK5_LOGIN_FAILURE_LIMIT")
    or "5"
)
LOGIN_FAILURE_WINDOW_SECONDS = int(
    os.environ.get("ERPLPH_LOGIN_FAILURE_WINDOW_SECONDS")
    or os.environ.get("STOCK5_LOGIN_FAILURE_WINDOW_SECONDS")
    or str(10 * 60)
)

_FAILURES_LOCK = threading.Lock()
_FAILURES: dict[str, list[dt.datetime]] = {}


class LoginThrottled(LoginError):
    """ใส่รหัสผิดเกินกำหนดในช่วงเวลาที่ตั้งไว้"""

    def __init__(self, retry_after_seconds: int):
        minutes = max(1, -(-retry_after_seconds // 60))
        super().__init__(f"ใส่รหัสผ่านผิดหลายครั้ง กรุณารอประมาณ {minutes} นาทีแล้วลองใหม่")
        self.retry_after_seconds = retry_after_seconds


def _throttle_key(username: str) -> str:
    return str(username or "").strip().upper()


def _recent_failures_locked(key: str, now: dt.datetime) -> list[dt.datetime]:
    recent = [
        moment for moment in _FAILURES.get(key, [])
        if (now - moment).total_seconds() < LOGIN_FAILURE_WINDOW_SECONDS
    ]
    if recent:
        _FAILURES[key] = recent
    else:
        _FAILURES.pop(key, None)
    return recent


def check_login_allowed(username: str) -> None:
    """raise LoginThrottled ถ้าบัญชีนี้ใส่รหัสผิดครบกำหนดแล้ว"""
    key = _throttle_key(username)
    if not key:
        return
    now = dt.datetime.now()
    with _FAILURES_LOCK:
        recent = _recent_failures_locked(key, now)
        if len(recent) >= LOGIN_FAILURE_LIMIT:
            waited = (now - recent[0]).total_seconds()
            raise LoginThrottled(int(LOGIN_FAILURE_WINDOW_SECONDS - waited) + 1)


def record_login_failure(username: str) -> None:
    key = _throttle_key(username)
    if not key:
        return
    now = dt.datetime.now()
    with _FAILURES_LOCK:
        _recent_failures_locked(key, now)
        _FAILURES.setdefault(key, []).append(now)


def clear_login_failures(username: str) -> None:
    with _FAILURES_LOCK:
        _FAILURES.pop(_throttle_key(username), None)


def _decode_com_password(com: bytes) -> str:
    """ถอดรหัสผ่านจากคอลัมน์ COM ของ SYSCONFIG

    SSB เก็บรหัสผ่านเป็นไบนารีในคอลัมน์ COM โดยรหัสเริ่มที่ไบต์ที่ 14 ไปจนเจอ 0x00
    และแต่ละไบต์ถูก XOR ด้วย 0xFF ไว้ — ตรรกะเดียวกับ Frm_login.cs ของโปรแกรมห้องยา
    """
    if not com:
        return ""
    out: list[str] = []
    for byte in com[14:]:
        if byte == 0x00:
            break
        out.append(chr(byte ^ 0xFF))
    return "".join(out)


def _read_com_ascii(com: bytes, start: int, end: int) -> str:
    """อ่านข้อความ ASCII ช่วงหนึ่งใน COM หยุดเมื่อเจอ 0x00"""
    if not com:
        return ""
    out: list[str] = []
    for i in range(start, min(end + 1, len(com))):
        if com[i] == 0x00:
            break
        out.append(chr(com[i]))
    return "".join(out).strip()


def verify_login(username: str, password: str) -> dict:
    """ตรวจ username/password กับ SYSCONFIG จริง คืน dict ข้อมูลผู้ใช้ถ้าถูกต้อง
    หรือ raise LoginError พร้อมข้อความที่แสดงให้ผู้ใช้เห็นได้โดยตรง"""
    user = str(username or "").strip().upper()
    pw = str(password or "").strip().upper()
    if not user or not pw:
        raise LoginError("กรุณากรอกชื่อผู้ใช้และรหัสผ่าน")

    query = (
        "SELECT TOP 1 CODE, ENGLISHNAME, THAINAME, COM "
        "FROM SSBHOSPITAL.dbo.SYSCONFIG WHERE CTRLCODE = '10000' AND CODE = ?"
    )

    try:
        conn = database.connect(timeout=5)
    except Exception as exc:
        # ต่อฐานข้อมูลโรงพยาบาลไม่ได้ — อนุญาตบัญชีลัด admin เฉพาะกรณีนี้เท่านั้น
        if _is_offline_admin(user, pw):
            return _OFFLINE_ADMIN_USER.copy()
        print(f"[Login] ติดต่อฐานข้อมูลโรงพยาบาลไม่ได้: {type(exc).__name__}: {exc}")
        hint = (" — หากใช้งานออฟไลน์ สามารถเข้าด้วยบัญชีผู้ดูแลสำรอง"
                if _offline_admin_enabled() else " กรุณาลองใหม่ภายหลัง หรือแจ้งผู้ดูแลระบบ")
        raise LoginUnavailable("ติดต่อฐานข้อมูลโรงพยาบาลไม่ได้" + hint) from exc

    try:
        cursor = conn.cursor()
        cursor.execute(query, user)
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        raise LoginError("ไม่พบชื่อผู้ใช้นี้ในระบบ")

    code, english_name, thai_name, com = row
    com_bytes = bytes(com) if com is not None else b""

    # SSB ทำเครื่องหมายบัญชีที่ยกเลิกไว้ในชื่อ
    combined_name = f"{english_name or ''} {thai_name or ''}"
    if "ยกเลิก" in combined_name:
        raise LoginError("บัญชีนี้ถูกยกเลิกการใช้งานแล้ว")

    if not com_bytes:
        raise LoginError("ไม่พบข้อมูลรหัสผ่านในระบบ")

    if _decode_com_password(com_bytes) != pw:
        raise LoginError("รหัสผ่านไม่ถูกต้อง")

    name = str(english_name).strip() if english_name else ""
    if not name:
        name = str(thai_name).strip() if thai_name else ""

    return {
        "username": user,
        "name": name or user,
        "access_group": _read_com_ascii(com_bytes, 32, 39),
        "facility": _read_com_ascii(com_bytes, 80, 84),
    }


def create_session(user: dict) -> str:
    """สร้าง session token ใหม่ เก็บไว้ในหน่วยความจำเท่านั้น (ไม่บันทึกลงดิสก์ ไม่เก็บรหัสผ่าน)"""
    token = secrets.token_hex(32)
    now = dt.datetime.now()
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {**user, "login_at": now, "last_seen_at": now}
    return token


def _purge_expired_locked() -> None:
    now = dt.datetime.now()
    expired = [
        token for token, item in _SESSIONS.items()
        if (now - item["last_seen_at"]).total_seconds() >= SESSION_TTL_SECONDS
    ]
    for token in expired:
        _SESSIONS.pop(token, None)


def get_session(token: str) -> dict | None:
    """คืนข้อมูล session ถ้ายังไม่หมดอายุ พร้อมต่ออายุ last_seen_at (activity-based TTL)"""
    if not token:
        return None
    with _SESSIONS_LOCK:
        _purge_expired_locked()
        item = _SESSIONS.get(token)
        if item is None:
            return None
        item["last_seen_at"] = dt.datetime.now()
        return dict(item)


def destroy_session(token: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def public_user(session: dict | None) -> dict | None:
    """ตัดฟิลด์ภายใน (login_at/last_seen_at) ออกก่อนส่งกลับเบราว์เซอร์"""
    if not session:
        return None
    return {
        "username": session["username"],
        "name": session["name"],
        "access_group": session.get("access_group", ""),
        "facility": session.get("facility", ""),
    }
