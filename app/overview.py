"""ภาพรวมคลังทั้งโรงพยาบาล แบบเดียวกับที่ Stock5 ให้ดู แต่ครบทุกประเภทของ

ผู้ใช้สั่ง 17 ก.ย. 2569:
    "อยากได้การดูข้อมูลเหมือน stock5 ครับ แต่ว่าตัดส่วนของข้อมูลส่งกระทรวงออก และดูได้ทุกอย่าง
     ทุกประเภทของ ยา อาหาร พัสดุ งานจ้าง งานก่อสร้าง วัสดุคอมพิวเตอร์ทุกอย่าง"

Stock5 มีหน้าดูข้อมูล 3 แบบ ที่นี่ทำแบบเดียวกันแต่กรองได้ทุกกลุ่มและทุกคลัง
    ภาพรวม            มูลค่าคงคลัง · ยอดรับ · ยอดใช้ · เดือนคงคลัง · ใกล้หมดอายุ · ค้างนิ่ง
    ค้นหารายการ        ค้นด้วยรหัสหรือชื่อ ข้ามทุกกลุ่ม
    รายละเอียดรายการ  คงคลังรายคลังรายล็อต · รับ/ใช้รายเดือน · ผู้ขาย · ใบรับ · หน่วยงานที่เบิก

กติกาที่ใช้ร่วมกับหน้าอื่น — ตัวเลขข้ามหน้าต้องตรงกันเสมอ
    ยอดใช้ทั้งโรงพยาบาล  ใบจ่ายชนิด 32 ขาออกที่สอบทานผ่าน หักใบคืนทั้งหมด (department_usage.net)
    ยอดใช้รายคลัง        นับการโอนออกด้วย ไม่งั้นคลังหลักซึ่งจ่ายออกด้วยการโอนจะดูเหมือนไม่ได้ใช้
    ช่วงเวลา             เดือนที่จบแล้วเท่านั้น นับถอยจากงวดล่าสุดที่มีข้อมูล ไม่ใช่จากวันนี้
    กลุ่มของ             คำนวณจากหมวดของรายการตอนอ่าน (categories.sql_group_expression)

**สิ่งที่ตัวเลขนี้ยังไม่รู้**
    - ครุภัณฑ์และงานจ้างคิดเดือนคงคลังไม่ได้ เพราะไม่ได้ถูก "ใช้หมด" (categories.CONSUMABLE_GROUPS)
    - งานจ้างไม่มีคงคลังและไม่ถูกเบิก เห็นได้จากยอดรับเท่านั้น
"""
from datetime import date
from typing import Any, Iterable

import categories
import department_usage
import departments
import metrics
import stock5_engine
import stores

DEFAULT_MONTHS = 12
SEARCH_LIMIT = 60


import name_cleaner


def clean_name(raw: object) -> str:
    """ชื่อผู้ขายจาก SSB มีอักขระตัวแรกซ้ำเหมือนชื่อยา และมีช่องว่างแบบไม่ตัดบรรทัด/backslash"""
    return name_cleaner.clean_vendor_name(raw)


# --------------------------------------------------------------------------- ขอบเขต

def latest_period(conn) -> str:
    """งวดล่าสุดที่มีข้อมูลใบรับหรือใบจ่าย — ใช้แทน "เดือนนี้" เพราะต้นทางเป็นสำเนารายวัน"""
    row = conn.execute(
        "SELECT MAX(period) FROM (SELECT MAX(period) AS period FROM issues WHERE LENGTH(period) = 6 "
        "UNION ALL SELECT MAX(period) FROM receipts WHERE LENGTH(period) = 6)").fetchone()
    return (row[0] if row else "") or ""


def window(months: int, latest: str) -> tuple[str, str]:
    """ช่วงเดือนที่จบแล้ว [first, latest) — ชุดเดียวกับหน้ารายแผนก"""
    _, (first, until) = department_usage._period_clause(months, latest)
    return first, until


def _item_filter(group: str | None, column: str) -> tuple[str, list[Any]]:
    if not group:
        return "", []
    clause = categories.sql_category_filter(group, "main_category")
    return f" AND {column} IN (SELECT stock_code FROM items WHERE {clause})", []


def _store_filter(store: str | None, column: str) -> tuple[str, list[Any]]:
    return (f" AND {column} = ?", [store]) if store else ("", [])


def _use_kinds(store: str | None) -> str:
    """ทั้งโรงพยาบาลนับเฉพาะจ่าย รายคลังนับโอนออกด้วย — ตามกติกาใน metrics.py"""
    return "('dispense')" if store is None else "('dispense', 'transfer')"


def _use_value(alias: str = "i", column: str = "value") -> str:
    """ยอดใช้สุทธิของหนึ่งแถว ขาออกที่สอบทานผ่าน หักใบคืน (ขาเข้าของใบจ่ายเท่านั้น)"""
    at = f"{alias}." if alias else ""
    return (f"CASE WHEN {at}direction = 'out' AND {at}check_status = '{metrics.VERIFIED}' "
            f"THEN {at}{column} "
            f"WHEN {at}direction = 'in' AND {at}movement_kind = 'dispense' THEN -{at}{column} "
            "ELSE 0 END")


def _balances(conn, store: str | None) -> tuple[str, list[Any]]:
    """ภาพคงคลังล่าสุดของแต่ละคลัง ในรูปตารางย่อยที่ join ต่อได้"""
    clause, values = metrics._balance_scope(conn, [store] if store else None)
    return f"(SELECT * FROM balances WHERE {clause})", values


def snapshot_day(conn, store: str | None = None) -> str:
    days = metrics.snapshot_days(conn, [store] if store else None)
    return max(days.values()) if days else ""


# --------------------------------------------------------------------------- ภาพรวม

def group_table(conn, months: int = DEFAULT_MONTHS, store: str | None = None,
                latest: str | None = None) -> dict[str, Any]:
    """หนึ่งแถวต่อกลุ่มของ — คงคลัง ยอดรับ ยอดใช้ และเดือนคงคลัง"""
    latest = latest or latest_period(conn)
    if not latest:
        return {"rows": [], "latest": "", "months": months, "reason": "ยังไม่มีข้อมูลในคลังข้อมูล"}
    first, until = window(months, latest)
    grouping = categories.sql_group_expression("m.main_category")
    found: dict[str, dict[str, Any]] = {
        group.key: {"key": group.key, "name": group.name_th, "stock": 0.0, "items_held": 0,
                    "received": 0.0, "receipts": 0, "used": 0.0}
        for group in categories.GROUPS}

    table, values = _balances(conn, store)
    for key, value, items in conn.execute(
            f"SELECT {grouping}, COALESCE(SUM(b.value), 0), COUNT(DISTINCT b.stock_code) "
            f"FROM {table} b LEFT JOIN items m ON m.stock_code = b.stock_code GROUP BY 1", values):
        found[key].update(stock=value, items_held=items)

    where, extra = _store_filter(store, "r.store")
    for key, value, receipts in conn.execute(
            f"SELECT {grouping}, COALESCE(SUM(r.value), 0), COUNT(DISTINCT r.rcv_no) "
            "FROM receipts r LEFT JOIN items m ON m.stock_code = r.stock_code "
            f"WHERE r.period >= ? AND r.period < ?{where} GROUP BY 1", [first, until, *extra]):
        found[key].update(received=value, receipts=receipts)

    where, extra = _store_filter(store, "i.store")
    for key, value in conn.execute(
            f"SELECT {grouping}, COALESCE(SUM({_use_value()}), 0) "
            "FROM issues i LEFT JOIN items m ON m.stock_code = i.stock_code "
            f"WHERE i.period >= ? AND i.period < ? AND i.movement_kind IN {_use_kinds(store)}"
            f"{where} GROUP BY 1", [first, until, *extra]):
        found[key]["used"] = value

    divisor = metrics.covered_months(conn, store, first, until) or months
    rows = []
    for row in found.values():
        if not (row["stock"] or row["received"] or row["used"]):
            continue
        average = row["used"] / divisor if divisor else 0.0
        consumable = row["key"] in categories.CONSUMABLE_GROUPS
        row.update(average_use=average,
                   months_of_stock=(row["stock"] / average
                                    if consumable and average > 0 else None),
                   consumable=consumable)
        rows.append(row)
    rows.sort(key=lambda row: -(row["received"] + row["used"] + row["stock"]))
    return {"rows": rows, "latest": latest, "first": first, "until": until, "months": months,
            "covered_months": divisor, "snapshot": snapshot_day(conn, store), "reason": "",
            "receipt_stores": [store_code for (store_code,) in conn.execute(
                "SELECT DISTINCT store FROM receipts WHERE period >= ? AND period < ?",
                [first, until])]}


def monthly(conn, months: int = DEFAULT_MONTHS, store: str | None = None,
            group: str | None = None, latest: str | None = None) -> list[dict[str, Any]]:
    """ยอดรับและยอดใช้รายเดือน รวมเดือนล่าสุดที่ยังไม่จบ (ทำเครื่องหมายไว้)"""
    latest = latest or latest_period(conn)
    if not latest:
        return []
    first, _ = window(months, latest)
    by_period: dict[str, dict[str, Any]] = {}

    item_where, _ = _item_filter(group, "r.stock_code")
    store_where, extra = _store_filter(store, "r.store")
    for period, value in conn.execute(
            "SELECT r.period, COALESCE(SUM(r.value), 0) FROM receipts r "
            f"WHERE r.period >= ? AND r.period <= ?{item_where}{store_where} GROUP BY 1",
            [first, latest, *extra]):
        by_period.setdefault(period, {"period": period, "received": 0.0, "used": 0.0})
        by_period[period]["received"] = value

    item_where, _ = _item_filter(group, "i.stock_code")
    store_where, extra = _store_filter(store, "i.store")
    for period, value in conn.execute(
            f"SELECT i.period, COALESCE(SUM({_use_value()}), 0) FROM issues i "
            f"WHERE i.period >= ? AND i.period <= ? AND i.movement_kind IN {_use_kinds(store)}"
            f"{item_where}{store_where} GROUP BY 1", [first, latest, *extra]):
        by_period.setdefault(period, {"period": period, "received": 0.0, "used": 0.0})
        by_period[period]["used"] = value

    rows = []
    year, month = int(first[:4]), int(first[4:6])
    while f"{year:04d}{month:02d}" <= latest:
        key = f"{year:04d}{month:02d}"
        row = by_period.get(key, {"period": key, "received": 0.0, "used": 0.0})
        rows.append({**row, "partial": key == latest})
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return rows


def headline(conn, months: int = DEFAULT_MONTHS, store: str | None = None,
             group: str | None = None, latest: str | None = None) -> dict[str, Any]:
    """การ์ดตัวเลขบนสุด แบบเดียวกับ Stock5 — ทุกใบกดดูรายการที่ประกอบเป็นตัวเลขได้"""
    latest = latest or latest_period(conn)
    first, until = window(months, latest) if latest else ("", "")
    table, values = _balances(conn, store)
    item_where, _ = _item_filter(group, "b.stock_code")
    today = date.today().strftime("%Y%m%d")
    critical = metrics._shift(today, metrics.EXPIRY_CRITICAL_MONTHS)
    warning = metrics._shift(today, metrics.EXPIRY_WARNING_MONTHS)
    (stock_value, lots, items, expired, expiring_critical,
     expiring_warning) = conn.execute(
        "SELECT COALESCE(SUM(b.value), 0), COUNT(*), COUNT(DISTINCT b.stock_code), "
        "  COALESCE(SUM(CASE WHEN b.expire_date <> '' AND b.expire_date < ? THEN b.value END), 0), "
        "  COALESCE(SUM(CASE WHEN b.expire_date >= ? AND b.expire_date < ? THEN b.value END), 0), "
        "  COALESCE(SUM(CASE WHEN b.expire_date >= ? AND b.expire_date < ? THEN b.value END), 0) "
        f"FROM {table} b WHERE 1 = 1{item_where}",
        [today, today, critical, critical, warning, *values]).fetchone()

    received = used = 0.0
    if latest:
        item_where, _ = _item_filter(group, "stock_code")
        store_where, extra = _store_filter(store, "store")
        received = conn.execute(
            "SELECT COALESCE(SUM(value), 0) FROM receipts "
            f"WHERE period >= ? AND period < ?{item_where}{store_where}",
            [first, until, *extra]).fetchone()[0]
        used = conn.execute(
            f"SELECT COALESCE(SUM({_use_value('')}), 0) FROM issues "
            f"WHERE period >= ? AND period < ? AND movement_kind IN {_use_kinds(store)}"
            f"{item_where}{store_where}", [first, until, *extra]).fetchone()[0]
    divisor = metrics.covered_months(conn, store, first, until) if latest else 0
    average = used / divisor if divisor else 0.0
    measurable = group is None or group in categories.CONSUMABLE_GROUPS
    return {
        # ห้ามตั้งชื่อคีย์ว่า items — Jinja อ่าน head.items เป็นเมธอดของ dict ไม่ใช่ค่าในนี้
        "stock_value": stock_value, "lots": lots, "item_count": items,
        "expired": expired, "expiring_critical": expiring_critical,
        "expiring_warning": expiring_warning,
        "dormant": sum(row["value"] for row in dormant(conn, store, group, latest=latest)),
        "received": received, "used": used, "average_use": average,
        "months_of_stock": stock_value / average if measurable and average > 0 else None,
        "latest": latest, "first": first, "until": until, "months": months,
        "covered_months": divisor, "snapshot": snapshot_day(conn, store),
    }


# --------------------------------------------------------------------------- รายการตามการ์ด

def expiring(conn, store: str | None = None, group: str | None = None,
             limit: int = 300) -> list[dict[str, Any]]:
    """ล็อตที่หมดอายุแล้วหรือจะหมดภายในเกณฑ์เตือน — เรียงตามระดับ แล้วตามมูลค่า

    รุ่นแรกเรียงตามวันหมดอายุล้วน หัวตารางจึงเต็มไปด้วยล็อตที่หมดอายุตั้งแต่ปี 2547 ซึ่ง
    เหลือศูนย์ชิ้นและไม่มีมูลค่า ล็อตที่ไม่มีของเหลือจึงไม่แสดง และในระดับเดียวกันเรียงตามเงิน
    """
    table, values = _balances(conn, store)
    item_where, _ = _item_filter(group, "b.stock_code")
    today = date.today().strftime("%Y%m%d")
    horizon = metrics._shift(today, metrics.EXPIRY_WARNING_MONTHS)
    critical = metrics._shift(today, metrics.EXPIRY_CRITICAL_MONTHS)
    rows = conn.execute(
        "SELECT b.store, b.stock_code, COALESCE(m.name, ''), COALESCE(m.main_category, ''), "
        "       b.lot_no, b.qty, b.unit, b.value, b.expire_date "
        f"FROM {table} b LEFT JOIN items m ON m.stock_code = b.stock_code "
        f"WHERE b.expire_date <> '' AND b.expire_date < ? AND (b.qty > 0 OR b.value > 0){item_where} "
        "ORDER BY CASE WHEN b.expire_date < ? THEN 0 WHEN b.expire_date < ? THEN 1 ELSE 2 END, "
        "         b.value DESC, b.expire_date LIMIT ?",
        [*values, horizon, today, critical, limit]).fetchall()
    return [{"store": code_store, "store_name": stores.store_name(code_store), "stock_code": code,
             "name": name, "group": categories.group_name(categories.group_of(category)),
             "lot_no": lot, "qty": qty, "unit": unit, "value": value, "expire_date": expire,
             "level": ("expired" if expire < today else
                       "critical" if expire < critical else "warning")}
            for code_store, code, name, category, lot, qty, unit, value, expire in rows]


def dormant(conn, store: str | None = None, group: str | None = None,
            months: int = metrics.DORMANT_MONTHS, latest: str | None = None,
            limit: int | None = None) -> list[dict[str, Any]]:
    """ของที่ยังมีอยู่แต่ไม่มีการเคลื่อนไหวออกเลยตามเกณฑ์ — กติกาเดียวกับ metrics.dormant"""
    latest = latest or latest_period(conn)
    if not latest:
        return []
    first = metrics._months_before(latest, months)
    table, values = _balances(conn, store)
    item_where, _ = _item_filter(group, "b.stock_code")
    rows = conn.execute(
        "SELECT b.store, b.stock_code, COALESCE(m.name, ''), COALESCE(m.main_category, ''), "
        "       SUM(b.qty), MAX(b.unit), SUM(b.value), MAX(b.last_in_date) "
        f"FROM {table} b LEFT JOIN items m ON m.stock_code = b.stock_code "
        "WHERE NOT EXISTS (SELECT 1 FROM issues s WHERE s.store = b.store "
        "    AND s.stock_code = b.stock_code AND s.direction = 'out' "
        f"    AND s.period >= ? AND s.period <= ?){item_where} "
        "GROUP BY b.store, b.stock_code HAVING SUM(b.value) > 0 ORDER BY 7 DESC"
        + (" LIMIT ?" if limit else ""),
        [*values, first, latest, *([limit] if limit else [])]).fetchall()
    return [{"store": code_store, "store_name": stores.store_name(code_store), "stock_code": code,
             "name": name, "group": categories.group_name(categories.group_of(category)),
             "qty": qty, "unit": unit, "value": value, "last_in_date": last_in}
            for code_store, code, name, category, qty, unit, value, last_in in rows]


# --------------------------------------------------------------------------- ค้นหา

def search(conn, text: str = "", group: str | None = None, store: str | None = None,
           months: int = DEFAULT_MONTHS, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
    """ค้นด้วยรหัสหรือชื่อ ข้ามทุกกลุ่ม ไม่ใส่คำค้นแต่เลือกกลุ่ม = รายการเด่นของกลุ่มนั้น

    เรียงตามความสำคัญ (คงคลัง + ยอดใช้ + ยอดรับ) เพราะคำค้นสั้น ๆ อย่าง "กระดาษ" ได้หลายร้อย
    รายการ รายการที่เงินหมุนเวียนมากควรขึ้นก่อน
    """
    text = (text or "").strip()[:60]
    where, values = ["1 = 1"], []
    if text:
        where.append("(m.stock_code LIKE ? OR m.name LIKE ? OR m.trade_name LIKE ?)")
        values += [f"{text}%", f"%{text}%", f"%{text}%"]
    if group:
        where.append(categories.sql_category_filter(group, "m.main_category"))
    if not text and not group:
        return []

    latest = latest_period(conn)
    first, until = window(months, latest) if latest else ("", "")
    table, balance_values = _balances(conn, store)
    store_where, store_values = _store_filter(store, "store")
    rows = conn.execute(
        "SELECT m.stock_code, m.name, m.trade_name, m.main_category, m.retired, "
        "       COALESCE(b.value, 0), COALESCE(b.stores, 0), "
        "       COALESCE(u.used, 0), COALESCE(r.received, 0), r.last_received "
        "FROM items m "
        f"LEFT JOIN (SELECT stock_code, SUM(value) AS value, COUNT(DISTINCT store) AS stores "
        f"           FROM {table} GROUP BY stock_code) b ON b.stock_code = m.stock_code "
        f"LEFT JOIN (SELECT stock_code, SUM({_use_value('')}) AS used FROM issues "
        f"           WHERE period >= ? AND period < ? AND movement_kind IN {_use_kinds(store)}"
        f"{store_where} GROUP BY stock_code) u ON u.stock_code = m.stock_code "
        "LEFT JOIN (SELECT stock_code, SUM(value) AS received, MAX(rcv_date) AS last_received "
        f"           FROM receipts WHERE period >= ? AND period < ?{store_where} "
        "           GROUP BY stock_code) r ON r.stock_code = m.stock_code "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY m.retired, (COALESCE(b.value, 0) + COALESCE(u.used, 0) + COALESCE(r.received, 0)) DESC, "
        "         m.stock_code LIMIT ?",
        [*balance_values, first, until, *store_values, first, until, *store_values,
         *values, limit]).fetchall()
    return [{"stock_code": code, "name": name, "trade_name": trade, "retired": bool(retired),
             "group_key": categories.group_of(category),
             "group": categories.group_name(categories.group_of(category)),
             "stock_value": stock_value, "stores": store_count, "used": used,
             "received": received, "last_received": last_received or ""}
            for code, name, trade, category, retired, stock_value, store_count, used, received,
            last_received in rows]


# --------------------------------------------------------------------------- รายละเอียดรายการ

def item(conn, code: str, months: int = DEFAULT_MONTHS) -> dict[str, Any] | None:
    """ทุกอย่างของรายการเดียว แบบหน้ารายละเอียดยาของ Stock5 แต่ใช้ได้กับของทุกประเภท"""
    found = conn.execute(
        "SELECT stock_code, name, trade_name, main_category, base_unit, retired "
        "FROM items WHERE stock_code = ?", [code]).fetchone()
    if found is None:
        return None
    code, name, trade, category, base_unit, retired = found
    group_key = categories.group_of(category)
    latest = latest_period(conn)
    first, until = window(months, latest) if latest else ("", "")

    table, values = _balances(conn, None)
    lots = [{"store": store_code, "store_name": stores.store_name(store_code), "lot_no": lot,
             "qty": qty, "unit": unit, "value": value, "expire_date": expire,
             "last_in_date": last_in}
            for store_code, lot, qty, unit, value, expire, last_in in conn.execute(
                "SELECT store, lot_no, qty, unit, value, expire_date, last_in_date "
                f"FROM {table} WHERE stock_code = ? "
                "ORDER BY store, CASE WHEN expire_date = '' THEN 1 ELSE 0 END, expire_date",
                [*values, code])]
    by_store: dict[str, dict[str, Any]] = {}
    for lot in lots:
        entry = by_store.setdefault(lot["store"], {
            "store": lot["store"], "store_name": lot["store_name"], "qty": 0.0, "value": 0.0,
            "lots": 0, "units": set(), "nearest_expiry": ""})
        entry["qty"] += lot["qty"] or 0
        entry["value"] += lot["value"] or 0
        entry["lots"] += 1
        if lot["unit"]:
            entry["units"].add(lot["unit"])
        if lot["expire_date"] and (not entry["nearest_expiry"]
                                   or lot["expire_date"] < entry["nearest_expiry"]):
            entry["nearest_expiry"] = lot["expire_date"]
    stock_by_store = sorted(({**entry, "units": ", ".join(sorted(entry["units"]))}
                             for entry in by_store.values()), key=lambda entry: -entry["value"])

    months_rows = []
    if latest:
        received = {period: (qty, value) for period, qty, value in conn.execute(
            "SELECT period, SUM(qty), SUM(value) FROM receipts "
            "WHERE stock_code = ? AND period >= ? AND period <= ? GROUP BY period",
            [code, first, latest])}
        used = {period: (qty, value, transfer) for period, qty, value, transfer in conn.execute(
            f"SELECT period, SUM(CASE WHEN movement_kind = 'dispense' THEN {_use_value('', 'qty')} END), "
            f"       SUM(CASE WHEN movement_kind = 'dispense' THEN {_use_value('')} END), "
            "       SUM(CASE WHEN movement_kind = 'transfer' AND direction = 'out' THEN value END) "
            "FROM issues WHERE stock_code = ? AND period >= ? AND period <= ? GROUP BY period",
            [code, first, latest])}
        for row in monthly_frame(first, latest):
            got_qty, got_value = received.get(row["period"], (0, 0))
            use_qty, use_value, transfer = used.get(row["period"], (0, 0, 0))
            months_rows.append({**row, "received_qty": got_qty or 0, "received": got_value or 0,
                                "used_qty": use_qty or 0, "used": use_value or 0,
                                "transferred": transfer or 0})

    closed = [row for row in months_rows if not row["partial"]]
    divisor = metrics.covered_months(conn, None, first, until) if latest else 0
    used_total = sum(row["used"] for row in closed)
    average = used_total / divisor if divisor else 0.0
    stock_value = sum(entry["value"] for entry in stock_by_store)

    vendors = [{"supplier": clean_name(supplier) or "(ไม่ระบุผู้ขาย)", "receipts": count, "qty": qty,
                "value": value, "last_date": last_date, "last_price": last_price}
               for supplier, count, qty, value, last_date, last_price in conn.execute(
                   "SELECT supplier, COUNT(DISTINCT rcv_no), SUM(qty), SUM(value), MAX(rcv_date), "
                   "  (SELECT r2.unit_price FROM receipts r2 WHERE r2.stock_code = r.stock_code "
                   "     AND COALESCE(r2.supplier, '') = COALESCE(r.supplier, '') "
                   "   ORDER BY r2.rcv_date DESC, r2.rcv_no DESC LIMIT 1) "
                   "FROM receipts r WHERE stock_code = ? GROUP BY supplier ORDER BY 5 DESC",
                   [code])]

    receipts = [{"rcv_date": rcv_date, "rcv_no": rcv_no, "store": store_code,
                 "store_name": stores.store_name(store_code), "po_no": po_no,
                 "supplier": clean_name(supplier), "lot_no": lot, "qty": qty, "unit": unit,
                 "unit_price": price, "value": value}
                for rcv_date, rcv_no, store_code, po_no, supplier, lot, qty, unit, price, value
                in conn.execute(
                    "SELECT rcv_date, rcv_no, store, po_no, supplier, lot_no, qty, unit, "
                    "       unit_price, value FROM receipts WHERE stock_code = ? "
                    "ORDER BY rcv_date DESC, rcv_no DESC LIMIT 40", [code])]

    taken_by = []
    if latest:
        for division, dept, qty, value, slips in conn.execute(
                f"SELECT division, dept, SUM({department_usage.net('qty')}), "
                f"       SUM({department_usage.net('value')}), COUNT(DISTINCT irno) "
                "FROM issues WHERE stock_code = ? AND document_type = ? "
                "  AND period >= ? AND period < ? AND division <> '' "
                "GROUP BY division, dept ORDER BY 4 DESC LIMIT 25",
                [code, department_usage.DISPENSE_TYPE, first, until]):
            taken_by.append({"division": division, "dept": dept,
                             "name": departments.full_name(division, dept),
                             "qty": qty, "value": value, "slips": slips})

    consumable = group_key in categories.CONSUMABLE_GROUPS
    return {
        "stock_code": code, "name": name, "trade_name": trade, "main_category": category,
        "group_key": group_key, "group": categories.group_name(group_key),
        "base_unit": base_unit, "retired": bool(retired), "consumable": consumable,
        "stock_value": stock_value, "stock_by_store": stock_by_store, "lots": lots,
        "months": months_rows, "average_use": average, "covered_months": divisor,
        "months_of_stock": stock_value / average if consumable and average > 0 else None,
        "received_total": sum(row["received"] for row in closed), "used_total": used_total,
        "vendors": vendors, "receipts": receipts, "taken_by": taken_by,
        "latest": latest, "first": first, "until": until, "window_months": months,
        "snapshot": snapshot_day(conn),
    }


def monthly_frame(first: str, latest: str) -> list[dict[str, Any]]:
    """ทุกเดือนตั้งแต่ first ถึง latest แม้เดือนนั้นไม่มีการเคลื่อนไหว — กราฟต้องไม่ข้ามเดือน"""
    rows = []
    year, month = int(first[:4]), int(first[4:6])
    while f"{year:04d}{month:02d}" <= latest:
        key = f"{year:04d}{month:02d}"
        rows.append({"period": key, "partial": key == latest})
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return rows


def receipt_coverage(conn, months: int = DEFAULT_MONTHS) -> dict[str, Any]:
    """ใบรับครบทุกคลังแล้วหรือยัง — ต้องบอกผู้ใช้ถ้ายังไม่ครบ ไม่งั้นยอดซื้อจะดูต่ำกว่าจริงมาก

    ก่อน 17 ก.ย. 2569 ตัวดึงเก็บใบรับได้แค่คลัง 2 (ดู queries._MAIN_STORE_RECEIPT_NUMBERING)
    คลังข้อมูลที่ยังไม่ได้ดึงใหม่จึงมียอดซื้อแค่ยาและเวชภัณฑ์ของคลังยา
    """
    latest = latest_period(conn)
    if not latest:
        return {"stores": [], "complete": False}
    first, until = window(months, latest)
    found = [store_code for (store_code,) in conn.execute(
        "SELECT DISTINCT store FROM receipts WHERE period >= ? AND period < ? ORDER BY store",
        [first, until])]
    return {"stores": found, "complete": len(found) > 1}
