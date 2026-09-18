"""คลังข้อมูลของ ERPLPH — เก็บการเคลื่อนไหวทุกคลังแบบเพิ่มทีละเดือน

Stock5 เก็บผลการดึงเป็นไฟล์ JSON ทั้งก้อนและเขียนใหม่ทุกรอบ ซึ่งพอเหลือคลังเดียว
(78,586 บรรทัด = 141 MB) ยังไหว แต่การสำรวจพบว่าทุกคลังรวมกันคือ 930,720 บรรทัด
ต่อ 12 เดือน — ไฟล์เดียวจะโตเป็นหลาย GB และต้องอ่านใหม่ทั้งหมดทุกรอบ

ที่นี่จึงเก็บเป็น SQLite และเติมเป็นราย "งวด" (เดือน x คลัง) เพื่อให้ดึงเฉพาะ
ส่วนที่ยังไม่มีหรือที่เปลี่ยนไป ไม่ต้องอ่านประวัติทั้งหมดซ้ำ

หลักการที่ยกมาจาก Stock5 โดยตั้งใจ
- งวดหนึ่งถูกเขียนแบบทั้งหมดหรือไม่เขียนเลย ไม่มีสภาพครึ่ง ๆ กลาง ๆ
- เก็บ checksum และที่มาของทุกงวด เพื่อตรวจได้ว่าตัวเลขมาจากการดึงรอบไหน
- ไม่เดาแทนผู้ใช้ งวดที่ยังดึงไม่ครบจะไม่ถูกนำไปคำนวณ
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "warehouse_data"
DB_PATH = DATA_DIR / "erplph.db"

#: สถานะของงวด — มีเฉพาะงวดที่ complete เท่านั้นที่ถูกนำไปคำนวณ
PERIOD_PENDING = "pending"
PERIOD_COMPLETE = "complete"
PERIOD_FAILED = "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS periods (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period        TEXT NOT NULL,           -- YYYYMM
    store         TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- receipt | issue | balance
    status        TEXT NOT NULL DEFAULT 'pending',
    source_rows   INTEGER DEFAULT 0,
    stored_rows   INTEGER DEFAULT 0,
    data_sha256   TEXT DEFAULT '',
    query_sha256  TEXT DEFAULT '',
    -- กติกาหน่วยที่ใช้สอบทานตอนดึง ถ้าเปลี่ยน สถานะที่เก็บไว้ถือว่าเก่า (ดู unit_rules.py)
    units_sha256  TEXT DEFAULT '',
    pulled_at     TEXT DEFAULT '',
    message       TEXT DEFAULT '',
    UNIQUE(period, store, kind)
);

CREATE TABLE IF NOT EXISTS items (
    stock_code     TEXT PRIMARY KEY,
    name           TEXT DEFAULT '',
    trade_name     TEXT DEFAULT '',
    main_category  TEXT DEFAULT '',
    item_group     TEXT DEFAULT '',        -- drug | medical_supply | material | other
    base_unit      TEXT DEFAULT '',
    retired        INTEGER DEFAULT 0,
    updated_at     TEXT DEFAULT ''
);

-- ภาพคงคลังวันละหนึ่งภาพต่อคลัง: period ของตารางนี้คือวันที่ YYYYMMDD ไม่ใช่เดือน
CREATE TABLE IF NOT EXISTS balances (
    period       TEXT NOT NULL,
    store        TEXT NOT NULL,
    stock_code   TEXT NOT NULL,
    lot_no       TEXT NOT NULL DEFAULT '',
    qty          REAL DEFAULT 0,
    value        REAL DEFAULT 0,
    unit         TEXT DEFAULT '',
    expire_date  TEXT DEFAULT '',
    last_in_date TEXT DEFAULT '',
    PRIMARY KEY (period, store, stock_code, lot_no)
);

CREATE TABLE IF NOT EXISTS receipts (
    period       TEXT NOT NULL,
    store        TEXT NOT NULL,
    rcv_no       TEXT NOT NULL,
    suffix       TEXT NOT NULL DEFAULT '',
    stock_code   TEXT NOT NULL,
    lot_no       TEXT DEFAULT '',
    qty          REAL DEFAULT 0,
    value        REAL DEFAULT 0,
    unit         TEXT DEFAULT '',
    unit_price   REAL DEFAULT 0,
    po_no        TEXT DEFAULT '',
    supplier     TEXT DEFAULT '',
    rcv_date     TEXT DEFAULT '',
    -- หน่วยงานบนใบรับ ยังไม่ยืนยันว่าเป็นผู้ขอซื้อ/ขอจ้าง (ดู queries._RECEIPT_DEPARTMENT)
    division     TEXT DEFAULT '',
    dept         TEXT DEFAULT '',
    section      TEXT DEFAULT '',
    PRIMARY KEY (period, store, rcv_no, suffix, stock_code)
);

CREATE TABLE IF NOT EXISTS issues (
    period       TEXT NOT NULL,
    store        TEXT NOT NULL,
    irno         TEXT NOT NULL,
    suffix       TEXT NOT NULL DEFAULT '',
    movement_key TEXT NOT NULL DEFAULT '',
    stock_code   TEXT NOT NULL,
    lot_no       TEXT DEFAULT '',
    qty          REAL DEFAULT 0,
    value        REAL DEFAULT 0,
    unit         TEXT DEFAULT '',
    -- รหัสกลุ่มของกระทรวง (1-9) ที่ Stock5 ใช้ส่งแฟ้ม ไม่ใช่หน่วยงานจริงของโรงพยาบาล
    department   TEXT DEFAULT '',
    -- หน่วยงานที่ขอเบิก 3 ชั้นตามผังของโรงพยาบาล เช่น 208-02-02
    -- ชื่อของแต่ละรหัสอยู่ใน SSBSTOCK.SYSCONFIG (CTRLCODE 10028/10029/10030)
    division     TEXT DEFAULT '',
    dept         TEXT DEFAULT '',
    section      TEXT DEFAULT '',
    issued_at    TEXT DEFAULT '',
    -- ชนิดเอกสารตัดสินว่าบรรทัดนี้เป็นการใช้จริงหรือแค่ย้ายของภายในโรงพยาบาล
    -- 32 จ่ายให้หน่วยเบิก / 35 โอนระหว่างคลัง / 33,34 ยังไม่ทราบความหมาย
    document_type TEXT DEFAULT '',
    movement_kind TEXT DEFAULT '',
    -- ทิศทางจากฝั่งการเคลื่อนไหว: out = ของออกจากคลังนี้, in = ของเข้ามา
    -- คำสั่งของ Stock5 คืนขาเข้าของการโอนมาด้วย ถ้าไม่แยก ขาเข้าจะดูเหมือนการจ่าย
    direction    TEXT DEFAULT '',
    check_status TEXT DEFAULT '',
    check_reason TEXT DEFAULT '',
    PRIMARY KEY (period, store, irno, suffix, stock_code, movement_key)
);

CREATE INDEX IF NOT EXISTS idx_balances_lookup ON balances(store, stock_code);
CREATE INDEX IF NOT EXISTS idx_receipts_lookup ON receipts(store, stock_code, period);
CREATE INDEX IF NOT EXISTS idx_issues_lookup   ON issues(store, stock_code, period);
CREATE INDEX IF NOT EXISTS idx_issues_period   ON issues(period, store);
CREATE INDEX IF NOT EXISTS idx_issues_kind     ON issues(movement_kind, period);
CREATE INDEX IF NOT EXISTS idx_issues_division  ON issues(division, period);
CREATE INDEX IF NOT EXISTS idx_issues_store_period ON issues(store, period);
CREATE INDEX IF NOT EXISTS idx_items_group     ON items(item_group, retired);
"""

TABLE_FOR_KIND = {"receipt": "receipts", "issue": "issues", "balance": "balances"}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # การเติมทีละงวดเขียนหลายพันแถวต่อครั้ง WAL ทำให้ผู้อ่านไม่ถูกบล็อกระหว่างนั้น
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


#: คอลัมน์ที่เพิ่มเข้ามาหลังจากเคยสร้างฐานข้อมูลไปแล้ว
#: CREATE TABLE IF NOT EXISTS ไม่เติมคอลัมน์ให้ตารางที่มีอยู่ ฐานข้อมูลรุ่นเก่า
#: จึงต้องถูกอัปเกรดก่อน มิฉะนั้น index ที่อ้างคอลัมน์ใหม่จะสร้างไม่ได้
_ADDED_COLUMNS = {
    "issues": (("document_type", "TEXT DEFAULT ''"), ("movement_kind", "TEXT DEFAULT ''"),
               ("direction", "TEXT DEFAULT ''"), ("division", "TEXT DEFAULT ''"),
               ("dept", "TEXT DEFAULT ''"), ("section", "TEXT DEFAULT ''")),
    "periods": (("units_sha256", "TEXT DEFAULT ''"),),
    "receipts": (("division", "TEXT DEFAULT ''"), ("dept", "TEXT DEFAULT ''"),
                 ("section", "TEXT DEFAULT ''")),
}


def _upgrade(conn: sqlite3.Connection) -> list[str]:
    """เติมคอลัมน์ที่ขาดให้ฐานข้อมูลรุ่นเก่า โดยไม่แตะข้อมูลเดิม"""
    applied = []
    existing_tables = {
        row["name"] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns:
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                applied.append(f"{table}.{name}")
    return applied


def _without_leading_comments(statement: str) -> str:
    # คำสั่งถูกแยกประเภทจากคำแรก ความเห็นนำหน้าจะทำให้ตารางไม่ถูกสร้างโดยไม่มีข้อผิดพลาด
    lines = statement.strip().splitlines()
    while lines and lines[0].strip().startswith("--"):
        lines.pop(0)
    return "\n".join(lines).strip()


def init_db() -> list[str]:
    """สร้างตารางที่ยังไม่มี และอัปเกรดตารางเดิมให้มีคอลัมน์ครบ"""
    with connect() as conn:
        # สร้างตารางก่อน แล้วค่อยเติมคอลัมน์ที่ขาด จากนั้นจึงสร้าง index
        # ที่อาจอ้างคอลัมน์ใหม่ ลำดับนี้ทำให้ฐานข้อมูลรุ่นเก่าอัปเกรดได้
        statements = [_without_leading_comments(part) for part in SCHEMA.split(";")]
        statements = [statement for statement in statements if statement]
        for statement in statements:
            if statement.upper().startswith("CREATE TABLE"):
                conn.execute(statement)
        applied = _upgrade(conn)
        for statement in statements:
            if statement.upper().startswith("CREATE INDEX"):
                conn.execute(statement)
        conn.commit()
    return applied


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


def _digest(rows: Iterable[dict]) -> str:
    payload = json.dumps(list(rows), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def period_status(period: str, store: str, kind: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM periods WHERE period = ? AND store = ? AND kind = ?",
            (period, store, kind)).fetchone()
    return dict(row) if row else None


def is_period_complete(period: str, store: str, kind: str) -> bool:
    found = period_status(period, store, kind)
    return bool(found and found["status"] == PERIOD_COMPLETE)


def missing_periods(periods: Iterable[str], stores: Iterable[str],
                    kinds: Iterable[str] = ("receipt", "issue")) -> list[tuple[str, str, str]]:
    """งวดที่ยังไม่มีหรือยังไม่สมบูรณ์ — คือรายการงานที่ต้องดึงเพิ่มเท่านั้น"""
    wanted = [(p, s, k) for p in periods for s in stores for k in kinds]
    if not wanted:
        return []
    with connect() as conn:
        done = {
            (row["period"], row["store"], row["kind"])
            for row in conn.execute(
                "SELECT period, store, kind FROM periods WHERE status = ?", (PERIOD_COMPLETE,))
        }
    return [item for item in wanted if item not in done]


def replace_period(period: str, store: str, kind: str, rows: list[dict[str, Any]],
                   source_rows: int | None = None, query_sha256: str = "",
                   units_sha256: str = "") -> dict[str, Any]:
    """เขียนงวดหนึ่งแบบทั้งหมดหรือไม่เขียนเลย

    ลบของเดิมของงวดนั้นแล้วเขียนใหม่ในธุรกรรมเดียว ถ้าล้มกลางทางฐานข้อมูลจะกลับ
    ไปสภาพเดิม ไม่เหลืองวดที่มีข้อมูลครึ่งเดียวให้เผลอนำไปคำนวณ
    """
    table = TABLE_FOR_KIND.get(kind)
    if table is None:
        raise ValueError(f"ชนิดข้อมูลไม่ถูกต้อง: {kind}")

    columns = _columns_of(table)
    prepared = _unique_by_key(table, [_row_for(table, columns, row, period, store) for row in rows])
    placeholders = ", ".join("?" for _ in columns)
    statement = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"

    with _transaction() as conn:
        conn.execute(f"DELETE FROM {table} WHERE period = ? AND store = ?", (period, store))
        conn.executemany(statement, [[row[c] for c in columns] for row in prepared])
        conn.execute(
            """INSERT INTO periods (period, store, kind, status, source_rows, stored_rows,
                                    data_sha256, query_sha256, units_sha256, pulled_at, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
               ON CONFLICT(period, store, kind) DO UPDATE SET
                   status = excluded.status, source_rows = excluded.source_rows,
                   stored_rows = excluded.stored_rows, data_sha256 = excluded.data_sha256,
                   query_sha256 = excluded.query_sha256, units_sha256 = excluded.units_sha256,
                   pulled_at = excluded.pulled_at, message = ''""",
            (period, store, kind, PERIOD_COMPLETE,
             int(source_rows if source_rows is not None else len(rows)), len(prepared),
             _digest(prepared), query_sha256, units_sha256,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return {"period": period, "store": store, "kind": kind, "rows": len(prepared)}


def stored_fingerprints() -> dict[tuple[str, str, str], str]:
    """ลายนิ้วมือคำสั่งของทุกงวดที่ดึงสำเร็จ ใช้หางวดที่ดึงด้วยคำสั่งรุ่นเก่า"""
    with connect() as conn:
        return {
            (row["period"], row["store"], row["kind"]): row["query_sha256"] or ""
            for row in conn.execute(
                "SELECT period, store, kind, query_sha256 FROM periods WHERE status = ?",
                (PERIOD_COMPLETE,))
        }


def stored_unit_digests(kind: str = "issue") -> dict[tuple[str, str], tuple[str, str]]:
    """กติกาหน่วยที่แต่ละงวดใช้สอบทาน คู่กับเวลาที่ดึง"""
    with connect() as conn:
        return {
            (row["period"], row["store"]): (row["units_sha256"] or "", row["pulled_at"] or "")
            for row in conn.execute(
                "SELECT period, store, units_sha256, pulled_at FROM periods "
                "WHERE status = ? AND kind = ?", (PERIOD_COMPLETE, kind))
        }


def codes_by_period() -> dict[tuple[str, str], set[str]]:
    """รหัสรายการที่อยู่ในแต่ละงวดของใบจ่าย ใช้หางวดที่กติกาหน่วยของรหัสนั้นเปลี่ยน"""
    found: dict[tuple[str, str], set[str]] = {}
    with connect() as conn:
        for row in conn.execute("SELECT DISTINCT period, store, stock_code FROM issues"):
            found.setdefault((row["period"], row["store"]), set()).add(row["stock_code"])
    return found


def adopt_unit_digests(digests: dict[tuple[str, str], str], kind: str = "issue") -> int:
    """บันทึกลายนิ้วมือกติกาหน่วยให้งวดรุ่นเก่าที่พิสูจน์แล้วว่าสอบทานด้วยกติกาปัจจุบัน

    แตะเฉพาะงวดที่ยังไม่มีลายนิ้วมือ งวดที่มีแล้วต้องเปลี่ยนด้วยการดึงใหม่เท่านั้น
    """
    if not digests:
        return 0
    with _transaction() as conn:
        conn.executemany(
            "UPDATE periods SET units_sha256 = ? WHERE period = ? AND store = ? AND kind = ? "
            "AND status = ? AND COALESCE(units_sha256, '') = ''",
            [(digest, period, store, kind, PERIOD_COMPLETE)
             for (period, store), digest in digests.items()])
    return len(digests)


def latest_snapshots() -> dict[str, str]:
    """วันของภาพคงคลังล่าสุดที่ดึงสำเร็จ รายคลัง"""
    with connect() as conn:
        return {
            row["store"]: row["day"]
            for row in conn.execute(
                "SELECT store, MAX(period) AS day FROM periods "
                "WHERE kind = 'balance' AND status = ? GROUP BY store", (PERIOD_COMPLETE,))
        }


def mark_period_failed(period: str, store: str, kind: str, message: str) -> None:
    """บันทึกว่างวดนี้ดึงไม่สำเร็จ ข้อมูลเดิม (ถ้ามี) ไม่ถูกแตะ"""
    with _transaction() as conn:
        conn.execute(
            """INSERT INTO periods (period, store, kind, status, pulled_at, message)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(period, store, kind) DO UPDATE SET
                   status = excluded.status, pulled_at = excluded.pulled_at,
                   message = excluded.message""",
            (period, store, kind, PERIOD_FAILED,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), str(message)[:500]))


def upsert_items(rows: Iterable[dict[str, Any]]) -> int:
    """ทะเบียนรายการยา/พัสดุ ใช้ร่วมทุกคลัง จึงเก็บแยกจากงวด"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = [
        (str(row.get("stock_code") or "").strip(), str(row.get("name") or ""),
         str(row.get("trade_name") or ""), str(row.get("main_category") or ""),
         str(row.get("item_group") or ""), str(row.get("base_unit") or ""),
         1 if row.get("retired") else 0, now)
        for row in rows if str(row.get("stock_code") or "").strip()
    ]
    if not payload:
        return 0
    with _transaction() as conn:
        conn.executemany(
            """INSERT INTO items (stock_code, name, trade_name, main_category,
                                  item_group, base_unit, retired, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(stock_code) DO UPDATE SET
                   name = COALESCE(NULLIF(excluded.name, ''), items.name),
                   trade_name = COALESCE(NULLIF(excluded.trade_name, ''), items.trade_name),
                   main_category = COALESCE(NULLIF(excluded.main_category, ''), items.main_category),
                   item_group = COALESCE(NULLIF(excluded.item_group, ''), items.item_group),
                   base_unit = COALESCE(NULLIF(excluded.base_unit, ''), items.base_unit),
                   retired = CASE WHEN excluded.name = '' THEN items.retired
                                  ELSE excluded.retired END,
                   updated_at = excluded.updated_at""", payload)
    # คำสั่งแต่ละชนิดคืนคอลัมน์ไม่ครบเท่ากัน (คงคลังไม่มีหมวด) ค่าว่างจึงต้องไม่ทับค่าเดิม
    return len(payload)


def coverage() -> dict[str, Any]:
    """ขอบเขตข้อมูลที่มีอยู่จริง — รายงานต้องบอกได้ว่าครอบคลุมแค่ไหน"""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT kind, status, COUNT(*) AS periods,
                      MIN(period) AS first_period, MAX(period) AS last_period,
                      SUM(stored_rows) AS rows
               FROM periods GROUP BY kind, status""")]
        stores = [r["store"] for r in conn.execute(
            "SELECT DISTINCT store FROM periods WHERE status = ? ORDER BY store",
            (PERIOD_COMPLETE,))]
        items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return {"periods": rows, "stores": stores, "items": items,
            "database": str(DB_PATH), "exists": DB_PATH.exists()}


def _columns_of(table: str) -> list[str]:
    with connect() as conn:
        return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


_NUMERIC_COLUMNS = {"qty", "value", "unit_price"}


def _unique_by_key(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ตัดสำเนาที่เหมือนกันทุกช่อง และหยุดถ้าคีย์เดียวกันแต่ตัวเลขต่างกัน

    INSERT OR REPLACE จะเก็บแถวสุดท้ายไว้เงียบ ๆ ซึ่งเท่ากับเลือกจำนวนหรือราคาแทน
    ผู้ใช้ — หลักเดียวกับ Stock5 คือหยุดแล้วให้คนตรวจ
    """
    with connect() as conn:
        keys = [row["name"] for row in sorted(
            (r for r in conn.execute(f"PRAGMA table_info({table})") if r["pk"]),
            key=lambda r: r["pk"])]
    seen: dict[tuple, dict[str, Any]] = {}
    unique = []
    for row in rows:
        key = tuple(row[column] for column in keys)
        if key in seen:
            if seen[key] != row:
                raise ValueError(
                    f"พบข้อมูลต่างกันที่คีย์เดียวกันใน {table}: {', '.join(map(str, key[2:]))} "
                    "หยุดเก็บงวดนี้เพื่อไม่เลือกจำนวนหรือราคาแทนกัน")
            continue
        seen[key] = row
        unique.append(row)
    return unique


def _row_for(table: str, columns: list[str], row: dict[str, Any],
             period: str, store: str) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for column in columns:
        if column == "period":
            prepared[column] = period
        elif column == "store":
            prepared[column] = store
        elif column in _NUMERIC_COLUMNS:
            try:
                prepared[column] = float(row.get(column) or 0)
            except (TypeError, ValueError):
                prepared[column] = 0.0
        else:
            prepared[column] = str(row.get(column) or "")
    return prepared
