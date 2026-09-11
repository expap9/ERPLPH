"""ยืมเครื่องคำนวณจาก Stock5 มาใช้ โดยไม่แก้ไฟล์ใด ๆ ของโปรเจกต์นั้น

เหตุผลที่ไม่คัดลอกโค้ดมา: สูตร MOS การสอบทานรายล็อต และการแปลงหน่วย ผ่านการ
ตรวจสอบกับข้อมูลจริงมาแล้ว ถ้าคัดลอกไว้อีกชุด วันหนึ่งสองฝั่งจะคำนวณไม่เหมือนกัน
แล้วไม่มีใครรู้ว่าฝั่งไหนถูก — ระบบการเงินรับความเสี่ยงแบบนั้นไม่ได้

ที่นี่จึงเพิ่ม app/ ของ Stock5 เข้า sys.path แล้ว import ตรง ๆ อ่านอย่างเดียว
ไม่เขียนทับ ไม่ patch และไม่เรียกส่วนที่ดึงข้อมูลหรือส่งข้อมูลกระทรวงของ Stock5

ตั้ง STOCK5_HOME ได้ถ้าติดตั้งไว้คนละที่ ค่าเริ่มต้นคือโฟลเดอร์ข้าง ๆ กัน
"""
import os
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parents[1]

#: โมดูลที่ยืมมา — เฉพาะส่วนคำนวณ ไม่รวมส่วนดึงข้อมูลหรือส่งกระทรวง
BORROWED_MODULES = (
    "lot_reconciliation",   # สอบทานจำนวนรายล็อตกับรายการหลัก
    "monitor_units",        # แปลงหน่วยจ่าย/คงคลังให้เทียบกันได้
    "special_units",        # ตารางหน่วยพิเศษของโรงพยาบาล
    "confirmed_units",      # หน่วยที่โรงพยาบาลยืนยันแล้ว
    "drug_names",           # ตัดอักขระซ้ำหน้าชื่อยา
)


def stock5_home() -> Path:
    configured = os.environ.get("STOCK5_HOME")
    if configured:
        return Path(configured).resolve()
    return (BASE_DIR.parent / "Stock5").resolve()


def engine_available() -> bool:
    home = stock5_home()
    return (home / "app" / "lot_reconciliation.py").is_file()


def install() -> Path:
    """ทำให้ import โมดูลของ Stock5 ได้ คืน path ที่ใช้

    ต่อท้าย sys.path ไม่ใช่แทรกหน้า เพื่อไม่ให้โมดูลชื่อซ้ำของ Stock5
    ไปบังโมดูลของโปรเจกต์นี้โดยไม่ตั้งใจ
    """
    home = stock5_home()
    app_dir = home / "app"
    if not (app_dir / "lot_reconciliation.py").is_file():
        raise FileNotFoundError(
            f"ไม่พบเครื่องคำนวณของ Stock5 ที่ {app_dir} "
            "ตั้งตัวแปรสภาพแวดล้อม STOCK5_HOME ให้ชี้ไปที่โฟลเดอร์ Stock5")
    for path in (str(home), str(app_dir)):
        if path not in sys.path:
            sys.path.append(path)
    return app_dir


def load(name: str):
    """นำเข้าโมดูลที่ยืมมาหนึ่งตัว ปฏิเสธชื่อที่ไม่ได้อยู่ในรายการ"""
    if name not in BORROWED_MODULES:
        raise ValueError(
            f"'{name}' ไม่อยู่ในรายการโมดูลที่ยืมได้: {', '.join(BORROWED_MODULES)}")
    install()
    return __import__(name)


def describe() -> dict[str, object]:
    home = stock5_home()
    return {
        "stock5_home": str(home),
        "available": engine_available(),
        "borrowed": list(BORROWED_MODULES),
        "note": "อ่านอย่างเดียว ไม่แก้ไฟล์ของ Stock5",
    }
