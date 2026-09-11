"""คำสั่งดึงข้อมูล — ยืมของ Stock5 มาเปลี่ยนขอบเขต ไม่เขียนใหม่

คำสั่งดึงข้อมูลของ Stock5 ไม่ใช่แค่ SELECT ธรรมดา แต่เป็นส่วนหนึ่งของเครื่อง
คำนวณ: มันประกอบข้อมูลรายล็อตให้ครบฟิลด์ที่ lot_reconciliation ต้องใช้สอบทาน
จำนวนกับรายการหลัก ถ้าเขียนคำสั่งใหม่เอง ตัวเลข MOS ของสองระบบจะไม่ตรงกัน
และไม่มีใครรู้ว่าฝั่งไหนถูก

ที่นี่จึงอ่านไฟล์ sql/monitor_queries.sql ของ Stock5 แล้วเปลี่ยนเฉพาะ "ขอบเขต"
สองอย่าง โดยไม่แตะไฟล์ต้นฉบับ
    คลัง        '2'              -> คลังที่ระบุ
    ตัวกรองรหัส  [12a-zA-Z]%      -> หมวดตาม MAINCATEGORY

การเปลี่ยนตัวกรองรหัสสำคัญ: การสำรวจพบว่ารูปแบบรหัสตัดยาจริงทิ้ง 157 ล็อต
มูลค่า 273,692 บาท (ยาที่โรงพยาบาลผลิตเอง รหัสขึ้นต้น 49)
"""
import hashlib
import re
from pathlib import Path

import categories
import stock5_engine

#: ชนิดเอกสารจ่าย ตามที่สำรวจฐานข้อมูลจริง 11 กันยายน 2569
DISPENSE = "32"       # จ่ายให้หน่วยเบิก 75,292 ใบ 21 คลัง — ไม่มีคลังคู่
TRANSFER = "35"       # โอนระหว่างคลัง  17,187 ใบ 14 คลัง — มีคลังคู่ทุกใบ
UNCLASSIFIED_33 = "33"  # 215 ใบ ยังไม่ทราบความหมาย
UNCLASSIFIED_34 = "34"  # 2,952 ใบ ออกจากคลังผลิต ยังไม่ทราบความหมาย

MOVEMENT_KINDS = {
    DISPENSE: "dispense",
    TRANSFER: "transfer",
    UNCLASSIFIED_33: "unclassified",
    UNCLASSIFIED_34: "unclassified",
}

#: ชนิดที่นับเป็น "การใช้จริง" ของโรงพยาบาล
#: การโอนไม่นับ เพราะของยังอยู่ในโรงพยาบาล และจะถูกจ่ายอีกทอดที่ปลายทาง
#: ชนิด 33/34 ยังไม่นับจนกว่าจะยืนยันความหมาย แต่เก็บข้อมูลไว้ครบ
CONSUMPTION_KINDS = frozenset({"dispense"})

_SOURCE_FILE = "sql/monitor_queries.sql"
_SECTION_PATTERN = r"-- [345]\. แฟ้ม (RECEIPT|DISTRIBUTION|INVENTORY)[^\n]*"


def movement_kind(document_type: object) -> str:
    return MOVEMENT_KINDS.get(str(document_type or "").strip(), "unclassified")


def counts_as_consumption(document_type: object) -> bool:
    return movement_kind(document_type) in CONSUMPTION_KINDS


def source_path() -> Path:
    return stock5_engine.stock5_home() / _SOURCE_FILE


def _read_source() -> str:
    path = source_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"ไม่พบคำสั่งดึงข้อมูลของ Stock5 ที่ {path} "
            "ตั้ง STOCK5_HOME ให้ชี้ไปที่โฟลเดอร์ Stock5")
    return path.read_text(encoding="utf-8")


def _category_filter(column: str, group_key: str) -> str:
    """ตัวกรองหมวด รองรับทั้งที่มีและไม่มี alias ของ STOCK_MASTER

    ในคำสั่งเดิม บางจุดกรองบน sm.STOCKCODE ซึ่ง join STOCK_MASTER ไว้แล้ว
    แต่บางจุดอยู่ใน subquery ของ SKMOVE ที่ไม่มี alias นั้น จึงใช้ IN (SELECT ...)
    ซึ่งใช้ได้ทั้งสองแบบ
    """
    values = categories.sql_category_filter(group_key, "cat_sm.MAINCATEGORY")
    return (f"{column} IN (SELECT cat_sm.STOCKCODE FROM dbo.STOCK_MASTER cat_sm "
            f"WITH (NOLOCK) WHERE {values})")


def build(kind: str, store: str, date_from: str, date_to: str,
          group_key: str = categories.DRUG, mappings: dict | None = None) -> str:
    """คำสั่งดึงข้อมูลหนึ่งชนิด สำหรับคลังหนึ่งและช่วงวันหนึ่ง"""
    wanted = kind.upper()
    if wanted not in ("RECEIPT", "DISTRIBUTION", "INVENTORY"):
        raise ValueError(f"ชนิดข้อมูลไม่ถูกต้อง: {kind}")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,10}", str(store or "")):
        raise ValueError(f"รหัสคลังไม่ถูกต้อง: {store}")
    for value in (date_from, date_to):
        if not re.fullmatch(r"\d{8}", str(value or "")):
            raise ValueError("วันที่ต้องเป็น YYYYMMDD")

    stock5_engine.install()
    from table_config import load_table_mappings, render_table_tokens

    content = render_table_tokens(_read_source(), mappings or load_table_mappings())
    sections = re.split(_SECTION_PATTERN, content)
    statements = {}
    for index in range(1, len(sections), 2):
        body = sections[index + 1]
        statements[sections[index]] = "SELECT" + body.split("SELECT", 1)[1].rsplit(";", 1)[0]
    if wanted not in statements:
        raise ValueError(f"ไม่พบคำสั่ง {wanted} ในไฟล์ต้นฉบับของ Stock5")

    sql = statements[wanted]
    # ขอบเขตคลัง — รูปแบบเดียวกันทุกจุดในไฟล์ต้นฉบับ
    sql = sql.replace("STORE = '2'", f"STORE = '{store}'")
    # ขอบเขตรายการ — เปลี่ยนจากรูปแบบรหัสเป็นหมวดที่โรงพยาบาลกำหนด
    sql = sql.replace("sm.STOCKCODE LIKE '[12a-zA-Z]%'",
                      _category_filter("sm.STOCKCODE", group_key))
    sql = sql.replace("iro.STOCKCODE LIKE '[12a-zA-Z]%'",
                      _category_filter("iro.STOCKCODE", group_key))
    sql = sql.replace("STOCKCODE LIKE '[12a-zA-Z]%'",
                      _category_filter("STOCKCODE", group_key))
    sql = sql.replace("{{DATE_FROM}}", date_from).replace("{{DATE_TO}}", date_to)

    leftover = re.findall(r"\[12a-zA-Z\]%|STORE = '2'|\{\{[A-Z_]+\}\}", sql)
    if leftover:
        raise ValueError("ยังเหลือขอบเขตเดิมที่ไม่ได้เปลี่ยน: " + ", ".join(sorted(set(leftover))))
    return sql


def fingerprint(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def describe() -> dict[str, object]:
    return {
        "source": str(source_path()),
        "available": source_path().is_file(),
        "document_types": dict(MOVEMENT_KINDS),
        "counts_as_consumption": sorted(CONSUMPTION_KINDS),
        "note": "ยืมคำสั่งของ Stock5 มาเปลี่ยนขอบเขต ไม่แก้ไฟล์ต้นฉบับ",
    }
