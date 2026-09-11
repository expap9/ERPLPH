"""ดึงข้อมูลทุกคลังเข้าฐานข้อมูล ทีละงวด (เดือน × คลัง)

ต่างจาก Stock5 ที่ดึงทั้งช่วงรายงานใหม่ทุกรอบแล้วเขียนไฟล์ทับ ที่นี่ดึงเฉพาะงวด
ที่ยังไม่มี เพราะทุกคลังรวมกันคือ 930,720 บรรทัดต่อ 12 เดือน การอ่านใหม่ทั้งหมด
ทุกชั่วโมงจะเป็นภาระกับเซิร์ฟเวอร์ของโรงพยาบาลโดยไม่ได้อะไรเพิ่ม

หลักการที่ยกมาจาก Stock5 โดยตั้งใจ
- พักระหว่างคำสั่ง ไม่รัวใส่เซิร์ฟเวอร์ของโรงพยาบาล
- งวดที่ดึงไม่สำเร็จถูกบันทึกไว้ และข้อมูลเดิมไม่ถูกแตะ
- เดือนที่ยังไม่ครบจะถูกดึงซ้ำได้เสมอ เพราะยอดยังเปลี่ยน
- ไม่เดาแทนผู้ใช้: ชนิดเอกสารที่ยังไม่รู้ความหมายถูกเก็บไว้แต่ไม่นับรวม
"""
from datetime import datetime, timezone
import time
from typing import Any, Callable, Iterable

import categories
import database
import queries
import reporting_window
import stock5_engine
import stores
import warehouse_db

#: พักระหว่างคำสั่ง เพื่อไม่ให้เซิร์ฟเวอร์ของโรงพยาบาลรับภาระเป็นช่วงพีค
PACING_SECONDS = 1.0

#: ชนิดข้อมูลที่ดึงรายงวด — คงคลังเป็นภาพ ณ ปัจจุบัน จึงไม่ผูกกับงวดย้อนหลัง
PERIOD_KINDS = ("receipt", "issue")


def _text(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _number(row: dict, *names: str) -> float:
    for name in names:
        value = row.get(name)
        if value in (None, ""):
            continue
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return 0.0


def _receipt_row(row: dict) -> dict[str, Any]:
    return {
        "rcv_no": _text(row, "RCV_NO"),
        "suffix": _text(row, "suffix", "SUFFIX"),
        "stock_code": _text(row, "WORKING_CODE"),
        "lot_no": _text(row, "LOT_NO", "SOURCE_LOTNO"),
        "qty": _number(row, "QTY_RCV"),
        "value": _number(row, "TOTAL_VALUE"),
        "unit": _text(row, "BASE_UNIT*", "BASE_UNIT", "STDIRUNITCODE"),
        "unit_price": _number(row, "PACK_COST"),
        "po_no": _text(row, "PO_NO"),
        "supplier": _text(row, "VENDOR_NAME"),
        "rcv_date": _text(row, "DATE_RCV"),
    }


def _issue_row(row: dict) -> dict[str, Any]:
    document_type = _text(row, "SOURCE_DOCUMENTTYPE", "DOCUMENTTYPE")
    return {
        "irno": _text(row, "IRNO"),
        "suffix": _text(row, "SUFFIX"),
        "movement_key": _text(row, "SOURCE_MOVEMENT_SUFFIX"),
        "stock_code": _text(row, "WORKING_CODE"),
        "lot_no": _text(row, "SOURCE_LOTNO"),
        "qty": _number(row, "QTY_DIS"),
        "value": _number(row, "VALUE"),
        "unit": _text(row, "ISSUEUNITCODE", "BASE_UNIT"),
        "department": _text(row, "DIS_DEPT_GROUP", "DIS"),
        "issued_at": _text(row, "SOURCE_MOVEMENT_DATETIME")[:19],
        "document_type": document_type,
        "movement_kind": queries.movement_kind(document_type),
        "check_status": _text(row, "SOURCE_MOS_STATUS"),
        "check_reason": _text(row, "SOURCE_MOS_REASON"),
    }


def _balance_row(row: dict) -> dict[str, Any]:
    return {
        "stock_code": _text(row, "WORKING_CODE"),
        "lot_no": _text(row, "LOTNO", "SOURCE_LOTNO"),
        "qty": _number(row, "QTY_ONHAND"),
        "value": _number(row, "SOURCE_STOCK_VALUE", "VALUE_ONHAND"),
        "unit": _text(row, "BASE_UNIT", "STDIRUNITCODE"),
        "expire_date": _text(row, "EXPIRE_DATE"),
        "last_in_date": _text(row, "SOURCE_DATE_LAST_IN"),
    }


_SHAPERS = {"receipt": _receipt_row, "issue": _issue_row, "balance": _balance_row}
_QUERY_FOR_KIND = {"receipt": "RECEIPT", "issue": "DISTRIBUTION", "balance": "INVENTORY"}


def _display_name(raw: str) -> str:
    """ชื่อยาในฐานข้อมูลมีอักขระตัวแรกซ้ำทุกแถว ใช้ตัวตัดของ Stock5 ตัวเดียวกัน"""
    try:
        return stock5_engine.load("drug_names").display_drug_name(raw)
    except Exception:
        return raw


def _item_rows(rows: Iterable[dict]) -> list[dict[str, Any]]:
    """ทะเบียนรายการที่พบในผลการดึง ใช้ร่วมทุกคลัง"""
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _text(row, "WORKING_CODE")
        if not code or code in seen:
            continue
        name = _display_name(_text(row, "ENGLISHNAME"))
        seen[code] = {
            "stock_code": code,
            "name": name,
            "trade_name": _display_name(_text(row, "TRADE_NAME")),
            "main_category": _text(row, "MAINCATEGORY"),
            "item_group": categories.DRUG,
            "base_unit": _text(row, "BASE_UNIT", "BASE_UNIT*", "STDIRUNITCODE"),
            "retired": categories.is_retired_item(name),
        }
    return list(seen.values())


def pull_period(connection, period: str, store: str, kind: str,
                group_key: str = categories.DRUG) -> dict[str, Any]:
    """ดึงงวดเดียว คืนผลสรุป ไม่โยนข้อผิดพลาดออกไปให้ลูปหลักล้ม"""
    date_from, date_to = reporting_window.period_bounds(period)
    try:
        sql = queries.build(_QUERY_FOR_KIND[kind], store, date_from, date_to, group_key)
    except Exception as exc:
        warehouse_db.mark_period_failed(period, store, kind, str(exc))
        return {"period": period, "store": store, "kind": kind, "status": "error",
                "message": str(exc)}

    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        columns = [column[0] for column in cursor.description]
        raw = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        summary = database.error_summary(exc)
        warehouse_db.mark_period_failed(period, store, kind, summary["message"])
        return {"period": period, "store": store, "kind": kind, "status": "error",
                "message": summary["message"]}
    finally:
        if cursor is not None:
            cursor.close()

    shaped = [_SHAPERS[kind](row) for row in raw]
    shaped = [row for row in shaped if row.get("stock_code")]
    warehouse_db.replace_period(period, store, kind, shaped,
                                source_rows=len(raw), query_sha256=queries.fingerprint(sql))
    warehouse_db.upsert_items(_item_rows(raw))
    return {"period": period, "store": store, "kind": kind, "status": "success",
            "source_rows": len(raw), "stored_rows": len(shaped)}


def plan(today=None, store_codes: Iterable[str] | None = None,
         kinds: Iterable[str] = PERIOD_KINDS, years_back: int | None = None,
         include_current: bool = True) -> list[tuple[str, str, str]]:
    """งานที่ต้องดึง = งวดที่ยังไม่มี บวกเดือนปัจจุบันซึ่งยอดยังเปลี่ยนได้"""
    years = reporting_window.DEFAULT_FISCAL_YEARS_BACK if years_back is None else years_back
    periods = reporting_window.periods(today, years)
    selected = list(store_codes) if store_codes is not None else stores.active_store_codes()
    work = warehouse_db.missing_periods(periods, selected, kinds)

    if include_current and periods:
        current = periods[-1]
        already = {item for item in work}
        for store in selected:
            for kind in kinds:
                item = (current, store, kind)
                if item not in already:
                    work.append(item)
    return work


def run(today=None, store_codes: Iterable[str] | None = None,
        kinds: Iterable[str] = PERIOD_KINDS, years_back: int | None = None,
        limit: int | None = None, pacing: float = PACING_SECONDS,
        progress: Callable[[dict], None] | None = None,
        connection_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """ดึงตามแผน คืนผลสรุป งวดที่ล้มไม่ทำให้งวดอื่นหยุด"""
    warehouse_db.init_db()
    work = plan(today, store_codes, kinds, years_back)
    if limit is not None:
        work = work[:limit]

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = {"started_at": started, "planned": len(work), "success": 0, "failed": 0,
               "rows": 0, "details": []}
    if not work:
        results["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return results

    factory = connection_factory or (lambda: database.connect(timeout=30))
    connection = None
    try:
        connection = factory()
        for index, (period, store, kind) in enumerate(work):
            if index:
                time.sleep(pacing)
            outcome = pull_period(connection, period, store, kind)
            results["details"].append(outcome)
            if outcome["status"] == "success":
                results["success"] += 1
                results["rows"] += outcome.get("stored_rows", 0)
            else:
                results["failed"] += 1
            if progress:
                progress(outcome)
    except Exception as exc:
        results["error"] = database.error_summary(exc)["message"]
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    results["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return results
