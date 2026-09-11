"""ทะเบียนคลังของโรงพยาบาล — รหัสคลัง ชื่อไทย และสถานะการใช้งาน

ฐานข้อมูลไม่มีตารางทะเบียนชื่อคลังที่อ่านได้ (TM_SK_SKSTORE_MT เก็บสินค้ารายคลัง
ไม่ใช่ชื่อคลัง) รายชื่อนี้จึงมาจากหน้าจอทะเบียนคลังของโรงพยาบาลโดยตรง

คลังที่เลิกใช้แล้วยังเก็บไว้ในทะเบียน เพราะข้อมูลเคลื่อนไหวย้อนหลังยังอ้างถึงอยู่
รายงานจึงต้องแปลรหัสเป็นชื่อได้ แต่ไม่ควรนับรวมในภาพรวมปัจจุบัน
"""
from typing import NamedTuple


class Store(NamedTuple):
    code: str
    name_th: str
    name_en: str
    active: bool = True


#: รหัสคลัง -> ข้อมูลคลัง (ตามทะเบียนของโรงพยาบาล)
STORES: dict[str, Store] = {
    store.code: store
    for store in (
        Store("1", "คลังพัสดุ", "ฝ่ายพัสดุและบำรุงรักษา"),
        Store("1R", "คลังพัสดุ (พักของ)", "กลุ่มงานพัสดุ"),
        Store("2", "คลังยาและเวชภัณฑ์", "กลุ่มงานเภสัชกรรม"),
        Store("3", "คลังจ่ายกลาง", "Central Supply"),
        Store("4", "คลังครุภัณฑ์", ""),
        Store("6", "คลังผลิตยาทั่วไป", "Finish Goods"),
        Store("7", "คลังยาผลิตปราศจากเชื้อ", "Sterile Product"),
        Store("8", "คลังโภชนาการ", "Nutrition Store"),
        Store("9", "คลังศูนย์เครื่องช่วยหายใจ", "Respiratory Center"),
        Store("20", "ห้องซักฟอกและตัดเย็บ", ""),
        Store("99", "ห้องจ่ายยาเคมีบำบัด", ""),
        Store("AN", "คลังวิสัญญี (เดิม)", "", active=False),
        Store("B", "คลังวัสดุดามกระดูก", "Bone Fixation Materials"),
        Store("CCU", "คลัง CCU", ""),
        Store("CL", "คลังห้องตรวจสวนหัวใจและหลอดเลือด", "Cath.Lab"),
        Store("COM", "คลังวัสดุคอมพิวเตอร์", ""),
        Store("DN", "คลังทันตกรรม", "กลุ่มงานทันตกรรม"),
        Store("EAR", "ห้องเครื่องช่วยฟัง", "Earaid"),
        Store("ER", "ห้องจ่ายยาฉุกเฉิน (ER)", "Drug Emergency"),
        Store("I1", "ห้องจ่ายยาชั้น 4 (เดิม)", "", active=False),
        Store("I2", "ห้องจ่ายยาผู้ป่วยใน", "Drug IPD"),
        Store("IPD", "Local Store IPD", "Local Store IPD"),
        Store("IR", "คลังรังสีรักษา", ""),
        Store("IVF", "คลังเบิกน้ำเกลือ", "IV Fluid"),
        Store("LAB", "คลังวัสดุตรวจและวิทยาศาสตร์การแพทย์", "กลุ่มงานพยาธิวิทยา"),
        Store("LR", "ห้องคลอด", "Delivery Room"),
        Store("MT", "คลังซ่อมบำรุง", ""),
        Store("O1", "ห้องจ่ายยาผู้ป่วยนอก (เดิม)", "", active=False),
        Store("O2", "ห้องจ่ายยาฉุกเฉิน (เดิม)", "", active=False),
        Store("O3", "ห้องจ่ายยาเมตตา (เดิม)", "", active=False),
        Store("O4", "ห้องจ่ายยาจิตเวช (เดิม)", "", active=False),
        Store("O5", "ห้องยาผู้ป่วยนอกตึก 8 ชั้น", "Drug OPD 8 Floor"),
        Store("O6", "ห้องจ่ายยาผู้ป่วยนอก ชั้น 4", "Drug OPD Floor 4"),
        Store("O7", "ห้องเตรียมยาปลอดเชื้อ", ""),
        Store("OR", "ห้องผ่าตัด", "Operating Room"),
        Store("OR1", "ห้องผ่าตัด (จ่ายกลาง)", "Operating Room"),
        Store("P2", "ห้องจ่ายยา รพ.ลำปาง สาขาเทศบาล (เดิม)", "", active=False),
        Store("P3", "ห้องจ่ายยา ศสม.ม่อนกระทิง", "PCU3"),
        Store("PAN", "คลังวิสัญญี", "Patient Anesthesia"),
        Store("PCU", "ห้องจ่ายยา ศสม.หัวเวียง (เดิม)", "", active=False),
        Store("PMR", "เวชกรรมฟื้นฟู", "กลุ่มงานเวชกรรมฟื้นฟู"),
        Store("RH", "คลังกายอุปกรณ์", "กลุ่มงานเวชกรรมฟื้นฟู"),
        Store("SMC", "ห้องจ่ายยาเมตตา", "Drug Special Medical Clinic"),
        Store("VAC", "คลังวัคซีน", "Vaccine Service"),
        Store("X", "ห้องเอกซเรย์", "X-Ray Store"),
    )
}

#: คลังที่เลิกใช้แล้ว — ยังแปลชื่อได้ แต่ไม่นับในภาพรวมปัจจุบัน
RETIRED_STORES = frozenset(code for code, store in STORES.items() if not store.active)

#: คลังยาและเวชภัณฑ์ ซึ่งเป็นขอบเขตเดียวที่ Stock5 รายงานอยู่
PHARMACY_MAIN_STORE = "2"


def store_name(code: str) -> str:
    """ชื่อคลังภาษาไทย คืนรหัสเดิมถ้าไม่รู้จัก เพื่อไม่ให้ข้อมูลหายจากรายงาน"""
    key = str(code or "").strip()
    found = STORES.get(key) or STORES.get(key.upper())
    return found.name_th if found else (key or "(ไม่ระบุคลัง)")


def is_active(code: str) -> bool:
    """คลังที่ไม่รู้จักถือว่ายังใช้งาน เพื่อไม่ตัดข้อมูลจริงทิ้งโดยไม่ตั้งใจ"""
    key = str(code or "").strip()
    found = STORES.get(key) or STORES.get(key.upper())
    return found.active if found else True


def active_store_codes() -> list[str]:
    return sorted(code for code, store in STORES.items() if store.active)


def describe(code: str) -> dict[str, object]:
    key = str(code or "").strip()
    found = STORES.get(key) or STORES.get(key.upper())
    return {
        "code": key,
        "name": store_name(key),
        "name_en": found.name_en if found else "",
        "active": is_active(key),
        "known": found is not None,
    }
