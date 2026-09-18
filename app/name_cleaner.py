"""เครื่องมือทำความสะอาดชื่อยา เวชภัณฑ์ พัสดุ และชื่อบริษัทคู่ค้า (Vendor / Supplier)

แก้ปัญหาอักขระหรือตัวเลขตัวแรกซ้ำจากระบบฐานข้อมูล SSB ของโรงพยาบาล:
1. ตัวอักษรตัวแรกซ้ำกัน (เช่น 'ฟฟิลิปส์' -> 'ฟิลิปส์', 'รรวยแน่' -> 'รวยแน่', 'ซซิลลิค' -> 'ซิลลิค')
2. ตัวเลขตัวแรกซ้ำกันในรายการยา/เวชภัณฑ์ (เช่น '33TC' -> '3TC', '110%Glycerine' -> '10%Glycerine',
   '66-MERCAPTOPURINE' -> '6-MERCAPTOPURINE', '11.5% CAPD' -> '1.5% CAPD', '33 in 1' -> '3 in 1')
3. พยัญชนะไทยถูกทำสำเนาไว้หน้าสระนำ (เ, แ, โ, ใ, ไ) (เช่น 'กโกสินทร์' -> 'โกสินทร์',
   'จเจ ที' -> 'เจ ที', 'คเค เค เอ็น' -> 'เค เค เอ็น', 'บไบโอคอททอน' -> 'ไบโอคอททอน')
4. เครื่องหมาย backslash คั่นประเภทนิติบุคคลในชื่อคู่ค้า (เช่น 'ฟิลิปส์\\บริษัท' -> 'ฟิลิปส์ บริษัท')
5. ช่องว่างผิดปกติ (U+00A0 non-breaking space)
"""
import re
from typing import Optional

GENUINE_REPETITIONS = {
    "MMR",      # Measles, Mumps, Rubella vaccine
    "DDAVP",    # Desmopressin acetate
    "DDR",      # Double Data Rate (DDR RAM)
    "LLETZ",    # Large Loop Excision of the Transformation Zone
    "SS",       # Salmonella-Shigella (SS agar)
    "SSC",      # Stainless Steel Crown
    "TTC",      # Tibiotalocalcaneal Nail
    "CC1",      # CC1 Predilute
}


def clean_drug_name(value: Optional[object]) -> str:
    """ทำความสะอาดชื่อยาและเวชภัณฑ์ ตัดอักขระหรือตัวเลขตัวแรกที่ซ้ำจาก SSB"""
    if not value:
        return ""
    text = str(value).replace("\xa0", " ").strip()
    if not text:
        return ""

    token = re.split(r"[\s(\-/]", text, maxsplit=1)[0].upper()
    if token in GENUINE_REPETITIONS:
        return text

    # 1. พยัญชนะไทยซ้ำหน้าสระนำ (เ, แ, โ, ใ, ไ) เช่น 'กโกสินทร์' -> 'โกสินทร์', 'จเจ' -> 'เจ'
    if len(text) > 2 and text[1] in "เแโใไ" and text[0] == text[2] and "ก" <= text[0] <= "ฮ":
        return text[1:]

    # 2. ตัวอักษรหรือตัวเลขตัวแรกซ้ำกัน (ครอบคลุมทั้ง 0-9, ๐-๙, และ A-Z, ก-ฮ)
    if len(text) > 2 and (text[0].isalnum() or "\u0e50" <= text[0] <= "\u0e59") and text[0] == text[1]:
        return text[1:]

    return text


def clean_vendor_name(value: Optional[object]) -> str:
    """ทำความสะอาดชื่อบริษัทคู่ค้า / ผู้ขายจากใบ PO หรือใบรับ (Receipts)"""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    text = text.replace("\xa0", " ")

    if "\\" in text:
        parts = text.split("\\")
        main_part = clean_drug_name(parts[0].strip())
        other_parts = [p.strip() for p in parts[1:] if p.strip()]
        return " ".join([main_part] + other_parts)

    cleaned = clean_drug_name(text)
    return " ".join(cleaned.split())


def clean_name(value: Optional[object]) -> str:
    """ฟังก์ชันกลางสำหรับทำความสะอาดชื่อ ไม่ว่าจะเป็นชื่อยา พัสดุ หรือชื่อคู่ค้า"""
    return clean_vendor_name(value)
