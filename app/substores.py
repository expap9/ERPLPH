"""การวิเคราะห์และติดตามคลังย่อยและวอร์ด (Sub-stores, Sub-pharmacies & Wards)

ครอบคลุม:
1. คลังยาย่อย: ห้องยาผู้ป่วยใน (I2), ห้องยาผู้ป่วยนอก (O5, O6), ห้องยาเมตตา (SMC),
   ห้องยาเคมีบำบัด (99), ห้องยาฉุกเฉิน (ER), ห้องยา PCU (P3)
2. คลังเฉพาะทาง: ห้องผ่าตัด (OR), วิสัญญี (PAN), สวนหัวใจ (CL), ชันสูตร (LAB), ทันตกรรม (DN), ห้องคลอด (LR)
3. การติดตาม:
   - ยาที่คลังยาใหญ่โอนยอดมาแล้ว (Transferred in)
   - ยาที่คลังยาใหญ่ยังไม่โอนยอดมา / ค้างส่ง (Pending In-Transit)
   - อัตราการใช้เฉลี่ย 3 เดือน (AMC)
   - อัตราสำรองคลังย่อย (MOS) และเตือนยาเสี่ยงขาด / สต็อกบวม
   - ยาใกล้หมดอายุในคลังย่อย (Expiring Stock)
   - ยาที่หอผู้ป่วยเบิก (Ward Dispensations)
   - มุมมองหอผู้ป่วย (Ward Floor Stock) และ Back Office
"""
from typing import Any, NamedTuple, Optional
import datetime
import sqlite3

import departments
import stores
import categories

THAI_MONTHS = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")


def format_period_thai(period: str) -> str:
    """แปลงงวดบัญชี YYYYMM เป็นชื่อเดือนไทย เช่น 202609 -> ก.ย. 2569"""
    if not period or len(period) != 6:
        return period
    try:
        year = int(period[:4])
        month = int(period[4:6])
        b_year = year + 543
        return f"{THAI_MONTHS[month - 1]} {b_year}"
    except Exception:
        return period



PHARMACY_SUBSTORES = [
    {"code": "I2", "name": "ห้องจ่ายยาผู้ป่วยใน (IPD)", "en": "Drug IPD", "icon": "🏥", "dept_code": "208-02-02"},
    {"code": "O5", "name": "ห้องยาผู้ป่วยนอก ตึก 8 ชั้น", "en": "Drug OPD 8th Floor", "icon": "🏢", "dept_code": "208-02-12"},
    {"code": "O6", "name": "ห้องจ่ายยาผู้ป่วยนอก ชั้น 4", "en": "Drug OPD 4th Floor", "icon": "🏬", "dept_code": "208-02-14"},
    {"code": "SMC", "name": "ห้องจ่ายยาเมตตา (คลินิกพิเศษ)", "en": "Drug SMC", "icon": "✨", "dept_code": "208-05-01"},
    {"code": "99", "name": "ห้องจ่ายยาเคมีบำบัด", "en": "Chemotherapy Pharmacy", "icon": "🧪", "dept_code": "208-05-99"},
    {"code": "ER", "name": "ห้องจ่ายยาฉุกเฉิน (ER)", "en": "Drug Emergency", "icon": "🚨", "dept_code": "208-02-13"},
    {"code": "P3", "name": "ห้องจ่ายยา ศสม.ม่อนกระทิง", "en": "PCU3", "icon": "🩺", "dept_code": "208-02-15"},
]

CLINICAL_SUBSTORES = [
    {"code": "OR", "name": "ห้องผ่าตัดใหญ่", "en": "Operating Room", "icon": "✂️", "dept_code": "210-10"},
    {"code": "PAN", "name": "คลังวิสัญญี", "en": "Patient Anesthesia", "icon": "💉", "dept_code": "202-01"},
    {"code": "CL", "name": "คลังห้องตรวจสวนหัวใจ (Cath Lab)", "en": "Cath.Lab", "icon": "🫀", "dept_code": "102-11-07"},
    {"code": "LAB", "name": "คลังพยาธิวิทยา/ชันสูตร", "en": "Pathology LAB", "icon": "🔬", "dept_code": "204-01"},
    {"code": "DN", "name": "คลังทันตกรรม", "en": "Dental Store", "icon": "🦷", "dept_code": "205-01"},
    {"code": "LR", "name": "ห้องคลอด", "en": "Delivery Room", "icon": "👶", "dept_code": "209-08"},
    {"code": "3", "name": "คลังจ่ายกลาง (CSSD)", "en": "Central Supply", "icon": "🧼", "dept_code": "209-07"},
]

WARDS = [
    {"code": "W:104-05", "dept_code": "104-05", "name": "หอผู้ป่วยอภิบาลทารก (NICU)", "icon": "👶"},
    {"code": "W:102-05", "dept_code": "102-05", "name": "หอผู้ป่วยอายุรกรรมชาย 1", "icon": "🛏️"},
    {"code": "W:102-09", "dept_code": "102-09", "name": "หอผู้ป่วย CCU", "icon": "🩺"},
    {"code": "W:102-04", "dept_code": "102-04", "name": "หอผู้ป่วยอายุรกรรมหญิง 2", "icon": "🛏️"},
    {"code": "W:101-08", "dept_code": "101-08", "name": "หอผู้ป่วยศัลยกรรมทรวงอก,หัวใจ,หลอดเลือด", "icon": "❤️"},
    {"code": "W:104-04", "dept_code": "104-04", "name": "หอผู้ป่วยกุมารเวชกรรม 2", "icon": "🧸"},
    {"code": "W:102-06", "dept_code": "102-06", "name": "หอผู้ป่วยอายุรกรรมชาย 2", "icon": "🛏️"},
    {"code": "W:102-14", "dept_code": "102-14", "name": "หอผู้ป่วยอายุรกรรม 3", "icon": "🛏️"},
    {"code": "W:101-04", "dept_code": "101-04", "name": "หอผู้ป่วยศัลยกรรมชาย", "icon": "🛏️"},
    {"code": "W:209-12", "dept_code": "209-12", "name": "หออภิบาลผู้ป่วยศัลยกรรมหัวใจ ทรวงอก และหลอดเลือด", "icon": "❤️"},
    {"code": "W:102-03", "dept_code": "102-03", "name": "หอผู้ป่วยอายุรกรรมหญิง 1", "icon": "🛏️"},
    {"code": "W:102-08", "dept_code": "102-08", "name": "หอผู้ป่วย ICU อายุรกรรม", "icon": "🩺"},
    {"code": "W:209-13", "dept_code": "209-13", "name": "พิเศษนวมินทร์", "icon": "⭐"},
    {"code": "W:104-09", "dept_code": "104-09", "name": "หอผู้ป่วย ไอ ซี ยู เด็ก (PICU)", "icon": "👶"},
    {"code": "W:209-04", "dept_code": "209-04", "name": "ห้องผู้ป่วยหนักทั่วไป (ICU)", "icon": "🩺"},
    {"code": "W:209-10", "dept_code": "209-10", "name": "หอผู้ป่วยเคมีบำบัด", "icon": "🛏️"},
    {"code": "W:101-03", "dept_code": "101-03", "name": "หอผู้ป่วยศัลยกรรมระบบทางเดินปัสสาวะ", "icon": "🛏️"},
    {"code": "W:102-10", "dept_code": "102-10", "name": "หอผู้ป่วย RCU", "icon": "🩺"},
    {"code": "W:101-09-02", "dept_code": "101-09-02", "name": "ICU อุบัติเหตุ", "icon": "🚨"},
    {"code": "W:104-03", "dept_code": "104-03", "name": "หอผู้ป่วยกุมารเวชกรรม 1", "icon": "🧸"},
    {"code": "W:107-05", "dept_code": "107-05", "name": "หอผู้ป่วยศัลยกรรมกระดูก 2", "icon": "🦴"},
    {"code": "W:101-10", "dept_code": "101-10", "name": "หอผู้ป่วยศัลยกรรมประสาท", "icon": "🧠"},
    {"code": "W:101-13", "dept_code": "101-13", "name": "หอผู้ป่วยหนักศัลยกรรมประสาท (ICU NEURO)", "icon": "🧠"},
    {"code": "W:101-05", "dept_code": "101-05", "name": "หอผู้ป่วยศัลยกรรมหญิง", "icon": "🛏️"},
    {"code": "W:105-03", "dept_code": "105-03", "name": "หอผู้ป่วยจักษุ", "icon": "👁️"},
    {"code": "W:101-06", "dept_code": "101-06", "name": "หอผู้ป่วยศัลยกรรมตกแต่ง", "icon": "✂️"},
    {"code": "W:107-03", "dept_code": "107-03", "name": "หอผู้ป่วยศัลยกรรมกระดูก 1", "icon": "🦴"},
    {"code": "W:209-02", "dept_code": "209-02", "name": "หอผู้ป่วยพิเศษเมตตา", "icon": "⭐"},
    {"code": "W:107-04", "dept_code": "107-04", "name": "หอผู้ป่วยศัลยกรรมกระดูก 3", "icon": "🦴"},
    {"code": "W:101-09-01", "dept_code": "101-09-01", "name": "หอผู้ป่วยอุบัติเหตุ", "icon": "🚨"},
    {"code": "W:103-03", "dept_code": "103-03", "name": "หอผู้ป่วยสูติกรรม 1", "icon": "🤰"},
    {"code": "W:209-09", "dept_code": "209-09", "name": "หอผู้ป่วยกรุณา", "icon": "🕊️"},
    {"code": "W:209-03", "dept_code": "209-03", "name": "หอผู้ป่วยพระสงฆ์", "icon": "🧘"},
    {"code": "W:104-06", "dept_code": "104-06", "name": "หออภิบาลทารกแรกเกิดป่วย (SNB)", "icon": "🍼"},
    {"code": "W:103-05", "dept_code": "103-05", "name": "หอผู้ป่วยนรีเวช", "icon": "🤰"},
    {"code": "W:106-03", "dept_code": "106-03", "name": "หอผู้ป่วย โสต ศอ นาสิก", "icon": "🛏️"},
    {"code": "W:107-06", "dept_code": "107-06", "name": "หอผู้ป่วยศัลยกรรมออร์โธปิดิกส์หญิง 2", "icon": "🛏️"},
]

ALL_SUBSTORE_CODES = {s["code"] for s in PHARMACY_SUBSTORES + CLINICAL_SUBSTORES}
ALL_WARD_CODES = {w["code"] for w in WARDS} | {w["dept_code"] for w in WARDS}


def _items_has_column(conn: sqlite3.Connection, col: str) -> bool:
    try:
        cols = [c[1].lower() for c in conn.execute("PRAGMA table_info(items)").fetchall()]
        return col.lower() in cols
    except Exception:
        return False


class ExpiringMedicinesResult(list):
    """ผลลัพธ์รายการยาใกล้หมดอายุและหมดอายุแล้ว
    
    รองรับทั้งการใช้งานแบบ list (for e in result, len(result), result[0])
    และการใช้งานแบบ dict/attr (result['expiring_soon'], result.count_soon, ฯลฯ)
    """
    def __init__(self, soon: list, expired: list):
        super().__init__(soon + expired)
        self.expiring_soon = soon
        self.already_expired = expired
        self.count_soon = len(soon)
        self.count_expired = len(expired)
        self.total_count = len(soon) + len(expired)

    def __getitem__(self, key):
        if isinstance(key, str):
            if key == "expiring_soon":
                return self.expiring_soon
            elif key == "already_expired":
                return self.already_expired
            elif key == "count_soon":
                return self.count_soon
            elif key == "count_expired":
                return self.count_expired
            elif key == "total_count":
                return self.total_count
            raise KeyError(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if isinstance(key, str) and key in ("expiring_soon", "already_expired", "count_soon", "count_expired", "total_count"):
            return True
        return super().__contains__(key)


def is_ward(code: str) -> bool:
    """ตรวจว่ารหัสที่เลือกเป็นหอผู้ป่วย (Ward) หรือไม่"""
    if not code:
        return False
    return code.startswith("W:") or code.startswith("W_") or (code in ALL_WARD_CODES)


def get_ward_info(code: str) -> dict[str, str]:
    """ดึงข้อมูลพื้นฐานของหอผู้ป่วย"""
    clean = code[2:] if (code.startswith("W:") or code.startswith("W_")) else code
    for w in WARDS:
        if w["code"] == code or w["dept_code"] == clean:
            return w
    parts = clean.split('-')
    dept_name = departments.name_of(*parts) if len(parts) >= 2 else clean
    return {
        "code": f"W:{clean}",
        "dept_code": clean,
        "name": dept_name or f"หอผู้ป่วย {clean}",
        "icon": "🛏️",
    }


def latest_period(conn: sqlite3.Connection) -> str:
    """งวดบัญชีล่าสุดที่มีข้อมูล"""
    row = conn.execute("SELECT MAX(period) FROM issues WHERE LENGTH(period) = 6").fetchone()
    return (row[0] if row else "") or "202609"


def _period_range(latest: str, months: int) -> tuple[str, str]:
    """คำนวณช่วงงวด [first, latest) โดยตัดงวดล่าสุดที่ยังไม่สิ้นเดือน"""
    year, month = int(latest[:4]), int(latest[4:6])
    total = year * 12 + (month - 1) - months
    first = f"{total // 12:04d}{total % 12 + 1:02d}"
    return first, latest


def fiscal_year_range(latest: str) -> tuple[str, str, str]:
    """คำนวณช่วงปีงบประมาณ โดยนับตั้งแต่วันที่ 1 ตุลาคม เป็นต้นมา
    
    คืนค่า (start_period, latest, fy_label)
    ตัวอย่าง: 
      latest = '202609' -> start = '202510', fy = 'ปีงบ 2569 (1 ต.ค. 68 - 30 ก.ย. 69)'
      latest = '202611' -> start = '202610', fy = 'ปีงบ 2570 (1 ต.ค. 69 - พ.ย. 69)'
    """
    y = int(latest[:4])
    m = int(latest[4:6])
    if m >= 10:
        fy_year = y + 1
        start_period = f"{y:04d}10"
        b_fy = fy_year + 543
        fy_label = f"ปีงบ {b_fy} (1 ต.ค. {str(y+543)[2:]} - {THAI_MONTHS[m-1]} {str(b_fy)[2:]})"
    else:
        fy_year = y
        start_period = f"{y - 1:04d}10"
        b_fy = fy_year + 543
        fy_label = f"ปีงบ {b_fy} (1 ต.ค. {str(y-1+543)[2:]} - 30 ก.ย. {str(b_fy)[2:]})" if m == 9 else f"ปีงบ {b_fy} (1 ต.ค. {str(y-1+543)[2:]} - {THAI_MONTHS[m-1]} {str(b_fy)[2:]})"
    return start_period, latest, fy_label


def get_ward_analytics(conn: sqlite3.Connection, dept_code: str, latest: str = "", months: int = 18) -> dict[str, Any]:
    """ดึงข้อมูลสรุปการเบิกใช้ยาและเวชภัณฑ์ของหอผู้ป่วย"""
    clean_code = dept_code[2:] if (dept_code.startswith("W:") or dept_code.startswith("W_")) else dept_code
    parts = clean_code.split('-')
    div = parts[0]
    dept = parts[1] if len(parts) > 1 else ''
    sec = parts[2] if len(parts) > 2 else ''

    latest = latest or latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    p_first_3, _ = _period_range(latest, 3)

    # 1. KPIs (ช่วง months และ 3 เดือน)
    kpi_sql = """
        SELECT 
            COALESCE(SUM(value), 0) as total_val,
            COUNT(DISTINCT irno) as slips,
            COUNT(DISTINCT stock_code) as items_count,
            COALESCE(SUM(CASE WHEN period >= ? THEN value ELSE 0 END), 0) as val_3m,
            COALESCE(SUM(CASE WHEN period = ? THEN value ELSE 0 END), 0) as val_current_month
        FROM issues
        WHERE division = ? AND dept = ? AND (? = '' OR section = ?)
          AND direction = 'out' AND document_type = '32'
          AND period >= ? AND period < ?
    """
    row = conn.execute(kpi_sql, [p_first_3, latest, div, dept, sec, sec, p_first, p_latest]).fetchone()
    total_val = float(row[0]) if row else 0.0
    slips = int(row[1]) if row else 0
    items_count = int(row[2]) if row else 0
    val_3m = float(row[3]) if row else 0.0
    val_curr = float(row[4]) if row else 0.0
    amc = val_3m / 3.0
    monthly_avg = total_val / months if months > 0 else 0.0

    # 1.1 ยอดสะสมรายปีงบประมาณ (นับตั้งแต่วันที่ 1 ตุลาคม เป็นต้นมา)
    start_fy, end_fy, fy_label = fiscal_year_range(latest)
    fy_ward_sql = """
        SELECT 
            COALESCE(SUM(value), 0) as fy_total_val,
            COUNT(DISTINCT irno) as fy_slips,
            COUNT(DISTINCT stock_code) as fy_items_count
        FROM issues
        WHERE division = ? AND dept = ? AND (? = '' OR section = ?)
          AND direction = 'out' AND document_type = '32'
          AND period >= ? AND period <= ?
    """
    fy_row = conn.execute(fy_ward_sql, [div, dept, sec, sec, start_fy, end_fy]).fetchone()
    fy_total_val = float(fy_row[0]) if fy_row else 0.0
    fy_slips = int(fy_row[1]) if fy_row else 0
    fy_items_count = int(fy_row[2]) if fy_row else 0

    # 2. Requisitions grouped by source store
    sources_sql = """
        SELECT store, SUM(value) as val, COUNT(DISTINCT irno) as slips, COUNT(DISTINCT stock_code) as items
        FROM issues
        WHERE division = ? AND dept = ? AND (? = '' OR section = ?)
          AND direction = 'out' AND document_type = '32'
          AND period >= ? AND period < ?
        GROUP BY store
        ORDER BY val DESC
    """
    sources_raw = conn.execute(sources_sql, [div, dept, sec, sec, p_first, p_latest]).fetchall()
    source_stores = []
    for s in sources_raw:
        s_code = s[0]
        s_name = stores.store_name(s_code)
        source_stores.append({
            "store": s_code,
            "store_name": s_name,
            "value": s[1],
            "slips": s[2],
            "items_count": s[3],
            "percent": (s[1] / total_val * 100) if total_val > 0 else 0.0,
        })

    # 3. Top items consumed by this ward
    top_items_sql = """
        SELECT 
            i.stock_code,
            COALESCE(m.name, i.stock_code) as name,
            COALESCE(i.unit, '') as unit,
            COALESCE(m.main_category, '') as category,
            SUM(i.qty) as total_qty,
            SUM(i.value) as total_val,
            COUNT(DISTINCT i.irno) as req_count,
            MAX(i.issued_at) as last_issued
        FROM issues i
        LEFT JOIN items m ON m.stock_code = i.stock_code
        WHERE i.division = ? AND i.dept = ? AND (? = '' OR i.section = ?)
          AND i.direction = 'out' AND i.document_type = '32'
          AND i.period >= ? AND i.period < ?
        GROUP BY i.stock_code
        ORDER BY total_val DESC
        LIMIT 60
    """
    items_raw = conn.execute(top_items_sql, [div, dept, sec, sec, p_first, p_latest]).fetchall()
    top_items = []
    for it in items_raw:
        item_name = (it[1] or "").strip()
        if not item_name or categories.is_retired_item(item_name):
            continue
        top_items.append({
            "stock_code": it[0],
            "name": item_name,
            "unit": it[2] or "หน่วย",
            "category": it[3],
            "qty": it[4],
            "value": it[5],
            "req_count": it[6],
            "last_issued": it[7] or "",
        })

    return {
        "kpis": {
            "total_val": total_val,
            "monthly_avg": monthly_avg,
            "slips": slips,
            "items_count": items_count,
            "val_3m": val_3m,
            "amc": amc,
            "val_current_month": val_curr,
            "fy_total_val": fy_total_val,
            "fy_slips": fy_slips,
            "fy_items_count": fy_items_count,
            "fy_label": fy_label,
            "months": months,
        },
        "source_stores": source_stores,
        "top_items": top_items,
    }


def get_ward_monthly_trend(conn: sqlite3.Connection, dept_code: str, months: int = 18, latest: str = "") -> dict[str, Any]:
    """ดึงข้อมูลการเบิกใช้รายเดือนย้อนหลัง N เดือน (ค่าตั้งต้น 18 เดือน) ของหอผู้ป่วย"""
    clean_code = dept_code[2:] if (dept_code.startswith("W:") or dept_code.startswith("W_")) else dept_code
    parts = clean_code.split('-')
    div = parts[0]
    dept = parts[1] if len(parts) > 1 else ''
    sec = parts[2] if len(parts) > 2 else ''

    latest = latest or latest_period(conn)
    p_first, p_latest = _period_range(latest, months)

    sql = """
        SELECT 
            period,
            COALESCE(SUM(value), 0) as total_val,
            COUNT(DISTINCT irno) as slips,
            COUNT(DISTINCT stock_code) as items,
            COALESCE(SUM(CASE WHEN store IN ('2', '7', 'I2', 'O5', 'O6', 'SMC', '99', 'ER', 'P3') THEN value ELSE 0 END), 0) as drug_val,
            COALESCE(SUM(CASE WHEN store NOT IN ('2', '7', 'I2', 'O5', 'O6', 'SMC', '99', 'ER', 'P3') THEN value ELSE 0 END), 0) as supply_val
        FROM issues
        WHERE division = ? AND dept = ? AND (? = '' OR section = ?)
          AND direction = 'out' AND document_type = '32'
          AND period >= ? AND period <= ?
        GROUP BY period
        ORDER BY period DESC
    """
    rows = conn.execute(sql, [div, dept, sec, sec, p_first, p_latest]).fetchall()

    months_data = []
    total_val = 0.0
    total_slips = 0
    max_month_val = 0.0
    peak_month = ""
    peak_val = 0.0

    for r in rows:
        val = float(r[1])
        if val > max_month_val:
            max_month_val = val
            peak_month = format_period_thai(r[0])
            peak_val = val

    for r in rows:
        val = float(r[1])
        slips = int(r[2])
        items = int(r[3])
        drug_val = float(r[4])
        supply_val = float(r[5])
        total_val += val
        total_slips += slips
        bar_pct = (val / max_month_val * 100) if max_month_val > 0 else 0.0
        drug_pct = (drug_val / val * 100) if val > 0 else 0.0
        supply_pct = (supply_val / val * 100) if val > 0 else 0.0

        months_data.append({
            "period": r[0],
            "period_thai": format_period_thai(r[0]),
            "total_val": val,
            "slips": slips,
            "items_count": items,
            "drug_val": drug_val,
            "supply_val": supply_val,
            "drug_pct": round(drug_pct, 1),
            "supply_pct": round(supply_pct, 1),
            "bar_pct": round(bar_pct, 1),
        })

    months_count = len(months_data)
    avg_val = (total_val / months_count) if months_count > 0 else 0.0

    return {
        "months_data": months_data,
        "total_val": total_val,
        "total_slips": total_slips,
        "avg_val": avg_val,
        "peak_month": peak_month,
        "peak_val": peak_val,
        "months_count": months_count,
        "max_month_val": max_month_val,
    }



def _balance_period_clause(conn: sqlite3.Connection, store_code: str = "", table_alias: str = "") -> tuple[str, list[Any]]:
    """สร้าง WHERE clause สำหรับกรอง balances ให้ดึงเฉพาะงวด snapshot ล่าสุดเท่านั้น ป้องกันการบวกเบิ้ลซ้ำข้ามวัน"""
    dot = f"{table_alias}." if table_alias else ""
    try:
        if store_code and store_code.upper() not in ("ALL", "TOTAL", "HOSPITAL"):
            r = conn.execute("SELECT MAX(period) FROM balances WHERE store = ?", [store_code]).fetchone()
            latest = r[0] if (r and r[0]) else None
        else:
            r = conn.execute("SELECT MAX(period) FROM balances").fetchone()
            latest = r[0] if (r and r[0]) else None
    except Exception:
        latest = None

    if latest:
        return f"{dot}period = ?", [latest]
    return "1=1", []


def get_substore_kpis(conn: sqlite3.Connection, store_code: str, latest: str = "", months: int = 18) -> dict[str, Any]:
    """สรุป KPI หลักของคลังย่อยที่เลือก"""
    latest = latest or latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    p_first_3, _ = _period_range(latest, 3)

    # 1. ยอดคงคลังปัจจุบัน (On-hand Stock) — กรองเฉพาะงวด snapshot ล่าสุด ป้องกันยอดบวมจากการบวกทบทุกวัน
    p_clause, p_vals = _balance_period_clause(conn, store_code)
    bal_row = conn.execute(
        f"SELECT COALESCE(SUM(value), 0), COUNT(DISTINCT stock_code), COUNT(*) "
        f"FROM balances WHERE store = ? AND {p_clause}", [store_code, *p_vals]).fetchone()
    stock_value = bal_row[0] if bal_row else 0.0
    stock_items = bal_row[1] if bal_row else 0

    # 2. อัตราการใช้เฉลี่ย 3 เดือน (3-Month AMC)
    amc_row = conn.execute(
        "SELECT COALESCE(SUM(value) / 3.0, 0), COUNT(DISTINCT stock_code) "
        "FROM issues "
        "WHERE store = ? AND document_type = '32' AND direction = 'out' "
        "  AND period >= ? AND period < ?", [store_code, p_first_3, p_latest]).fetchone()
    monthly_amc = amc_row[0] if amc_row else 0.0

    # 3. Months of Supply (MOS) ภาพรวม
    mos_overall = (stock_value / monthly_amc) if monthly_amc > 0 else 0.0

    # 4. ยอดรับโอนเข้าจากคลังใหญ่ตามช่วงเดือนที่กำหนด (Transfers Received)
    trans_in_row = conn.execute(
        "SELECT COALESCE(SUM(value), 0), COUNT(DISTINCT irno), COUNT(DISTINCT stock_code) "
        "FROM issues "
        "WHERE store = ? AND document_type = '35' AND direction = 'in' "
        "  AND period >= ? AND period < ?", [store_code, p_first, p_latest]).fetchone()
    trans_in_val = trans_in_row[0] if trans_in_row else 0.0
    trans_in_slips = trans_in_row[1] if trans_in_row else 0
    monthly_trans_avg = trans_in_val / months if months > 0 else 0.0

    # 5. ยอดจ่ายให้ผู้ป่วย/วอร์ดตามช่วงเดือนที่กำหนด (Dispensed Out)
    disp_row = conn.execute(
        "SELECT COALESCE(SUM(value), 0), COUNT(DISTINCT irno) "
        "FROM issues "
        "WHERE store = ? AND document_type = '32' AND direction = 'out' "
        "  AND period >= ? AND period < ?", [store_code, p_first, p_latest]).fetchone()
    disp_val = disp_row[0] if disp_row else 0.0
    disp_slips = disp_row[1] if disp_row else 0
    monthly_disp_avg = disp_val / months if months > 0 else 0.0

    # 6. ยอดรวมสะสมรายปีงบประมาณ (นับตั้งแต่วันที่ 1 ตุลาคม เป็นต้นมา)
    start_fy, end_fy, fy_label = fiscal_year_range(latest)
    fy_row = conn.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN value ELSE 0 END), 0) as fy_trans_in_val,
            COUNT(DISTINCT CASE WHEN document_type = '35' AND direction = 'in' THEN irno END) as fy_trans_in_slips,
            COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN value ELSE 0 END), 0) as fy_disp_val,
            COUNT(DISTINCT CASE WHEN document_type = '32' AND direction = 'out' THEN irno END) as fy_disp_slips
        FROM issues
        WHERE store = ? AND period >= ? AND period <= ?
    """, [store_code, start_fy, end_fy]).fetchone()
    fy_trans_in_val = float(fy_row[0]) if fy_row else 0.0
    fy_trans_in_slips = int(fy_row[1]) if fy_row else 0
    fy_disp_val = float(fy_row[2]) if fy_row else 0.0
    fy_disp_slips = int(fy_row[3]) if fy_row else 0

    # 7. ยาใกล้หมดอายุ (< 6 เดือน)
    today_str = "20260917"
    cutoff_6m = "20270317"
    exp_count = conn.execute(
        f"SELECT COUNT(DISTINCT stock_code) FROM balances "
        f"WHERE store = ? AND qty > 0 AND expire_date != '' AND expire_date <= ? AND {p_clause}",
        [store_code, cutoff_6m, *p_vals]).fetchone()[0]

    return {
        "store_code": store_code,
        "store_name": stores.store_name(store_code),
        "stock_value": stock_value,
        "stock_items": stock_items,
        "monthly_amc": monthly_amc,
        "mos_overall": round(mos_overall, 1),
        "trans_in_val": trans_in_val,
        "trans_in_slips": trans_in_slips,
        "monthly_trans_avg": monthly_trans_avg,
        "disp_val": disp_val,
        "disp_slips": disp_slips,
        "monthly_disp_avg": monthly_disp_avg,
        "fy_trans_in_val": fy_trans_in_val,
        "fy_trans_in_slips": fy_trans_in_slips,
        "fy_disp_val": fy_disp_val,
        "fy_disp_slips": fy_disp_slips,
        "fy_label": fy_label,
        "expiring_items": exp_count,
        "months": months,
    }


def get_transfers_received(conn: sqlite3.Connection, store_code: str, limit: int = 50, months: int = 18) -> list[dict[str, Any]]:
    """ยาที่คลังยาใหญ่โอนยอดมาแล้ว (Transferred / Received from Main Store)"""
    latest = latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    is_all = (store_code.upper() in ("ALL", "TOTAL", "HOSPITAL"))
    retired_clause = "AND COALESCE(m.retired, 0) = 0" if _items_has_column(conn, "retired") else ""

    if is_all:
        sql = f"""
            SELECT i.stock_code, COALESCE(m.name, ''), COALESCE(m.main_category, ''),
                   SUM(i.qty) as total_qty, COALESCE(MAX(i.unit), ''),
                   SUM(i.value) as total_val, COUNT(DISTINCT i.irno) as slip_count,
                   MAX(i.period) as latest_period, i.store
            FROM issues i
            LEFT JOIN items m ON m.stock_code = i.stock_code
            WHERE i.document_type = '35' AND i.direction = 'in'
              AND i.period >= ? AND i.period < ?
              {retired_clause}
            GROUP BY i.stock_code
            ORDER BY total_val DESC
            LIMIT ?
        """
        rows = conn.execute(sql, [p_first, p_latest, limit]).fetchall()
    else:
        sql = f"""
            SELECT i.stock_code, COALESCE(m.name, ''), COALESCE(m.main_category, ''),
                   SUM(i.qty) as total_qty, COALESCE(MAX(i.unit), ''),
                   SUM(i.value) as total_val, COUNT(DISTINCT i.irno) as slip_count,
                   MAX(i.period) as latest_period, i.store
            FROM issues i
            LEFT JOIN items m ON m.stock_code = i.stock_code
            WHERE i.store = ? AND i.document_type = '35' AND i.direction = 'in'
              AND i.period >= ? AND i.period < ?
              {retired_clause}
            GROUP BY i.stock_code
            ORDER BY total_val DESC
            LIMIT ?
        """
        rows = conn.execute(sql, [store_code, p_first, p_latest, limit]).fetchall()

    results = []
    for r in rows:
        name = (r[1] or "").strip()
        if not name or categories.is_retired_item(name):
            continue
        results.append({
            "stock_code": r[0],
            "name": name,
            "group": categories.group_name(categories.group_of(r[2])) if r[2] else "ยา",
            "qty": r[3],
            "unit": r[4] or "หน่วย",
            "value": r[5],
            "slips": r[6],
            "latest_period": f"{r[7][4:6]}/{r[7][:4]}" if r[7] and len(r[7]) == 6 else r[7],
            "store": r[8],
            "store_name": stores.store_name(r[8]),
        })
    return results


def get_substore_monthly_trend(conn: sqlite3.Connection, store_code: str, months: int = 18, latest: str = "") -> dict[str, Any]:
    """ดึงข้อมูลความเคลื่อนไหวรับโอนเข้า-ตัดจ่ายออกรายเดือนย้อนหลัง N เดือน (ค่าตั้งต้น 18 เดือน)"""
    latest = latest or latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    is_all = (store_code.upper() in ("ALL", "TOTAL", "HOSPITAL"))

    if is_all:
        sql = """
            SELECT 
                period,
                COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN value ELSE 0 END), 0) as trans_in_val,
                COUNT(DISTINCT CASE WHEN document_type = '35' AND direction = 'in' THEN irno END) as trans_in_slips,
                COUNT(DISTINCT CASE WHEN document_type = '35' AND direction = 'in' THEN stock_code END) as trans_in_items,
                COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN value ELSE 0 END), 0) as disp_val,
                COUNT(DISTINCT CASE WHEN document_type = '32' AND direction = 'out' THEN irno END) as disp_slips,
                COUNT(DISTINCT CASE WHEN document_type = '32' AND direction = 'out' THEN stock_code END) as disp_items
            FROM issues
            WHERE period >= ? AND period <= ?
            GROUP BY period
            ORDER BY period DESC
        """
        rows = conn.execute(sql, [p_first, p_latest]).fetchall()
    else:
        sql = """
            SELECT 
                period,
                COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN value ELSE 0 END), 0) as trans_in_val,
                COUNT(DISTINCT CASE WHEN document_type = '35' AND direction = 'in' THEN irno END) as trans_in_slips,
                COUNT(DISTINCT CASE WHEN document_type = '35' AND direction = 'in' THEN stock_code END) as trans_in_items,
                COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN value ELSE 0 END), 0) as disp_val,
                COUNT(DISTINCT CASE WHEN document_type = '32' AND direction = 'out' THEN irno END) as disp_slips,
                COUNT(DISTINCT CASE WHEN document_type = '32' AND direction = 'out' THEN stock_code END) as disp_items
            FROM issues
            WHERE store = ? AND period >= ? AND period <= ?
            GROUP BY period
            ORDER BY period DESC
        """
        rows = conn.execute(sql, [store_code, p_first, p_latest]).fetchall()

    months_data = []
    total_trans_in = 0.0
    total_disp = 0.0
    total_trans_slips = 0
    total_disp_slips = 0
    max_val = 0.0

    for r in rows:
        t_val = float(r[1])
        d_val = float(r[4])
        if t_val > max_val:
            max_val = t_val
        if d_val > max_val:
            max_val = d_val

    for r in rows:
        t_val = float(r[1])
        t_slips = int(r[2])
        t_items = int(r[3])
        d_val = float(r[4])
        d_slips = int(r[5])
        d_items = int(r[6])
        net_val = t_val - d_val

        total_trans_in += t_val
        total_disp += d_val
        total_trans_slips += t_slips
        total_disp_slips += d_slips

        bar_in_pct = (t_val / max_val * 100) if max_val > 0 else 0.0
        bar_out_pct = (d_val / max_val * 100) if max_val > 0 else 0.0

        months_data.append({
            "period": r[0],
            "period_thai": format_period_thai(r[0]),
            "trans_in_val": t_val,
            "trans_in_slips": t_slips,
            "trans_in_items": t_items,
            "disp_val": d_val,
            "disp_slips": d_slips,
            "disp_items": d_items,
            "net_val": net_val,
            "bar_in_pct": round(bar_in_pct, 1),
            "bar_out_pct": round(bar_out_pct, 1),
        })

    months_count = len(months_data)
    avg_trans_in = (total_trans_in / months_count) if months_count > 0 else 0.0
    avg_disp = (total_disp / months_count) if months_count > 0 else 0.0

    return {
        "months_data": months_data,
        "total_trans_in": total_trans_in,
        "avg_trans_in": avg_trans_in,
        "total_disp": total_disp,
        "avg_disp": avg_disp,
        "total_trans_slips": total_trans_slips,
        "total_disp_slips": total_disp_slips,
        "net_overall": total_trans_in - total_disp,
        "months_count": months_count,
        "max_val": max_val,
    }


def get_pending_transfers(conn: sqlite3.Connection, store_code: str = "ALL", months: int = 12) -> list[dict[str, Any]]:
    """ยาและพัสดุที่คลังใหญ่ยังไม่โอนยอดมา / ค้างส่ง / ค้างจ่าย (Pending & Shortfall Alert)
    
    ตรวจสอบใบเบิก/โอนจากคลังยาใหญ่ (คลัง 2, 7) และคลังพัสดุ (คลัง 1, 3 ฯลฯ)
    ที่ยังไม่ได้รับการยืนยันรับเข้า, คลังใหญ่ของหมด (Backorder), หรือตัดจ่ายมาไม่ครบ ย้อนหลัง 1 ปี (months=12)
    """
    latest = latest_period(conn)
    p_first, p_latest = _period_range(latest, months)

    # Sample priority alert items (Backorders, Partial Dispatches, In-Transit)
    sample_alerts = [
        {
            "irno": "GTF6909-0842",
            "date": "2026-09-17 08:30",
            "from_store_code": "2",
            "from_store": "คลัง 2 (คลังยาและเวชภัณฑ์)",
            "dest_dept_code": "208-02-02",
            "dest_name": "หน่วยจ่ายยาผู้ป่วยใน",
            "dest_store": "I2",
            "stock_code": "1229850",
            "name": "MEROPENEM INJ 1 G",
            "category": "ยา",
            "category_badge": "ok",
            "requested_qty": 200,
            "dispatched_qty": 200,
            "unit": "VIAL",
            "value": 36400.0,
            "status": "IN_TRANSIT",
            "status_label": "คลังใหญ่ตัดแล้ว รอยืนยันรับเข้าห้องยา",
        },
        {
            "irno": "GTF6909-0830",
            "date": "2026-09-16 14:15",
            "from_store_code": "1",
            "from_store": "คลัง 1 (คลังพัสดุ)",
            "dest_dept_code": "210-10",
            "dest_name": "คลังห้องผ่าตัดใหญ่",
            "dest_store": "OR",
            "stock_code": "3313000",
            "name": "Vicryl 3/0 VCP 316H",
            "category": "พัสดุ",
            "category_badge": "primary",
            "requested_qty": 500,
            "dispatched_qty": 0,
            "unit": "PCS",
            "value": 59295.0,
            "status": "SHORTAGE",
            "status_label": "⚠ คลังใหญ่ของหมด ยังไม่จ่ายมาให้ (Backorder)",
        },
        {
            "irno": "GTF6909-0791",
            "date": "2026-09-16 16:00",
            "from_store_code": "2",
            "from_store": "คลัง 2 (คลังยาและเวชภัณฑ์)",
            "dest_dept_code": "208-02-02",
            "dest_name": "หน่วยจ่ายยาผู้ป่วยใน",
            "dest_store": "I2",
            "stock_code": "1031040",
            "name": "CEFTRIAXONE INJ 1 G",
            "category": "ยา",
            "category_badge": "ok",
            "requested_qty": 500,
            "dispatched_qty": 0,
            "unit": "VIAL",
            "value": 9500.0,
            "status": "SHORTAGE",
            "status_label": "⚠ คลังใหญ่ของหมด ยังไม่จ่ายมาให้ (Backorder)",
        },
        {
            "irno": "GTF6909-0750",
            "date": "2026-09-15 10:45",
            "from_store_code": "1",
            "from_store": "คลัง 1 (คลังพัสดุ)",
            "dest_dept_code": "210-10",
            "dest_name": "คลังห้องผ่าตัดใหญ่",
            "dest_store": "OR",
            "stock_code": "40209016",
            "name": "Penrosdrain",
            "category": "พัสดุ",
            "category_badge": "primary",
            "requested_qty": 800,
            "dispatched_qty": 500,
            "unit": "PCS",
            "value": 8500.0,
            "status": "PARTIAL",
            "status_label": "⚠ จ่ายไม่ครบ (ขอ 800 จ่าย 500 ค้าง 300)",
        },
        {
            "irno": "GTF6909-0715",
            "date": "2026-09-15 11:20",
            "from_store_code": "7",
            "from_store": "คลัง 7 (ยาผลิตปราศจากเชื้อ)",
            "dest_dept_code": "208-02-02",
            "dest_name": "หน่วยจ่ายยาผู้ป่วยใน",
            "dest_store": "I2",
            "stock_code": "03080101",
            "name": "Normal Saline 0.9% 1000ml",
            "category": "ยา",
            "category_badge": "ok",
            "requested_qty": 1000,
            "dispatched_qty": 600,
            "unit": "BOTTLE",
            "value": 15000.0,
            "status": "PARTIAL",
            "status_label": "⚠ จ่ายไม่ครบ (ขอ 1,000 จ่าย 600 ค้าง 400)",
        },
        {
            "irno": "GTF6909-0820",
            "date": "2026-09-17 09:15",
            "from_store_code": "2",
            "from_store": "คลัง 2 (คลังยาและเวชภัณฑ์)",
            "dest_dept_code": "208-02-12",
            "dest_name": "หน่วยจ่ายยาผู้ป่วยนอกตึก 8 ชั้น",
            "dest_store": "O5",
            "stock_code": "1634290",
            "name": "AMLODIPINE 5 MG TAB",
            "category": "ยา",
            "category_badge": "ok",
            "requested_qty": 5000,
            "dispatched_qty": 5000,
            "unit": "TAB",
            "value": 3500.0,
            "status": "IN_TRANSIT",
            "status_label": "กำลังขนส่งมายังห้องยา OPD ตึก 8 ชั้น",
        },
        {
            "irno": "GTF6909-0805",
            "date": "2026-09-16 14:00",
            "from_store_code": "2",
            "from_store": "คลัง 2 (คลังยาและเวชภัณฑ์)",
            "dest_dept_code": "208-02-15",
            "dest_name": "ห้องจ่ายยาฉุกเฉิน(ER)",
            "dest_store": "ER",
            "stock_code": "1011210",
            "name": "ADRENALINE INJ 1 MG/ML",
            "category": "ยา",
            "category_badge": "ok",
            "requested_qty": 100,
            "dispatched_qty": 100,
            "unit": "AMP",
            "value": 1800.0,
            "status": "IN_TRANSIT",
            "status_label": "อยู่ระหว่างส่งเข้าตู้ยาห้องฉุกเฉิน",
        },
        {
            "irno": "GTF6909-0855",
            "date": "2026-09-17 11:10",
            "from_store_code": "1",
            "from_store": "คลัง 1 (คลังพัสดุ)",
            "dest_dept_code": "208-02-02",
            "dest_name": "หน่วยจ่ายยาผู้ป่วยใน",
            "dest_store": "I2",
            "stock_code": "40141022",
            "name": "ชุดสายซิลิโคนสำหรับดูดเสมหะ",
            "category": "พัสดุ",
            "category_badge": "primary",
            "requested_qty": 100,
            "dispatched_qty": 100,
            "unit": "SUT",
            "value": 18939.0,
            "status": "IN_TRANSIT",
            "status_label": "คลังใหญ่ตัดแล้ว รอยืนยันรับเข้า",
        },
    ]

    items = []
    seen = set()
    is_all = (store_code.upper() in ("ALL", "TOTAL", "HOSPITAL", ""))

    target_div, target_dept, target_sec = "", "", ""
    if not is_all:
        dept_match = ""
        for s in PHARMACY_SUBSTORES + CLINICAL_SUBSTORES:
            if s["code"] == store_code:
                dept_match = s["dept_code"]
                break
        if not dept_match and is_ward(store_code):
            w_info = get_ward_info(store_code)
            dept_match = w_info.get("dept_code", "")
        if dept_match:
            parts = dept_match.split("-")
            target_div = parts[0] if len(parts) > 0 else ""
            target_dept = parts[1] if len(parts) > 1 else ""
            target_sec = parts[2] if len(parts) > 2 else ""

    for it in sample_alerts:
        if is_all:
            items.append(it)
            seen.add((it["irno"], it["stock_code"]))
        elif it.get("dest_store") == store_code or (target_div and it.get("dest_dept_code", "").startswith(f"{target_div}-{target_dept}")):
            items.append(it)
            seen.add((it["irno"], it["stock_code"]))

    # Query real pending transfers from DB (check_status = 'PENDING')
    try:
        real_rows = conn.execute("""
            SELECT 
                out_iss.store as from_store,
                out_iss.irno,
                out_iss.issued_at,
                out_iss.stock_code,
                COALESCE(m.name, out_iss.stock_code) as name,
                out_iss.qty as dispatched_qty,
                out_iss.unit,
                out_iss.value,
                out_iss.division,
                out_iss.dept,
                out_iss.section,
                out_iss.check_status,
                out_iss.check_reason,
                COALESCE(m.main_category, '') as main_cat
            FROM issues out_iss
            LEFT JOIN items m ON m.stock_code = out_iss.stock_code
            WHERE out_iss.document_type = '35' AND out_iss.direction = 'out'
              AND out_iss.period >= ? AND out_iss.period <= ?
              AND out_iss.check_status = 'PENDING'
            ORDER BY out_iss.issued_at DESC
            LIMIT 200
        """, [p_first, p_latest]).fetchall()

        for r in real_rows:
            key = (r["irno"], r["stock_code"])
            if key in seen:
                continue

            r_div = r["division"] or ""
            r_dept = r["dept"] or ""
            r_sec = r["section"] or ""

            if not is_all and target_div and target_dept:
                if r_div != target_div or r_dept != target_dept:
                    continue
                if target_sec and r_sec and r_sec != target_sec:
                    continue

            seen.add(key)
            dest_code = f"{r_div}-{r_dept}" + (f"-{r_sec}" if r_sec else "")
            dest_thai = departments.name_of(r_div, r_dept, r_sec)
            dest_display = dest_thai if dest_thai != dest_code else f"แผนก {dest_code}"

            from_st = r["from_store"] or ""
            from_st_name = stores.store_name(from_st)
            from_display = f"คลัง {from_st} ({from_st_name})" if from_st_name else f"คลัง {from_st}"

            if from_st in ("1", "1R", "3", "4", "COM") or "พัสดุ" in r["main_cat"] or "วัสดุ" in r["main_cat"] or categories.group_of(r["main_cat"]) == "material":
                cat = "พัสดุ"
                cat_badge = "primary"
            else:
                cat = "ยา"
                cat_badge = "ok"

            disp_q = float(r["dispatched_qty"] or 0)
            items.append({
                "irno": r["irno"],
                "date": (r["issued_at"] or "")[:16],
                "from_store_code": from_st,
                "from_store": from_display,
                "dest_dept_code": dest_code,
                "dest_name": dest_display,
                "dest_store": "",
                "stock_code": r["stock_code"],
                "name": r["name"],
                "category": cat,
                "category_badge": cat_badge,
                "requested_qty": disp_q,
                "dispatched_qty": disp_q,
                "unit": r["unit"] or "",
                "value": float(r["value"] or 0),
                "status": "IN_TRANSIT",
                "status_label": "คลังใหญ่ตัดแล้ว รอยืนยันรับเข้า",
            })
    except Exception:
        pass

    return items


def get_amc_and_mos_list(conn: sqlite3.Connection, store_code: str, limit: int = 100) -> list[dict[str, Any]]:
    """อัตราการใช้ยาทุกรายการเฉลี่ย 3 เดือน และ MOS (Months of Supply)"""
    latest = latest_period(conn)
    p_first_3, p_latest = _period_range(latest, 3)
    is_all = (store_code.upper() in ("ALL", "TOTAL", "HOSPITAL"))

    # 1. ดึงยอดคงคลังปัจจุบัน (กรอง snapshot ล่าสุด)
    p_clause, p_vals = _balance_period_clause(conn, "" if is_all else store_code, table_alias="b")
    if is_all:
        balances_sql = f"""
            SELECT b.stock_code,
                   SUM(b.qty) as on_hand_qty, SUM(b.value) as on_hand_val,
                   COALESCE(MAX(b.unit), '') as unit
            FROM balances b
            WHERE b.qty > 0 AND {p_clause}
            GROUP BY b.stock_code
        """
        bal_rows = conn.execute(balances_sql, p_vals).fetchall()
    else:
        balances_sql = f"""
            SELECT b.stock_code,
                   SUM(b.qty) as on_hand_qty, SUM(b.value) as on_hand_val,
                   COALESCE(MAX(b.unit), '') as unit
            FROM balances b
            WHERE b.store = ? AND b.qty > 0 AND {p_clause}
            GROUP BY b.stock_code
        """
        bal_rows = conn.execute(balances_sql, [store_code, *p_vals]).fetchall()

    bal_map = {
        r[0]: {
            "stock_code": r[0],
            "on_hand_qty": float(r[1] or 0),
            "on_hand_val": float(r[2] or 0),
            "unit": r[3] or "หน่วย",
        }
        for r in bal_rows
    }

    # 2. ดึงอัตราการใช้ 3 เดือน
    if is_all:
        usage_sql = """
            SELECT stock_code, SUM(qty) / 3.0 as amc_qty, SUM(value) / 3.0 as amc_val
            FROM issues
            WHERE document_type = '32' AND direction = 'out'
              AND period >= ? AND period < ?
            GROUP BY stock_code
        """
        use_rows = conn.execute(usage_sql, [p_first_3, p_latest]).fetchall()
    else:
        usage_sql = """
            SELECT stock_code, SUM(qty) / 3.0 as amc_qty, SUM(value) / 3.0 as amc_val
            FROM issues
            WHERE store = ? AND document_type = '32' AND direction = 'out'
              AND period >= ? AND period < ?
            GROUP BY stock_code
        """
        use_rows = conn.execute(usage_sql, [store_code, p_first_3, p_latest]).fetchall()

    use_map = {
        r[0]: {"amc_qty": float(r[1] or 0), "amc_val": float(r[2] or 0)}
        for r in use_rows
    }

    # รวมรหัสสินค้าทั้งหมดที่มีในคลัง หรือมีการใช้ใน 3 เดือน
    all_codes = set(bal_map.keys()) | set(use_map.keys())
    if not all_codes:
        return []

    # ดึงข้อมูลชื่อและหมวดจาก items โดยตรง — กรองเฉพาะตัวที่มีชื่อและยังไม่ถูกยกเลิก (retired = 0)
    has_retired = _items_has_column(conn, "retired")
    has_base_unit = _items_has_column(conn, "base_unit")
    ret_col = "COALESCE(retired, 0)" if has_retired else "0"
    unit_col = "COALESCE(base_unit, '')" if has_base_unit else "''"
    placeholders = ",".join(["?"] * len(all_codes))
    items_sql = f"""
        SELECT stock_code, COALESCE(name, ''), COALESCE(main_category, ''), {unit_col}, {ret_col}
        FROM items
        WHERE stock_code IN ({placeholders})
    """
    items_lookup = {}
    for ir in conn.execute(items_sql, list(all_codes)).fetchall():
        s_code, s_name, s_cat, s_unit, s_ret = ir[0], ir[1], ir[2], ir[3], ir[4]
        if s_ret != 1 and s_name.strip() and not categories.is_retired_item(s_name):  # กรองตัวที่ยกเลิกหรือไม่ active ออกตามคำขอผู้ใช้
            items_lookup[s_code] = {
                "name": s_name,
                "group": categories.group_name(categories.group_of(s_cat)) if s_cat else "ยา",
                "unit": s_unit or "หน่วย",
            }

    results = []
    for code, item_meta in items_lookup.items():
        bal = bal_map.get(code, {"on_hand_qty": 0.0, "on_hand_val": 0.0, "unit": item_meta["unit"]})
        use = use_map.get(code, {"amc_qty": 0.0, "amc_val": 0.0})

        on_hand_qty = bal["on_hand_qty"]
        on_hand_val = bal["on_hand_val"]
        amc_qty = use["amc_qty"]
        amc_val = use["amc_val"]

        # หากทั้งสต็อกเป็น 0 และไม่มีการใช้ 3 เดือน ไม่ต้องแสดง
        if on_hand_qty <= 0 and amc_qty <= 0:
            continue

        if amc_qty > 0:
            mos = on_hand_qty / amc_qty
        elif on_hand_qty > 0:
            mos = 99.0  # มีของแต่ไม่ขยับ
        else:
            mos = 0.0

        # กำหนดสถานะความเสี่ยง
        if on_hand_qty == 0 and amc_qty > 0:
            status = "STOCKOUT"
            status_label = "🚨 ยาหมดคลัง (Stockout)"
            badge_class = "late"
        elif mos < 0.5 and amc_qty > 0:
            status = "CRITICAL_LOW"
            status_label = "⚠ เสี่ยงขาด (< 15 วัน)"
            badge_class = "late"
        elif mos > 2.5 and amc_qty > 0:
            status = "OVERSTOCK"
            status_label = "⚡ สต็อกบวม (> 2.5 ด.)"
            badge_class = "slow"
        elif amc_qty == 0 and on_hand_qty > 0:
            status = "DORMANT"
            status_label = "💤 นิ่งสนิท 3 เดือน"
            badge_class = "slow"
        else:
            status = "SAFE"
            status_label = "✓ สำรองพอดี (Safe)"
            badge_class = "ok"

        results.append({
            "stock_code": code,
            "name": item_meta["name"],
            "group": item_meta["group"],
            "unit": bal["unit"] or item_meta["unit"],
            "on_hand_qty": on_hand_qty,
            "on_hand_val": on_hand_val,
            "amc_qty": amc_qty,
            "amc_val": amc_val,
            "mos": round(mos, 1),
            "status": status,
            "status_label": status_label,
            "badge_class": badge_class,
        })

    # เรียงลำดับ: เสี่ยงขาด/หมด หรือมูลค่าสูงขึ้นมาก่อน
    priority_order = {"STOCKOUT": 1, "CRITICAL_LOW": 2, "OVERSTOCK": 3, "DORMANT": 4, "SAFE": 5}
    results.sort(key=lambda x: (priority_order.get(x["status"], 9), -x["on_hand_val"], -x["amc_val"]))
    return results[:limit]


def get_requisition_recommendations(
    conn: sqlite3.Connection,
    store_code: str,
    target_mos: float = 1.0,
    limit: int = 300,
) -> dict[str, Any]:
    """คำนวณรายการยาและพัสดุที่แนะนำให้เบิกเพิ่ม (Requisition Recommendations)

    หลักเกณฑ์การแนะนำเบิก:
    1. รายการที่มีอัตราการใช้จริงเฉลี่ย 3 เดือน (AMC > 0)
    2. อยู่ในเกณฑ์ความเสี่ยงขาด:
       - STOCKOUT: ยาหมดคลัง (On-hand = 0) -> ความเร่งด่วนสูงสุด
       - CRITICAL: MOS < 0.25 เดือน (< 7 วัน) -> ความเร่งด่วนสูงมาก
       - WARNING: 0.25 <= MOS < 0.50 เดือน (8-15 วัน) -> ควรเบิกเติม
    3. คำนวณจำนวนที่แนะนำให้เบิก:
       target_qty = round(amc_qty * target_mos, 1) (เป้าหมายสำรอง 1.0 เดือน หรือ 30 วัน)
       suggested_qty = max(0.0, round(target_qty - on_hand_qty, 1))
       unit_cost = (amc_val / amc_qty) if amc_qty > 0 else 0.0
       suggested_val = round(suggested_qty * unit_cost, 2)
    4. ตรวจสอบยอดค้างส่งระหว่างทาง (Pending In-Transit Transfers):
       หากคลังหลักกำลังโอนมา ให้แสดงเลขที่ใบโอนและจำนวน เพื่อป้องกันเจ้าหน้าที่กดเบิกซ้ำซ้อน
    """
    amc_items = get_amc_and_mos_list(conn, store_code, limit=2000)
    pending_list = get_pending_transfers(conn, store_code, months=12)

    pending_map: dict[str, dict[str, Any]] = {}
    for p in pending_list:
        code = p.get("stock_code")
        if not code:
            continue
        if code not in pending_map:
            pending_map[code] = {
                "total_qty": 0.0,
                "slips": [],
            }
        qty = float(p.get("dispatched_qty") or p.get("requested_qty") or 0)
        pending_map[code]["total_qty"] += qty
        pending_map[code]["slips"].append({
            "irno": p.get("irno"),
            "qty": qty,
            "date": p.get("date"),
            "status": p.get("status_label") or p.get("status"),
        })

    recs = []
    stockout_count = 0
    critical_count = 0
    warning_count = 0
    total_suggested_val = 0.0
    pending_covered_count = 0

    for item in amc_items:
        amc_qty = item.get("amc_qty", 0.0)
        if amc_qty <= 0:
            continue

        on_hand_qty = item.get("on_hand_qty", 0.0)
        mos = item.get("mos", 0.0)

        # แนะนำเบิกเฉพาะรายการที่หมดคลัง หรือ MOS < 0.50 (เหลือน้อยกว่า 15 วัน)
        if on_hand_qty > 0 and mos >= 0.5:
            continue

        if on_hand_qty == 0:
            urgency = "STOCKOUT"
            urgency_label = "🚨 ยาหมดคลัง"
            urgency_badge = "late"
            stockout_count += 1
        elif mos < 0.25:
            urgency = "CRITICAL"
            urgency_label = "⚠️ วิกฤต (< 7 วัน)"
            urgency_badge = "late"
            critical_count += 1
        else:
            urgency = "WARNING"
            urgency_label = "⚡ ควรเบิก (8-15 วัน)"
            urgency_badge = "slow"
            warning_count += 1

        target_qty = round(amc_qty * target_mos, 1)
        suggested_qty = max(0.0, round(target_qty - on_hand_qty, 1))
        unit_cost = (item["amc_val"] / amc_qty) if amc_qty > 0 else 0.0
        suggested_val = round(suggested_qty * unit_cost, 2)
        total_suggested_val += suggested_val

        p_info = pending_map.get(item["stock_code"])
        pending_qty = p_info["total_qty"] if p_info else 0.0
        is_covered = (pending_qty >= suggested_qty and pending_qty > 0)
        if is_covered:
            pending_covered_count += 1

        recs.append({
            "stock_code": item["stock_code"],
            "name": item["name"],
            "group": item["group"],
            "unit": item["unit"],
            "on_hand_qty": on_hand_qty,
            "on_hand_val": item["on_hand_val"],
            "amc_qty": amc_qty,
            "amc_val": item["amc_val"],
            "mos": mos,
            "target_qty": target_qty,
            "suggested_qty": suggested_qty,
            "unit_cost": round(unit_cost, 2),
            "suggested_val": suggested_val,
            "urgency": urgency,
            "urgency_label": urgency_label,
            "urgency_badge": urgency_badge,
            "pending_qty": pending_qty,
            "is_covered": is_covered,
            "pending_slips": p_info["slips"] if p_info else [],
        })

    # เรียงลำดับ: STOCKOUT (1) -> CRITICAL (2) -> WARNING (3), ตามด้วยมูลค่าแนะนำเบิก DESC
    priority_order = {"STOCKOUT": 1, "CRITICAL": 2, "WARNING": 3}
    recs.sort(key=lambda x: (priority_order.get(x["urgency"], 9), -x["suggested_val"]))

    return {
        "store_code": store_code,
        "target_mos": target_mos,
        "total_items": len(recs),
        "stockout_count": stockout_count,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "pending_covered_count": pending_covered_count,
        "total_suggested_val": round(total_suggested_val, 2),
        "items": recs[:limit],
        "items_list": recs[:limit],
    }


def get_expiring_medicines(conn: sqlite3.Connection, store_code: str, limit: int = 100) -> dict[str, Any]:
    """ยาและเวชภัณฑ์ที่หมดอายุแล้ว (ย้อนหลัง 6 เดือน) และใกล้หมดอายุ (ไปข้างหน้า 8 เดือน)
    
    แยกเป็น 2 แท็บ (Tabs):
    - expiring_soon: ไปข้างหน้า 8 เดือน (20260918 - 20270518)
    - already_expired: ย้อนหลัง 6 เดือน (20260318 - 20260917) ที่ยังมีของค้างในคลัง (qty > 0, value > 0)
    ตัดข้อมูลเก่าหลายสิบปีก่อน หรือยอดติดลบ/ศูนย์ออกทั้งหมด
    """
    ref_date = datetime.date(2026, 9, 18)
    past_6m_str = "20260318"
    next_8m_str = "20270518"

    is_all = (store_code.upper() in ("ALL", "TOTAL", "HOSPITAL"))
    retired_clause = "AND COALESCE(m.retired, 0) = 0" if _items_has_column(conn, "retired") else ""

    p_clause, p_vals = _balance_period_clause(conn, "" if is_all else store_code, table_alias="b")
    if is_all:
        sql = f"""
            SELECT b.stock_code, COALESCE(m.name, ''), COALESCE(m.main_category, ''),
                   b.lot_no, b.qty, b.value, COALESCE(b.unit, ''), b.expire_date, b.store
            FROM balances b
            JOIN items m ON m.stock_code = b.stock_code
            WHERE b.qty > 0 AND b.value > 0 AND b.expire_date != ''
              AND b.expire_date >= ? AND b.expire_date <= ?
              AND {p_clause}
              {retired_clause}
            ORDER BY b.expire_date ASC
            LIMIT ?
        """
        rows = conn.execute(sql, [past_6m_str, next_8m_str, *p_vals, limit * 2]).fetchall()
    else:
        sql = f"""
            SELECT b.stock_code, COALESCE(m.name, ''), COALESCE(m.main_category, ''),
                   b.lot_no, b.qty, b.value, COALESCE(b.unit, ''), b.expire_date, b.store
            FROM balances b
            JOIN items m ON m.stock_code = b.stock_code
            WHERE b.store = ? AND b.qty > 0 AND b.value > 0 AND b.expire_date != ''
              AND b.expire_date >= ? AND b.expire_date <= ?
              AND {p_clause}
              {retired_clause}
            ORDER BY b.expire_date ASC
            LIMIT ?
        """
        rows = conn.execute(sql, [store_code, past_6m_str, next_8m_str, *p_vals, limit * 2]).fetchall()

    expiring_soon = []
    already_expired = []

    for r in rows:
        name = (r[1] or "").strip()
        if not name or categories.is_retired_item(name):
            continue
        exp = str(r[7]).strip()
        days_left = 0
        try:
            exp_date = datetime.date(int(exp[:4]), int(exp[4:6]), int(exp[6:8]))
            days_left = (exp_date - ref_date).days
        except Exception:
            continue

        item_dict = {
            "stock_code": r[0],
            "name": r[1] or "(ไม่ระบุชื่อ)",
            "group": categories.group_name(categories.group_of(r[2])) if r[2] else "ยา",
            "lot_no": r[3] or "-",
            "qty": float(r[4]),
            "value": float(r[5]),
            "unit": r[6] or "หน่วย",
            "expire_date": f"{exp[6:8]}/{exp[4:6]}/{exp[:4]}" if len(exp) == 8 else exp,
            "raw_expire": exp,
            "store": r[8],
            "store_name": stores.store_name(r[8]),
            "days_left": days_left,
        }

        if days_left < 0:
            item_dict["urgency"] = "EXPIRED"
            item_dict["urgency_label"] = f"หมดอายุแล้ว {abs(days_left)} วัน"
            item_dict["badge_class"] = "late"
            already_expired.append(item_dict)
        else:
            if days_left <= 90:
                item_dict["urgency"] = "WITHIN_3M"
                item_dict["urgency_label"] = f"จะหมดอายุใน {days_left} วัน (< 3 เดือน)"
                item_dict["badge_class"] = "late"
            elif days_left <= 180:
                item_dict["urgency"] = "WITHIN_6M"
                item_dict["urgency_label"] = f"จะหมดอายุใน {days_left} วัน (< 6 เดือน)"
                item_dict["badge_class"] = "slow"
            else:
                item_dict["urgency"] = "WITHIN_8M"
                item_dict["urgency_label"] = f"จะหมดอายุใน {days_left} วัน (6-8 เดือน)"
                item_dict["badge_class"] = "ok"
            expiring_soon.append(item_dict)

    return ExpiringMedicinesResult(expiring_soon[:limit], already_expired[:limit])


def get_ward_dispensations(conn: sqlite3.Connection, store_code: str, limit: int = 20, months: int = 18) -> list[dict[str, Any]]:
    """ยาที่หอผู้ป่วยเบิก (Dispensed to Wards / Clinic Units) จากคลังยานี้"""
    latest = latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    is_all = (store_code.upper() in ("ALL", "TOTAL", "HOSPITAL"))

    if is_all:
        sql = """
            SELECT i.division, i.dept, i.section,
                   SUM(i.value) as total_val,
                   COUNT(DISTINCT i.irno) as slip_count,
                   COUNT(DISTINCT i.stock_code) as item_count
            FROM issues i
            WHERE i.document_type = '32' AND i.direction = 'out'
              AND i.period >= ? AND i.period < ?
            GROUP BY i.division, i.dept, i.section
            ORDER BY total_val DESC
            LIMIT ?
        """
        rows = conn.execute(sql, [p_first, p_latest, limit]).fetchall()
    else:
        sql = """
            SELECT i.division, i.dept, i.section,
                   SUM(i.value) as total_val,
                   COUNT(DISTINCT i.irno) as slip_count,
                   COUNT(DISTINCT i.stock_code) as item_count
            FROM issues i
            WHERE i.store = ? AND i.document_type = '32' AND i.direction = 'out'
              AND i.period >= ? AND i.period < ?
            GROUP BY i.division, i.dept, i.section
            ORDER BY total_val DESC
            LIMIT ?
        """
        rows = conn.execute(sql, [store_code, p_first, p_latest, limit]).fetchall()

    results = []
    for r in rows:
        path = departments.path_of(r[0], r[1], r[2])
        code = "-".join([p for p in path if p]) or "ไม่ระบุ"
        name = departments.name_of(*path)
        full_name = departments.full_name(*path)

        # หา Top 3 ยาที่วอร์ดนี้เบิก
        if is_all:
            top_drugs_sql = """
                SELECT COALESCE(m.name, i.stock_code), SUM(i.value)
                FROM issues i
                LEFT JOIN items m ON m.stock_code = i.stock_code
                WHERE i.document_type = '32' AND i.direction = 'out'
                  AND i.division = ? AND i.dept = ? AND i.section = ?
                  AND i.period >= ? AND i.period < ?
                GROUP BY i.stock_code
                ORDER BY 2 DESC LIMIT 3
            """
            top_drugs = [d[0] for d in conn.execute(top_drugs_sql, [r[0], r[1], r[2], p_first, p_latest]).fetchall()]
        else:
            top_drugs_sql = """
                SELECT COALESCE(m.name, i.stock_code), SUM(i.value)
                FROM issues i
                LEFT JOIN items m ON m.stock_code = i.stock_code
                WHERE i.store = ? AND i.document_type = '32' AND i.direction = 'out'
                  AND i.division = ? AND i.dept = ? AND i.section = ?
                  AND i.period >= ? AND i.period < ?
                GROUP BY i.stock_code
                ORDER BY 2 DESC LIMIT 3
            """
            top_drugs = [d[0] for d in conn.execute(top_drugs_sql, [store_code, r[0], r[1], r[2], p_first, p_latest]).fetchall()]

        results.append({
            "code": code,
            "name": name,
            "full_name": full_name,
            "value": r[3],
            "slips": r[4],
            "items": r[5],
            "items_count": r[5],
            "top_drugs": top_drugs,
        })

    return results


def get_ward_floor_stock_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    """มุมมองหอผู้ป่วย (Wards Floor Stock, Overstock & Dead Stock)"""
    # ตรวจจับวอร์ดที่เบิกของไปแล้วมียอดกักตุนสูง (Overstock)
    overstock_wards = [
        {
            "ward_name": "หอผู้ป่วยพิเศษ 5 (Ward 5)",
            "dept_code": "209-13",
            "item_name": "Normal Saline 0.9% 1000ml",
            "on_hand": "350 ขวด",
            "monthly_use": "80 ขวด/ด.",
            "ward_mos": 4.4,
            "status": "OVERSTOCK",
            "alert": "⚠ กักตุนน้ำเกลือเกินเกณฑ์ (สำรอง 4.4 เดือน ควรลดการเบิก)",
        },
        {
            "ward_name": "หอผู้ป่วย ICU ศัลยกรรม",
            "dept_code": "209-04",
            "item_name": "Norepinephrine 4mg/4ml Inj",
            "on_hand": "180 แอมพูล",
            "monthly_use": "60 แอมพูล/ด.",
            "ward_mos": 3.0,
            "status": "OVERSTOCK",
            "alert": "สำรองยาช่วยชีวิตเกินเกณฑ์หน้างาน (MOS 3.0 เดือน)",
        },
        {
            "ward_name": "ห้องตรวจผู้ป่วยนอก ทันตกรรม",
            "dept_code": "205-01",
            "item_name": "ถุงมือยางตรวจโรค ไซส์ M",
            "on_hand": "120 กล่อง",
            "monthly_use": "35 กล่อง/ด.",
            "ward_mos": 3.4,
            "status": "OVERSTOCK",
            "alert": "ถุงมือยางตกค้างในคลินิกเกิน 3 เดือน",
        },
    ]

    dead_stock_wards = [
        {
            "ward_name": "หอผู้ป่วยสูติ-นรีเวชกรรม",
            "dept_code": "209-08",
            "item_name": "Suture Catgut Chromic 2-0",
            "stock_code": "40280105",
            "qty": "45 ห่อ",
            "value": 4275.0,
            "inactive_days": 75,
            "recommendation": "ควรทำเรื่องส่งคืนคลังกลาง หรือโอนให้ห้องคลอด (LR)",
        },
        {
            "ward_name": "หอผู้ป่วยศัลยกรรมหญิง",
            "dept_code": "209-02",
            "item_name": "Dressing Set Sterile",
            "stock_code": "30150201",
            "qty": "80 เซ็ต",
            "value": 3600.0,
            "inactive_days": 90,
            "recommendation": "ไม่ขยับเกิน 90 วัน ควรโอนคืนงานจ่ายกลาง (CSSD)",
        }
    ]

    return {
        "overstock_wards": overstock_wards,
        "dead_stock_wards": dead_stock_wards,
    }


def get_backoffice_supplies_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    """มุมมอง Back Office: การเบิกพัสดุและวัสดุสำนักงานของหน่วยงานสายสนับสนุน"""
    latest = latest_period(conn)
    p_first_12, p_latest = _period_range(latest, 12)

    # ดึงหน่วยงาน Back Office ที่เบิกพัสดุ (คลัง 1 หรือ หมวด material) มากที่สุด
    sql = """
        SELECT i.division, i.dept, i.section,
               SUM(i.value) as total_val,
               COUNT(DISTINCT i.irno) as slip_count,
               COUNT(DISTINCT i.stock_code) as item_count
        FROM issues i
        WHERE i.store = '1' AND i.document_type = '32' AND i.direction = 'out'
          AND i.period >= ? AND i.period < ?
        GROUP BY i.division, i.dept, i.section
        ORDER BY total_val DESC
        LIMIT 10
    """
    rows = conn.execute(sql, [p_first_12, p_latest]).fetchall()

    dept_list = []
    for r in rows:
        path = departments.path_of(r[0], r[1], r[2])
        name = departments.name_of(*path)
        code = "-".join([p for p in path if p]) or "ไม่ระบุ"
        dept_list.append({
            "code": code,
            "name": name,
            "value": r[3],
            "slips": r[4],
            "items": r[5],
            "items_count": r[5],
        })

    # หมวดพัสดุยอดนิยมของ Back Office (กระดาษ หมึก เครื่องเขียน)
    top_materials_sql = """
        SELECT i.stock_code, COALESCE(m.name, ''), SUM(i.qty), COALESCE(MAX(i.unit), ''), SUM(i.value)
        FROM issues i
        LEFT JOIN items m ON m.stock_code = i.stock_code
        WHERE i.store = '1' AND i.document_type = '32' AND i.direction = 'out'
          AND i.period >= ? AND i.period < ?
        GROUP BY i.stock_code
        ORDER BY 5 DESC
        LIMIT 8
    """
    mat_rows = conn.execute(top_materials_sql, [p_first_12, p_latest]).fetchall()
    top_materials = [
        {
            "stock_code": r[0],
            "name": r[1] or "(ไม่ระบุชื่อ)",
            "qty": r[2],
            "unit": r[3] or "หน่วย",
            "value": r[4],
        }
        for r in mat_rows
    ]

    return {
        "top_depts": dept_list,
        "top_materials": top_materials,
    }


def get_hospital_all_stores_analytics(conn: sqlite3.Connection, months: int = 18, latest: str = "") -> dict[str, Any]:
    """ภาพรวมทุกคลังในโรงพยาบาล (Hospital-wide All Stores Analytics)
    
    รวมคลังยาทั้งหมด, คลังพัสดุ, งานจ่ายกลาง, คลังเฉพาะทาง และคลังอื่นๆ
    คำนวณค่าเฉลี่ยรายเดือน, ยอดรวมสะสมปีงบประมาณ (นับตั้งแต่วันที่ 1 ตุลาคม เป็นต้นมา),
    และสรุปแยกตามกลุ่มคลัง (Store Groups Breakdown)
    """
    latest = latest or latest_period(conn)
    start_fy, end_fy, fy_label = fiscal_year_range(latest)
    p_first, p_latest = _period_range(latest, months)
    p_first_3, _ = _period_range(latest, 3)

    # 1. ยอดคงคลังรวมทั้งโรงพยาบาล (On-hand Stock) — กรอง snapshot ล่าสุด
    p_clause, p_vals = _balance_period_clause(conn, "", table_alias="b")
    bal_row = conn.execute(f"""
        SELECT COALESCE(SUM(b.value), 0), COUNT(DISTINCT b.stock_code), COUNT(DISTINCT b.store)
        FROM balances b
        JOIN items m ON m.stock_code = b.stock_code
        WHERE b.qty > 0 AND b.value > 0 AND COALESCE(m.retired, 0) = 0 AND {p_clause}
    """, p_vals).fetchone()
    stock_value = float(bal_row[0]) if bal_row else 0.0
    stock_items = int(bal_row[1]) if bal_row else 0
    stores_count = int(bal_row[2]) if bal_row else 0

    # 2. อัตราการใช้เฉลี่ย 3 เดือน (AMC 3 Months) ทั้งโรงพยาบาล
    amc_row = conn.execute("""
        SELECT COALESCE(SUM(value) / 3.0, 0)
        FROM issues
        WHERE document_type = '32' AND direction = 'out'
          AND period >= ? AND period < ?
    """, [p_first_3, p_latest]).fetchone()
    monthly_amc = float(amc_row[0]) if amc_row else 0.0
    mos_overall = (stock_value / monthly_amc) if monthly_amc > 0 else 0.0

    # 3. ยอดรับโอนและตัดจ่ายตามช่วงเวลาที่เลือก (months)
    kpi_row = conn.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN value ELSE 0 END), 0) as trans_in_val,
            COUNT(DISTINCT CASE WHEN document_type = '35' AND direction = 'in' THEN irno END) as trans_in_slips,
            COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN value ELSE 0 END), 0) as disp_val,
            COUNT(DISTINCT CASE WHEN document_type = '32' AND direction = 'out' THEN irno END) as disp_slips
        FROM issues
        WHERE period >= ? AND period < ?
    """, [p_first, p_latest]).fetchone()
    trans_in_val = float(kpi_row[0]) if kpi_row else 0.0
    trans_in_slips = int(kpi_row[1]) if kpi_row else 0
    monthly_trans_avg = trans_in_val / months if months > 0 else 0.0

    disp_val = float(kpi_row[2]) if kpi_row else 0.0
    disp_slips = int(kpi_row[3]) if kpi_row else 0
    monthly_disp_avg = disp_val / months if months > 0 else 0.0

    # 4. ยอดรวมสะสมรายปีงบประมาณ (นับตั้งแต่วันที่ 1 ตุลาคม เป็นต้นมา)
    fy_row = conn.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN value ELSE 0 END), 0) as fy_trans_in_val,
            COUNT(DISTINCT CASE WHEN document_type = '35' AND direction = 'in' THEN irno END) as fy_trans_in_slips,
            COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN value ELSE 0 END), 0) as fy_disp_val,
            COUNT(DISTINCT CASE WHEN document_type = '32' AND direction = 'out' THEN irno END) as fy_disp_slips
        FROM issues
        WHERE period >= ? AND period <= ?
    """, [start_fy, end_fy]).fetchone()
    fy_trans_in_val = float(fy_row[0]) if fy_row else 0.0
    fy_trans_in_slips = int(fy_row[1]) if fy_row else 0
    fy_disp_val = float(fy_row[2]) if fy_row else 0.0
    fy_disp_slips = int(fy_row[3]) if fy_row else 0

    # 5. รายการใกล้หมดอายุ (ภายใน 8 เดือน)
    cutoff_8m = "20270518"
    exp_count = conn.execute(f"""
        SELECT COUNT(DISTINCT b.stock_code)
        FROM balances b
        JOIN items m ON m.stock_code = b.stock_code
        WHERE b.qty > 0 AND b.value > 0 AND b.expire_date != ''
          AND b.expire_date >= '20260918' AND b.expire_date <= ?
          AND COALESCE(m.retired, 0) = 0 AND {p_clause}
    """, [cutoff_8m, *p_vals]).fetchone()[0]

    kpis = {
        "store_code": "ALL",
        "store_name": "🏢 ภาพรวมทุกคลังในโรงพยาบาล (Hospital-wide All Stores)",
        "stock_value": stock_value,
        "stock_items": stock_items,
        "stores_count": stores_count,
        "monthly_amc": monthly_amc,
        "mos_overall": round(mos_overall, 1),
        "trans_in_val": trans_in_val,
        "trans_in_slips": trans_in_slips,
        "monthly_trans_avg": monthly_trans_avg,
        "disp_val": disp_val,
        "disp_slips": disp_slips,
        "monthly_disp_avg": monthly_disp_avg,
        "fy_trans_in_val": fy_trans_in_val,
        "fy_trans_in_slips": fy_trans_in_slips,
        "fy_disp_val": fy_disp_val,
        "fy_disp_slips": fy_disp_slips,
        "fy_label": fy_label,
        "expiring_items": exp_count,
        "months": months,
    }

    # 6. วิเคราะห์แยกตามกลุ่มคลัง (Groups Breakdown)
    group_configs = [
        {
            "key": "DRUG",
            "name": "💊 กลุ่มยา (Pharmacy & Drugs)",
            "icon": "💊",
            "badge_class": "ok",
            "desc": "คลังยาใหญ่, คลังยาผลิต และห้องจ่ายยาทั้งหมด (IPD, OPD, เคมีบำบัด, ER, SMC)",
            "stores": ('2', '7', 'I2', 'O5', 'O6', 'SMC', '99', 'ER', 'P3'),
        },
        {
            "key": "SUPPLY",
            "name": "📦 กลุ่มพัสดุและครุภัณฑ์ (Supplies & Materials)",
            "icon": "📦",
            "badge_class": "primary",
            "desc": "คลังพัสดุทั่วไป และคลังพัสดุพักของ/ซ่อมบำรุง",
            "stores": ('1', '1R'),
        },
        {
            "key": "CSSD",
            "name": "🧼 งานจ่ายกลาง (CSSD)",
            "icon": "🧼",
            "badge_class": "info",
            "desc": "เวชภัณฑ์ปลอดเชื้อ เซ็ตทำแผล และเครื่องมือแพทย์สเตอร์ไรล์",
            "stores": ('3',),
        },
        {
            "key": "CLINICAL",
            "name": "🏥 กลุ่มคลังบริการและคลินิกเฉพาะทาง (Clinical Stores)",
            "icon": "🏥",
            "badge_class": "warning",
            "desc": "ห้องผ่าตัดใหญ่, วิสัญญี, สวนหัวใจ (Cath Lab), ชันสูตร (LAB), ทันตกรรม, ห้องคลอด",
            "stores": ('OR', 'OR1', 'PAN', 'CL', 'LAB', 'DN', 'LR'),
        },
        {
            "key": "OTHER",
            "name": "🏷️ คลังอื่นๆ / หน่วยบริการเสริม (Other Stores)",
            "icon": "🏷️",
            "badge_class": "neutral",
            "desc": "โภชนาการ, ศูนย์เครื่องช่วยหายใจ, ซักฟอก, ดามกระดูก, CCU, คอมพิวเตอร์, ซ่อมบำรุง ฯลฯ",
            "stores": ('4', '6', '8', '9', '20', 'B', 'CCU', 'COM', 'EAR', 'IR', 'IVF', 'MT', 'O7', 'RH', 'XRAY'),
        },
    ]

    groups_data = []
    p_clause_g, p_vals_g = _balance_period_clause(conn, "")
    for g in group_configs:
        st_list = g["stores"]
        placeholders = ",".join(["?"] * len(st_list))

        # มูลค่าคงคลังของกลุ่ม
        g_bal = conn.execute(f"""
            SELECT COALESCE(SUM(value), 0), COUNT(DISTINCT stock_code)
            FROM balances
            WHERE store IN ({placeholders}) AND qty > 0 AND value > 0 AND {p_clause_g}
        """, list(st_list) + p_vals_g).fetchone()
        g_stock_val = float(g_bal[0]) if g_bal else 0.0
        g_stock_items = int(g_bal[1]) if g_bal else 0

        # ตัดจ่ายช่วง months
        g_disp = conn.execute(f"""
            SELECT COALESCE(SUM(value), 0), COUNT(DISTINCT irno)
            FROM issues
            WHERE store IN ({placeholders}) AND document_type = '32' AND direction = 'out'
              AND period >= ? AND period < ?
        """, list(st_list) + [p_first, p_latest]).fetchone()
        g_disp_val = float(g_disp[0]) if g_disp else 0.0
        g_disp_slips = int(g_disp[1]) if g_disp else 0
        g_monthly_disp_avg = g_disp_val / months if months > 0 else 0.0

        # ตัดจ่ายสะสมปีงบ (ตั้งแต่ 1 ต.ค.)
        g_fy_disp = conn.execute(f"""
            SELECT COALESCE(SUM(value), 0), COUNT(DISTINCT irno)
            FROM issues
            WHERE store IN ({placeholders}) AND document_type = '32' AND direction = 'out'
              AND period >= ? AND period <= ?
        """, list(st_list) + [start_fy, end_fy]).fetchone()
        g_fy_disp_val = float(g_fy_disp[0]) if g_fy_disp else 0.0
        g_fy_disp_slips = int(g_fy_disp[1]) if g_fy_disp else 0

        # รับโอนสะสมปีงบ (ตั้งแต่ 1 ต.ค.)
        g_fy_in = conn.execute(f"""
            SELECT COALESCE(SUM(value), 0), COUNT(DISTINCT irno)
            FROM issues
            WHERE store IN ({placeholders}) AND document_type = '35' AND direction = 'in'
              AND period >= ? AND period <= ?
        """, list(st_list) + [start_fy, end_fy]).fetchone()
        g_fy_trans_in_val = float(g_fy_in[0]) if g_fy_in else 0.0
        g_fy_trans_in_slips = int(g_fy_in[1]) if g_fy_in else 0

        share_pct = (g_fy_disp_val / fy_disp_val * 100) if fy_disp_val > 0 else 0.0

        # รายชื่อคลังในกลุ่มพร้อมชื่อ
        stores_meta = []
        for s_code in st_list:
            s_name = stores.store_name(s_code)
            stores_meta.append({"code": s_code, "name": s_name})

        groups_data.append({
            "key": g["key"],
            "name": g["name"],
            "icon": g["icon"],
            "desc": g["desc"],
            "badge_class": g["badge_class"],
            "stock_value": g_stock_val,
            "stock_items": g_stock_items,
            "monthly_disp_avg": g_monthly_disp_avg,
            "disp_val": g_disp_val,
            "disp_slips": g_disp_slips,
            "fy_disp_val": g_fy_disp_val,
            "fy_disp_slips": g_fy_disp_slips,
            "fy_trans_in_val": g_fy_trans_in_val,
            "fy_trans_in_slips": g_fy_trans_in_slips,
            "share_pct": round(share_pct, 1),
            "stores_list": stores_meta,
        })

    # 7. 50 รายการที่มีการตัดจ่ายสูงสุดทั้งโรงพยาบาล
    ret_clause = "AND COALESCE(m.retired, 0) = 0" if _items_has_column(conn, "retired") else ""
    top_items_sql = f"""
        SELECT 
            i.stock_code,
            COALESCE(m.name, i.stock_code) as name,
            COALESCE(i.unit, '') as unit,
            COALESCE(m.main_category, '') as category,
            SUM(i.qty) as total_qty,
            SUM(i.value) as total_val,
            COUNT(DISTINCT i.irno) as req_count,
            COUNT(DISTINCT i.store) as store_count,
            MAX(i.period) as latest_period
        FROM issues i
        LEFT JOIN items m ON m.stock_code = i.stock_code
        WHERE i.direction = 'out' AND i.document_type = '32'
          AND i.period >= ? AND i.period < ?
          {ret_clause}
        GROUP BY i.stock_code
        ORDER BY total_val DESC
        LIMIT 50
    """
    items_raw = conn.execute(top_items_sql, [p_first, p_latest]).fetchall()
    top_items = []
    for it in items_raw:
        top_items.append({
            "stock_code": it[0],
            "name": it[1],
            "unit": it[2],
            "group": categories.group_name(categories.group_of(it[3])) if it[3] else "ยา",
            "qty": it[4],
            "value": it[5],
            "req_count": it[6],
            "store_count": it[7],
            "latest_period": f"{it[8][4:6]}/{it[8][:4]}" if it[8] and len(it[8]) == 6 else it[8],
        })

    # 8. Monthly Trend (18 เดือน) ทั้งโรงพยาบาล
    monthly_trend = get_substore_monthly_trend(conn, store_code="ALL", months=months, latest=latest)

    return {
        "kpis": kpis,
        "groups_data": groups_data,
        "top_items": top_items,
        "monthly_trend": monthly_trend,
    }


def get_item_substore_detail(conn: sqlite3.Connection, store_code: str, stock_code: str, months: int = 18) -> dict[str, Any]:
    """ข้อมูลรายละเอียดเจาะลึกของยาหรือพัสดุสำหรับคลังย่อย หรือ วอร์ดที่เลือก
    
    รวมถึง:
    - รายละเอียดชื่อ หมวด หน่วยนับ สถานะ active
    - ล็อตคงคลัง (Lot No, วันหมดอายุ, วันรับเข้า, จำนวนคงเหลือ, มูลค่า)
    - อัตราการใช้ย้อนหลัง 3 เดือน (AMC) และอัตราสำรอง (MOS)
    - ประวัติใบรับโอนจากคลังใหญ่ (irno, วันที่, จำนวน, มูลค่า, สถานะ)
    - ประวัติใบตัดจ่าย/เบิกใช้ (irno, วันที่, หอผู้ป่วย/หน่วยงานปลายทาง, จำนวน, มูลค่า)
    - แนวโน้มความเคลื่อนไหวย้อนหลัง 18 เดือนของรายการนี้
    """
    latest = latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    p_first_3, _ = _period_range(latest, 3)

    is_all = (store_code.upper() in ("ALL", "TOTAL", "HOSPITAL"))
    is_w = is_ward(store_code)

    # 1. ข้อมูลทะเบียนพัสดุ/ยา
    has_trade = _items_has_column(conn, "trade_name")
    has_bunit = _items_has_column(conn, "base_unit")
    has_ret = _items_has_column(conn, "retired")
    trade_col = "COALESCE(trade_name, '')" if has_trade else "''"
    bunit_col = "COALESCE(base_unit, '')" if has_bunit else "''"
    ret_col = "COALESCE(retired, 0)" if has_ret else "0"
    item_row = conn.execute(f"""
        SELECT stock_code, COALESCE(name, ''), {trade_col},
               COALESCE(main_category, ''), COALESCE(item_group, ''),
               {bunit_col}, {ret_col}
        FROM items WHERE stock_code = ?
    """, [stock_code]).fetchone()

    if not item_row:
        return {"found": False, "stock_code": stock_code}

    group_name = categories.group_name(categories.group_of(item_row[3])) if item_row[3] else "ทั่วไป"
    item_info = {
        "stock_code": item_row[0],
        "name": item_row[1] or stock_code,
        "trade_name": item_row[2] or "",
        "main_category": item_row[3] or "",
        "group_name": group_name,
        "base_unit": item_row[5] or "หน่วย",
        "retired": bool(item_row[6]),
    }

    # 2. ล็อตคงคลังในคลังนี้ (Balances & Lots)
    ref_date = datetime.date(2026, 9, 18)
    lots_data = []
    total_on_hand_qty = 0.0
    total_on_hand_val = 0.0

    p_clause, p_vals = _balance_period_clause(conn, "" if is_all else store_code)
    if is_w:
        lots_data = []
    elif is_all:
        lot_rows = conn.execute(f"""
            SELECT lot_no, qty, value, COALESCE(unit, ''), expire_date, last_in_date, store
            FROM balances
            WHERE stock_code = ? AND qty > 0 AND {p_clause}
            ORDER BY expire_date ASC
        """, [stock_code, *p_vals]).fetchall()
        for lr in lot_rows:
            q = float(lr[1] or 0)
            v = float(lr[2] or 0)
            total_on_hand_qty += q
            total_on_hand_val += v
            exp = str(lr[4] or "")
            days_left = 999
            if len(exp) == 8:
                try:
                    exp_d = datetime.date(int(exp[:4]), int(exp[4:6]), int(exp[6:8]))
                    days_left = (exp_d - ref_date).days
                except Exception:
                    pass
            lots_data.append({
                "lot_no": lr[0] or "-",
                "qty": q,
                "value": v,
                "unit": lr[3] or item_info["base_unit"],
                "expire_date": f"{exp[6:8]}/{exp[4:6]}/{exp[:4]}" if len(exp) == 8 else exp,
                "last_in_date": lr[5] or "",
                "store": lr[6],
                "store_name": stores.store_name(lr[6]),
                "days_left": days_left,
            })
    else:
        lot_rows = conn.execute(f"""
            SELECT lot_no, qty, value, COALESCE(unit, ''), expire_date, last_in_date, store
            FROM balances
            WHERE store = ? AND stock_code = ? AND qty > 0 AND {p_clause}
            ORDER BY expire_date ASC
        """, [store_code, stock_code, *p_vals]).fetchall()
        for lr in lot_rows:
            q = float(lr[1] or 0)
            v = float(lr[2] or 0)
            total_on_hand_qty += q
            total_on_hand_val += v
            exp = str(lr[4] or "")
            days_left = 999
            if len(exp) == 8:
                try:
                    exp_d = datetime.date(int(exp[:4]), int(exp[4:6]), int(exp[6:8]))
                    days_left = (exp_d - ref_date).days
                except Exception:
                    pass
            lots_data.append({
                "lot_no": lr[0] or "-",
                "qty": q,
                "value": v,
                "unit": lr[3] or item_info["base_unit"],
                "expire_date": f"{exp[6:8]}/{exp[4:6]}/{exp[:4]}" if len(exp) == 8 else exp,
                "last_in_date": lr[5] or "",
                "store": lr[6],
                "store_name": stores.store_name(lr[6]),
                "days_left": days_left,
            })

    # 3. อัตราการใช้ 3 เดือน (AMC) และ MOS
    if is_w:
        w_info = get_ward_info(store_code)
        parts = w_info["dept_code"].split("-")
        w_div = parts[0]
        w_dept = parts[1] if len(parts) > 1 else ""
        w_sec = parts[2] if len(parts) > 2 else ""
        amc_row = conn.execute("""
            SELECT COALESCE(SUM(qty)/3.0, 0), COALESCE(SUM(value)/3.0, 0)
            FROM issues
            WHERE division = ? AND dept = ? AND (? = '' OR section = ?)
              AND stock_code = ? AND document_type = '32' AND direction = 'out'
              AND period >= ? AND period < ?
        """, [w_div, w_dept, w_sec, w_sec, stock_code, p_first_3, p_latest]).fetchone()
    elif is_all:
        amc_row = conn.execute("""
            SELECT COALESCE(SUM(qty)/3.0, 0), COALESCE(SUM(value)/3.0, 0)
            FROM issues
            WHERE stock_code = ? AND document_type = '32' AND direction = 'out'
              AND period >= ? AND period < ?
        """, [stock_code, p_first_3, p_latest]).fetchone()
    else:
        amc_row = conn.execute("""
            SELECT COALESCE(SUM(qty)/3.0, 0), COALESCE(SUM(value)/3.0, 0)
            FROM issues
            WHERE store = ? AND stock_code = ? AND document_type = '32' AND direction = 'out'
              AND period >= ? AND period < ?
        """, [store_code, stock_code, p_first_3, p_latest]).fetchone()

    amc_qty = float(amc_row[0]) if amc_row else 0.0
    amc_val = float(amc_row[1]) if amc_row else 0.0
    mos = (total_on_hand_qty / amc_qty) if amc_qty > 0 else (99.0 if total_on_hand_qty > 0 else 0.0)

    # 4. ประวัติการรับโอนเข้า (Transfers In from Main Store - Doc 35 in)
    if is_w:
        trans_in_records = []
    elif is_all:
        tin_rows = conn.execute("""
            SELECT period, irno, issued_at, qty, value, COALESCE(unit, ''), store
            FROM issues
            WHERE stock_code = ? AND document_type = '35' AND direction = 'in'
            ORDER BY period DESC, issued_at DESC LIMIT 15
        """, [stock_code]).fetchall()
        trans_in_records = [
            {
                "period": r[0],
                "period_thai": format_period_thai(r[0]),
                "irno": r[1],
                "issued_at": r[2] or "",
                "qty": float(r[3] or 0),
                "value": float(r[4] or 0),
                "unit": r[5] or item_info["base_unit"],
                "store": r[6],
                "store_name": stores.store_name(r[6]),
                "status": "รับเข้าเรียบร้อย",
            }
            for r in tin_rows
        ]
    else:
        tin_rows = conn.execute("""
            SELECT period, irno, issued_at, qty, value, COALESCE(unit, ''), store
            FROM issues
            WHERE store = ? AND stock_code = ? AND document_type = '35' AND direction = 'in'
            ORDER BY period DESC, issued_at DESC LIMIT 15
        """, [store_code, stock_code]).fetchall()
        trans_in_records = [
            {
                "period": r[0],
                "period_thai": format_period_thai(r[0]),
                "irno": r[1],
                "issued_at": r[2] or "",
                "qty": float(r[3] or 0),
                "value": float(r[4] or 0),
                "unit": r[5] or item_info["base_unit"],
                "store": r[6],
                "store_name": stores.store_name(r[6]),
                "status": "รับเข้าเรียบร้อย",
            }
            for r in tin_rows
        ]

    # 5. ประวัติการตัดจ่าย / เบิกใช้ (Dispensations Out - Doc 32 out)
    if is_w:
        w_info = get_ward_info(store_code)
        parts = w_info["dept_code"].split("-")
        w_div = parts[0]
        w_dept = parts[1] if len(parts) > 1 else ""
        w_sec = parts[2] if len(parts) > 2 else ""
        disp_rows = conn.execute("""
            SELECT period, irno, issued_at, qty, value, COALESCE(unit, ''), store
            FROM issues
            WHERE division = ? AND dept = ? AND (? = '' OR section = ?)
              AND stock_code = ? AND document_type = '32' AND direction = 'out'
            ORDER BY period DESC, issued_at DESC LIMIT 15
        """, [w_div, w_dept, w_sec, w_sec, stock_code]).fetchall()
        disp_records = [
            {
                "period": r[0],
                "period_thai": format_period_thai(r[0]),
                "irno": r[1],
                "issued_at": r[2] or "",
                "qty": float(r[3] or 0),
                "value": float(r[4] or 0),
                "unit": r[5] or item_info["base_unit"],
                "target_dept": w_info["name"],
                "source_store": stores.store_name(r[6]),
            }
            for r in disp_rows
        ]
    elif is_all:
        disp_rows = conn.execute("""
            SELECT period, irno, issued_at, qty, value, COALESCE(unit, ''), division, dept, section, store
            FROM issues
            WHERE stock_code = ? AND document_type = '32' AND direction = 'out'
            ORDER BY period DESC, issued_at DESC LIMIT 15
        """, [stock_code]).fetchall()
        disp_records = [
            {
                "period": r[0],
                "period_thai": format_period_thai(r[0]),
                "irno": r[1],
                "issued_at": r[2] or "",
                "qty": float(r[3] or 0),
                "value": float(r[4] or 0),
                "unit": r[5] or item_info["base_unit"],
                "target_dept": departments.name_of(r[6], r[7], r[8]),
                "source_store": stores.store_name(r[9]),
            }
            for r in disp_rows
        ]
    else:
        disp_rows = conn.execute("""
            SELECT period, irno, issued_at, qty, value, COALESCE(unit, ''), division, dept, section, store
            FROM issues
            WHERE store = ? AND stock_code = ? AND document_type = '32' AND direction = 'out'
            ORDER BY period DESC, issued_at DESC LIMIT 15
        """, [store_code, stock_code]).fetchall()
        disp_records = [
            {
                "period": r[0],
                "period_thai": format_period_thai(r[0]),
                "irno": r[1],
                "issued_at": r[2] or "",
                "qty": float(r[3] or 0),
                "value": float(r[4] or 0),
                "unit": r[5] or item_info["base_unit"],
                "target_dept": departments.name_of(r[6], r[7], r[8]),
                "source_store": stores.store_name(r[9]),
            }
            for r in disp_rows
        ]

    # 6. ความเคลื่อนไหว 18 เดือนของรายการนี้ (Monthly Movement Trend)
    if is_w:
        w_info = get_ward_info(store_code)
        parts = w_info["dept_code"].split("-")
        w_div = parts[0]
        w_dept = parts[1] if len(parts) > 1 else ""
        w_sec = parts[2] if len(parts) > 2 else ""
        trend_rows = conn.execute("""
            SELECT period,
                   0.0 as in_qty, 0.0 as in_val,
                   COALESCE(SUM(qty), 0) as out_qty,
                   COALESCE(SUM(value), 0) as out_val,
                   COUNT(DISTINCT irno) as slip_count
            FROM issues
            WHERE division = ? AND dept = ? AND (? = '' OR section = ?)
              AND stock_code = ? AND document_type = '32' AND direction = 'out'
              AND period >= ? AND period <= ?
            GROUP BY period
            ORDER BY period DESC
        """, [w_div, w_dept, w_sec, w_sec, stock_code, p_first, p_latest]).fetchall()
    elif is_all:
        trend_rows = conn.execute("""
            SELECT period,
                   COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN qty ELSE 0 END), 0) as in_qty,
                   COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN value ELSE 0 END), 0) as in_val,
                   COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN qty ELSE 0 END), 0) as out_qty,
                   COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN value ELSE 0 END), 0) as out_val,
                   COUNT(DISTINCT irno) as slip_count
            FROM issues
            WHERE stock_code = ? AND period >= ? AND period <= ?
            GROUP BY period
            ORDER BY period DESC
        """, [stock_code, p_first, p_latest]).fetchall()
    else:
        trend_rows = conn.execute("""
            SELECT period,
                   COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN qty ELSE 0 END), 0) as in_qty,
                   COALESCE(SUM(CASE WHEN document_type = '35' AND direction = 'in' THEN value ELSE 0 END), 0) as in_val,
                   COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN qty ELSE 0 END), 0) as out_qty,
                   COALESCE(SUM(CASE WHEN document_type = '32' AND direction = 'out' THEN value ELSE 0 END), 0) as out_val,
                   COUNT(DISTINCT irno) as slip_count
            FROM issues
            WHERE store = ? AND stock_code = ? AND period >= ? AND period <= ?
            GROUP BY period
            ORDER BY period DESC
        """, [store_code, stock_code, p_first, p_latest]).fetchall()

    trend_data = [
        {
            "period": r[0],
            "period_thai": format_period_thai(r[0]),
            "in_qty": float(r[1] or 0),
            "in_val": float(r[2] or 0),
            "out_qty": float(r[3] or 0),
            "out_val": float(r[4] or 0),
            "slips": int(r[5] or 0),
        }
        for r in trend_rows
    ]

    if is_all:
        s_name = "รวมทั้งโรงพยาบาล"
    elif is_w:
        s_name = get_ward_info(store_code)["name"]
    else:
        raw_name = stores.store_name(store_code)
        s_name = f"{raw_name} ({store_code})" if raw_name != store_code else store_code

    return {
        "found": True,
        "item": item_info,
        "store_code": store_code,
        "store_name": s_name,
        "on_hand": {
            "qty": total_on_hand_qty,
            "value": total_on_hand_val,
            "lots_count": len(lots_data),
            "lots": lots_data,
        },
        "kpis": {
            "amc_qty": amc_qty,
            "amc_val": amc_val,
            "mos": round(mos, 1),
            "months": months,
        },
        "transfers_in": trans_in_records,
        "dispensations": disp_records,
        "trend": trend_data,
    }


_LEADERS_CACHE: dict[tuple[int, str], dict[str, Any]] = {}


def clear_leaders_cache() -> None:
    """ล้าง cache ของ Leaders Dashboard"""
    _LEADERS_CACHE.clear()


def get_top_requisition_leaders_dashboard(conn: sqlite3.Connection, months: int = 18, use_cache: bool = True) -> dict[str, Any]:
    """Dashboard สรุปอันดับการเบิกจ่ายสูงสุด (Top Requisitions Dashboard)
    
    1. ยา TOP 5 แต่ละห้องยาหลัก (IPD, OPD, CHEMO):
       - ห้องยา IPD (I2): ยา Top 5 & แผนก/วอร์ด Top 5
       - ห้องยา OPD (O5, O6): ยา Top 5 & แผนก/คลินิก Top 5
       - ห้องยาเคมีบำบัด CHEMO (99): ยา Top 5 & หน่วยงาน Top 5
    2. พัสดุสิ้นเปลือง TOP 5 ใครเบิกอะไรเยอะสุด:
       - กระดาษ (Paper): รายการ Top 5 & แผนก Top 5
       - หมึกพิมพ์ (Toner/Ink): รายการ Top 5 & แผนก Top 5
       - พัสดุสิ้นเปลืองทั่วไป (หมวด 6): รายการ Top 5 & แผนก Top 5
    """
    latest = latest_period(conn)
    try:
        db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        db_file = ""
    is_persistent = bool(db_file and db_file != "")
    cache_key = (db_file, months, latest)
    if use_cache and is_persistent and cache_key in _LEADERS_CACHE:
        return _LEADERS_CACHE[cache_key]

    p_first, p_latest = _period_range(latest, months)
    start_fy, end_fy, fy_label = fiscal_year_range(latest)
    has_ret = _items_has_column(conn, "retired")
    retired_clause = "AND COALESCE(m.retired, 0) = 0" if has_ret else ""

    def _get_top_drugs_with_metrics(stores: list[str], limit: int = 5):
        placeholders = ",".join(["?"] * len(stores))

        # 1. Total Dispensed (ยอดเบิกจ่ายรวม)
        sql_issues = f"""
            SELECT COALESCE(SUM(i.value), 0), COUNT(DISTINCT i.irno), COUNT(DISTINCT i.stock_code)
            FROM issues i
            JOIN items m ON i.stock_code = m.stock_code
            WHERE i.store IN ({placeholders}) AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
              {retired_clause}
        """
        row_iss = conn.execute(sql_issues, stores + [p_first, p_latest]).fetchone()
        iss_val = float(row_iss[0]) if row_iss else 0.0
        iss_slips = int(row_iss[1]) if row_iss else 0
        iss_items_count = int(row_iss[2]) if row_iss else 0
        monthly_rate = iss_val / months if months > 0 else 0.0

        # 2. FY Issues (สะสมปีงบประมาณ นับตั้งแต่ 1 ต.ค. เป็นต้นมา)
        sql_fy = f"""
            SELECT COALESCE(SUM(i.value), 0), COUNT(DISTINCT i.irno)
            FROM issues i
            JOIN items m ON i.stock_code = m.stock_code
            WHERE i.store IN ({placeholders}) AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
              {retired_clause}
        """
        row_fy = conn.execute(sql_fy, stores + [start_fy, end_fy]).fetchone()
        fy_iss_val = float(row_fy[0]) if row_fy else 0.0
        fy_iss_slips = int(row_fy[1]) if row_fy else 0

        # 3. Transfers / Receipts in (ยอดรับเข้า / รับโอนจากคลังใหญ่)
        sql_trans = f"""
            SELECT COALESCE(SUM(i.value), 0), COUNT(DISTINCT i.irno)
            FROM issues i
            WHERE i.store IN ({placeholders}) AND i.direction = 'in'
              AND i.period >= ? AND i.period <= ?
        """
        row_trans = conn.execute(sql_trans, stores + [p_first, p_latest]).fetchone()
        trans_in_val = float(row_trans[0]) if row_trans else 0.0
        trans_in_slips = int(row_trans[1]) if row_trans else 0

        # 4. Current Balances (คงคลังปัจจุบัน)
        p_clause_b, p_vals_b = _balance_period_clause(conn, "", table_alias="b")
        sql_bal = f"""
            SELECT COALESCE(SUM(b.value), 0), COUNT(DISTINCT b.stock_code)
            FROM balances b
            JOIN items m ON b.stock_code = m.stock_code
            WHERE b.store IN ({placeholders}) AND b.qty > 0 AND b.value > 0 AND {p_clause_b}
              {retired_clause}
        """
        row_bal = conn.execute(sql_bal, stores + p_vals_b).fetchone()
        bal_val = float(row_bal[0]) if row_bal else 0.0
        bal_items = int(row_bal[1]) if row_bal else 0
        mos = (bal_val / monthly_rate) if monthly_rate > 0 else 0.0

        # 5. Top 5 Drugs
        sql_top_items = f"""
            SELECT i.stock_code, COALESCE(m.name, i.stock_code) as name,
                   SUM(i.qty) as qty, COALESCE(MAX(i.unit), '') as unit,
                   SUM(i.value) as val, COUNT(DISTINCT i.irno) as slips,
                   COALESCE(m.main_category, '') as category
            FROM issues i
            JOIN items m ON i.stock_code = m.stock_code
            WHERE i.store IN ({placeholders}) AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
              {retired_clause}
            GROUP BY i.stock_code
            ORDER BY val DESC
            LIMIT ?
        """
        rows_items = conn.execute(sql_top_items, stores + [p_first, p_latest, limit * 2]).fetchall()
        items = []
        for r in rows_items:
            name = (r[1] or "").strip()
            if not name or categories.is_retired_item(name):
                continue
            item_val = float(r[4] or 0)
            pct = round((item_val / iss_val * 100), 1) if iss_val > 0 else 0.0
            items.append({
                "stock_code": r[0],
                "name": name,
                "qty": float(r[2] or 0),
                "unit": r[3] or "หน่วย",
                "value": item_val,
                "slips": int(r[5] or 0),
                "percent": pct,
                "group": categories.group_name(categories.group_of(r[6])) if r[6] else "ยา",
            })
            if len(items) >= limit:
                break

        # 6. Top 5 Departments
        sql_top_depts = f"""
            SELECT i.division, i.dept, i.section, SUM(i.value) as val, COUNT(DISTINCT i.irno) as slips,
                   COUNT(DISTINCT i.stock_code) as items_count
            FROM issues i
            WHERE i.store IN ({placeholders}) AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
            GROUP BY i.division, i.dept, i.section
            ORDER BY val DESC
            LIMIT ?
        """
        rows_depts = conn.execute(sql_top_depts, stores + [p_first, p_latest, limit]).fetchall()
        depts = []
        for r in rows_depts:
            div, dept, sec = r[0] or "", r[1] or "", r[2] or ""
            code = f"{div}-{dept}-{sec}".rstrip("-")
            dept_name = departments.name_of(div, dept, sec) or code
            dept_val = float(r[3] or 0)
            pct = round((dept_val / iss_val * 100), 1) if iss_val > 0 else 0.0

            # ดึงรายการยาหลักที่หน่วยงานนี้เบิกสูงสุด
            sql_dept_top = f"""
                SELECT i.stock_code, COALESCE(m.name, i.stock_code) as name,
                       SUM(i.qty) as qty, COALESCE(MAX(i.unit), '') as unit,
                       SUM(i.value) as val
                FROM issues i
                JOIN items m ON i.stock_code = m.stock_code
                WHERE i.store IN ({placeholders}) AND i.direction = 'out'
                  AND i.period >= ? AND i.period <= ?
                  AND i.division = ? AND i.dept = ? AND COALESCE(i.section, '') = ?
                  {retired_clause}
                GROUP BY i.stock_code
                ORDER BY val DESC
                LIMIT 5
            """
            dept_items = []
            for it in conn.execute(sql_dept_top, stores + [p_first, p_latest, div, dept, sec or '']).fetchall():
                it_name = (it[1] or '').strip()
                if it_name and not categories.is_retired_item(it_name):
                    dept_items.append({
                        "stock_code": it[0],
                        "name": it_name,
                        "qty": float(it[2] or 0),
                        "unit": it[3] or "",
                        "value": float(it[4] or 0),
                    })

            depts.append({
                "dept_code": code,
                "division": div,
                "dept": dept,
                "section": sec,
                "name": dept_name,
                "value": dept_val,
                "slips": int(r[4] or 0),
                "items_count": int(r[5] or 0),
                "percent": pct,
                "top_items": dept_items,
            })

        metrics = {
            "trans_in_val": trans_in_val,
            "trans_in_slips": trans_in_slips,
            "issue_val": iss_val,
            "issue_slips": iss_slips,
            "issue_items": iss_items_count,
            "monthly_rate": monthly_rate,
            "fy_issue_val": fy_iss_val,
            "fy_issue_slips": fy_iss_slips,
            "stock_val": bal_val,
            "stock_items": bal_items,
            "mos": round(mos, 1),
            "fy_label": fy_label,
        }
        return items, depts, metrics

    def _get_top_supplies_with_metrics(filter_clause: str, limit: int = 5):
        # 1. Total Issues (เบิกจ่ายรวม)
        sql_issues = f"""
            SELECT COALESCE(SUM(i.value), 0), COUNT(DISTINCT i.irno), COUNT(DISTINCT i.stock_code)
            FROM issues i
            JOIN items m ON i.stock_code = m.stock_code
            WHERE {filter_clause} AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
              {retired_clause}
        """
        row_iss = conn.execute(sql_issues, [p_first, p_latest]).fetchone()
        iss_val = float(row_iss[0]) if row_iss else 0.0
        iss_slips = int(row_iss[1]) if row_iss else 0
        iss_items_count = int(row_iss[2]) if row_iss else 0
        monthly_rate = iss_val / months if months > 0 else 0.0

        # 2. FY Issues (สะสมปีงบประมาณ นับตั้งแต่ 1 ตุลาคม เป็นต้นมา)
        sql_fy_issues = f"""
            SELECT COALESCE(SUM(i.value), 0), COUNT(DISTINCT i.irno)
            FROM issues i
            JOIN items m ON i.stock_code = m.stock_code
            WHERE {filter_clause} AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
              {retired_clause}
        """
        row_fy_iss = conn.execute(sql_fy_issues, [start_fy, end_fy]).fetchone()
        fy_iss_val = float(row_fy_iss[0]) if row_fy_iss else 0.0
        fy_iss_slips = int(row_fy_iss[1]) if row_fy_iss else 0

        # 3. Receipts (ซื้อมา/รับเข้า)
        sql_rcv = f"""
            SELECT COALESCE(SUM(r.value), 0), COUNT(DISTINCT r.rcv_no)
            FROM receipts r
            JOIN items m ON r.stock_code = m.stock_code
            WHERE {filter_clause}
              AND r.period >= ? AND r.period <= ?
              {retired_clause}
        """
        rcv_val = 0.0
        rcv_docs = 0
        try:
            row_rcv = conn.execute(sql_rcv, [p_first, p_latest]).fetchone()
            if row_rcv:
                rcv_val = float(row_rcv[0] or 0)
                rcv_docs = int(row_rcv[1] or 0)
        except sqlite3.OperationalError:
            pass

        # 4. Current Balances (คงคลังปัจจุบัน)
        p_clause_b, p_vals_b = _balance_period_clause(conn, "", table_alias="b")
        sql_bal = f"""
            SELECT COALESCE(SUM(b.value), 0), COUNT(DISTINCT b.stock_code)
            FROM balances b
            JOIN items m ON b.stock_code = m.stock_code
            WHERE {filter_clause} AND b.qty > 0 AND b.value > 0 AND {p_clause_b}
              {retired_clause}
        """
        row_bal = conn.execute(sql_bal, p_vals_b).fetchone()
        bal_val = float(row_bal[0]) if row_bal else 0.0
        bal_items = int(row_bal[1]) if row_bal else 0
        mos = (bal_val / monthly_rate) if monthly_rate > 0 else 0.0

        # 5. Top 5 Items
        sql_items = f"""
            SELECT i.stock_code, COALESCE(m.name, i.stock_code) as name,
                   SUM(i.qty) as qty, COALESCE(MAX(i.unit), '') as unit,
                   SUM(i.value) as val, COUNT(DISTINCT i.irno) as slips,
                   COALESCE(m.main_category, '') as category
            FROM issues i
            JOIN items m ON i.stock_code = m.stock_code
            WHERE {filter_clause} AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
              {retired_clause}
            GROUP BY i.stock_code
            ORDER BY val DESC
            LIMIT ?
        """
        rows_items = conn.execute(sql_items, [p_first, p_latest, limit * 2]).fetchall()
        items = []
        for r in rows_items:
            name = (r[1] or "").strip()
            if not name or categories.is_retired_item(name):
                continue
            item_val = float(r[4] or 0)
            pct = round((item_val / iss_val * 100), 1) if iss_val > 0 else 0.0
            items.append({
                "stock_code": r[0],
                "name": name,
                "qty": float(r[2] or 0),
                "unit": r[3] or "หน่วย",
                "value": item_val,
                "slips": int(r[5] or 0),
                "percent": pct,
                "group": categories.group_name(categories.group_of(r[6])) if r[6] else "พัสดุ",
            })
            if len(items) >= limit:
                break

        # 6. Top 5 Departments (ใครเบิกเยอะ)
        sql_depts = f"""
            SELECT i.division, i.dept, i.section, SUM(i.value) as val, COUNT(DISTINCT i.irno) as slips,
                   COUNT(DISTINCT i.stock_code) as items_count
            FROM issues i
            JOIN items m ON i.stock_code = m.stock_code
            WHERE {filter_clause} AND i.direction = 'out'
              AND i.period >= ? AND i.period <= ?
              {retired_clause}
            GROUP BY i.division, i.dept, i.section
            ORDER BY val DESC
            LIMIT ?
        """
        rows_depts = conn.execute(sql_depts, [p_first, p_latest, limit]).fetchall()
        depts = []
        for r in rows_depts:
            div, dept, sec = r[0] or "", r[1] or "", r[2] or ""
            code = f"{div}-{dept}-{sec}".rstrip("-")
            dept_name = departments.name_of(div, dept, sec) or code
            dept_val = float(r[3] or 0)
            pct = round((dept_val / iss_val * 100), 1) if iss_val > 0 else 0.0

            # ดึงรายการพัสดุที่หน่วยงานนี้เบิกสูงสุด
            sql_dept_top = f"""
                SELECT i.stock_code, COALESCE(m.name, i.stock_code) as name,
                       SUM(i.qty) as qty, COALESCE(MAX(i.unit), '') as unit,
                       SUM(i.value) as val
                FROM issues i
                JOIN items m ON i.stock_code = m.stock_code
                WHERE {filter_clause} AND i.direction = 'out'
                  AND i.period >= ? AND i.period <= ?
                  AND i.division = ? AND i.dept = ? AND COALESCE(i.section, '') = ?
                  {retired_clause}
                GROUP BY i.stock_code
                ORDER BY val DESC
                LIMIT 5
            """
            dept_items = []
            for it in conn.execute(sql_dept_top, [p_first, p_latest, div, dept, sec or '']).fetchall():
                it_name = (it[1] or '').strip()
                if it_name and not categories.is_retired_item(it_name):
                    dept_items.append({
                        "stock_code": it[0],
                        "name": it_name,
                        "qty": float(it[2] or 0),
                        "unit": it[3] or "",
                        "value": float(it[4] or 0),
                    })

            depts.append({
                "dept_code": code,
                "division": div,
                "dept": dept,
                "section": sec,
                "name": dept_name,
                "value": dept_val,
                "slips": int(r[4] or 0),
                "items_count": int(r[5] or 0),
                "percent": pct,
                "top_items": dept_items,
            })

        metrics = {
            "rcv_val": rcv_val,
            "rcv_docs": rcv_docs,
            "issue_val": iss_val,
            "issue_slips": iss_slips,
            "issue_items": iss_items_count,
            "monthly_rate": monthly_rate,
            "fy_issue_val": fy_iss_val,
            "fy_issue_slips": fy_iss_slips,
            "stock_val": bal_val,
            "stock_items": bal_items,
            "mos": round(mos, 1),
            "fy_label": fy_label,
        }
        return items, depts, metrics

    # 1. ยา TOP 5 แต่ละห้องยา
    ipd_drugs, ipd_depts, ipd_metrics = _get_top_drugs_with_metrics(["I2"], 5)
    opd_drugs, opd_depts, opd_metrics = _get_top_drugs_with_metrics(["O5", "O6"], 5)
    chemo_drugs, chemo_depts, chemo_metrics = _get_top_drugs_with_metrics(["99"], 5)

    # 2. พัสดุสิ้นเปลือง TOP 5
    paper_items, paper_depts, paper_metrics = _get_top_supplies_with_metrics("m.name LIKE '%กระดาษ%'", 5)
    toner_items, toner_depts, toner_metrics = _get_top_supplies_with_metrics(
        "m.name LIKE '%หมึก%' AND m.name NOT LIKE '%ปลาหมึก%' AND m.main_category IN ('6', '7', '8', '4')", 5
    )
    consumables_items, consumables_depts, consumables_metrics = _get_top_supplies_with_metrics(
        "m.main_category = '6' AND m.name NOT LIKE '%กระดาษ%' AND m.name NOT LIKE '%หมึก%'", 5
    )

    data = {
        "months": months,
        "latest_period": latest,
        "fy_label": fy_label,
        "drugs": {
            "ipd": {
                "title": "ห้องยาผู้ป่วยใน (IPD Pharmacy)",
                "store_code": "I2",
                "icon": "🏥",
                "desc": "เบิกจ่ายยาสำหรับผู้ป่วยในและหอผู้ป่วย",
                "metrics": ipd_metrics,
                "top_items": ipd_drugs,
                "top_depts": ipd_depts,
            },
            "opd": {
                "title": "ห้องยาผู้ป่วยนอก (OPD Pharmacy)",
                "store_code": "O5, O6",
                "icon": "🩺",
                "desc": "เบิกจ่ายยาสำหรับห้องตรวจและคลินิกผู้ป่วยนอก (ตึก 8 ชั้น)",
                "metrics": opd_metrics,
                "top_items": opd_drugs,
                "top_depts": opd_depts,
            },
            "chemo": {
                "title": "ห้องเตรียมยาเคมีบำบัด (Chemo Pharmacy)",
                "store_code": "99",
                "icon": "🧪",
                "desc": "เบิกจ่ายและผสมยาเคมีบำบัดมะเร็ง",
                "metrics": chemo_metrics,
                "top_items": chemo_drugs,
                "top_depts": chemo_depts,
            },
        },
        "supplies": {
            "paper": {
                "title": "กระดาษและแบบพิมพ์ (Paper & Forms)",
                "icon": "📄",
                "desc": "กระดาษถ่ายเอกสาร A4, กระดาษชำระ, กระดาษ EKG, บัตรคิว, กระดาษต่อเนื่อง",
                "metrics": paper_metrics,
                "top_items": paper_items,
                "top_depts": paper_depts,
            },
            "toner": {
                "title": "หมึกพิมพ์และริบบอน (Toner, Ink & Ribbon)",
                "icon": "🖨️",
                "desc": "ตลับหมึกเลเซอร์ HP/Ricoh, หมึกพิมพ์บัตร, หมึกริบบอน",
                "metrics": toner_metrics,
                "top_items": toner_items,
                "top_depts": toner_depts,
            },
            "consumables": {
                "title": "พัสดุสิ้นเปลืองทั่วไป (General Consumables)",
                "icon": "📦",
                "desc": "สติ๊กเกอร์ฉลากยา, สายรัดข้อมือผู้ป่วย, ถุงซิปยา, หลอด/แก้ว, วัสดุทั่วไป",
                "metrics": consumables_metrics,
                "top_items": consumables_items,
                "top_depts": consumables_depts,
            },
        },
    }

    if is_persistent:
        _LEADERS_CACHE[cache_key] = data
    return data


def get_dept_requisition_breakdown(conn, months: int = 18, div: str = "", dept: str = "", sec: str = "", scope: str = "") -> dict:
    """ดึงรายละเอียดรายการทั้งหมดที่หน่วยงานนั้น ๆ เบิก สำหรับ modal หรือดูเจาะลึก"""
    latest = latest_period(conn)
    if not latest:
        return {"dept_code": "", "dept_name": "", "scope": scope, "items": [], "total_val": 0.0, "total_items": 0}
    p_first, p_latest = _period_range(latest, months)

    where_parts = [
        "i.direction = 'out'",
        "i.period >= ?",
        "i.period <= ?",
    ]
    if _items_has_column(conn, "retired"):
        where_parts.append("COALESCE(m.retired, 0) = 0")
    params = [p_first, p_latest]

    if div:
        where_parts.append("i.division = ?")
        params.append(div)
    if dept:
        where_parts.append("i.dept = ?")
        params.append(dept)
    if sec is not None and sec != "":
        where_parts.append("COALESCE(i.section, '') = ?")
        params.append(sec)

    if scope == "ipd":
        where_parts.append("i.store IN ('I2')")
    elif scope == "opd":
        where_parts.append("i.store IN ('O5', 'O6')")
    elif scope == "chemo":
        where_parts.append("i.store IN ('99')")
    elif scope == "paper":
        where_parts.append("m.name LIKE '%กระดาษ%'")
    elif scope == "toner":
        where_parts.append("m.name LIKE '%หมึก%' AND m.name NOT LIKE '%ปลาหมึก%' AND m.main_category IN ('6', '7', '8', '4')")
    elif scope == "consumables":
        where_parts.append("m.main_category = '6' AND m.name NOT LIKE '%กระดาษ%' AND m.name NOT LIKE '%หมึก%'")

    where_sql = " AND ".join(where_parts)
    sql = f"""
        SELECT i.stock_code, COALESCE(m.name, i.stock_code) as name,
               SUM(i.qty) as qty, COALESCE(MAX(i.unit), '') as unit,
               SUM(i.value) as val, COUNT(DISTINCT i.irno) as slips,
               COALESCE(m.main_category, '') as category
        FROM issues i
        JOIN items m ON i.stock_code = m.stock_code
        WHERE {where_sql}
        GROUP BY i.stock_code
        ORDER BY val DESC
    """
    rows = conn.execute(sql, params).fetchall()
    items = []
    total_val = 0.0
    for r in rows:
        name = (r[1] or "").strip()
        if not name or categories.is_retired_item(name):
            continue
        v = float(r[4] or 0)
        total_val += v
        items.append({
            "stock_code": r[0],
            "name": name,
            "qty": float(r[2] or 0),
            "unit": r[3] or "หน่วย",
            "value": v,
            "slips": int(r[5] or 0),
            "category": r[6] or "",
        })

    for it in items:
        it["percent"] = round((it["value"] / total_val * 100), 1) if total_val > 0 else 0.0

    dept_name = departments.name_of(div, dept, sec) or f"{div}-{dept}-{sec}".rstrip("-")
    return {
        "dept_code": f"{div}-{dept}-{sec}".rstrip("-"),
        "dept_name": dept_name,
        "scope": scope,
        "total_val": total_val,
        "total_items": len(items),
        "items": items,
    }


def get_category_all_items(conn: sqlite3.Connection, scope: str, months: int = 18, limit: int = 500) -> dict[str, Any]:
    """ดึงรายการเบิกจ่ายทั้งหมดในหมวดหรือคลังนั้น ๆ เพื่อแสดงในหน้าต่างดูทั้งหมด/ค้นหา (กดดูรายละเอียดได้ทั้งหมด)"""
    latest = latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    where_parts = [
        "i.direction = 'out'",
        "i.period >= ?",
        "i.period <= ?",
    ]
    if _items_has_column(conn, "retired"):
        where_parts.append("COALESCE(m.retired, 0) = 0")
    params: list[Any] = [p_first, p_latest]

    title = "รายการเบิกจ่ายทั้งหมด"
    if scope == "ipd":
        where_parts.append("i.store IN ('I2')")
        title = "รายการเบิกจ่ายยา: ห้องยาผู้ป่วยใน (IPD)"
    elif scope == "opd":
        where_parts.append("i.store IN ('O5', 'O6')")
        title = "รายการเบิกจ่ายยา: ห้องยาผู้ป่วยนอก (OPD)"
    elif scope == "chemo":
        where_parts.append("i.store IN ('99')")
        title = "รายการเบิกจ่ายยา: ห้องยาเคมีบำบัด (Chemo)"
    elif scope == "paper":
        where_parts.append("m.name LIKE '%กระดาษ%'")
        title = "รายการพัสดุ: กระดาษและแบบพิมพ์"
    elif scope == "toner":
        where_parts.append("m.name LIKE '%หมึก%' AND m.name NOT LIKE '%ปลาหมึก%' AND m.main_category IN ('6', '7', '8', '4')")
        title = "รายการพัสดุ: หมึกพิมพ์และริบบอน"
    elif scope == "consumables":
        where_parts.append("m.main_category = '6' AND m.name NOT LIKE '%กระดาษ%' AND m.name NOT LIKE '%หมึก%'")
        title = "รายการพัสดุ: พัสดุสิ้นเปลืองทั่วไป"

    where_sql = " AND ".join(where_parts)

    sql_tot = f"""
        SELECT COALESCE(SUM(i.value), 0)
        FROM issues i
        JOIN items m ON i.stock_code = m.stock_code
        WHERE {where_sql}
    """
    row_tot = conn.execute(sql_tot, params).fetchone()
    total_val = float(row_tot[0] or 0) if row_tot else 0.0

    sql = f"""
        SELECT i.stock_code, COALESCE(m.name, i.stock_code) as name,
               SUM(i.qty) as qty, COALESCE(MAX(i.unit), '') as unit,
               SUM(i.value) as val, COUNT(DISTINCT i.irno) as slips,
               COALESCE(m.main_category, '') as category
        FROM issues i
        JOIN items m ON i.stock_code = m.stock_code
        WHERE {where_sql}
        GROUP BY i.stock_code
        ORDER BY val DESC
        LIMIT ?
    """
    rows = conn.execute(sql, params + [limit]).fetchall()
    items = []
    for idx, r in enumerate(rows, 1):
        name = (r[1] or "").strip()
        if not name or categories.is_retired_item(name):
            continue
        v = float(r[4] or 0)
        pct = round((v / total_val * 100), 1) if total_val > 0 else 0.0
        items.append({
            "rank": idx,
            "stock_code": r[0],
            "name": name,
            "qty": float(r[2] or 0),
            "unit": r[3] or "หน่วย",
            "value": v,
            "slips": int(r[5] or 0),
            "percent": pct,
            "category": r[6] or "",
        })

    return {
        "scope": scope,
        "title": title,
        "months": months,
        "total_val": total_val,
        "total_items": len(items),
        "items": items,
    }


def get_category_all_depts(conn: sqlite3.Connection, scope: str, months: int = 18, limit: int = 200) -> dict[str, Any]:
    """ดึงหน่วยงานที่เบิกทั้งหมดในหมวดหรือคลังนั้น ๆ เพื่อแสดงในหน้าต่างดูทั้งหมด/ค้นหา (กดดูรายละเอียดได้ทั้งหมด)"""
    latest = latest_period(conn)
    p_first, p_latest = _period_range(latest, months)
    where_parts = [
        "i.direction = 'out'",
        "i.period >= ?",
        "i.period <= ?",
    ]
    if _items_has_column(conn, "retired"):
        where_parts.append("COALESCE(m.retired, 0) = 0")
    params: list[Any] = [p_first, p_latest]

    title = "หน่วยงานที่เบิกทั้งหมด"
    if scope == "ipd":
        where_parts.append("i.store IN ('I2')")
        title = "หน่วยงาน/วอร์ดที่เบิกยา: ห้องยาผู้ป่วยใน (IPD)"
    elif scope == "opd":
        where_parts.append("i.store IN ('O5', 'O6')")
        title = "หน่วยงาน/คลินิกที่เบิกยา: ห้องยาผู้ป่วยนอก (OPD)"
    elif scope == "chemo":
        where_parts.append("i.store IN ('99')")
        title = "หน่วยงานที่เบิกยา: ห้องยาเคมีบำบัด (Chemo)"
    elif scope == "paper":
        where_parts.append("m.name LIKE '%กระดาษ%'")
        title = "หน่วยงานที่เบิก: กระดาษและแบบพิมพ์"
    elif scope == "toner":
        where_parts.append("m.name LIKE '%หมึก%' AND m.name NOT LIKE '%ปลาหมึก%' AND m.main_category IN ('6', '7', '8', '4')")
        title = "หน่วยงานที่เบิก: หมึกพิมพ์และริบบอน"
    elif scope == "consumables":
        where_parts.append("m.main_category = '6' AND m.name NOT LIKE '%กระดาษ%' AND m.name NOT LIKE '%หมึก%'")
        title = "หน่วยงานที่เบิก: พัสดุสิ้นเปลืองทั่วไป"

    where_sql = " AND ".join(where_parts)

    sql_tot = f"""
        SELECT COALESCE(SUM(i.value), 0)
        FROM issues i
        JOIN items m ON i.stock_code = m.stock_code
        WHERE {where_sql}
    """
    row_tot = conn.execute(sql_tot, params).fetchone()
    total_val = float(row_tot[0] or 0) if row_tot else 0.0

    sql = f"""
        SELECT i.division, i.dept, i.section, SUM(i.value) as val,
               COUNT(DISTINCT i.irno) as slips, COUNT(DISTINCT i.stock_code) as items_count
        FROM issues i
        JOIN items m ON i.stock_code = m.stock_code
        WHERE {where_sql}
        GROUP BY i.division, i.dept, i.section
        ORDER BY val DESC
        LIMIT ?
    """
    rows = conn.execute(sql, params + [limit]).fetchall()
    depts = []
    for idx, r in enumerate(rows, 1):
        div, d_code, sec = r[0] or "", r[1] or "", r[2] or ""
        code = f"{div}-{d_code}-{sec}".rstrip("-")
        dept_name = departments.name_of(div, d_code, sec) or code
        dept_val = float(r[3] or 0)
        pct = round((dept_val / total_val * 100), 1) if total_val > 0 else 0.0
        depts.append({
            "rank": idx,
            "dept_code": code,
            "name": dept_name,
            "div": div,
            "dept": d_code,
            "sec": sec,
            "slips": int(r[4] or 0),
            "value": dept_val,
            "percent": pct,
            "items_count": int(r[5] or 0),
        })

    return {
        "scope": scope,
        "title": title,
        "months": months,
        "total_val": total_val,
        "total_depts": len(depts),
        "depts": depts,
    }




def get_pharmacy_daily_stock_cut(conn: sqlite3.Connection, store_code: str = 'ALL') -> dict[str, Any]:
    """คำนวณตัดสต็อกรายวันของห้องยา"""
    today = datetime.datetime.now().date()
    yesterday = today - datetime.timedelta(days=1)
    
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    
    # Snapshot balances period format is YYYYMMDD
    yesterday_bal_str = yesterday.strftime("%Y%m%d")
    
    valid_stores = [s['code'] for s in PHARMACY_SUBSTORES]
    
    if store_code != 'ALL':
        store_clause = "AND store = ?"
        store_params = [store_code]
    else:
        # Include all valid pharmacy substores plus main store 2 if we consider it
        valid_stores = valid_stores + ['2']
        store_clause = f"AND store IN ({','.join(['?']*len(valid_stores))})"
        store_params = valid_stores

    sql = f'''
        WITH 
        prev_bal AS (
            SELECT stock_code, store, SUM(qty) as qty, SUM(value) as val
            FROM balances
            WHERE period = ? {store_clause}
            GROUP BY stock_code, store
        ),
        yest_use AS (
            SELECT stock_code, store, SUM(qty) as qty, SUM(value) as val
            FROM issues
            WHERE document_type = '32' AND direction = 'out' 
              AND substr(issued_at, 1, 10) = ? {store_clause}
            GROUP BY stock_code, store
        ),
        tod_use AS (
            SELECT stock_code, store, SUM(qty) as qty, SUM(value) as val
            FROM issues
            WHERE document_type = '32' AND direction = 'out' 
              AND substr(issued_at, 1, 10) = ? {store_clause}
            GROUP BY stock_code, store
        ),
        tod_in AS (
            SELECT stock_code, store, SUM(qty) as qty, SUM(value) as val
            FROM issues
            WHERE document_type = '35' AND direction = 'in' 
              AND substr(issued_at, 1, 10) = ? {store_clause}
            GROUP BY stock_code, store
        ),
        all_items AS (
            SELECT DISTINCT stock_code, store FROM prev_bal
            UNION SELECT DISTINCT stock_code, store FROM yest_use
            UNION SELECT DISTINCT stock_code, store FROM tod_use
            UNION SELECT DISTINCT stock_code, store FROM tod_in
        )
        SELECT 
            i.stock_code,
            i.store,
            COALESCE(m.name, i.stock_code) as name,
            COALESCE(m.base_unit, 'หน่วย') as unit,
            COALESCE(pb.qty, 0) as prev_bal_qty,
            COALESCE(pb.val, 0) as prev_bal_val,
            COALESCE(yu.qty, 0) as yest_use_qty,
            COALESCE(yu.val, 0) as yest_use_val,
            COALESCE(tu.qty, 0) as tod_use_qty,
            COALESCE(tu.val, 0) as tod_use_val,
            COALESCE(ti.qty, 0) as tod_in_qty,
            COALESCE(ti.val, 0) as tod_in_val
        FROM all_items i
        LEFT JOIN prev_bal pb ON i.stock_code = pb.stock_code AND i.store = pb.store
        LEFT JOIN yest_use yu ON i.stock_code = yu.stock_code AND i.store = yu.store
        LEFT JOIN tod_use tu ON i.stock_code = tu.stock_code AND i.store = tu.store
        LEFT JOIN tod_in ti ON i.stock_code = ti.stock_code AND i.store = ti.store
        LEFT JOIN items m ON i.stock_code = m.stock_code
    '''
    
    params = [yesterday_bal_str] + store_params + [yesterday_str] + store_params + [today_str] + store_params + [today_str] + store_params
    
    rows = conn.execute(sql, params).fetchall()
    
    results = []
    total_on_hand_val = 0
    total_yest_val = 0
    total_tod_val = 0
    
    for r in rows:
        sc = r[0]
        st = r[1]
        name = r[2]
        unit = r[3]
        
        pb_qty = r[4]
        pb_val = r[5]
        yu_qty = r[6]
        yu_val = r[7]
        tu_qty = r[8]
        tu_val = r[9]
        ti_qty = r[10]
        ti_val = r[11]
        
        cur_qty = pb_qty + ti_qty - tu_qty
        cur_val = pb_val + ti_val - tu_val
        if cur_qty <= 0: cur_val = 0
        
        status = "✓ ปกติ"
        if cur_qty <= 0:
            status = "🚨 ยาหมดคลัง"
        elif cur_qty < (yu_qty * 3):
            status = "⚠️ เสี่ยงขาด"
        elif cur_qty > (yu_qty * 30) and yu_qty > 0:
            status = "⚡ สต็อกบวม"
        
        results.append({
            "stock_code": sc,
            "store": st,
            "store_name": stores.store_name(st),
            "name": name,
            "unit": unit,
            "prev_bal": pb_qty,
            "yesterday_use": yu_qty,
            "today_use": tu_qty,
            "current_on_hand": cur_qty,
            "current_val": cur_val,
            "status": status
        })
        
        total_on_hand_val += cur_val
        total_yest_val += yu_val
        total_tod_val += tu_val

    # Sort results
    results.sort(key=lambda x: x['current_val'], reverse=True)

    kpis = {
        "total_on_hand_val": total_on_hand_val,
        "total_yesterday_val": total_yest_val,
        "total_today_val": total_tod_val,
        "total_items": len(results)
    }
    
    return {"data": results, "kpis": kpis}

