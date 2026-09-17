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
import functools
import hashlib
import re
from pathlib import Path

import categories
import stock5_engine
import stores

#: ชนิดเอกสารจ่าย ตามที่สำรวจฐานข้อมูลจริง 11 กันยายน 2569
DISPENSE = "32"       # จ่ายให้หน่วยเบิก 75,292 ใบ 21 คลัง — ไม่มีคลังคู่
TRANSFER = "35"       # โอนระหว่างคลัง  17,187 ใบ 14 คลัง — มีคลังคู่ทุกใบ
#: อนุมาน รอยืนยัน (docs/document_checklist.md ข้อ 5) — ทั้งสองชนิดไม่ถูกนับในยอดใด
UNCLASSIFIED_33 = "33"  # 215 ใบ เลขใบตัวอย่าง ADJ... น่าจะเป็นใบปรับยอด
UNCLASSIFIED_34 = "34"  # 2,952 ใบ พบในคลังผลิต 6, 7, O7

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


#: ยอดใช้สุทธิ = จ่ายออกที่สอบทานผ่าน − รับคืนทั้งหมด (ผู้ใช้ตัดสิน 11 ก.ย. 2569)
#:
#: ใบรับคืนสอบทานไม่ผ่านเสมอ เพราะเครื่องของ Stock5 ไม่รับรองการเคลื่อนไหวขาเข้า
#: ถ้านับตามสถานะอย่างเดียว ของที่จ่ายแล้วถูกคืนจะถูกนับเป็นการใช้เต็มจำนวน
#: ข้อมูลจริงของห้องจ่ายยาผู้ป่วยใน: รับคืน ฿54.5 ล้าน 99.95% อยู่บนล็อต "." และ
#: หักล้างกับใบจ่ายบนล็อต "." ฿54.1 ล้านที่สอบทานผ่าน — นับแบบไม่หักจะเกินจริง 22%
def is_return(document_type: object, direction: str) -> bool:
    return movement_kind(document_type) == "dispense" and direction == "in"


def source_path() -> Path:
    return stock5_engine.stock5_home() / _SOURCE_FILE


def _read_source() -> str:
    path = source_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"ไม่พบคำสั่งดึงข้อมูลของ Stock5 ที่ {path} "
            "ตั้ง STOCK5_HOME ให้ชี้ไปที่โฟลเดอร์ Stock5")
    return _cached_source(str(path), path.stat().st_mtime_ns)


@functools.lru_cache(maxsize=4)
def _cached_source(path: str, _mtime: int) -> str:
    # คีย์รวม mtime ไว้ ถ้า Stock5 แก้ไฟล์คำสั่ง แคชจะหมดอายุเอง
    return Path(path).read_text(encoding="utf-8")


@functools.lru_cache(maxsize=4)
def _statements(mappings_key: tuple, source: str) -> dict[str, str]:
    stock5_engine.install()
    from table_config import render_table_tokens

    content = render_table_tokens(source, dict(mappings_key))
    sections = re.split(_SECTION_PATTERN, content)
    found = {}
    for index in range(1, len(sections), 2):
        body = sections[index + 1]
        found[sections[index]] = "SELECT" + body.split("SELECT", 1)[1].rsplit(";", 1)[0]
    return found


#: รูปแบบเลขเอกสารของคลังหลัก ที่ Stock5 ใช้กรอง
#: ห้องจ่ายยาย่อยใช้เลขเอกสารคนละรูปแบบ ถ้าคงตัวกรองนี้ไว้ การจ่ายให้ผู้ป่วยของ
#: ห้องยาทั้งหมดจะถูกตัดทิ้ง — การดึงรอบแรกได้ชนิด 35 ล้วน ไม่มีชนิด 32 สักบรรทัด
_MAIN_STORE_ISSUE_NUMBERING = (
    ("AND (iro.IRNO LIKE '[0-9][0-9]D%' OR iro.IRNO LIKE 'M[0-9][0-9]%')",
     "AND iro.DOCUMENTTYPE IN ({types})"),
    ("AND (DOCUMENTNO LIKE '[0-9][0-9]D%' OR DOCUMENTNO LIKE 'M[0-9][0-9]%')",
     "AND DOCUMENTTYPE IN ({types})"),
)

#: หน่วยงานที่ขอเบิก มี 3 ชั้น (SKIR.DIVISION / DEPT / SECTION) เช่น 208-02-02
#: คำสั่งของ Stock5 ต่อสามช่องเป็นสายเดียวชื่อ DIS เพื่อ join ตารางรหัสกลุ่มของกระทรวง
#: แล้วส่งออกเป็น DIS_DEPT_GROUP ซึ่งเหลือแค่รหัสกลุ่ม 1-9 — พอสำหรับส่งกระทรวง
#: แต่ไม่พอสำหรับรายงาน "แผนกไหนเบิกอะไร" ที่ผู้บริหารขอ
#:
#: จึงขอทั้งสามช่องแยกกันมาด้วย แทนที่จะตัดสตริง DIS เอง เพราะยังไม่รู้ความกว้างจริง
#: ของแต่ละช่อง (ตาราง SYSCONFIG เก็บรหัสงานเป็น '101   01' คือกลุ่มงานกว้าง 6 ตัว
#: แต่ค่าที่อ่านจาก SKIR มาเป็น '208' ซึ่งผ่านการ strip แล้ว จึงบอกไม่ได้)
#: การเพิ่มคอลัมน์ผลลัพธ์ไม่เปลี่ยนแถวหรือตัวเลขใด ๆ ยอดของคลังหลักจึงยังตรงกับ Stock5
_DEPARTMENT_LEVELS = (
    "p.DIVISION + p.DEPT + p.[SECTION] AS DIS,",
    "p.DIVISION + p.DEPT + p.[SECTION] AS DIS,\n"
    "    p.DIVISION AS DIS_DIVISION, p.DEPT AS DIS_DEPT, p.[SECTION] AS DIS_SECTION,",
)

#: หมวดของรายการ ใช้จัดกลุ่มเป็น ยา / เวชภัณฑ์ / พัสดุ / อื่น ๆ
#:
#: คำสั่งของ Stock5 ไม่เคยส่งค่านี้ออกมา มันโผล่เฉพาะในเงื่อนไข WHERE ที่ ERPLPH เติมเข้าไป
#: ตอนกรองหมวด ทะเบียนรายการจึงเคยได้กลุ่มจาก "หมวดที่สั่งดึง" ไม่ใช่หมวดของรายการเอง
#: พอเปลี่ยนมาดึงทุกหมวดพร้อมกัน วิธีเดิมใช้ไม่ได้ ต้องขอค่ามาตรง ๆ
#: (ตรวจพบ 17 ก.ย. 2569 หลังดึงจริง: ทะเบียน 8,887 รายการตกไปอยู่กลุ่ม "อื่น ๆ" ทั้งหมด)
_ITEM_CATEGORY = (
    "LTRIM(RTRIM(sm.ENGLISHNAME)) AS ENGLISHNAME,",
    "LTRIM(RTRIM(sm.ENGLISHNAME)) AS ENGLISHNAME,\n    sm.MAINCATEGORY AS MAINCATEGORY,",
)


def _category_filter(column: str, group_key: str) -> str:
    """ตัวกรองหมวด รองรับทั้งที่มีและไม่มี alias ของ STOCK_MASTER

    ในคำสั่งเดิม บางจุดกรองบน sm.STOCKCODE ซึ่ง join STOCK_MASTER ไว้แล้ว
    แต่บางจุดอยู่ใน subquery ของ SKMOVE ที่ไม่มี alias นั้น จึงใช้ IN (SELECT ...)
    ซึ่งใช้ได้ทั้งสองแบบ
    """
    # ดึงทุกหมวดคือไม่กรองเลย ถ้าใส่ IN (SELECT ... FROM STOCK_MASTER) ทั้งที่ไม่กรองอะไร
    # จะกลายเป็นเงื่อนไขใหม่ว่ารหัสต้องมีในทะเบียน ซึ่งตัดของบางส่วนทิ้งโดยไม่ตั้งใจ
    if group_key == categories.ALL:
        return "1 = 1"
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
    from table_config import load_table_mappings

    resolved = mappings or load_table_mappings()
    statements = _statements(tuple(sorted(resolved.items())), _read_source())
    if wanted not in statements:
        raise ValueError(f"ไม่พบคำสั่ง {wanted} ในไฟล์ต้นฉบับของ Stock5")

    sql = statements[wanted]
    main_store = str(store) == stores.PHARMACY_MAIN_STORE

    original, replacement = _ITEM_CATEGORY
    if sql.count(original) != 1:
        raise ValueError("ไม่พบชื่อรายการในคำสั่งของ Stock5 ตามที่คาด "
                         "ต้องตรวจก่อนว่ายังจัดกลุ่ม ยา/เวชภัณฑ์/พัสดุ ได้ถูกต้อง")
    sql = sql.replace(original, replacement)

    if wanted == "DISTRIBUTION":
        original, replacement = _DEPARTMENT_LEVELS
        if original not in sql:
            raise ValueError("คำสั่งจ่ายของ Stock5 ไม่มีช่องหน่วยงานตามที่คาด "
                             "ต้องตรวจก่อนว่ารายงานรายแผนกยังดึงข้อมูลได้ถูกต้อง")
        sql = sql.replace(original, replacement)

    # คลังหลักใช้ขอบเขตของ Stock5 ตามเดิมทุกตัวอักษร ตัวเลขคลัง 2 ของสองระบบจึง
    # ต้องตรงกันเสมอ ซึ่งเป็นตัวตรวจไขว้ที่ดีที่สุดที่มี
    if not main_store and wanted == "DISTRIBUTION":
        types = ", ".join(f"'{value}'" for value in sorted(MOVEMENT_KINDS))
        for original, replacement in _MAIN_STORE_ISSUE_NUMBERING:
            if original not in sql:
                raise ValueError("รูปแบบคำสั่งจ่ายของ Stock5 เปลี่ยนไป ต้องตรวจการปรับขอบเขตใหม่")
            sql = sql.replace(original, replacement.format(types=types))

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

    # เมื่อคลังเป้าหมายคือ 2 เอง STORE = '2' คือค่าที่ถูกต้อง ไม่ใช่ของเหลือ
    # การ์ดรุ่นแรกนับมันเป็นของเหลือ คลังยาหลักจึงล้มครบทั้ง 24 งวดในการดึงรอบแรก
    leftover_pattern = r"\[12a-zA-Z\]%|\{\{[A-Z_]+\}\}"
    if not main_store:
        leftover_pattern += r"|STORE = '2'"
    leftover = re.findall(leftover_pattern, sql)
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
