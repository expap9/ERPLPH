"""ชั้นคำนวณตัวชี้วัด — อ่านจากฐานข้อมูลของ ERPLPH เท่านั้น ไม่แตะฐานข้อมูลโรงพยาบาล

เกณฑ์ที่ผู้ใช้กำหนด 12 กันยายน 2569
    ใกล้หมด        เหลือพอใช้น้อยกว่า 1 เดือน
    ใกล้หมดอายุ    ภายใน 6 เดือน โดยแยก 3 เดือนเป็นระดับวิกฤต
    ของค้างนิ่ง     ไม่มีการเคลื่อนไหวออกเลย 6 เดือน
    มูลค่าคงคลัง    รายงานยอดรวม แล้วแยกบรรทัด "ในนั้นหมดอายุแล้ว"

สูตรเปลี่ยนตามขอบเขต ไม่ใช่แค่กรองข้อมูล
    ทั้งโรงพยาบาล   ยอดใช้ = จ่ายผู้ป่วยที่สอบทานผ่าน ลบรับคืน ไม่นับการโอน
    รายคลัง         ยอดออก = จ่าย บวกโอนออก ที่สอบทานผ่าน ลบรับคืน
ถ้าเอายอดออกของทุกคลังมาบวกกัน ยาใน 24 เดือนจะได้ 3,039 ล้านบาท แทนที่จะเป็น
1,508 ล้านบาท เพราะยาชุดเดียวถูกนับตอนคลังหลักโอนออก และนับอีกครั้งตอนห้องยาจ่าย

เดือนคงคลังคิดจากมูลค่า ไม่ใช่จำนวน เพราะหน่วยนับของคงคลังกับของใบจ่ายเป็นคนละ
หน่วยได้ (ดู unit_rules.py) การหารมูลค่าด้วยมูลค่าทำให้หน่วยตัดกันไปเอง ส่วนรายการ
ที่ระบบไม่ได้ลงมูลค่าไว้ (เช่น วัคซีนที่ได้รับจัดสรร) คิดเดือนคงคลังไม่ได้ จึงถูกแยก
รายงานไว้แทนการเดา
"""
from datetime import date, timedelta
from typing import Any, Iterable

import warehouse_db

#: เกณฑ์ที่ผู้ใช้กำหนด แก้ได้ที่นี่ที่เดียว
LOW_STOCK_MONTHS = 1.0
EXPIRY_CRITICAL_MONTHS = 3
EXPIRY_WARNING_MONTHS = 6
DORMANT_MONTHS = 6
AVERAGE_MONTHS = 6

#: สถานะสอบทานที่นับเป็นของจริง ขาเข้าไม่เคยผ่านการสอบทาน จึงหักด้วยยอดรวมทุกสถานะ
VERIFIED = "VERIFIED"


def _days(months: int) -> int:
    return int(round(months * 30.4375))


def _shift(day: str, months: int) -> str:
    moment = date(int(day[:4]), int(day[4:6]), int(day[6:8])) + timedelta(days=_days(months))
    return moment.strftime("%Y%m%d")


def _months_before(period: str, months: int) -> str:
    year, month = int(period[:4]), int(period[4:6])
    total = year * 12 + (month - 1) - months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def covered_months(conn, store: str | None, first: str, latest: str) -> int:
    """จำนวนเดือนที่ระบบมีข้อมูลใบจ่ายครบ ใช้เป็นตัวหารของค่าเฉลี่ย

    ถ้าหารด้วยจำนวนเดือนที่ "มีการเคลื่อนไหว" ยาที่ใช้เดือนเดียวใน 6 เดือนจะถูกมองว่า
    ใช้เดือนละเท่ากับทั้งก้อน ทำให้เดือนคงคลังสั้นกว่าจริงหกเท่า
    """
    where = ["kind = 'issue'", "status = 'complete'", "period >= ?", "period < ?"]
    values: list[Any] = [first, latest]
    if store is not None:
        where.append("store = ?")
        values.append(store)
    found = conn.execute(
        f"SELECT COUNT(DISTINCT period) FROM periods WHERE {' AND '.join(where)}",
        values).fetchone()[0]
    return int(found or 0)


def snapshot_days(conn, stores: Iterable[str] | None = None) -> dict[str, str]:
    """วันของภาพคงคลังล่าสุดที่ดึงสำเร็จ รายคลัง"""
    rows = conn.execute(
        "SELECT store, MAX(period) FROM periods WHERE kind = 'balance' AND status = 'complete' "
        "GROUP BY store").fetchall()
    wanted = set(stores) if stores is not None else None
    return {store: day for store, day in rows if wanted is None or store in wanted}


def _balance_scope(conn, stores: Iterable[str] | None) -> tuple[str, list]:
    """เงื่อนไขเลือกเฉพาะภาพคงคลังล่าสุดของแต่ละคลัง"""
    days = snapshot_days(conn, stores)
    if not days:
        return "0 = 1", []
    clause = " OR ".join("(store = ? AND period = ?)" for _ in days)
    values = [value for store, day in sorted(days.items()) for value in (store, day)]
    return "(" + clause + ")", values


def stock(conn, stores: Iterable[str] | None = None) -> dict[str, Any]:
    """มูลค่าคงคลังตามภาพล่าสุด พร้อมส่วนที่หมดอายุแล้ว"""
    clause, values = _balance_scope(conn, stores)
    today = date.today().strftime("%Y%m%d")
    lots, total, expired_lots, expired_value = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(value), 0), "
        "       COALESCE(SUM(CASE WHEN expire_date <> '' AND expire_date < ? THEN 1 ELSE 0 END), 0), "
        "       COALESCE(SUM(CASE WHEN expire_date <> '' AND expire_date < ? THEN value ELSE 0 END), 0) "
        f"FROM balances WHERE {clause}", [today, today, *values]).fetchone()
    by_store = [
        {"store": store, "lots": store_lots, "value": store_value}
        for store, store_lots, store_value in conn.execute(
            f"SELECT store, COUNT(*), COALESCE(SUM(value), 0) FROM balances WHERE {clause} "
            "GROUP BY store ORDER BY 3 DESC", values)
    ]
    return {"as_of": snapshot_days(conn, stores), "lots": lots, "value": total,
            "expired_lots": expired_lots, "expired_value": expired_value, "by_store": by_store}


def consumption(conn, store: str | None = None, months: int = AVERAGE_MONTHS,
                until: str | None = None) -> dict[str, Any]:
    """ยอดใช้รายเดือน — สูตรต่างกันระหว่างทั้งโรงพยาบาลกับรายคลัง

    เดือนที่ยังไม่จบถูกตัดออกจากค่าเฉลี่ยเสมอ มิฉะนั้นค่าเฉลี่ยจะต่ำกว่าจริงทุกต้นเดือน
    """
    latest = until or date.today().strftime("%Y%m")
    first = _months_before(latest, months)
    kinds = "('dispense')" if store is None else "('dispense', 'transfer')"
    where = ["period >= ?", "period < ?", "movement_kind IN " + kinds]
    values: list[Any] = [first, latest]
    if store is not None:
        where.append("store = ?")
        values.append(store)
    rows = conn.execute(
        "SELECT period, "
        "       COALESCE(SUM(CASE WHEN direction = 'out' AND check_status = ? "
        "                         THEN value ELSE 0 END), 0), "
        "       COALESCE(SUM(CASE WHEN direction = 'in' AND movement_kind = 'dispense' "
        "                         THEN value ELSE 0 END), 0) "
        f"FROM issues WHERE {' AND '.join(where)} GROUP BY period ORDER BY period",
        [VERIFIED, *values]).fetchall()
    monthly = [{"period": period, "issued": issued, "returned": returned,
                "net": issued - returned} for period, issued, returned in rows]
    divisor = covered_months(conn, store, first, latest) or len(monthly)
    average = sum(row["net"] for row in monthly) / divisor if divisor else 0.0
    return {"scope": store or "ทั้งโรงพยาบาล", "months": monthly, "average": average,
            "covered_months": divisor, "from_period": first, "until_period": latest}


def months_of_stock(stock_value: float, average: float) -> float | None:
    """เดือนคงคลัง คืน None เมื่อไม่มียอดใช้ให้หาร ไม่แทนด้วยศูนย์หรือค่าอนันต์"""
    return stock_value / average if average > 0 else None


def expiring(conn, stores: Iterable[str] | None = None) -> dict[str, Any]:
    """คงคลังแยกตามวันหมดอายุ ตามเกณฑ์ 6 เดือน และ 3 เดือนเป็นระดับวิกฤต"""
    clause, values = _balance_scope(conn, stores)
    today = date.today().strftime("%Y%m%d")
    bounds = {
        "expired": ("00000000", today),
        "critical": (today, _shift(today, EXPIRY_CRITICAL_MONTHS)),
        "warning": (_shift(today, EXPIRY_CRITICAL_MONTHS), _shift(today, EXPIRY_WARNING_MONTHS)),
    }
    buckets: dict[str, Any] = {}
    for name, (low, high) in bounds.items():
        lots, value = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(value), 0) FROM balances WHERE {clause} "
            "AND expire_date <> '' AND expire_date >= ? AND expire_date < ?",
            [*values, low, high]).fetchone()
        buckets[name] = {"lots": lots, "value": value}
    lots, value = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(value), 0) FROM balances WHERE {clause} AND expire_date = ''",
        values).fetchone()
    buckets["no_expiry_date"] = {"lots": lots, "value": value}
    return buckets


def expiring_lots(conn, stores: Iterable[str] | None = None,
                  limit: int = 50) -> list[dict[str, Any]]:
    """ล็อตที่หมดอายุแล้วหรือจะหมดภายในเกณฑ์เตือน เรียงตามมูลค่า"""
    clause, values = _balance_scope(conn, stores)
    today = date.today().strftime("%Y%m%d")
    horizon = _shift(today, EXPIRY_WARNING_MONTHS)
    rows = conn.execute(
        "SELECT b.store, b.stock_code, COALESCE(i.name, ''), b.lot_no, b.qty, b.value, b.expire_date "
        "FROM balances b LEFT JOIN items i ON i.stock_code = b.stock_code "
        f"WHERE {clause} AND b.expire_date <> '' AND b.expire_date < ? "
        "ORDER BY b.value DESC LIMIT ?", [*values, horizon, limit]).fetchall()
    return [{"store": store, "stock_code": code, "name": name, "lot_no": lot, "qty": qty,
             "value": value, "expire_date": expire, "expired": expire < today}
            for store, code, name, lot, qty, value, expire in rows]


def dormant(conn, stores: Iterable[str] | None = None, months: int = DORMANT_MONTHS,
            until: str | None = None) -> list[dict[str, Any]]:
    """ของที่ยังมีอยู่แต่ไม่มีการเคลื่อนไหวออกเลยตามจำนวนเดือนที่กำหนด

    นับการเคลื่อนไหวออกทุกแบบ ทั้งจ่ายและโอน คลังหลักจ่ายออกด้วยการโอนเป็นหลัก
    ถ้านับเฉพาะการจ่ายผู้ป่วย คลังหลักจะกลายเป็นของค้างนิ่งเกือบทั้งคลัง
    """
    clause, values = _balance_scope(conn, stores)
    latest = until or date.today().strftime("%Y%m")
    first = _months_before(latest, months)
    rows = conn.execute(
        "SELECT b.store, b.stock_code, COALESCE(i.name, ''), SUM(b.qty), SUM(b.value) "
        "FROM balances b LEFT JOIN items i ON i.stock_code = b.stock_code "
        f"WHERE {clause} AND NOT EXISTS ("
        "    SELECT 1 FROM issues s WHERE s.store = b.store AND s.stock_code = b.stock_code "
        "      AND s.direction = 'out' AND s.period >= ? AND s.period <= ?) "
        "GROUP BY b.store, b.stock_code HAVING SUM(b.value) > 0 ORDER BY 5 DESC",
        [*values, first, latest]).fetchall()
    return [{"store": store, "stock_code": code, "name": name, "qty": qty, "value": value}
            for store, code, name, qty, value in rows]


def _unit_set(text: str | None) -> set[str]:
    return {unit.strip() for unit in (text or "").split(",") if unit and unit.strip()}


def low_stock(conn, stores: Iterable[str] | None = None, months: int = AVERAGE_MONTHS,
              threshold: float = LOW_STOCK_MONTHS, until: str | None = None) -> dict[str, Any]:
    """รายการที่เหลือพอใช้ต่ำกว่าเกณฑ์ รวมของที่หมดเกลี้ยงแล้วแต่ยังมีการใช้

    ของที่หมดแล้วไม่มีอยู่ในภาพคงคลัง เพราะระบบเก็บเฉพาะล็อตที่เหลือมากกว่าศูนย์
    ถ้าดูจากคงคลังอย่างเดียว รายการที่เร่งด่วนที่สุดจะหายไปจากรายงาน

    ขอบเขตเปลี่ยนทั้งการรวมของและสูตรยอดใช้ ทั้งโรงพยาบาลรวมคงคลังทุกคลังเข้าด้วยกัน
    และนับเฉพาะการจ่ายผู้ป่วย ส่วนรายคลังดูเฉพาะของในคลังนั้นและนับการโอนออกด้วย

    ปกติวัดด้วยมูลค่า เพราะหน่วยตัดกันไปเอง ส่วนรายการที่ระบบไม่ได้ลงมูลค่าไว้ (เช่น
    วัคซีนที่ได้รับจัดสรร) วัดด้วยจำนวนได้ก็ต่อเมื่อหน่วยของคงคลังกับของใบจ่ายตรงกัน
    ถ้าไม่ตรงจะรายงานแยกไว้ ไม่แปลงหน่วยเอง
    """
    store = next(iter(stores), None) if stores is not None else None
    clause, values = _balance_scope(conn, stores)
    latest = until or date.today().strftime("%Y%m")
    first = _months_before(latest, months)
    divisor = covered_months(conn, store, first, latest) or months

    held = {
        code: (qty, value, _unit_set(units)) for code, qty, value, units in conn.execute(
            f"SELECT stock_code, SUM(qty), SUM(value), GROUP_CONCAT(DISTINCT unit) "
            f"FROM balances WHERE {clause} GROUP BY stock_code", values)
    }
    kinds = "('dispense')" if store is None else "('dispense', 'transfer')"
    where = ["period >= ?", "period < ?", "movement_kind IN " + kinds]
    used_values: list[Any] = [VERIFIED, VERIFIED, first, latest]
    if store is not None:
        where.append("store = ?")
        used_values.append(store)
    used = {
        code: (out_value - in_value, out_qty - in_qty, _unit_set(units))
        for code, out_value, in_value, out_qty, in_qty, units in conn.execute(
            "SELECT stock_code, "
            "       COALESCE(SUM(CASE WHEN direction = 'out' AND check_status = ? "
            "                         THEN value ELSE 0 END), 0), "
            "       COALESCE(SUM(CASE WHEN direction = 'in' AND movement_kind = 'dispense' "
            "                         THEN value ELSE 0 END), 0), "
            "       COALESCE(SUM(CASE WHEN direction = 'out' AND check_status = ? "
            "                         THEN qty ELSE 0 END), 0), "
            "       COALESCE(SUM(CASE WHEN direction = 'in' AND movement_kind = 'dispense' "
            "                         THEN qty ELSE 0 END), 0), "
            "       GROUP_CONCAT(DISTINCT unit) "
            f"FROM issues WHERE {' AND '.join(where)} GROUP BY stock_code", used_values)
    }
    catalogue = {code: (name, bool(retired)) for code, name, retired in
                 conn.execute("SELECT stock_code, name, retired FROM items")}

    items, unmeasurable, retired_items = [], [], []
    for code in set(held) | set(used):
        qty, value, stock_units = held.get(code, (0.0, 0.0, set()))
        used_value, used_qty, issue_units = used.get(code, (0.0, 0.0, set()))
        monthly_value, monthly_qty = used_value / divisor, used_qty / divisor
        name, retired = catalogue.get(code, ("", False))
        row = {"store": store or "", "stock_code": code, "name": name, "qty": qty,
               "value": value, "monthly_use": monthly_value, "monthly_use_qty": monthly_qty}
        if monthly_value <= 0 and monthly_qty <= 0:
            continue          # ไม่มีการใช้ ไม่ใช่ของใกล้หมด (ดูของค้างนิ่งแทน)
        if retired:
            retired_items.append(row)
            continue          # รหัสที่เลิกใช้แล้ว ไม่ต้องสั่งเพิ่ม
        if value > 0 and monthly_value > 0:
            remaining, basis = value / monthly_value, "value"
        elif monthly_qty > 0 and qty <= 0:
            remaining, basis = 0.0, "empty"   # หมดเกลี้ยง ไม่ต้องเทียบหน่วยก็รู้ว่าหมด
        elif monthly_qty > 0 and len(stock_units | issue_units) == 1:
            remaining, basis = qty / monthly_qty, "qty"
        else:
            reason = ("ระบบไม่ได้ลงมูลค่าไว้ และหน่วยคงคลังกับหน่วยจ่ายไม่ตรงกัน"
                      if stock_units | issue_units else "ระบบไม่ได้ลงมูลค่าและไม่ระบุหน่วย")
            unmeasurable.append({**row, "stock_units": sorted(stock_units),
                                 "issue_units": sorted(issue_units), "reason": reason})
            continue
        if remaining < threshold:
            items.append({**row, "months_left": remaining, "basis": basis,
                          "out_of_stock": qty <= 0})
    items.sort(key=lambda row: (not row["out_of_stock"], row["months_left"], -row["monthly_use"]))
    return {"threshold_months": threshold, "items": items, "without_value": unmeasurable,
            "retired_in_use": retired_items}


def summary(store: str | None = None, conn=None) -> dict[str, Any]:
    """ตัวชี้วัดชุดผู้บริหารสำหรับขอบเขตหนึ่ง ทั้งโรงพยาบาลเมื่อไม่ระบุคลัง"""
    own = conn is None
    conn = conn or warehouse_db.connect()
    try:
        stores = None if store is None else [store]
        held = stock(conn, stores)
        used = consumption(conn, store)
        return {
            "scope": store or "ทั้งโรงพยาบาล",
            "as_of": held["as_of"],
            "stock": held,
            "consumption": used,
            "months_of_stock": months_of_stock(held["value"], used["average"]),
            "expiring": expiring(conn, stores),
            "dormant_value": sum(row["value"] for row in dormant(conn, stores)),
            "low_stock_count": len(low_stock(conn, stores)["items"]),
        }
    finally:
        if own:
            conn.close()
