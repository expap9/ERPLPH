"""แบ่งรายการพัสดุเป็น 4 กลุ่มด้วย STOCK_MASTER.MAINCATEGORY

Stock5 กรองด้วยรูปแบบรหัส `[12a-zA-Z]%` ซึ่งบังเอิญตรงกับ "ยา" เกือบทั้งหมด
แต่ไม่ใช่ทั้งหมด — การสำรวจฐานข้อมูลจริงพบว่ามียา 157 ล็อต มูลค่า 273,692 บาท
ที่รูปแบบรหัสตัดทิ้ง (ยาที่โรงพยาบาลผลิตเอง รหัสขึ้นต้น 49) ส่วน MAINCATEGORY
เป็นการจัดหมวดที่โรงพยาบาลกำหนดเอง จึงตรงกว่าและอธิบายได้

จำนวนรายการตามการสำรวจ 11 กันยายน 2569 (ทั้งหมด 41,083 รายการ):
    ยา                2,798    เวชภัณฑ์มิใช่ยา   2,907
    พัสดุ            30,366    อื่น ๆ            5,012

อนุมาน รอยืนยัน: รหัสหมวดใดอยู่กลุ่มไหนใน GROUPS จัดจากชื่อรายการตัวอย่าง ยังไม่ได้
เทียบกับหน้าจอกำหนดหมวดของ SSB (docs/document_checklist.md ข้อ 0)
"""
from typing import NamedTuple

DRUG = "drug"
MEDICAL_SUPPLY = "medical_supply"
MATERIAL = "material"
OTHER = "other"

#: ไม่กรองหมวดเลย — ใช้เมื่อต้องการทั้งโรงพยาบาลในการดึงครั้งเดียว
#:
#: ผู้ใช้สั่ง 16 ก.ย. 2569 ว่าทุกแผนกที่เบิกของต้องเห็นภาพ ไม่ใช่เฉพาะยา ถ้าดึงทีละหมวด
#: แต่ละงวดจะทับกันเอง เพราะกุญแจของตาราง periods คือ (งวด, คลัง, ชนิด) ไม่มีมิติหมวด
#: ดึงรวดเดียวแล้วค่อยแยกกลุ่มจาก MAINCATEGORY ของแต่ละรายการจึงตรงกว่าและง่ายกว่า
ALL = "all"


class Group(NamedTuple):
    key: str
    name_th: str
    categories: frozenset


GROUPS: tuple[Group, ...] = (
    Group(DRUG, "ยา", frozenset({"10", "11", "12", "14", "17"})),
    Group(MEDICAL_SUPPLY, "เวชภัณฑ์มิใช่ยา", frozenset({"2", "3"})),
    Group(MATERIAL, "พัสดุ", frozenset({"4", "5", "6", "7", "8"})),
    Group(OTHER, "อื่น ๆ", frozenset()),  # ทุกหมวดที่เหลือ เช่น 9 จ้างเหมา, 03 อาหาร
)

GROUP_BY_KEY: dict[str, Group] = {group.key: group for group in GROUPS}

_CATEGORY_TO_GROUP: dict[str, str] = {
    category: group.key for group in GROUPS for category in group.categories
}

#: ทะเบียนไม่มีฟิลด์บอกว่ารหัสถูกเลิกใช้ เจ้าหน้าที่จึงเขียนไว้ในชื่อแทน
#: รูปแบบที่พบจริงในฐานข้อมูลมีสามแบบ และสะกด "ยกเเลิก" ผิดอยู่ด้วย
#:   ((ยกเลิก) ถังขยะพลาสติก
#:   ((ไม่ใช้รหัสนี้) จ้างปรับปรุงอาคาร
#:   ((ใช้ 90104002 แทน)จ้างถ่ายเอกสารทั่วไป   <- ไม่มีคำว่ายกเลิกเลย
RETIRED_NAME_MARKERS = ("ยกเลิก", "ยกเเลิก", "ไม่ใช้", "ใช้รหัส", "แทน)")


def group_of(main_category: object) -> str:
    """กลุ่มของรายการ หมวดที่ไม่รู้จักตกไปอยู่ 'อื่น ๆ' ไม่ถูกทิ้ง"""
    return _CATEGORY_TO_GROUP.get(str(main_category or "").strip(), OTHER)


def group_name(key: str) -> str:
    group = GROUP_BY_KEY.get(key)
    return group.name_th if group else GROUP_BY_KEY[OTHER].name_th


def is_retired_item(name: object) -> bool:
    """รหัสที่เลิกใช้ถูกทำเครื่องหมายไว้ในชื่อ ไม่มีฟิลด์สถานะแยกในฐานข้อมูล"""
    text = str(name or "")
    return any(marker in text for marker in RETIRED_NAME_MARKERS)


def sql_group_expression(column: str = "sm.MAINCATEGORY") -> str:
    """CASE สำหรับใส่ใน SELECT ให้ SQL Server จัดกลุ่มให้ตั้งแต่ต้นทาง"""
    clauses = []
    for group in GROUPS:
        if not group.categories:
            continue
        values = ", ".join(f"'{value}'" for value in sorted(group.categories))
        clauses.append(f"WHEN {column} IN ({values}) THEN '{group.key}'")
    return "CASE " + " ".join(clauses) + f" ELSE '{OTHER}' END"


def sql_category_filter(group_key: str, column: str = "sm.MAINCATEGORY") -> str:
    """WHERE สำหรับดึงเฉพาะกลุ่มที่ต้องการ — ALL คือไม่กรอง"""
    if group_key == ALL:
        return "1 = 1"
    group = GROUP_BY_KEY.get(group_key)
    if group is None or not group.categories:
        known = ", ".join(
            f"'{value}'" for group_ in GROUPS for value in sorted(group_.categories)
        )
        return f"{column} NOT IN ({known})" if known else "1 = 1"
    values = ", ".join(f"'{value}'" for value in sorted(group.categories))
    return f"{column} IN ({values})"
