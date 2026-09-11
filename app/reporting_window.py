"""ช่วงเวลาที่รายงานครอบคลุม — ปีงบประมาณไทย เหมือน Stock5

ใช้กติกาเดียวกับ Stock5 โดยเจตนา: ปีงบประมาณก่อนหน้า + ปีงบประมาณปัจจุบัน
เพื่อให้ตัวเลขสองระบบเทียบกันได้ และช่วงข้อมูลไม่ยาวขึ้นทุกปีไม่สิ้นสุด

ต่างจาก Stock5 ตรงที่ระบบนี้เก็บเป็น "งวด" (เดือน x คลัง) จึงต้องแตกช่วงออกเป็น
รายเดือนด้วย เพื่อให้ดึงเฉพาะเดือนที่ยังไม่มี
"""
import datetime
from typing import Iterator

import stock5_engine

DEFAULT_FISCAL_YEARS_BACK = 1


def _fiscal_helpers():
    """ยืมกติกาปีงบประมาณจาก Stock5 ไม่เขียนสูตรซ้ำ"""
    stock5_engine.install()
    from db_extractor import thai_fiscal_year_label, thai_fiscal_year_start
    return thai_fiscal_year_start, thai_fiscal_year_label


def window(today: datetime.date | None = None,
           years_back: int = DEFAULT_FISCAL_YEARS_BACK) -> tuple[datetime.date, datetime.date]:
    """(วันเริ่ม, วันสิ้นสุดแบบไม่รวม) ของช่วงที่รายงานครอบคลุม"""
    fiscal_start, _ = _fiscal_helpers()
    today = today or datetime.date.today()
    start = fiscal_start(today, max(0, int(years_back)))
    return start, today + datetime.timedelta(days=1)


def periods(today: datetime.date | None = None,
            years_back: int = DEFAULT_FISCAL_YEARS_BACK) -> list[str]:
    """รายชื่องวด YYYYMM ตั้งแต่ต้นช่วงถึงเดือนปัจจุบัน"""
    start, end = window(today, years_back)
    return list(_months_between(start, end))


def closed_periods(today: datetime.date | None = None,
                   years_back: int = DEFAULT_FISCAL_YEARS_BACK) -> list[str]:
    """งวดที่สิ้นเดือนแล้ว — เดือนปัจจุบันยังไม่ครบ จึงไม่ใช้คำนวณอัตราเบิกจ่าย"""
    today = today or datetime.date.today()
    current = today.strftime("%Y%m")
    return [period for period in periods(today, years_back) if period < current]


def period_bounds(period: str) -> tuple[str, str]:
    """(วันแรกของงวด, วันแรกของงวดถัดไป) เป็น YYYYMMDD สำหรับใส่ใน SQL"""
    year, month = int(period[:4]), int(period[4:6])
    first = datetime.date(year, month, 1)
    following = datetime.date(year + (month // 12), (month % 12) + 1, 1)
    return first.strftime("%Y%m%d"), following.strftime("%Y%m%d")


def describe(today: datetime.date | None = None,
             years_back: int = DEFAULT_FISCAL_YEARS_BACK) -> dict[str, object]:
    _, fiscal_label = _fiscal_helpers()
    today = today or datetime.date.today()
    start, end = window(today, years_back)
    every = periods(today, years_back)
    year_be = fiscal_label(today)
    return {
        "fiscal_year": str(year_be),
        "label": f"ปีงบประมาณ {year_be} และย้อนหลัง {years_back} ปีงบประมาณ",
        "date_from": start.strftime("%Y%m%d"),
        "date_to_exclusive": end.strftime("%Y%m%d"),
        "periods": every,
        "closed_periods": closed_periods(today, years_back),
        "months": len(every),
    }


def _months_between(start: datetime.date, end_exclusive: datetime.date) -> Iterator[str]:
    year, month = start.year, start.month
    while (year, month) < (end_exclusive.year, end_exclusive.month) or \
            (year, month) == (end_exclusive.year, end_exclusive.month) and end_exclusive.day > 1:
        yield f"{year}{month:02d}"
        month += 1
        if month > 12:
            year, month = year + 1, 1
