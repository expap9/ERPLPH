"""API รูปแบบเดียวกับ Stock5 — ให้หน้าจอ Angular ของ Stock5 แสดงข้อมูลทุกคลังทุกประเภทของ

ผู้ใช้สั่ง 17 ก.ย. 2569 ว่าอยากได้ "การดูข้อมูลเหมือน stock5 ครับ แต่ว่าตัดส่วนของข้อมูลส่งกระทรวงออก
และดูได้ทุกอย่าง ทุกประเภทของ ยา อาหาร พัสดุ งานจ้าง งานก่อสร้าง วัสดุคอมพิวเตอร์" และส่งภาพ
หน้าจอ Stock5 มาให้ดู ตรงกับแบบระบบที่ตกลงไว้ (docs/system_design.md ข้อ 3 "หน้าเว็บ Angular
เหมือน Stock5")

หน้าจอยกมาจาก Stock5 ทั้งชุด (frontend/) ที่นี่จึงตอบข้อมูลรูปเดียวกับที่หน้าจอนั้นอ่าน
โดยคำนวณจากคลังข้อมูลของ ERPLPH ไม่ได้เรียกโค้ดคำนวณของ Stock5 เพราะของ Stock5 อ่านแฟ้ม
ของคลัง 2 คลังเดียว และบางส่วนเขียนฐานข้อมูลของ Stock5 เอง (บันทึกใบ PO บริษัทหลัก)

ต่างจาก Stock5 โดยตั้งใจ
    - ขอบเขต: ทุกคลัง ทุกกลุ่มของ กรองด้วย ?group= และ ?store= ได้
    - ยอดจ่าย: ทั้งโรงพยาบาลนับใบจ่ายให้หน่วยเบิกที่สอบทานผ่าน หักใบคืน ไม่นับการโอน
      (กติกาเดียวกับ overview.py และหน้ารายแผนก — ตัวเลขข้ามหน้าต้องตรงกัน)
    - เดือนคงคลัง: คิดจากมูลค่า ไม่ใช่จำนวน เพราะหน่วยนับของคงคลังกับใบจ่ายเป็นคนละหน่วยได้
      และ ERPLPH ยังไม่มีตารางยืนยันหน่วยของทุกประเภทของแบบที่ Stock5 มีสำหรับยา
    - อ่านอย่างเดียว: ไม่มีการบันทึกใบ PO บริษัทหลัก หรือคำชี้แจง (ยังไม่มีระบบล็อกอิน)
    - ใบ PO ที่ยังไม่ได้ของ: ยังไม่ได้ดึงตาราง SKPO จึงยังไม่มี แสดงได้เฉพาะ PO ที่รับของแล้ว
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable

from flask import Blueprint, jsonify, request

import categories
import department_usage
import departments
import metrics
import overview
import stores
import warehouse_db

bp = Blueprint("stock5_api", __name__)

TARGET_DAYS = 30
USAGE_MONTHS = 6
EXPIRY_DAYS = 240
#: ตารางละไม่เกินนี้ — ทุกประเภทของรวมกันหลายพันรายการ ถ้าส่งครบหน้าแดชบอร์ดหนักหลาย MB
TABLE_LIMIT = 1000
TIMELINE_LIMIT = 160
TREND_MONTHS = 18

READ_ONLY_USER = {
    "username": "erplph",
    "name": "ผู้ใช้ภายใน (อ่านอย่างเดียว)",
    "access_group": "read_only",
    "facility": "",
}


# --------------------------------------------------------------------------- ตัวช่วยทั่วไป

def _open():
    path = Path(warehouse_db.DB_PATH)
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _round(value: Any, digits: int = 2) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(number, digits) if math.isfinite(number) else 0.0


def _scope_args() -> tuple[str | None, str | None]:
    group = request.args.get("group") or None
    if group not in categories.GROUP_BY_KEY:
        group = None
    store = (request.args.get("store") or "").strip() or None
    if store not in stores.STORES:
        store = None
    return group, store


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _item_where(group: str | None, column: str) -> str:
    if not group:
        return ""
    clause = categories.sql_category_filter(group, "main_category")
    return f" AND {column} IN (SELECT stock_code FROM items WHERE {clause})"


def _store_where(store: str | None, column: str) -> tuple[str, list[Any]]:
    return (f" AND {column} = ?", [store]) if store else ("", [])


def _use_kinds(store: str | None) -> str:
    return "('dispense')" if store is None else "('dispense', 'transfer')"


def _add_months(period: str, count: int) -> str:
    year, month = int(period[:4]), int(period[4:6])
    total = year * 12 + (month - 1) + count
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _periods_between(first: str, last_exclusive: str) -> list[str]:
    periods, current = [], first
    while current < last_exclusive:
        periods.append(current)
        current = _add_months(current, 1)
    return periods


def _quantities(pairs: Iterable[tuple[str, float]], divisor: float = 1.0) -> list[dict[str, Any]]:
    """จำนวนแยกตามหน่วยนับ แบบเดียวกับ stock_quantities ของ Stock5 — ไม่แปลงหน่วยเอง"""
    grouped: dict[str, float] = defaultdict(float)
    for unit, qty in pairs:
        grouped[str(unit or "").strip() or "ไม่ระบุหน่วย"] += float(qty or 0)
    div = divisor if divisor and divisor > 0 else 1.0
    return [{"unit": unit, "quantity": _round(qty / div, 4)} for unit, qty in grouped.items()]


def _iso_day(text: Any) -> str | None:
    value = str(text or "").replace("-", "")[:8]
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return None


def _days_between(day: str | None, today: date) -> int | None:
    if not day:
        return None
    try:
        return (datetime.strptime(day, "%Y-%m-%d").date() - today).days
    except ValueError:
        return None


# แคชตามเวลาแก้ไขของคลังข้อมูล — ข้อมูลเปลี่ยนเฉพาะตอนดึงข้อมูลรอบใหม่ หน้าแดชบอร์ดคำนวณหนักหลายวินาที
_CACHE: dict[tuple, Any] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 32


def _cached(key: tuple, build):
    path = Path(warehouse_db.DB_PATH)
    stamp = path.stat().st_mtime_ns if path.is_file() else 0
    full_key = (stamp, str(path)) + key
    with _CACHE_LOCK:
        if full_key in _CACHE:
            return _CACHE[full_key]
    value = build()
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[full_key] = value
    return value


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# --------------------------------------------------------------------------- ข้อมูลรายรายการ

def _usage_window(conn, months: int) -> tuple[str, list[str], str]:
    latest = overview.latest_period(conn)
    if not latest:
        return "", [], ""
    first = _add_months(latest, -months)
    return latest, _periods_between(first, latest), first


def _stock_by_item(conn, store: str | None, group: str | None) -> dict[str, dict[str, Any]]:
    table, values = overview._balances(conn, store)
    found: dict[str, dict[str, Any]] = {}
    for code, qty, unit, value, lots in conn.execute(
            f"SELECT b.stock_code, SUM(b.qty), b.unit, SUM(b.value), COUNT(*) FROM {table} b "
            f"WHERE 1 = 1{_item_where(group, 'b.stock_code')} GROUP BY b.stock_code, b.unit", values):
        entry = found.setdefault(code, {"stock_qty": 0.0, "stock_value": 0.0, "lots": 0, "_units": []})
        entry["stock_qty"] += float(qty or 0)
        entry["stock_value"] += float(value or 0)
        entry["lots"] += int(lots or 0)
        entry["_units"].append((unit, qty))
    for entry in found.values():
        entry["stock_quantities"] = _quantities(entry.pop("_units"))
    return found


def _usage_by_item(conn, first: str, latest: str, store: str | None,
                   group: str | None) -> dict[str, dict[str, Any]]:
    where, extra = _store_where(store, "store")
    found: dict[str, dict[str, Any]] = {}
    for code, unit, qty, value, rows, pending in conn.execute(
            f"SELECT stock_code, unit, SUM({overview._use_value('', 'qty')}), "
            f"       SUM({overview._use_value('')}), "
            "       SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END), "
            f"      SUM(CASE WHEN direction = 'out' AND check_status <> '{metrics.VERIFIED}' THEN 1 ELSE 0 END) "
            "FROM issues WHERE period >= ? AND period < ? "
            f"AND movement_kind IN {_use_kinds(store)}{where}{_item_where(group, 'stock_code')} "
            "GROUP BY stock_code, unit", [first, latest, *extra]):
        entry = found.setdefault(code, {"use_qty": 0.0, "issue_value": 0.0, "issue_rows": 0,
                                        "pending_rows": 0, "_units": []})
        entry["use_qty"] += float(qty or 0)
        entry["issue_value"] += float(value or 0)
        entry["issue_rows"] += int(rows or 0)
        entry["pending_rows"] += int(pending or 0)
        entry["_units"].append((unit, qty))
    return found


def _names(conn, codes: Iterable[str]) -> dict[str, tuple[str, str, str]]:
    wanted = list(set(codes))
    found: dict[str, tuple[str, str, str]] = {}
    for start in range(0, len(wanted), 800):
        chunk = wanted[start:start + 800]
        marks = ", ".join("?" for _ in chunk)
        for code, name, trade, category in conn.execute(
                f"SELECT stock_code, name, trade_name, main_category FROM items WHERE stock_code IN ({marks})",
                chunk):
            found[code] = (name or "", trade or "", category or "")
    return found


# --------------------------------------------------------------------------- /api/monitor/summary

def build_summary(conn, target_days: int = TARGET_DAYS, usage_months: int = USAGE_MONTHS,
                  expiry_days: int = EXPIRY_DAYS, group: str | None = None,
                  store: str | None = None, today: date | None = None) -> dict[str, Any] | None:
    """ข้อมูลแดชบอร์ด รูปเดียวกับ stock_monitor.build_monitor_dashboard ของ Stock5"""
    today = today or date.today()
    latest, periods, first = _usage_window(conn, usage_months)
    if not latest:
        return None
    months = len(periods) or 1
    covered = metrics.covered_months(conn, store, first, latest) or months

    stock = _stock_by_item(conn, store, group)
    usage = _usage_by_item(conn, first, latest, store, group)
    names = _names(conn, set(stock) | set(usage))
    retired = {code for (code,) in conn.execute("SELECT stock_code FROM items WHERE retired = 1")}

    products = []
    for code in set(stock) | set(usage):
        held = stock.get(code, {})
        used = usage.get(code, {})
        name, trade, category = names.get(code, ("", "", ""))
        stock_value = float(held.get("stock_value", 0.0))
        issue_value = float(used.get("issue_value", 0.0))
        avg_value = issue_value / covered if covered else 0.0
        consumable = categories.group_of(category) in categories.CONSUMABLE_GROUPS
        months_on_hand = stock_value / avg_value if consumable and avg_value > 0 and stock_value > 0 else None
        days_on_hand = months_on_hand * 30 if months_on_hand is not None else None
        if days_on_hand is not None and days_on_hand > 365 * 5:
            days_on_hand = months_on_hand = None
        excess = 0.0
        if days_on_hand is not None and days_on_hand > target_days:
            excess = stock_value * (days_on_hand - target_days) / days_on_hand
        products.append({
            "WORKING_CODE": code, "name": name or trade or code, "group_key": categories.group_of(category),
            "stock_value": stock_value, "stock_qty": float(held.get("stock_qty", 0.0)),
            "stock_quantities": held.get("stock_quantities", []), "lots": int(held.get("lots", 0)),
            "issue_value": issue_value, "issue_rows": int(used.get("issue_rows", 0)),
            "use_value": issue_value, "consumable": consumable,
            "avg_monthly_issue_quantities": _quantities(used.get("_units", []), covered),
            "avg_monthly_use": avg_value, "days_on_hand": days_on_hand,
            "months_on_hand": months_on_hand, "excess_value": excess,
        })

    has_stock = lambda p: p["stock_value"] > 0 or p["stock_qty"] > 0  # noqa: E731
    active = lambda p: p["issue_value"] > 0 or p["issue_rows"] > 0    # noqa: E731
    non_moving = [p for p in products if has_stock(p) and not active(p)]
    over_target = [p for p in products if has_stock(p) and active(p) and p["days_on_hand"] is not None
                   and p["days_on_hand"] > target_days]
    over_90 = [p for p in over_target if p["days_on_hand"] > 90]
    low_stock = [p for p in products if has_stock(p) and active(p) and p["days_on_hand"] is not None
                 and 0 <= p["days_on_hand"] <= 7]
    # รหัสที่เลิกใช้แล้วหมดคลังเป็นเรื่องปกติ ไม่ใช่ของขาด (แบบเดียวกับ metrics.low_stock)
    stockout = [p for p in products if not has_stock(p) and active(p) and p["consumable"]
                and p["WORKING_CODE"] not in retired]

    total_stock_value = sum(p["stock_value"] for p in products)
    consumable_stock = sum(p["stock_value"] for p in products if p["consumable"])
    consumable_use = sum(p["issue_value"] for p in products if p["consumable"])
    total_use = sum(p["issue_value"] for p in products)
    system_mos = consumable_stock / (consumable_use / covered) if consumable_use > 0 else None
    non_moving_value = sum(p["stock_value"] for p in non_moving)
    active_excess_value = sum(p["excess_value"] for p in over_target)
    protected_value = max(total_stock_value - non_moving_value - active_excess_value, 0.0)
    pending_rows = sum(int(usage.get(p["WORKING_CODE"], {}).get("pending_rows", 0)) for p in products)

    lots = _lots(conn, store, group, today)
    expired = [lot for lot in lots if lot["days_to_expire"] is not None and -365 <= lot["days_to_expire"] <= -1]
    expiry_90 = [lot for lot in lots if lot["days_to_expire"] is not None and 0 <= lot["days_to_expire"] <= 90]
    expiry_selected = [lot for lot in lots if lot["days_to_expire"] is not None
                       and 0 <= lot["days_to_expire"] <= expiry_days]
    old_lots = [lot for lot in lots if lot["lot_age"] is not None and lot["lot_age"] > 365]

    receipts_value = _receipt_value(conn, first, latest, store, group)
    complete = receipts_complete(conn, first, latest)
    trend = _monthly_trend(conn, latest, store, group, total_stock_value, mos_available=complete)
    department_list = _department_usage(conn, first, latest, store, group)
    table_rows = _tables(conn, products, over_target, non_moving, low_stock, stockout, expiry_selected,
                         expired, first, latest, store, group)
    recommendations = _recommendations(target_days, system_mos, non_moving, non_moving_value, expiry_90,
                                       low_stock, stockout, periods)
    snapshot = overview.snapshot_day(conn, store)
    as_of = datetime.strptime(snapshot, "%Y%m%d").date() if snapshot else today
    last_issue = conn.execute("SELECT MAX(issued_at) FROM issues WHERE period = ?", [latest]).fetchone()[0]

    scope_label = (stores.store_name(store) if store else "ทุกคลัง") + " · " + (
        categories.group_name(group) if group else "ทุกประเภทของ")
    result = {
        "meta": {
            "analytics_bundle": None, "analytics_only": True, "read_only": True,
            "as_of": as_of.isoformat(), "as_of_thai": as_of.strftime("%d/%m/%Y"),
            "distribution_as_of": _thai_datetime(last_issue) or as_of.strftime("%d/%m/%Y"),
            "target_days": target_days, "usage_months_requested": usage_months,
            "usage_months_used": len(periods), "usage_periods": periods,
            "usage_days": len(periods) * 30, "calculation_days": len(periods) * 30,
            "historical_inventory_available": False,
            "scope": {"group": group, "store": store, "label": scope_label},
            "receipts_complete": complete,
            "trend_mos_available": complete,
            "trend_mos_note": "" if complete else (
                "ยังไม่คำนวณ MOS รายเดือน เพราะใบรับในคลังข้อมูลยังมีแค่คลังยา ยอดคงคลังย้อนหลังจะสูงเกินจริง "
                "รัน pull_warehouse_data.bat แล้วกราฟนี้จะคำนวณได้"),
            "movement_source": "คลังข้อมูล ERPLPH: ใบจ่าย (SKMOVE สอบทานรายล็อตด้วยเครื่องเดียวกับ Stock5) · "
                               "ใบรับ (SKRECVDTL) · คงคลัง (STOCK_LOT ภาพล่าสุดของแต่ละคลัง)",
            "expiry_days": expiry_days, "files": {},
            "method_note": ("ยอดจ่ายทั้งโรงพยาบาล = ใบจ่ายให้หน่วยเบิกที่สอบทานผ่าน หักใบคืน ไม่นับการโอนระหว่างคลัง "
                            "(เลือกรายคลังจะนับการโอนออกด้วย) · เฉลี่ยต่อเดือน = ยอดจ่ายในเดือนที่จบแล้ว ÷ จำนวนเดือนที่ดึงครบ "
                            "· เดือนคงคลังคิดจากมูลค่า เพราะหน่วยนับของคงคลังกับใบจ่ายอาจต่างกัน"),
            "mos_item_formula": "MOS รายการ = มูลค่าคงคลัง ÷ (มูลค่าจ่ายสุทธิในช่วง ÷ จำนวนเดือน)",
            "mos_system_formula": "MOS ภาพรวม = มูลค่าคงคลังของของใช้สิ้นเปลือง ÷ มูลค่าจ่ายเฉลี่ยต่อเดือน (ไม่รวมครุภัณฑ์และงานจ้าง)",
            "stock_days_formula": "จำนวนวันคงคลัง = จำนวนเดือนสำรอง × 30 วัน",
            "usage_period_note": "ใช้เดือนที่สิ้นสุดแล้วเท่านั้น เดือนปัจจุบันที่ยังไม่จบไม่นำมาคำนวณ",
            "calculation_limit_note": ("ครุภัณฑ์และงานจ้างไม่คำนวณเดือนคงคลัง รายการที่ไม่มีการจ่ายไม่คำนวณ "
                                       "มูลค่าเป็นต้นทุนที่ระบบบันทึก ไม่ใช่ราคาที่เรียกเก็บผู้ป่วย"),
            "movement_note": "ไม่พบการเคลื่อนไหว = มีคงคลังแต่ไม่มีการจ่ายหรือโอนออกในช่วงที่วิเคราะห์",
            "dedupe_note": "ใบจ่ายตัดสำเนาและสอบทานรายล็อตตอนดึงข้อมูลแล้ว",
        },
        "kpis": {
            "unit_pending_count": 0, "unit_pending_value": 0.0,
            "cost_reference_rows": 0, "cost_pending_rows": pending_rows, "movement_pending_rows": pending_rows,
            "verified_scope_stock_days": _round(system_mos * 30, 1) if system_mos is not None else None,
            "verified_scope_mos": _round(system_mos, 1) if system_mos is not None else None,
            "verified_scope_stock_value": _round(consumable_stock),
            "verified_scope_stock_percent": _round(consumable_stock / total_stock_value * 100, 1)
            if total_stock_value > 0 else None,
            "verified_scope_excluded_drugs": len([p for p in products if not p["consumable"]]),
            "stock_value": _round(total_stock_value),
            "system_stock_days": _round(system_mos * 30, 1) if system_mos is not None else None,
            "system_mos": _round(system_mos, 1) if system_mos is not None else None,
            "target_days": target_days, "target_mos": _round(target_days / 30.0, 1),
            "target_multiple": _round(system_mos * 30 / target_days, 1) if system_mos is not None else None,
            "inventory_drugs": len([p for p in products if has_stock(p)]),
            "inventory_lots": len(lots), "active_drugs": len([p for p in products if active(p)]),
            "non_moving_drugs": len(non_moving), "non_moving_value": _round(non_moving_value),
            "over_target_drugs": len(over_target),
            "over_target_value": _round(sum(p["stock_value"] for p in over_target)),
            "active_excess_value": _round(active_excess_value), "excess_value": _round(active_excess_value),
            "over_90_drugs": len(over_90), "stockout_drugs": len(stockout), "low_stock_drugs": len(low_stock),
            "expired_lots": len(expired), "expired_value": _round(sum(l["stock_value"] for l in expired)),
            "expiry_90_lots": len(expiry_90), "expiry_90_value": _round(sum(l["stock_value"] for l in expiry_90)),
            "expiry_selected_lots": len(expiry_selected),
            "expiry_selected_value": _round(sum(l["stock_value"] for l in expiry_selected)),
            "old_lot_count": len(old_lots), "old_lot_value": _round(sum(l["stock_value"] for l in old_lots)),
            "avg_monthly_issue_value": _round(total_use / covered) if covered else 0.0,
            "receipt_issue_ratio": _round(receipts_value / total_use, 1) if total_use > 0 else 0.0,
        },
        "stock_buckets": [
            {"key": "unit_pending", "label": "รอยืนยันหน่วย ยังไม่ประเมินส่วนเกิน", "value": 0.0, "color": "#64748b"},
            {"key": "non_moving", "label": "รายการที่ไม่พบประวัติการเบิกจ่าย", "value": _round(non_moving_value), "color": "#f43f5e"},
            {"key": "active_excess", "label": f"มูลค่าส่วนที่เกินเกณฑ์ {target_days} วัน", "value": _round(active_excess_value), "color": "#f59e0b"},
            {"key": "protected", "label": "มูลค่าที่อยู่ภายในเกณฑ์ (รวมครุภัณฑ์และงานจ้าง)", "value": _round(protected_value), "color": "#10b981"},
        ],
        "monthly_trend": trend,
        "department_usage": department_list,
        "distribution_as_of": _thai_datetime(last_issue) or as_of.strftime("%d/%m/%Y"),
        "analysis_as_of": as_of.strftime("%d/%m/%Y"),
        "recommendations": recommendations,
        "tables": table_rows,
        "data_quality": _data_quality(conn, first, latest, pending_rows),
    }
    return result


def _thai_datetime(text: Any) -> str:
    value = str(text or "")
    if len(value) < 16:
        return ""
    return f"{value[8:10]}/{value[5:7]}/{value[:4]} {value[11:16]}"


def _lots(conn, store: str | None, group: str | None, today: date) -> list[dict[str, Any]]:
    table, values = overview._balances(conn, store)
    rows = []
    for code, name, store_code, lot, qty, unit, value, expire, last_in in conn.execute(
            "SELECT b.stock_code, COALESCE(m.name, ''), b.store, b.lot_no, b.qty, b.unit, b.value, "
            "       b.expire_date, b.last_in_date "
            f"FROM {table} b LEFT JOIN items m ON m.stock_code = b.stock_code "
            f"WHERE (b.qty > 0 OR b.value > 0){_item_where(group, 'b.stock_code')}", values):
        expiry = _iso_day(expire)
        last_in_day = _iso_day(last_in)
        age = _days_between(last_in_day, today)
        rows.append({
            "WORKING_CODE": code, "name": name or code, "store": store_code,
            "lot_no": lot or "", "expiry_date": expiry, "days_to_expire": _days_between(expiry, today),
            "stock_qty_actual": _round(qty, 4), "stock_unit": unit or "", "stock_value": _round(value),
            "lot_age": -age if age is not None else None,
        })
    return rows


def _receipt_value(conn, first: str, latest: str, store: str | None, group: str | None) -> float:
    where, extra = _store_where(store, "store")
    return float(conn.execute(
        f"SELECT COALESCE(SUM(value), 0) FROM receipts WHERE period >= ? AND period < ?{where}"
        f"{_item_where(group, 'stock_code')}", [first, latest, *extra]).fetchone()[0] or 0)


def receipts_complete(conn, first: str, latest: str) -> bool:
    """ใบรับครบทุกคลังหรือยัง — ก่อนแก้ตัวดึง 17 ก.ย. 2569 มีใบรับแค่คลังยาคลังเดียว"""
    stores_with_receipts = conn.execute(
        "SELECT COUNT(DISTINCT store) FROM receipts WHERE period >= ? AND period < ?",
        [first, latest]).fetchone()[0] or 0
    return stores_with_receipts > 1


def _monthly_trend(conn, latest: str, store: str | None, group: str | None,
                   total_stock_value: float, mos_available: bool = True) -> list[dict[str, Any]]:
    """ย้อนยอดคงคลังจากปัจจุบันถอยหลังทีละเดือน แบบเดียวกับ Stock5

    การย้อนยอด (คงคลังเดือนก่อน = คงคลัง − รับ + จ่าย) ถูกต้องก็ต่อเมื่อใบรับครบ ถ้าขาดใบรับของ
    คลังพัสดุ ยอดย้อนหลังจะบวกยอดจ่ายเข้าไปโดยไม่หักยอดรับ คงคลังในอดีตจะสูงเกินจริงสะสมทุกเดือน
    เห็นจริงบนหน้าจอ 17 ก.ย. 2569: MOS ต.ค. 68 ขึ้นเป็น 5.41 ทั้งที่ภาพปัจจุบันคือ 2.3
    ถ้ายังไม่ครบจึงไม่คำนวณ MOS รายเดือนเลย และเดือนที่ยังไม่จบก็ไม่คำนวณ เพราะยอดจ่ายยังไม่ครบเดือน
    """
    first = _add_months(latest, -(TREND_MONTHS - 1))
    where, extra = _store_where(store, "store")
    received = dict(conn.execute(
        f"SELECT period, SUM(value) FROM receipts WHERE period >= ? AND period <= ?{where}"
        f"{_item_where(group, 'stock_code')} GROUP BY period", [first, latest, *extra]).fetchall())
    issued = dict(conn.execute(
        f"SELECT period, SUM({overview._use_value('')}) FROM issues WHERE period >= ? AND period <= ? "
        f"AND movement_kind IN {_use_kinds(store)}{where}{_item_where(group, 'stock_code')} GROUP BY period",
        [first, latest, *extra]).fetchall())
    months = _periods_between(first, _add_months(latest, 1))
    running = float(total_stock_value)
    reverse = []
    for month in reversed(months):
        r_val = float(received.get(month) or 0)
        i_val = float(issued.get(month) or 0)
        partial = month == latest
        usable = mos_available and not partial and i_val > 0
        reverse.append({"period": month, "receipt_value": _round(r_val), "issue_value": _round(i_val),
                        "end_stock": _round(running) if mos_available else None,
                        "mos": round(running / i_val, 2) if usable else None,
                        "partial": partial})
        running = max(0.0, running - r_val + i_val)
    return list(reversed(reverse))


def _department_usage(conn, first: str, latest: str, store: str | None,
                      group: str | None) -> list[dict[str, Any]]:
    """มูลค่าการเบิกจ่ายแยกตามหน่วยเบิก — ชื่อหน่วยงานจากตารางรหัสของ SSB (config/departments.json)"""
    where, extra = _store_where(store, "i.store")
    item_where = _item_where(group, "i.stock_code")
    base = (f"FROM issues i LEFT JOIN items m ON m.stock_code = i.stock_code "
            f"WHERE i.period >= ? AND i.period < ? AND i.document_type = '{department_usage.DISPENSE_TYPE}'"
            f"{where}{item_where}")
    values = [first, latest, *extra]
    net_value = department_usage.net("value", "i")
    net_qty = department_usage.net("qty", "i")

    totals = conn.execute(
        f"SELECT i.division, i.dept, SUM({net_value}), COUNT(DISTINCT i.stock_code), "
        f"       SUM(CASE WHEN i.direction = 'out' THEN 1 ELSE 0 END), MAX(i.issued_at) {base} "
        "GROUP BY i.division, i.dept HAVING SUM(" + net_value + ") <> 0 ORDER BY 3 DESC",
        values).fetchall()
    grand = sum(float(row[2] or 0) for row in totals)

    top: dict[tuple, list] = defaultdict(list)
    for division, dept, code, name, unit, qty, value, last in conn.execute(
            f"SELECT i.division, i.dept, i.stock_code, COALESCE(m.name, ''), MAX(i.unit), "
            f"       SUM({net_qty}), SUM({net_value}), MAX(i.issued_at) {base} "
            "GROUP BY i.division, i.dept, i.stock_code", values):
        top[(division or "", dept or "")].append((code, name, unit, qty, value, last))

    monthly: dict[tuple, list] = defaultdict(list)
    for division, dept, period, value in conn.execute(
            f"SELECT i.division, i.dept, i.period, SUM({net_value}) {base} "
            "GROUP BY i.division, i.dept, i.period ORDER BY i.period", values):
        monthly[(division or "", dept or "")].append({"period": period, "value": _round(value)})

    result = []
    for rank, (division, dept, value, item_count, issue_count, last) in enumerate(totals, start=1):
        key = (division or "", dept or "")
        department_value = float(value or 0)
        drugs = sorted(top.get(key, []), key=lambda row: -(row[4] or 0))[:20]
        result.append({
            "rank": rank,
            "department": departments.full_name(division, dept) if division
            else "ไม่ระบุหน่วยเบิก (ใบเบิกไม่มีรหัสหน่วยงาน)",
            "department_code": "-".join(part for part in key if part),
            "value": _round(department_value),
            "percent_of_total": _round(department_value / grand * 100, 1) if grand > 0 else 0.0,
            "drug_count": int(item_count or 0), "issue_count": int(issue_count or 0),
            "last_movement": str(last or "-")[:16],
            "top_drugs": [{
                "code": code, "name": name or code, "qty": _round(qty, 1), "unit": unit or "",
                "value": _round(item_value, 2),
                "percent": _round(float(item_value or 0) / department_value * 100, 1) if department_value > 0 else 0.0,
                "last_date": str(item_last or "-")[:16],
            } for code, name, unit, qty, item_value, item_last in drugs],
            "monthly_trend": monthly.get(key, []),
        })
    return result


def _tables(conn, products, over_target, non_moving, low_stock, stockout, expiry_selected, expired,
            first, latest, store, group) -> dict[str, list[dict[str, Any]]]:
    def rows(items, columns, key, limit=TABLE_LIMIT):
        ordered = sorted(items, key=key)[:limit]
        return [{column: item.get(column) for column in columns} for item in ordered]

    excess = rows(over_target, ["WORKING_CODE", "name", "stock_value", "stock_quantities", "days_on_hand",
                                "months_on_hand", "excess_value", "avg_monthly_issue_quantities"],
                  lambda p: -p["excess_value"])
    for row in excess:
        for column in ("days_on_hand", "months_on_hand"):
            row[column] = _round(row[column], 1) if row[column] is not None else None
        row["stock_value"] = _round(row["stock_value"])
        row["excess_value"] = _round(row["excess_value"])

    shortage_source = [{**p, "status": "ไม่มีคงคลัง"} for p in stockout] + \
                      [{**p, "status": "เหลือไม่เกิน 7 วัน"} for p in low_stock]
    shortage = rows(shortage_source, ["WORKING_CODE", "name", "status", "days_on_hand",
                                      "avg_monthly_issue_quantities", "issue_value"],
                    lambda p: (p["status"], -p["issue_value"]))

    lot_columns = ["WORKING_CODE", "name", "lot_no", "expiry_date", "days_to_expire", "stock_qty_actual",
                   "stock_unit", "stock_value"]
    expiry = rows(expiry_selected, lot_columns, lambda l: (l["days_to_expire"], -l["stock_value"]))
    already = rows([{**l, "days_to_expire": abs(l["days_to_expire"])} for l in expired], lot_columns,
                   lambda l: (l["days_to_expire"], -l["stock_value"]))

    top_receipt = []
    where, extra = _store_where(store, "r.store")
    for code, name, value, qty, count in conn.execute(
            "SELECT r.stock_code, COALESCE(m.name, ''), SUM(r.value), SUM(r.qty), COUNT(*) "
            "FROM receipts r LEFT JOIN items m ON m.stock_code = r.stock_code "
            f"WHERE r.period >= ? AND r.period < ?{where}{_item_where(group, 'r.stock_code')} "
            "GROUP BY r.stock_code ORDER BY 3 DESC LIMIT ?", [first, latest, *extra, TABLE_LIMIT]):
        top_receipt.append({"WORKING_CODE": code, "name": name or code, "receipt_value": _round(value),
                            "receipt_qty": _round(qty, 4), "receipt_rows": int(count or 0)})

    def money_rows(items, columns, sort_key):
        result = rows(items, columns, sort_key)
        for row in result:
            for column in ("stock_value", "issue_value"):
                if column in row:
                    row[column] = _round(row[column])
        return result

    return {
        "backorder": [],
        "unit_pending": [],
        "excess": excess,
        "non_moving": money_rows(non_moving, ["WORKING_CODE", "name", "stock_quantities", "stock_value", "lots"],
                                 lambda p: -p["stock_value"]),
        "expiry": expiry,
        "already_expired": already,
        "shortage": shortage,
        "top_value": money_rows([p for p in products if p["stock_value"] > 0],
                                ["WORKING_CODE", "name", "stock_value", "stock_quantities", "lots"],
                                lambda p: -p["stock_value"]),
        "top_receipt_value": top_receipt,
        "top_distribution_value": money_rows([p for p in products if p["issue_value"] > 0],
                                             ["WORKING_CODE", "name", "issue_value",
                                              "avg_monthly_issue_quantities", "issue_rows"],
                                             lambda p: -p["issue_value"]),
    }


def _recommendations(target_days, system_mos, non_moving, non_moving_value, expiry_90, low_stock,
                     stockout, periods) -> list[dict[str, Any]]:
    found = []
    if system_mos is not None and system_mos * 30 > target_days:
        days = system_mos * 30
        found.append({
            "severity": "critical" if days > target_days * 2 else "warning",
            "title": "ทบทวนระดับคงคลังขั้นต่ำ–สูงสุดและแผนการจัดซื้อ",
            "finding": f"มูลค่าคงคลังของใช้สิ้นเปลืองรองรับการเบิกจ่ายได้ประมาณ {days:,.0f} วัน สูงกว่าเกณฑ์ {target_days} วัน",
            "action": "ตรวจสอบรายการที่มีการใช้แต่คงคลังเกินเกณฑ์ โดยเรียงตามมูลค่าส่วนเกิน และปรับปริมาณสั่งซื้อครั้งถัดไปเป็นรายรายการ",
        })
    if non_moving_value > 0:
        found.append({
            "severity": "critical",
            "title": f"ตรวจสอบรายการที่ไม่พบการเบิกจ่ายในช่วง {len(periods)} เดือน",
            "finding": f"พบ {len(non_moving):,} รายการ มูลค่า {non_moving_value:,.2f} บาท ที่มีคงคลังแต่ไม่มีการจ่ายหรือโอนออก",
            "action": "ครุภัณฑ์ที่รอส่งมอบอาจอยู่ในกลุ่มนี้ ให้ดูประกอบกับประเภทของ ก่อนพิจารณาโอนระหว่างคลัง คืนผู้ขาย หรือระงับการจัดซื้อ",
        })
    expiry_value = sum(lot["stock_value"] for lot in expiry_90)
    if expiry_value > 0:
        found.append({
            "severity": "critical",
            "title": "จัดทำแผนบริหารรายการที่จะหมดอายุภายใน 90 วัน",
            "finding": f"พบ {len(expiry_90):,} รุ่นการผลิต มูลค่า {expiry_value:,.2f} บาท",
            "action": "จ่ายของที่หมดอายุก่อนออกก่อน แจ้งหน่วยงานที่ใช้ พิจารณากระจายหรือคืนผู้ขาย และตรวจคำสั่งซื้อที่ยังค้าง",
        })
    if stockout or low_stock:
        found.append({
            "severity": "warning",
            "title": "ตรวจสอบรายการที่อาจไม่เพียงพอต่อการให้บริการ",
            "finding": f"มีประวัติการใช้แต่ไม่มีคงคลัง {len(stockout):,} รายการ และคาดว่าจะใช้ได้ไม่เกิน 7 วัน {len(low_stock):,} รายการ",
            "action": "ตรวจนับยอดคงเหลือจริงและรายการค้างรับก่อนจัดซื้อเร่งด่วน",
        })
    return found


def _data_quality(conn, first: str, latest: str, pending_rows: int) -> list[dict[str, Any]]:
    total_issue = conn.execute(
        "SELECT COUNT(*) FROM issues WHERE period >= ? AND period < ? AND direction = 'out'",
        [first, latest]).fetchone()[0] or 0
    receipt_stores = [code for (code,) in conn.execute(
        "SELECT DISTINCT store FROM receipts WHERE period >= ? AND period < ?", [first, latest])]
    no_department = conn.execute(
        "SELECT COUNT(*) FROM issues WHERE period >= ? AND period < ? AND document_type = '32' "
        "AND COALESCE(division, '') = ''", [first, latest]).fetchone()[0] or 0
    no_expiry = conn.execute(
        "SELECT COUNT(*), (SELECT COUNT(*) FROM balances b2 WHERE b2.period = b.period) FROM balances b "
        "WHERE b.period = (SELECT MAX(period) FROM balances) AND b.expire_date = ''").fetchone()

    def item(file, check, affected, total, severity, note):
        return {"file": file, "check": check, "affected": int(affected), "total": int(total),
                "percent": _round(affected / total * 100, 1) if total else 0.0,
                "severity": severity, "note": note, "sample": []}

    quality = [
        item("ข้อมูลจ่าย", "ล็อตหรือจำนวนจ่ายยังสอบทานกับรายการหลักไม่ผ่าน", pending_rows, total_issue, "critical",
             "บรรทัดเหล่านี้ไม่นับในยอดจ่าย ตามกติกาเดียวกับ Stock5"),
        item("ข้อมูลจ่าย", "ใบจ่ายให้หน่วยเบิกไม่ระบุรหัสหน่วยงาน", no_department, total_issue, "warning",
             "นับรวมในยอดจ่าย แต่แยกหน่วยเบิกไม่ได้"),
        item("ข้อมูลคงคลัง", "ไม่ระบุวันหมดอายุ", no_expiry[0] or 0, no_expiry[1] or 0, "warning",
             "พัสดุ ครุภัณฑ์ และงานจ้างส่วนใหญ่ไม่มีวันหมดอายุตามธรรมชาติ"),
    ]
    if len(receipt_stores) <= 1:
        quality.insert(0, item("ข้อมูลรับ", "ใบรับมีเพียงคลังเดียว ยอดรับของคลังอื่นยังไม่ถูกดึง",
                               1, 1, "critical",
                               "รัน pull_warehouse_data.bat หลังปรับตัวดึงใบรับ (17 ก.ย. 2569) ยอดซื้อและงานจ้างจึงจะครบ"))
    return quality


# --------------------------------------------------------------------------- /api/drugs/search

def build_catalog(conn, group: str | None = None, store: str | None = None,
                  today: date | None = None) -> list[dict[str, Any]]:
    """ทะเบียนรายการสำหรับหน้าค้นหา รูปเดียวกับ drug_explorer._build_catalog ของ Stock5"""
    today = today or date.today()
    stock = _stock_by_item(conn, store, group)
    table, values = overview._balances(conn, store)
    expiry: dict[str, tuple[int, int]] = {}
    horizon_180 = (today + timedelta(days=180)).strftime("%Y%m%d")
    horizon_240 = (today + timedelta(days=240)).strftime("%Y%m%d")
    now = today.strftime("%Y%m%d")
    for code, within_180, within_240 in conn.execute(
            "SELECT b.stock_code, "
            "  SUM(CASE WHEN b.expire_date >= ? AND b.expire_date <= ? THEN 1 ELSE 0 END), "
            "  SUM(CASE WHEN b.expire_date >= ? AND b.expire_date <= ? THEN 1 ELSE 0 END) "
            f"FROM {table} b WHERE b.expire_date <> '' GROUP BY b.stock_code",
            [now, horizon_180, now, horizon_240, *values]):
        expiry[code] = (int(within_180 or 0), int(within_240 or 0))

    where, extra = _store_where(store, "store")
    receipts = {code: (count, value, last, supplier) for code, count, value, last, supplier in conn.execute(
        "SELECT stock_code, COUNT(*), SUM(value), MAX(rcv_date), MAX(supplier) FROM receipts "
        f"WHERE 1 = 1{where}{_item_where(group, 'stock_code')} GROUP BY stock_code", extra)}
    issues = {code: (rows, value, last) for code, rows, value, last in conn.execute(
        f"SELECT stock_code, SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END), "
        f"       SUM({overview._use_value('')}), MAX(period) FROM issues "
        f"WHERE movement_kind IN {_use_kinds(store)}{where}{_item_where(group, 'stock_code')} "
        "GROUP BY stock_code", extra)}

    codes = set(stock) | set(receipts) | set(issues)
    names = {code: (name, trade, category) for code, name, trade, category in conn.execute(
        "SELECT stock_code, name, trade_name, main_category FROM items")}
    catalog = []
    for code in codes:
        name, trade, category = names.get(code, ("", "", ""))
        held = stock.get(code, {})
        receipt_count, receipt_value, last_receipt, supplier = receipts.get(code, (0, 0.0, None, ""))
        issue_rows, issue_value, last_issue = issues.get(code, (0, 0.0, None))
        within_180, within_240 = expiry.get(code, (0, 0))
        stock_qty = float(held.get("stock_qty", 0.0))
        group_key = categories.group_of(category)
        catalog.append({
            "WORKING_CODE": code, "name": name or trade or code, "trade_name": trade or "",
            "names": [name] if name else [], "trade_names": [trade] if trade else [],
            "vendors": [overview.clean_name(supplier)] if supplier else [], "tpuids": [],
            "group_key": group_key, "group": categories.group_name(group_key),
            "stock_qty": _round(stock_qty, 4), "stock_quantities": held.get("stock_quantities", []),
            "stock_value": _round(held.get("stock_value", 0.0)), "stock_lots": int(held.get("lots", 0)),
            "receipt_rows": int(receipt_count or 0), "receipt_value": _round(receipt_value),
            "last_receipt": _iso_day(last_receipt), "issue_rows": int(issue_rows or 0),
            "issue_value": _round(issue_value), "last_issue_period": last_issue,
            "expiry_180_lots": within_180, "expiry_240_lots": within_240, "duplicate_rows": 0,
            "raw_rows": int(held.get("lots", 0)) + int(receipt_count or 0) + int(issue_rows or 0),
            "has_stock_no_issue": bool(stock_qty > 0 and not issue_rows),
            "_search": " ".join(filter(None, [code, name, trade, overview.clean_name(supplier)])).casefold(),
        })
    return catalog


def search_items(conn, query: str = "", status: str = "all", page: int = 1, per_page: int = 24,
                 group: str | None = None, store: str | None = None) -> dict[str, Any]:
    catalog = _cached(("catalog", group, store), lambda: build_catalog(conn, group, store))
    query = (query or "").strip()[:80]
    terms = [term.casefold() for term in query.split() if term]
    items = [row for row in catalog if all(term in row["_search"] for term in terms)]
    if status == "no_issue":
        items = [row for row in items if row["has_stock_no_issue"]]
    elif status == "has_stock":
        items = [row for row in items if row["stock_qty"] > 0 or row["stock_value"] > 0]
    elif status == "expiry":
        items = [row for row in items if row["expiry_240_lots"] or row["expiry_180_lots"]]
    elif status == "duplicates":
        items = []
    if terms:
        needle = query.casefold()

        def rank(row):
            if row["WORKING_CODE"].casefold() == needle:
                return 0
            if row["name"].casefold().startswith(needle):
                return 1
            if row["trade_name"].casefold().startswith(needle):
                return 2
            return 3
        items.sort(key=lambda row: (rank(row), -row["stock_value"], row["name"]))
    else:
        items.sort(key=lambda row: (-row["stock_value"], -row["issue_value"], row["name"]))
    per_page = max(1, min(int(per_page), 100))
    total = len(items)
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(int(page), pages))
    start = (page - 1) * per_page
    snapshot = overview.snapshot_day(conn)
    return {
        "query": query, "status": status, "page": page, "per_page": per_page, "total": total,
        "pages": pages, "as_of": _iso_day(snapshot) or date.today().isoformat(),
        "items": [{key: value for key, value in row.items() if key != "_search"}
                  for row in items[start:start + per_page]],
    }


# --------------------------------------------------------------------------- /api/drugs/<code>

RECEIPT_LABELS = {
    "RCV_NO": "เลขที่ใบรับ", "suffix": "ลำดับ", "DATE_RCV": "วันที่รับ", "SOURCE_STORE": "คลังที่รับ",
    "PO_NO": "เลขที่ใบสั่งซื้อ (PO)", "VENDOR_NAME": "ผู้ขาย/ผู้รับจ้าง", "LOT_NO": "เลขรุ่นการผลิต",
    "QTY_RCV": "จำนวนรับ", "STDIRUNITCODE": "หน่วยนับ", "PACK_COST": "ราคาต่อหน่วย", "TOTAL_VALUE": "มูลค่ารับ",
    "RCV_DEPT": "หน่วยงานบนใบรับ (ยังไม่ยืนยันความหมาย)",
}
DISTRIBUTION_LABELS = {
    "IRNO": "เลขที่ใบเบิก/ใบจ่าย", "SUFFIX": "ลำดับ", "SOURCE_STORE": "คลังที่จ่าย", "PERIOD_RPT": "งวดเดือน",
    "SOURCE_MOVEMENT_DATETIME": "วันเวลาตัดสต๊อก", "DOCUMENT_KIND": "ชนิดเอกสาร", "DIRECTION": "ทิศทาง",
    "DIS": "รหัสหน่วยเบิก", "DEPT_NAME": "หน่วยเบิก", "SOURCE_LOTNO": "ล็อต", "QTY_DIS": "จำนวน",
    "ISSUEUNITCODE": "หน่วยนับ", "VALUE": "มูลค่า", "SOURCE_MOS_STATUS": "ผลสอบทานรายล็อต",
}
INVENTORY_LABELS = {
    "SOURCE_STORE": "คลัง", "LOTNO": "เลขรุ่นการผลิต", "QTY_ONHAND": "จำนวนคงคลัง", "BASE_UNIT": "หน่วยนับ",
    "VALUE_ONHAND": "มูลค่าคงคลัง", "EXPIRE_DATE": "วันหมดอายุ", "DATE_ONHAND": "รับเข้าล่าสุด",
    "SNAPSHOT_DAY": "วันที่ภาพคงคลัง",
}
SOURCE_LABELS = {"receipt": "ข้อมูลรับ", "distribution": "ข้อมูลจ่าย", "inventory": "ข้อมูลคงคลัง"}


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _item_rows(conn, code: str) -> dict[str, list[dict[str, Any]]]:
    # หน้าเว็บเปิดคลังข้อมูลแบบอ่านอย่างเดียว จึงอัปเกรดตารางเองไม่ได้ คลังข้อมูลที่ยังไม่ได้ดึงรอบใหม่
    # ยังไม่มีช่องหน่วยงานบนใบรับ (เพิ่ม 17 ก.ย. 2569) ต้องอ่านได้ทั้งสองรุ่น
    receipt_department = ("division, dept" if {"division", "dept"} <= _columns(conn, "receipts")
                          else "'' AS division, '' AS dept")
    receipt = [{
        "RCV_NO": rcv_no, "suffix": suffix, "DATE_RCV": rcv_date, "SOURCE_STORE": store_code,
        "PO_NO": po_no, "VENDOR_NAME": overview.clean_name(supplier), "LOT_NO": lot, "QTY_RCV": qty,
        "STDIRUNITCODE": unit, "PACK_COST": price, "TOTAL_VALUE": value,
        "RCV_DEPT": departments.full_name(division, dept) if division else "",
        "_period": period,
    } for period, store_code, rcv_no, suffix, lot, qty, value, unit, price, po_no, supplier, rcv_date,
        division, dept in conn.execute(
        "SELECT period, store, rcv_no, suffix, lot_no, qty, value, unit, unit_price, po_no, supplier, "
        f"       rcv_date, {receipt_department} FROM receipts WHERE stock_code = ? "
        "ORDER BY rcv_date DESC, rcv_no DESC, suffix DESC", [code])]

    distribution = [{
        "IRNO": irno, "SUFFIX": suffix, "SOURCE_STORE": store_code, "PERIOD_RPT": period,
        "SOURCE_MOVEMENT_DATETIME": issued_at, "DOCUMENT_KIND": {"dispense": "จ่ายให้หน่วยเบิก",
                                                                   "transfer": "โอนระหว่างคลัง"}.get(kind, kind),
        "DIRECTION": "ออก" if direction == "out" else "เข้า (คืน/รับโอน)",
        "DIS": "-".join(part for part in (division, dept, section) if part),
        "DEPT_NAME": departments.full_name(division, dept) if division else "",
        "SOURCE_LOTNO": lot, "QTY_DIS": qty, "ISSUEUNITCODE": unit, "VALUE": value,
        "SOURCE_MOS_STATUS": status,
        "_kind": kind, "_direction": direction, "_division": division or "", "_dept": dept or "",
    } for period, store_code, irno, suffix, lot, qty, value, unit, division, dept, section, issued_at, kind,
        direction, status in conn.execute(
        "SELECT period, store, irno, suffix, lot_no, qty, value, unit, division, dept, section, issued_at, "
        "       movement_kind, direction, check_status FROM issues WHERE stock_code = ? "
        "ORDER BY issued_at DESC, irno DESC", [code])]

    table, values = overview._balances(conn, None)
    inventory = [{
        "SOURCE_STORE": store_code, "LOTNO": lot, "QTY_ONHAND": qty, "BASE_UNIT": unit, "VALUE_ONHAND": value,
        "EXPIRE_DATE": expire, "DATE_ONHAND": last_in, "SNAPSHOT_DAY": period,
    } for period, store_code, lot, qty, unit, value, expire, last_in in conn.execute(
        f"SELECT period, store, lot_no, qty, unit, value, expire_date, last_in_date FROM {table} "
        "WHERE stock_code = ? ORDER BY CASE WHEN expire_date = '' THEN 1 ELSE 0 END, expire_date",
        [*values, code])]
    return {"receipt": receipt, "distribution": distribution, "inventory": inventory}


def _counts_as_use(row: dict[str, Any]) -> bool:
    """ทั้งโรงพยาบาล: ใบจ่ายให้หน่วยเบิกที่สอบทานผ่าน และใบคืนทุกสถานะ"""
    if row["_kind"] != "dispense":
        return False
    return row["_direction"] == "in" or row["SOURCE_MOS_STATUS"] == metrics.VERIFIED


def _signed(row: dict[str, Any], column: str) -> float:
    value = float(row.get(column) or 0)
    return -value if row["_direction"] == "in" else value


def build_item_detail(conn, code: str, today: date | None = None) -> dict[str, Any] | None:
    today = today or date.today()
    found = conn.execute(
        "SELECT name, trade_name, main_category, base_unit FROM items WHERE stock_code = ?", [code]).fetchone()
    rows = _item_rows(conn, code)
    if found is None and not any(rows.values()):
        return None
    name, trade, category, base_unit = found or ("", "", "", "")
    group_key = categories.group_of(category)
    consumable = group_key in categories.CONSUMABLE_GROUPS
    receipts, distributions, inventory = rows["receipt"], rows["distribution"], rows["inventory"]
    used = [row for row in distributions if _counts_as_use(row)]
    pending = [row for row in distributions if row["_kind"] == "dispense" and row["_direction"] == "out"
               and row["SOURCE_MOS_STATUS"] != metrics.VERIFIED]
    transfers = [row for row in distributions if row["_kind"] == "transfer" and row["_direction"] == "out"]

    latest = overview.latest_period(conn)
    periods_6 = _periods_between(_add_months(latest, -USAGE_MONTHS), latest) if latest else []
    periods_3 = periods_6[-3:]
    covered_6 = metrics.covered_months(conn, None, periods_6[0], latest) if periods_6 else 0
    use_6 = [row for row in used if row["PERIOD_RPT"] in periods_6]
    use_3 = [row for row in used if row["PERIOD_RPT"] in periods_3]

    stock_value = sum(float(row["VALUE_ONHAND"] or 0) for row in inventory)
    stock_qty = sum(float(row["QTY_ONHAND"] or 0) for row in inventory)
    use_value_6 = sum(_signed(row, "VALUE") for row in use_6)
    avg_value_6 = use_value_6 / covered_6 if covered_6 else 0.0
    months_on_hand = stock_value / avg_value_6 if consumable and avg_value_6 > 0 and stock_value > 0 else None
    if months_on_hand is None and consumable and stock_value <= 0 and stock_qty > 0:
        units = {row["BASE_UNIT"] for row in inventory} | {row["ISSUEUNITCODE"] for row in use_6}
        use_qty_6 = sum(_signed(row, "QTY_DIS") for row in use_6)
        if len(units) == 1 and use_qty_6 > 0 and covered_6:
            months_on_hand = stock_qty / (use_qty_6 / covered_6)
    days_on_hand = months_on_hand * 30 if months_on_hand is not None else None
    months_3 = len(periods_3) or 3

    expire_days = [_days_between(_iso_day(row["EXPIRE_DATE"]), today) for row in inventory]
    receipt_lots = {row["LOT_NO"] for row in receipts if row["LOT_NO"] not in ("", "*", ".")}
    stock_lots = {row["LOTNO"] for row in inventory if row["LOTNO"] not in ("", "*", ".")}
    unmatched = sorted(stock_lots - receipt_lots)

    vendors = _vendors(receipts)
    primary = vendors[0] if vendors else None
    display_name = name or trade or code

    facts = []
    if pending:
        facts.append({"severity": "critical", "title": "มีบรรทัดจ่ายที่ยังสอบทานรายล็อตไม่ผ่าน",
                      "evidence": f"{len(pending):,} บรรทัด ไม่นับในยอดจ่ายตามกติกาเดียวกับ Stock5",
                      "source": "สถานะสอบทานรายล็อตจากเครื่องคำนวณที่ยืมจาก Stock5 ตอนดึงข้อมูล"})
    if (stock_value > 0 or stock_qty > 0) and not used:
        facts.append({"severity": "critical", "title": "มียอดคงคลังแต่ไม่พบประวัติการเบิกจ่าย",
                      "evidence": f"คงคลัง {len(inventory):,} ล็อต มูลค่า {stock_value:,.2f} บาท แต่ไม่มีใบจ่ายให้หน่วยเบิกในช่วงข้อมูล",
                      "source": "เทียบคงคลังภาพล่าสุดกับใบจ่ายทุกคลังในคลังข้อมูล ERPLPH"})
    elif used:
        facts.append({"severity": "info", "title": "พบประวัติการเบิกจ่ายในช่วงข้อมูล",
                      "evidence": f"ใบจ่ายให้หน่วยเบิก {len(used):,} บรรทัด งวดล่าสุด {max(r['PERIOD_RPT'] for r in used)}",
                      "source": "ใบจ่ายทุกคลัง มีวันเวลาตัดสต๊อก เลขใบเบิก ลำดับ และล็อต"})
    if transfers:
        facts.append({"severity": "info", "title": "มีการโอนระหว่างคลัง (ไม่นับเป็นยอดใช้)",
                      "evidence": f"{len(transfers):,} บรรทัด มูลค่า {sum(float(r['VALUE'] or 0) for r in transfers):,.2f} บาท",
                      "source": "ของยังอยู่ในโรงพยาบาล จะถูกนับเมื่อคลังปลายทางจ่ายให้หน่วยเบิก"})
    zero_cost = [row for row in receipts if float(row["PACK_COST"] or 0) <= 0]
    if zero_cost:
        facts.append({"severity": "warning", "title": "ใบรับบางบรรทัดไม่มีราคาหรือราคาเป็นศูนย์",
                      "evidence": f"{len(zero_cost):,} บรรทัด",
                      "source": "อาจเป็นของรับบริจาคหรือได้รับจัดสรร ต้องยืนยันกับงานพัสดุ"})
    if unmatched:
        facts.append({"severity": "warning", "title": "พบรุ่นการผลิตในคงคลังที่ไม่มีประวัติรับในช่วงข้อมูล",
                      "evidence": ", ".join(unmatched[:12]) + (" …" if len(unmatched) > 12 else ""),
                      "source": "เทียบเลขรุ่นในคงคลังกับใบรับที่ดึงได้ (ใบรับคลังอื่นนอกจากคลังยาอาจยังไม่ถูกดึง)"})
    if days_on_hand is not None and days_on_hand > 30:
        facts.append({"severity": "warning", "title": "ระยะเวลาคงคลังสูงกว่าเกณฑ์ 30 วัน",
                      "evidence": f"ประมาณ {days_on_hand:,.1f} วัน หรือ {days_on_hand / 30:,.1f} เดือน จากยอดใช้ {covered_6} เดือนที่จบแล้ว",
                      "source": "มูลค่าคงคลัง ÷ มูลค่าจ่ายสุทธิเฉลี่ยต่อเดือน"})
    if not consumable:
        facts.append({"severity": "info", "title": f"{categories.group_name(group_key)}ไม่คำนวณเดือนคงคลัง",
                      "evidence": "เป็นของลงทุนหรืองานบริการ ไม่ได้ถูกใช้หมดแบบของสิ้นเปลือง",
                      "source": "categories.CONSUMABLE_GROUPS"})

    monthly = _item_monthly_movement(receipts, used)
    trend = _item_monthly_trend(monthly, stock_qty, stock_value, consumable)
    dept_list, signals = _item_departments(used)

    received_pos = _received_pos(code, receipts, display_name)
    return {
        "code": code, "hospital_codes": [], "name": display_name, "names": [name] if name else [],
        "trade_names": [trade] if trade else [], "tpuids": [],
        "vendors": [vendor["vendor_name"] for vendor in vendors],
        "group_key": group_key, "group": categories.group_name(group_key), "main_category": category,
        "read_only": True,
        "procurement_info": {
            "primary_vendor": {
                "working_code": code, "vendor_name": primary["vendor_name"] if primary else "",
                "supplier_code": "", "trade_name": trade or "", "tpuid": "", "note": "", "is_manual": 0,
                "updated_at": None,
            },
            "note": "", "past_year_vendors": vendors, "system_vendors": [],
        },
        "as_of": _iso_day(overview.snapshot_day(conn)) or today.isoformat(),
        "summary": {
            "stock_qty": _round(stock_qty, 4), "stock_quantities": _quantities((r["BASE_UNIT"], r["QTY_ONHAND"]) for r in inventory),
            "stock_calc_qty": _round(stock_qty, 4), "stock_value": _round(stock_value), "stock_lots": len(inventory),
            "receipt_rows_raw": len(receipts), "receipt_rows_used": len(receipts),
            "receipt_qty": _round(sum(float(r["QTY_RCV"] or 0) for r in receipts), 4),
            "receipt_quantities": _quantities((r["STDIRUNITCODE"], r["QTY_RCV"]) for r in receipts),
            "receipt_calc_qty": _round(sum(float(r["QTY_RCV"] or 0) for r in receipts), 4),
            "receipt_value": _round(sum(float(r["TOTAL_VALUE"] or 0) for r in receipts)),
            "last_receipt": _iso_day(receipts[0]["DATE_RCV"]) if receipts else None,
            "issue_rows_raw": len([r for r in distributions if r["_kind"] == "dispense"]), "issue_rows_used": len(used),
            "issue_qty": _round(sum(_signed(r, "QTY_DIS") for r in used), 4),
            "issue_quantities": _quantities((r["ISSUEUNITCODE"], _signed(r, "QTY_DIS")) for r in used),
            "issue_calc_qty": _round(sum(_signed(r, "QTY_DIS") for r in used), 4),
            "issue_value": _round(sum(_signed(r, "VALUE") for r in used)),
            "last_issue_period": max((r["PERIOD_RPT"] for r in used), default=None),
            "avg_monthly_issue_qty_3m": _round(sum(_signed(r, "QTY_DIS") for r in use_3) / months_3, 4),
            "avg_monthly_issue_quantities_3m": _quantities(((r["ISSUEUNITCODE"], _signed(r, "QTY_DIS")) for r in use_3), months_3)
            or [{"unit": base_unit or "หน่วย", "quantity": 0}],
            "avg_monthly_issue_value_3m": _round(sum(_signed(r, "VALUE") for r in use_3) / months_3),
            "avg_monthly_issue_periods_3m": periods_3, "avg_monthly_issue_months_3m": months_3,
            "days_on_hand": _round(days_on_hand, 1) if days_on_hand is not None else None,
            "unit_pending": False, "usage_periods": periods_6, "usage_days": len(periods_6) * 30,
            "months_on_hand": _round(months_on_hand, 1) if months_on_hand is not None else None,
            "expiry_90_lots": sum(1 for d in expire_days if d is not None and 0 <= d <= 90),
            "expiry_180_lots": sum(1 for d in expire_days if d is not None and 0 <= d <= 180),
            "expiry_240_lots": sum(1 for d in expire_days if d is not None and 0 <= d <= 240),
            "expired_lots": sum(1 for d in expire_days if d is not None and d < 0),
            "raw_rows_total": len(receipts) + len(distributions) + len(inventory),
        },
        "facts": facts, "questions": [], "question_items": [],
        "investigation": {"overall_status": "pending", "respondent": "", "reviewed_at": "", "updated_at": "",
                          "answers": {}},
        "monthly_movement": monthly, "monthly_trend": trend,
        "department_distribution": dept_list, "audit_signals": signals,
        "timeline": _item_timeline(receipts, distributions, inventory),
        "po_tracking": {"pending_pos": [], "received_pos": received_pos, "alerts": [], "events": [],
                        "pending_count": 0, "received_count": len(received_pos)},
        "record_counts": {kind: len(values) for kind, values in rows.items()},
        "source_notes": {
            "receipt": "ใบรับทุกคลังที่ดึงเข้าคลังข้อมูล ERPLPH วันที่รับแสดงเป็นรายวัน อ้างอิงย้อนกลับได้ด้วยเลขที่ใบรับและลำดับ",
            "distribution": "ใบจ่ายให้หน่วยเบิกทุกคลัง มีวันเวลาตัดสต๊อก เลขใบเบิก ลำดับ ล็อต และหน่วยงานผู้เบิก การโอนระหว่างคลังแสดงแยก ไม่นับเป็นยอดใช้",
            "inventory": "คงคลังภาพล่าสุดของแต่ละคลัง แยกตามรุ่นการผลิต",
            "calculation": "เดือนคงคลัง = มูลค่าคงคลัง ÷ (มูลค่าจ่ายสุทธิในเดือนที่จบแล้ว ÷ จำนวนเดือน) คิดจากมูลค่าเพราะหน่วยนับของคงคลังกับใบจ่ายอาจต่างกัน จำนวนวันสำรอง = เดือน × 30",
            "calculation_limit": "ครุภัณฑ์และงานจ้างไม่คำนวณเดือนคงคลัง รายการที่ไม่มีประวัติการเบิกจ่ายไม่คำนวณ",
        },
    }


def _vendors(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in receipts:
        name = row["VENDOR_NAME"]
        if not name:
            continue
        group = groups.setdefault(name, {
            "vendor_name": name, "supplier_code": "", "vendor_code": "", "trade_name": "", "tpuid": "",
            "receipt_count": 0, "total_qty": 0.0, "unit": row["STDIRUNITCODE"] or "", "total_value": 0.0,
            "latest_date": "", "latest_unit_cost": 0.0, "is_primary": False})
        group["receipt_count"] += 1
        group["total_qty"] += float(row["QTY_RCV"] or 0)
        group["total_value"] += float(row["TOTAL_VALUE"] or 0)
        day = _iso_day(row["DATE_RCV"]) or ""
        if day >= group["latest_date"]:
            group["latest_date"] = day
            price = float(row["PACK_COST"] or 0)
            if price > 0:
                group["latest_unit_cost"] = price
    result = sorted(groups.values(), key=lambda v: (v["receipt_count"], v["latest_date"]), reverse=True)
    for index, vendor in enumerate(result):
        vendor["is_primary"] = index == 0
        vendor["total_qty"] = _round(vendor["total_qty"], 2)
        vendor["total_value"] = _round(vendor["total_value"])
        vendor["latest_unit_cost"] = _round(vendor["latest_unit_cost"])
    return result


def _item_monthly_movement(receipts, used) -> list[dict[str, Any]]:
    by_month_r: dict[str, list] = defaultdict(list)
    for row in receipts:
        month = str(row["DATE_RCV"] or "")[:6] or row["_period"]
        by_month_r[month].append(row)
    by_month_i: dict[str, list] = defaultdict(list)
    for row in used:
        by_month_i[row["PERIOD_RPT"]].append(row)
    rows = []
    for month in sorted(set(by_month_r) | set(by_month_i)):
        if not (len(month) == 6 and month.isdigit()):
            continue
        recs = sorted(by_month_r.get(month, []), key=lambda r: (r["DATE_RCV"] or "", r["RCV_NO"], r["suffix"]))
        issues = by_month_i.get(month, [])
        r_value = sum(float(r["TOTAL_VALUE"] or 0) for r in recs)
        r_qty = sum(float(r["QTY_RCV"] or 0) for r in recs)
        i_value = sum(_signed(r, "VALUE") for r in issues)
        i_qty = sum(_signed(r, "QTY_DIS") for r in issues)
        rows.append({
            "period": month,
            "receipt_items": [{
                "po_no": r["PO_NO"] or "-", "lot_no": r["LOT_NO"] or "-", "rcv_no": r["RCV_NO"] or "-",
                "date_rcv": r["DATE_RCV"] or "", "qty": _round(r["QTY_RCV"], 4), "unit": r["STDIRUNITCODE"] or "",
                "unit_cost": _round(r["PACK_COST"]), "value": _round(r["TOTAL_VALUE"]),
                "vendor_name": r["VENDOR_NAME"], "trade_name": "", "supplier_code": "", "tpuid": "",
                "store": stores.store_name(r["SOURCE_STORE"]),
            } for r in recs],
            "receipt_quantities": _quantities((r["STDIRUNITCODE"], r["QTY_RCV"]) for r in recs),
            "receipt_box_qty": "", "receipt_calc_qty": _round(r_qty),
            "receipt_unit_price": _round(r_value / r_qty) if r_qty > 0 else 0,
            "receipt_value": _round(r_value),
            "issue_quantities": _quantities((r["ISSUEUNITCODE"], _signed(r, "QTY_DIS")) for r in issues),
            "issue_box_qty": "", "issue_calc_qty": _round(i_qty),
            "issue_unit_price": _round(i_value / i_qty) if i_qty > 0 else 0,
            "issue_value": _round(i_value),
        })
    return rows


def _item_monthly_trend(monthly, stock_qty, stock_value, consumable) -> list[dict[str, Any]]:
    running_qty, running_val = float(stock_qty or 0), float(stock_value or 0)
    reverse = []
    for month in reversed(monthly):
        r_qty, r_val = float(month["receipt_calc_qty"] or 0), float(month["receipt_value"] or 0)
        i_qty, i_val = float(month["issue_calc_qty"] or 0), float(month["issue_value"] or 0)
        end_qty, end_val = running_qty, running_val
        prev_qty, prev_val = max(0.0, running_qty - r_qty + i_qty), max(0.0, running_val - r_val + i_val)
        reverse.append({"period": month["period"], "stock_qty": _round(end_qty), "stock_value": _round(end_val),
                        "receipt_qty": _round(r_qty), "receipt_value": _round(r_val),
                        "issue_qty": _round(i_qty), "issue_value": _round(i_val),
                        "stock_change_qty": _round(end_qty - prev_qty)})
        running_qty, running_val = prev_qty, prev_val
    trend = list(reversed(reverse))
    for index, item in enumerate(trend):
        window_3 = trend[max(0, index - 2): index + 1]
        window_6 = trend[max(0, index - 5): index + 1]
        avg_3 = sum(w["issue_qty"] for w in window_3) / len(window_3) if window_3 else 0.0
        avg_6 = sum(w["issue_qty"] for w in window_6) / len(window_6) if window_6 else 0.0
        avg_3_value = sum(w["issue_value"] for w in window_3) / len(window_3) if window_3 else 0.0
        item["moving_avg_issue_3m"] = _round(avg_3)
        item["moving_avg_issue_6m"] = _round(avg_6)
        # เดือนคงคลังรายเดือนคิดจากมูลค่า ด้วยเหตุผลเดียวกับภาพรวม — หน่วยนับอาจปนกัน
        mos = _round(item["stock_value"] / avg_3_value, 2) if consumable and avg_3_value > 0 else None
        item["mos"] = mos
        item["days_on_hand"] = _round(mos * 30, 1) if mos is not None else None
        item["is_spike"] = bool(avg_3 > 0 and item["issue_qty"] >= 1.8 * avg_3 and item["issue_qty"] > 2)
    return trend


def _item_departments(used) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not used:
        return [], {"overall_risk": "normal", "risk_label": "ปกติ", "risk_color": "emerald",
                    "total_departments": 0, "total_issues": 0, "spikes_count": 0,
                    "high_concentration": False, "off_hours_count": 0, "unverified_cost_count": 0,
                    "findings": ["ไม่พบประวัติการเบิกจ่ายของรายการนี้"]}
    total_value = sum(_signed(r, "VALUE") for r in used) or 1.0
    groups: dict[tuple, dict[str, Any]] = {}
    off_hours = 0
    for row in used:
        key = (row["_division"], row["_dept"])
        group = groups.setdefault(key, {
            "dept_code": "-".join(part for part in key if part),
            "dept_name": departments.full_name(*key) if key[0] else "ไม่ระบุหน่วยเบิก",
            "total_qty": 0.0, "total_value": 0.0, "irnos": set(), "latest_date": "",
            "monthly_qty": defaultdict(float), "off_hours_count": 0})
        group["total_qty"] += _signed(row, "QTY_DIS")
        group["total_value"] += _signed(row, "VALUE")
        group["irnos"].add(row["IRNO"])
        moment = str(row["SOURCE_MOVEMENT_DATETIME"] or "")
        if moment[:10] > group["latest_date"]:
            group["latest_date"] = moment[:10]
        group["monthly_qty"][row["PERIOD_RPT"]] += _signed(row, "QTY_DIS")
        if len(moment) >= 16 and (moment[11:16] >= "20:00" or moment[11:16] < "06:00") and row["_direction"] == "out":
            group["off_hours_count"] += 1
            off_hours += 1
    result, high = [], False
    for group in groups.values():
        percent = _round(group["total_value"] / total_value * 100, 1)
        anomalies = []
        if percent >= 50 and len(groups) > 1:
            high = True
            anomalies.append(f"สัดส่วนการใช้สูงมาก ({percent}% ของทั้ง รพ.)")
        if group["off_hours_count"]:
            anomalies.append(f"มีการเบิกนอกเวลาทำการ ({group['off_hours_count']} ครั้ง)")
        result.append({"dept_code": group["dept_code"], "dept_name": group["dept_name"],
                       "total_qty": _round(group["total_qty"]), "total_value": _round(group["total_value"]),
                       "req_count": len(group["irnos"]) or 1, "percent": percent,
                       "latest_date": group["latest_date"], "monthly_qty": dict(group["monthly_qty"]),
                       "anomalies": anomalies})
    result.sort(key=lambda g: g["total_value"], reverse=True)
    findings = []
    if high:
        findings.append(f"ตรวจพบการกระจุกตัวของการเบิกจ่ายสูง: {result[0]['dept_name']} เบิกใช้ถึง {result[0]['percent']}% ของมูลค่าทั้งโรงพยาบาล")
    if off_hours:
        findings.append(f"พบการบันทึกเบิกจ่ายนอกเวลาทำการ (20:00 - 06:00 น.) {off_hours} ครั้ง — ห้องยาผู้ป่วยในและฉุกเฉินทำงาน 24 ชั่วโมง ต้องดูประกอบ")
    if not findings:
        findings.append("ไม่พบสัญญาณความผิดปกติเด่นชัด")
    risk = ("warning", "เฝ้าระวัง / ควรตรวจสอบ", "amber") if high else \
        (("notice", "ข้อสังเกตเบื้องต้น", "sky") if off_hours else ("normal", "ปกติ", "emerald"))
    return result, {"overall_risk": risk[0], "risk_label": risk[1], "risk_color": risk[2],
                    "total_departments": len(result), "total_issues": len(used), "spikes_count": 0,
                    "high_concentration": high, "off_hours_count": off_hours, "unverified_cost_count": 0,
                    "findings": findings}


def _item_timeline(receipts, distributions, inventory) -> list[dict[str, Any]]:
    events = []
    for index, row in enumerate(receipts):
        events.append((_iso_day(row["DATE_RCV"]) or "", {
            "kind": "receipt", "date": _iso_day(row["DATE_RCV"]), "date_precision": "day",
            "title": f"รับ ใบรับ {row['RCV_NO']}/{row['suffix']}",
            "detail": f"จำนวน {_round(row['QTY_RCV'], 4):,} {row['STDIRUNITCODE'] or ''} · Lot {row['LOT_NO'] or '-'} · "
                      f"มูลค่า {_round(row['TOTAL_VALUE']):,.2f} บาท · {stores.store_name(row['SOURCE_STORE'])}"
                      + (f" · {row['VENDOR_NAME']}" if row["VENDOR_NAME"] else ""),
            "reference": f"ใบรับ {row['RCV_NO']} · PO {row['PO_NO'] or '-'}", "duplicate": False, "note": ""}))
    for row in distributions[:TIMELINE_LIMIT * 2]:
        moment = str(row["SOURCE_MOVEMENT_DATETIME"] or "")
        verb = {"dispense": "จ่าย" if row["_direction"] == "out" else "รับคืน",
                "transfer": "โอนออก" if row["_direction"] == "out" else "รับโอน"}.get(row["_kind"], "เคลื่อนไหว")
        events.append((moment[:10], {
            "kind": "distribution", "date": moment[:10] or None, "date_precision": "day",
            "title": f"{verb} ใบเบิก {row['IRNO']}/{row['SUFFIX']}",
            "detail": f"จำนวน {_round(row['QTY_DIS'], 4):,} {row['ISSUEUNITCODE'] or ''} · ล็อต {row['SOURCE_LOTNO'] or '-'} · "
                      f"{stores.store_name(row['SOURCE_STORE'])}"
                      + (f" · หน่วยเบิก {row['DEPT_NAME']}" if row["DEPT_NAME"] else "")
                      + (f" · มูลค่า {_round(row['VALUE']):,.2f} บาท" if row["SOURCE_MOS_STATUS"] == metrics.VERIFIED
                         or row["_direction"] == "in" else " · รอสอบทาน ไม่นับยอด")
                      + (f" · บันทึก {moment[11:19]}" if len(moment) >= 19 else ""),
            "reference": f"{row['DOCUMENT_KIND']} · งวด {row['PERIOD_RPT']}", "duplicate": False, "note": ""}))
    for row in inventory:
        day = _iso_day(row["SNAPSHOT_DAY"])
        events.append((day or "", {
            "kind": "inventory", "date": day, "date_precision": "day", "title": f"Lot คงคลัง {row['LOTNO'] or '-'}",
            "detail": f"คงเหลือ {_round(row['QTY_ONHAND'], 4):,} {row['BASE_UNIT'] or ''} · มูลค่า {_round(row['VALUE_ONHAND']):,.2f} บาท · "
                      f"{stores.store_name(row['SOURCE_STORE'])}"
                      + (f" · หมดอายุ {_iso_day(row['EXPIRE_DATE'])}" if _iso_day(row["EXPIRE_DATE"]) else ""),
            "reference": f"ภาพคงคลังวันที่ {day or '-'}", "duplicate": False, "note": ""}))
    events.sort(key=lambda pair: pair[0], reverse=True)
    return [event for _, event in events[:TIMELINE_LIMIT]]


def _received_pos(code: str, receipts, display_name: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in receipts:
        po_no = str(row["PO_NO"] or "").strip()
        if not po_no or po_no in ("*", "-"):
            continue
        group = groups.setdefault(po_no, {
            "po_no": po_no, "working_code": code, "trade_name": display_name, "vendor_name": row["VENDOR_NAME"],
            "po_date": "", "received_date": "", "received_rcv_no": "", "received_lot_no": "",
            "received_qty": 0.0, "order_qty": 0.0, "remaining_qty": 0.0, "unit": row["STDIRUNITCODE"] or "",
            "unit_price": _round(row["PACK_COST"]), "total_amount": 0.0, "status": "received",
            "is_sent_to_vendor": 1, "sent_to_vendor_date": "", "sent_to_vendor_note": "",
            "director_approved_date": "", "days_since_sent": 0})
        group["received_qty"] += float(row["QTY_RCV"] or 0)
        group["order_qty"] = group["received_qty"]
        group["total_amount"] += float(row["TOTAL_VALUE"] or 0)
        day = _iso_day(row["DATE_RCV"]) or ""
        if day >= group["received_date"]:
            group["received_date"] = day
            group["received_rcv_no"] = row["RCV_NO"]
            group["received_lot_no"] = row["LOT_NO"]
    for group in groups.values():
        group["received_qty"] = _round(group["received_qty"], 2)
        group["order_qty"] = group["received_qty"]
        group["total_amount"] = _round(group["total_amount"])
    return sorted(groups.values(), key=lambda g: g["received_date"], reverse=True)


def build_records(conn, code: str, kind: str, page: int = 1, per_page: int = 30,
                  query: str = "") -> dict[str, Any] | None:
    labels = {"receipt": RECEIPT_LABELS, "distribution": DISTRIBUTION_LABELS, "inventory": INVENTORY_LABELS}.get(kind)
    if labels is None:
        return None
    rows = _item_rows(conn, code)[kind]
    needle = (query or "").strip().casefold()
    if needle:
        rows = [row for row in rows if needle in " ".join(str(row.get(c, "")) for c in labels).casefold()]
    per_page = max(1, min(int(per_page), 200))
    total = len(rows)
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(int(page), pages))
    start = (page - 1) * per_page
    records = [{
        "source_file": SOURCE_LABELS[kind], "source_line": start + index + 1, "business_key": "",
        "duplicate_key": False, "values": {column: row.get(column, "") for column in labels},
    } for index, row in enumerate(rows[start:start + per_page])]
    return {
        "kind": kind, "label": SOURCE_LABELS[kind], "code": code, "page": page, "pages": pages,
        "per_page": per_page, "total": total, "query": needle,
        "columns": [{"name": column, "label": label, "help": ""} for column, label in labels.items()],
        "records": records,
        "date_note": {
            "receipt": "DATE_RCV คือวันที่รับเข้าคลังตามใบรับ",
            "distribution": "SOURCE_MOVEMENT_DATETIME คือวันเวลาตัดสต๊อกจากใบจ่าย · PERIOD_RPT คือเดือนที่บันทึก",
            "inventory": "SNAPSHOT_DAY คือวันที่ถ่ายภาพคงคลัง · DATE_ONHAND คือวันรับเข้าล่าสุดของล็อต",
        }[kind],
    }


# --------------------------------------------------------------------------- เส้นทาง

def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


NO_WAREHOUSE = {"status": "error",
                "message": "ยังไม่มีคลังข้อมูลของ ERPLPH บนเครื่องนี้ (warehouse_data/erplph.db) ให้รัน pull_warehouse_data.bat ก่อน"}


@bp.route("/api/auth/me")
def auth_me():
    # ยังไม่มีระบบล็อกอิน (docs/system_design.md ข้อ 6) ทุกคนเป็นผู้ใช้อ่านอย่างเดียว
    return _no_store(jsonify({"status": "ok", "user": READ_ONLY_USER}))


@bp.route("/api/auth/login", methods=["POST"])
def auth_login():
    return _no_store(jsonify({"status": "ok", "user": READ_ONLY_USER}))


@bp.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    return _no_store(jsonify({"status": "ok"}))


@bp.route("/api/status")
def status():
    conn = _open()
    if conn is None:
        return jsonify({"status": "empty", "warehouse": False})
    try:
        return jsonify({"status": "ok", "warehouse": True, "latest_period": overview.latest_period(conn),
                        "snapshot": overview.snapshot_day(conn)})
    finally:
        conn.close()


@bp.route("/api/scopes")
def scopes():
    """ตัวเลือกขอบเขตบนหน้าจอ — ประเภทของตาม categories.GROUPS และคลังที่มีข้อมูลจริง"""
    conn = _open()
    if conn is None:
        return jsonify({"groups": [], "stores": []})
    try:
        present = {categories.group_of(category) for (category,) in
                   conn.execute("SELECT DISTINCT main_category FROM items")}
        codes = [code for (code,) in conn.execute(
            "SELECT store FROM (SELECT store, SUM(value) AS total FROM balances GROUP BY store "
            "UNION ALL SELECT store, 0 FROM issues WHERE period >= ? GROUP BY store) "
            "GROUP BY store ORDER BY SUM(total) DESC",
            [_add_months(overview.latest_period(conn) or "200001", -6)])]
    finally:
        conn.close()
    return jsonify({
        "groups": [{"key": group.key, "name": group.name_th} for group in categories.GROUPS
                   if group.key in present],
        "stores": [{"code": code, "name": stores.store_name(code)} for code in codes
                   if stores.is_active(code)],
    })


@bp.route("/api/monitor/summary")
def monitor_summary():
    group, store = _scope_args()
    target = _int_arg("target_days", TARGET_DAYS, 1, 365)
    months = _int_arg("usage_months", USAGE_MONTHS, 1, 24)
    expiry = _int_arg("expiry_days", EXPIRY_DAYS, 1, 730)
    conn = _open()
    if conn is None:
        return jsonify(NO_WAREHOUSE), 404
    try:
        payload = _cached(("summary", target, months, expiry, group, store),
                          lambda: build_summary(conn, target, months, expiry, group, store))
    finally:
        conn.close()
    if payload is None:
        return jsonify({"status": "error", "message": "คลังข้อมูลยังว่าง"}), 404
    return jsonify(payload)


@bp.route("/api/monitor/auto-sync/status")
def auto_sync_status():
    conn = _open()
    snapshot = ""
    if conn is not None:
        try:
            snapshot = overview.snapshot_day(conn)
        finally:
            conn.close()
    label = f"{snapshot[6:8]}/{snapshot[4:6]}/{snapshot[:4]}" if snapshot else "-"
    return jsonify({"enabled": False, "is_syncing": False, "last_status": "ok",
                    "last_message": f"ข้อมูลถึง {label} · ดึงด้วย pull_warehouse_data.bat"})


@bp.route("/api/monitor/auto-sync/trigger", methods=["POST"])
@bp.route("/api/monitor/pull", methods=["POST"])
@bp.route("/api/json/clear", methods=["POST"])
def read_only_refused():
    return jsonify({"status": "error", "message": "ERPLPH อ่านอย่างเดียว ดึงข้อมูลด้วย pull_warehouse_data.bat"}), 403


@bp.route("/api/procurement/plan-summary")
@bp.route("/api/procurement/plan-file")
def procurement_plan():
    # แผนจัดซื้อของ Stock5 เป็นแฟ้มแผนยาที่ผู้ใช้อัปโหลด ยังไม่มีแผนของทุกประเภทของใน ERPLPH
    return jsonify({"status": "unavailable",
                    "message": "ยังไม่มีแผนจัดซื้อของทุกประเภทของใน ERPLPH (แผนยาอยู่ใน Stock5)"}), 404


@bp.route("/api/pos/alerts")
def po_alerts():
    return jsonify({"has_alerts": False, "total_count": 0, "alerts": [],
                    "note": "ยังไม่ได้ดึงใบ PO ที่ยังไม่ได้รับของเข้าคลังข้อมูล ERPLPH"})


@bp.route("/api/documents/search")
def documents_search():
    return jsonify({"query": request.args.get("q", ""), "total": 0, "documents": []})


@bp.route("/api/drugs/search")
def drugs_search():
    group, store = _scope_args()
    conn = _open()
    if conn is None:
        return jsonify(NO_WAREHOUSE), 404
    try:
        return jsonify(search_items(conn, request.args.get("q", ""), request.args.get("status", "all"),
                                    _int_arg("page", 1, 1, 100000), _int_arg("per_page", 24, 1, 100),
                                    group, store))
    finally:
        conn.close()


@bp.route("/api/drugs/<code>/records/<kind>")
def drug_records(code, kind):
    conn = _open()
    if conn is None:
        return jsonify(NO_WAREHOUSE), 404
    try:
        payload = build_records(conn, code[:30], kind, _int_arg("page", 1, 1, 100000),
                                _int_arg("per_page", 30, 1, 200), request.args.get("q", ""))
    finally:
        conn.close()
    if payload is None:
        return jsonify({"status": "error", "message": "ชนิดแฟ้มไม่ถูกต้อง"}), 400
    return jsonify(payload)


@bp.route("/api/drugs/<code>/pos", methods=["GET"])
def drug_pos(code):
    conn = _open()
    if conn is None:
        return jsonify(NO_WAREHOUSE), 404
    try:
        detail = build_item_detail(conn, code[:30])
    finally:
        conn.close()
    if detail is None:
        return jsonify({"status": "error", "message": "ไม่พบรายการ"}), 404
    return jsonify(detail["po_tracking"])


@bp.route("/api/drugs/<code>/pos", methods=["POST"])
@bp.route("/api/drugs/<code>/investigation", methods=["POST"])
@bp.route("/api/drugs/<code>/primary-vendor", methods=["POST"])
@bp.route("/api/pos/<po_no>/dispatch", methods=["POST"])
@bp.route("/api/pos/<po_no>/receive", methods=["POST"])
def write_refused(**_kwargs):
    return jsonify({"status": "error",
                    "message": "ERPLPH ยังอ่านอย่างเดียว การบันทึกต้องรอระบบล็อกอิน (docs/system_design.md ข้อ 6)"}), 403


@bp.route("/api/drugs/<code>")
def drug_detail(code):
    conn = _open()
    if conn is None:
        return jsonify(NO_WAREHOUSE), 404
    try:
        detail = _cached(("detail", code[:30]), lambda: build_item_detail(conn, code[:30]))
    finally:
        conn.close()
    if detail is None:
        return jsonify({"status": "error", "message": "ไม่พบรายการนี้ในคลังข้อมูล"}), 404
    return jsonify(detail)
