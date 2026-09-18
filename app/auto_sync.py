"""ระบบดึงข้อมูลอัตโนมัติในเบื้องหลัง (Automated Background Sync) สำหรับ ERPLPH

ทำหน้าที่:
1. ตั้งเวลาดึงข้อมูลอัตโนมัติเป็นระยะ (Scheduler Daemon) ทุก 6 ชั่วโมง (ปรับแต่งได้)
2. รองรับการสั่งดึงผ่าน Web API (/api/monitor/pull และ /api/monitor/auto-sync/trigger)
3. มีระบบล็อค (Thread Lock) ป้องกันการดึงข้อมูลชนกัน
4. ล้างแคช API (stock5_api.clear_cache()) ทันทีหลังดึงเสร็จเพื่อให้หน้าจอแสดงข้อมูลสด
5. รายงานสถานะ (auto_sync_status) ตามรูปแบบที่ Frontend Angular รองรับ
"""
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import threading
import time
from typing import Any, Callable

import categories
import extractor
import stock5_api
import warehouse_db

logger = logging.getLogger("erplph.auto_sync")

DEFAULT_INTERVAL_SECONDS = 6 * 3600  #: ค่าเริ่มต้น 6 ชั่วโมง (4 รอบต่อวัน)
RETRY_INTERVAL_SECONDS = 5 * 60     #: เมื่อล้มเหลว รอ 5 นาทีแล้วลองใหม่ตามกติกาหน้าจอ

_sync_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_running = False

# สถานะส่วนกลางของระบบ Auto-Sync
_state: dict[str, Any] = {
    "enabled": True,
    "is_syncing": False,
    "interval_seconds": DEFAULT_INTERVAL_SECONDS,
    "last_sync": None,
    "next_run": None,
    "last_status": "ok",
    "last_message": "ระบบพร้อมทำงาน",
    "last_error": None,
    "last_result": None,
    "current_progress": None,
}


def _get_snapshot_label() -> str:
    """อ่านวันที่ของ snapshot ล่าสุดจากคลังข้อมูลเพื่อแสดงบนแถบสถานะ"""
    try:
        latest = warehouse_db.latest_snapshots()
        if not latest:
            return "-"
        day = max(latest.values())
        if len(day) == 8:
            return f"{day[6:8]}/{day[4:6]}/{day[:4]}"
        return day
    except Exception:
        return "-"


def get_status() -> dict[str, Any]:
    """คืนสถานะปัจจุบันของระบบ Auto-Sync ในรูปแบบที่ Angular Component รองรับ"""
    with _sync_lock:
        state_copy = dict(_state)

    # หากยังไม่มี last_message ให้คำนวณจาก snapshot วันล่าสุด
    if not state_copy.get("last_sync"):
        label = _get_snapshot_label()
        state_copy["last_message"] = f"ข้อมูลถึง {label} · ระบบดึงอัตโนมัติพร้อมทำงาน"

    # คำนวณ next_run หากเปิดใช้งานและยังไม่ได้กำหนด
    if state_copy["enabled"] and not state_copy["next_run"] and not state_copy["is_syncing"]:
        next_dt = datetime.now(timezone.utc) + timedelta(seconds=state_copy["interval_seconds"])
        state_copy["next_run"] = next_dt.isoformat()

    return state_copy


def set_enabled(enabled: bool) -> dict[str, Any]:
    """เปิดหรือปิดการทำงานของ Auto-Sync"""
    with _sync_lock:
        _state["enabled"] = bool(enabled)
        if not enabled:
            _state["next_run"] = None
            _state["last_message"] = "ปิดการดึงข้อมูลอัตโนมัติแล้ว"
        else:
            next_dt = datetime.now(timezone.utc) + timedelta(seconds=_state["interval_seconds"])
            _state["next_run"] = next_dt.isoformat()
            _state["last_message"] = "เปิดการดึงข้อมูลอัตโนมัติแล้ว"
    return get_status()


def set_interval(seconds: int) -> dict[str, Any]:
    """กำหนดระยะเวลาห่างระหว่างรอบ (วินาที)"""
    if seconds < 300:
        seconds = 300  # ขั้นต่ำ 5 นาที เพื่อไม่ให้โหลดเซิร์ฟเวอร์โรงพยาบาลเกินไป
    with _sync_lock:
        _state["interval_seconds"] = seconds
        if _state["enabled"]:
            next_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            _state["next_run"] = next_dt.isoformat()
    return get_status()


def _progress_callback(outcome: dict[str, Any]) -> None:
    """บันทึกความคืบหน้าระหว่างการดึงข้อมูล"""
    with _sync_lock:
        _state["current_progress"] = {
            "period": outcome.get("period"),
            "store": outcome.get("store"),
            "kind": outcome.get("kind"),
            "status": outcome.get("status"),
            "rows": outcome.get("stored_rows", 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def run_sync(group_key: str = categories.ALL,
             recheck_months: int = 0,
             store_codes: list[str] | None = None,
             kinds: list[str] = list(extractor.ALL_KINDS),
             connection_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """ทำการดึงข้อมูลเข้าฐานข้อมูลแบบ synchronous พร้อมจัดการล็อคและอัปเดตสถานะ"""
    acquired = _sync_lock.acquire(blocking=False)
    if not acquired:
        return {
            "status": "busy",
            "message": "ระบบกำลังดึงข้อมูลอยู่ในขณะนี้ ไม่สามารถเริ่มซ้ำได้",
            "is_syncing": True,
        }

    now_utc = datetime.now(timezone.utc)
    _state["is_syncing"] = True
    _state["current_progress"] = {"phase": "starting", "started_at": now_utc.isoformat()}
    _state["last_error"] = None

    try:
        logger.info("เริ่มกระบวนการดึงข้อมูลอัตโนมัติ (ขอบเขต: %s)", group_key)
        result = extractor.run(
            store_codes=store_codes,
            kinds=kinds,
            recheck_months=recheck_months,
            group_key=group_key,
            progress=_progress_callback,
            connection_factory=connection_factory,
        )

        completed_utc = datetime.now(timezone.utc)
        _state["last_result"] = result
        _state["last_sync"] = completed_utc.isoformat()

        if result.get("error"):
            _state["last_status"] = "error"
            _state["last_error"] = result["error"]
            _state["last_message"] = f"Auto-Sync ขัดข้อง: {result['error']}"
            # ตั้งรอบถัดไปให้ลองใหม่ใน 5 นาที
            _state["next_run"] = (completed_utc + timedelta(seconds=RETRY_INTERVAL_SECONDS)).isoformat()
            logger.error("การดึงข้อมูลเกิดข้อผิดพลาด: %s", result["error"])
        elif result.get("failed", 0) > 0 and result.get("success", 0) == 0 and result.get("planned", 0) > 0:
            _state["last_status"] = "error"
            _state["last_error"] = f"ล้มเหลวทั้ง {result['failed']} งวด"
            _state["last_message"] = f"Auto-Sync ไม่สำเร็จ: ล้มเหลว {result['failed']} งวด"
            _state["next_run"] = (completed_utc + timedelta(seconds=RETRY_INTERVAL_SECONDS)).isoformat()
        else:
            _state["last_status"] = "ok"
            label = _get_snapshot_label()
            _state["last_message"] = (
                f"ข้อมูลถึง {label} · อัปเดตสำเร็จ {result.get('success', 0)} งวด "
                f"({result.get('rows', 0):,} แถว)"
            )
            interval = _state.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)
            _state["next_run"] = (completed_utc + timedelta(seconds=interval)).isoformat()
            logger.info("ดึงข้อมูลสำเร็จ %s งวด (%s แถว)", result.get("success", 0), result.get("rows", 0))

        # ล้างแคช API เพื่อให้หน้าเว็บโหลดข้อมูลใหม่ทันที
        stock5_api.clear_cache()
        return {
            "status": "success" if _state["last_status"] == "ok" else "error",
            "message": _state["last_message"],
            "result": result,
            "auto_sync_status": dict(_state),
        }

    except Exception as exc:
        logger.exception("เกิดข้อผิดพลาดระหว่างดึงข้อมูลอัตโนมัติ")
        err_msg = str(exc)
        _state["last_status"] = "error"
        _state["last_error"] = err_msg
        _state["last_message"] = f"Auto-Sync เกิดข้อผิดพลาด: {err_msg}"
        _state["next_run"] = (datetime.now(timezone.utc) + timedelta(seconds=RETRY_INTERVAL_SECONDS)).isoformat()
        return {
            "status": "error",
            "message": _state["last_message"],
            "error": err_msg,
            "auto_sync_status": dict(_state),
        }
    finally:
        _state["is_syncing"] = False
        _state["current_progress"] = None
        _sync_lock.release()


def trigger_sync(group_key: str = categories.ALL,
                 recheck_months: int = 0,
                 background: bool = True) -> dict[str, Any]:
    """เริ่มการดึงข้อมูล หากระบุ background=True จะเริ่มใน Background Thread แล้วตอบกลับทันที"""
    if _state["is_syncing"]:
        return {
            "status": "busy",
            "message": "ระบบกำลังดึงข้อมูลอยู่ในขณะนี้ กรุณารอสักครู่",
            "is_syncing": True,
        }

    if not background:
        return run_sync(group_key=group_key, recheck_months=recheck_months)

    worker = threading.Thread(
        target=run_sync,
        kwargs={"group_key": group_key, "recheck_months": recheck_months},
        name="ERPLPH-AutoSyncWorker",
        daemon=True,
    )
    worker.start()

    return {
        "status": "success",
        "message": "เริ่มดึงข้อมูลอัตโนมัติในเบื้องหลังเรียบร้อยแล้ว",
        "is_syncing": True,
    }


def _scheduler_loop() -> None:
    """ลูปตรวจสอบเวลาสำหรับตัวตั้งเวลาอัตโนมัติ (Background Scheduler)"""
    global _scheduler_running
    logger.info("เริ่มต้น Background Scheduler สำหรับ Auto-Sync แล้ว")

    while _scheduler_running:
        try:
            now = datetime.now(timezone.utc)
            should_run = False

            with _sync_lock:
                if _state["enabled"] and not _state["is_syncing"]:
                    next_run_str = _state.get("next_run")
                    if not next_run_str:
                        # กำหนดเวลารอบแรก
                        _state["next_run"] = (now + timedelta(seconds=_state["interval_seconds"])).isoformat()
                    else:
                        try:
                            next_run_dt = datetime.fromisoformat(next_run_str)
                            if now >= next_run_dt:
                                should_run = True
                        except (ValueError, TypeError):
                            _state["next_run"] = (now + timedelta(seconds=_state["interval_seconds"])).isoformat()

            if should_run:
                logger.info("ถึงเวลาดึงข้อมูลรอบกำหนดการ เริ่มทำการดึงข้อมูล...")
                run_sync(group_key=categories.ALL)

        except Exception as exc:
            logger.error("ข้อผิดพลาดใน Scheduler Loop: %s", exc)

        # หลับตรวจสอบทุก 15 วินาที
        for _ in range(15):
            if not _scheduler_running:
                break
            time.sleep(1)


def start_scheduler(interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
    """เปิดการทำงานของตัวตั้งเวลาอัตโนมัติในเบื้องหลัง (Idempotent)"""
    global _scheduler_thread, _scheduler_running
    if _scheduler_running:
        return

    _state["interval_seconds"] = interval_seconds
    _state["enabled"] = True
    _scheduler_running = True

    # ตั้งเวลารอบถัดไป
    next_dt = datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
    _state["next_run"] = next_dt.isoformat()

    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        name="ERPLPH-AutoSyncScheduler",
        daemon=True,
    )
    _scheduler_thread.start()
    logger.info("เปิดระบบตั้งเวลาดึงข้อมูลอัตโนมัติเรียบร้อย (รอบละ %s วินาที)", interval_seconds)


def stop_scheduler() -> None:
    """หยุดการทำงานของตัวตั้งเวลาอัตโนมัติ"""
    global _scheduler_running
    _scheduler_running = False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ERPLPH Auto Sync Runner / Daemon")
    parser.add_argument("--once", action="store_true", help="ดึงข้อมูลทันที 1 รอบแล้วจบ")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help="ระยะห่างระหว่างรอบ (วินาที) สำหรับโหมด daemon")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.once:
        print("กำลังดึงข้อมูล 1 รอบ...")
        outcome = run_sync(group_key=categories.ALL)
        print("ผลลัพธ์:", outcome)
    else:
        print(f"เริ่มต้น Auto-Sync Daemon ทุก {args.interval} วินาที... กด Ctrl+C เพื่อออก")
        start_scheduler(interval_seconds=args.interval)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nหยุดการทำงานเรียบร้อย")
            stop_scheduler()
