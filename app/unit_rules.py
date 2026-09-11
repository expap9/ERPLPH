"""กติกาเทียบหน่วยที่เครื่องสอบทานของ Stock5 ใช้ — ตัดสินว่างวดไหนต้องสอบทานใหม่

สถานะสอบทาน (VERIFIED/PENDING) ถูกคำนวณตอนดึงแล้วเก็บลงฐานข้อมูล ถ้าผู้ใช้เติม
ตารางหน่วยของ Stock5 ภายหลัง Stock5 จะใช้กติกาใหม่ในรอบถัดไป แต่สถานะที่เก็บไว้
ที่นี่จะยังเป็นของเดิม ตัวเลขคลัง 2 ของสองระบบจะไม่ตรงกันโดยไม่มีสัญญาณเตือน

ที่นี่จึงเก็บ "ลายนิ้วมือกติกาหน่วย" ของทุกงวด คำนวณจากกติกาของรายการที่อยู่ใน
งวดนั้นเท่านั้น การเติมหน่วยของยาหนึ่งตัวจึงดึงใหม่เฉพาะงวดที่มียาตัวนั้น ส่วนการ
แก้โค้ดของเครื่องสอบทานเองมีผลกับทุกงวด
"""
import functools
import hashlib
import json
from typing import Iterable

import stock5_engine

#: โค้ดของเครื่องสอบทาน ถ้าเปลี่ยน สถานะของทุกงวดอาจเปลี่ยน
#: confirmed_units.py ไม่อยู่ในนี้ เพราะเนื้อหาคือกติการายรหัส จึงนับเป็นรายรหัสแทน
ENGINE_FILES = ("lot_reconciliation.py", "monitor_units.py", "special_units.py")

#: ฟิลด์ที่ไม่มีผลต่อการแปลงหน่วย — แทรกแถวในตารางแล้วเลขแถวเลื่อน ต้องไม่ทำให้ดึงใหม่
_POSITION_FIELDS = ("source_row", "clean_name")


def _digest(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_plain)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain(value: object) -> object:
    # กติกาที่ยืนยันแล้วของ Stock5 ใช้ set ซึ่ง json เขียนตรง ๆ ไม่ได้
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)


def rules() -> dict[str, str]:
    """ลายนิ้วมือกติกาหน่วยรายรหัส อ่านจากตารางชุดเดียวกับที่เครื่องสอบทานใช้"""
    special = stock5_engine.load("special_units").get_special_units()
    confirmed = stock5_engine.load("confirmed_units").CONFIRMED_UNITS
    found = {}
    for code in set(special) | set(confirmed):
        entry = {key: value for key, value in (special.get(code) or {}).items()
                 if key not in _POSITION_FIELDS}
        found[str(code)] = _digest({"special": entry, "confirmed": confirmed.get(code)})
    return found


@functools.lru_cache(maxsize=1)
def engine_digest() -> str:
    """ลายนิ้วมือโค้ดเครื่องสอบทาน จำไว้ตลอดโปรเซส

    Python โหลดโมดูลครั้งเดียวต่อโปรเซส ถ้าไฟล์ถูกแก้ระหว่างรัน โค้ดที่ทำงานจริงยัง
    เป็นรุ่นเดิม ลายนิ้วมือจึงต้องเป็นของรุ่นที่โหลดตอนเริ่ม ไม่ใช่ของไฟล์ล่าสุด
    """
    app_dir = stock5_engine.stock5_home() / "app"
    digest = hashlib.sha256()
    for name in ENGINE_FILES:
        digest.update(name.encode("utf-8"))
        # git บนเซิร์ฟเวอร์อาจแปลงท้ายบรรทัด ไฟล์เดียวกันต้องได้ลายนิ้วมือเดียวกัน
        digest.update((app_dir / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def period_digest(codes: Iterable[str], rule_map: dict[str, str], engine: str) -> str:
    """ลายนิ้วมือของงวดหนึ่ง = โค้ดเครื่องสอบทาน + กติกาของรหัสที่อยู่ในงวดนั้น"""
    relevant = sorted((code, rule_map[code]) for code in set(codes) if code in rule_map)
    return _digest({"engine": engine, "rules": relevant})
