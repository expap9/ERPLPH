"""ข้อมูลที่ผู้ใช้บันทึกเอง — แยกไฟล์จากข้อมูลที่ดึงมาจากโรงพยาบาล

ข้อมูลในนี้ **ดึงคืนไม่ได้** ถ้าหายต้องให้คนกรอกใหม่ จึงแยกไฟล์เพื่อสำรองได้ง่าย
และเพื่อไม่ให้การเขียนงวดใหญ่ของตัวดึงไปล็อกฐานข้อมูลจนคนกดบันทึกแล้วค้าง
(ดู docs/system_design.md)

ทุกการเปลี่ยนแปลงบันทึกว่าใครทำเมื่อไร และประวัติไม่ถูกลบ เพราะเครื่องหมายเหล่านี้
เปลี่ยนความหมายของตัวเลขที่ผู้บริหารเห็น เช่น ยาที่ทำเครื่องหมายว่าสั่งเฉพาะราย
จะไม่ถูกนับเป็นของใกล้หมดอีกต่อไป
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "work_data"
DB_PATH = DATA_DIR / "work.db"

#: เครื่องหมายที่ติดกับรายการได้ พร้อมข้อความที่ผู้ใช้เห็น
PATIENT_SPECIFIC = "patient_specific"
MARKS = {
    PATIENT_SPECIFIC: "บริการผู้ป่วยเฉพาะราย ไม่ได้ซื้อประจำ",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_marks (
    stock_code TEXT NOT NULL,
    mark       TEXT NOT NULL,
    note       TEXT DEFAULT '',
    set_by     TEXT NOT NULL,
    set_at     TEXT NOT NULL,
    PRIMARY KEY (stock_code, mark)
);

CREATE TABLE IF NOT EXISTS item_mark_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    mark       TEXT NOT NULL,
    action     TEXT NOT NULL,          -- set | clear
    note       TEXT DEFAULT '',
    actor      TEXT NOT NULL,
    at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_item_marks_mark ON item_marks(mark);
CREATE INDEX IF NOT EXISTS idx_mark_events_code ON item_mark_events(stock_code, id);
"""


def label(mark: str) -> str:
    return MARKS.get(mark, mark)


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _checked(stock_code: Any, mark: str, actor: str) -> tuple[str, str, str]:
    code = str(stock_code or "").strip()
    actor = str(actor or "").strip()
    if not code:
        raise ValueError("ต้องระบุรหัสรายการ")
    if mark not in MARKS:
        raise ValueError(f"ไม่รู้จักเครื่องหมาย '{mark}' มีเฉพาะ: {', '.join(MARKS)}")
    if not actor:
        # ไม่รับการแก้แบบไม่ระบุตัวตน เพราะเครื่องหมายเปลี่ยนตัวเลขที่ผู้บริหารเห็น
        raise ValueError("ต้องระบุผู้บันทึก")
    return code, mark, actor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_mark(stock_code: str, mark: str, actor: str, note: str = "") -> dict[str, Any]:
    """ติดเครื่องหมายให้รายการหนึ่ง ทับของเดิมได้ และบันทึกประวัติเสมอ"""
    code, mark, actor = _checked(stock_code, mark, actor)
    init_db()
    moment = _now()
    with _transaction() as conn:
        conn.execute(
            "INSERT INTO item_marks (stock_code, mark, note, set_by, set_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(stock_code, mark) DO UPDATE SET note = excluded.note, "
            "set_by = excluded.set_by, set_at = excluded.set_at",
            (code, mark, str(note or ""), actor, moment))
        conn.execute(
            "INSERT INTO item_mark_events (stock_code, mark, action, note, actor, at) "
            "VALUES (?, ?, 'set', ?, ?, ?)", (code, mark, str(note or ""), actor, moment))
    return {"stock_code": code, "mark": mark, "action": "set", "actor": actor, "at": moment}


def clear_mark(stock_code: str, mark: str, actor: str, note: str = "") -> dict[str, Any]:
    """เอาเครื่องหมายออก ประวัติยังอยู่"""
    code, mark, actor = _checked(stock_code, mark, actor)
    init_db()
    moment = _now()
    with _transaction() as conn:
        removed = conn.execute(
            "DELETE FROM item_marks WHERE stock_code = ? AND mark = ?", (code, mark)).rowcount
        conn.execute(
            "INSERT INTO item_mark_events (stock_code, mark, action, note, actor, at) "
            "VALUES (?, ?, 'clear', ?, ?, ?)", (code, mark, str(note or ""), actor, moment))
    return {"stock_code": code, "mark": mark, "action": "clear", "removed": removed,
            "actor": actor, "at": moment}


def marked(mark: str = PATIENT_SPECIFIC) -> dict[str, dict[str, Any]]:
    """รายการที่ติดเครื่องหมายนี้อยู่ คีย์คือรหัสรายการ"""
    if not DB_PATH.exists():
        return {}
    with connect() as conn:
        return {row["stock_code"]: dict(row) for row in conn.execute(
            "SELECT stock_code, mark, note, set_by, set_at FROM item_marks WHERE mark = ?",
            (mark,))}


def history(stock_code: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """ประวัติการติดและปลดเครื่องหมาย ล่าสุดก่อน"""
    if not DB_PATH.exists():
        return []
    where, values = "", []
    if stock_code:
        where, values = "WHERE stock_code = ?", [str(stock_code).strip()]
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            f"SELECT * FROM item_mark_events {where} ORDER BY id DESC LIMIT ?",
            [*values, limit])]
