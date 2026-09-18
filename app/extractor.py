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
from datetime import date, datetime, timezone
import time
from typing import Any, Callable, Iterable

import categories
import database
import name_cleaner
import queries
import reporting_window
import stock5_engine
import stores
import unit_rules
import warehouse_db

#: พักระหว่างคำสั่ง เพื่อไม่ให้เซิร์ฟเวอร์ของโรงพยาบาลรับภาระเป็นช่วงพีค
PACING_SECONDS = 1.0

#: ชนิดข้อมูลที่ดึงรายงวด — คงคลังเป็นภาพ ณ ปัจจุบัน จึงไม่ผูกกับงวดย้อนหลัง
PERIOD_KINDS = ("receipt", "issue")

#: คงคลังเก็บวันละหนึ่งภาพต่อคลัง ดึงซ้ำในวันเดียวกันแทนภาพเดิม ภาพของวันก่อนเก็บไว้
#: ดูแนวโน้ม — ช่อง period ของภาพคงคลังจึงเป็นวันที่ YYYYMMDD ไม่ใช่เดือน
SNAPSHOT_KIND = "balance"
ALL_KINDS = PERIOD_KINDS + (SNAPSHOT_KIND,)

#: คีย์ตัดแถวซ้ำชุดเดียวกับที่ Stock5 ใช้ก่อนเขียนแฟ้มรับและคงคลัง
#: ใบจ่ายไม่ต้องใช้ เพราะการสอบทานรายล็อตจัดการแถวซ้ำอยู่แล้ว
_DEDUPE_KEYS = {"receipt": ["RCV_NO", "suffix", "WORKING_CODE"],
                SNAPSHOT_KIND: ["WORKING_CODE", "LOTNO"]}


def snapshot_day(today=None) -> str:
    """วันของภาพคงคลัง ตามเวลาเครื่องที่ดึง ซึ่งอยู่ในโรงพยาบาล"""
    return (today or date.today()).strftime("%Y%m%d")


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
    pack_unit = _text(row, "STDIRUNITCODE")
    base_unit = _text(row, "BASE_UNIT*", "BASE_UNIT")
    pack_size = _number(row, "PACK_SIZE") or 1.0
    return {
        "rcv_no": _text(row, "RCV_NO"),
        "suffix": _text(row, "suffix", "SUFFIX"),
        "stock_code": _text(row, "WORKING_CODE"),
        "lot_no": _text(row, "LOT_NO", "SOURCE_LOTNO"),
        "qty": _number(row, "QTY_RCV"),
        "value": _number(row, "TOTAL_VALUE"),
        "unit": pack_unit or base_unit,
        "unit_price": _number(row, "PACK_COST"),
        "po_no": _text(row, "PO_NO"),
        "supplier": name_cleaner.clean_vendor_name(_text(row, "VENDOR_NAME")),
        "rcv_date": _text(row, "DATE_RCV"),
        "division": _text(row, "RCV_DIVISION"),
        "dept": _text(row, "RCV_DEPT"),
        "section": _text(row, "RCV_SECTION"),
        "pack_size": pack_size,
        "pack_unit": pack_unit,
        "base_unit": base_unit,
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
        # หน่วยงานจริง 3 ชั้น — เก็บแยกจาก department ซึ่งเป็นรหัสกลุ่มของกระทรวง
        "division": _text(row, "DIS_DIVISION"),
        "dept": _text(row, "DIS_DEPT"),
        "section": _text(row, "DIS_SECTION"),
        "issued_at": _text(row, "SOURCE_MOVEMENT_DATETIME")[:19],
        "document_type": document_type,
        "movement_kind": queries.movement_kind(document_type),
        "direction": _direction(row),
        "check_status": _text(row, "SOURCE_MOS_STATUS"),
        "check_reason": _text(row, "SOURCE_MOS_REASON"),
    }


def _direction(row: dict) -> str:
    """out เมื่อของออกจากคลังนี้จริง อย่างอื่นคือขาเข้าหรือการกลับรายการ"""
    nature_out = _number(row, "SOURCE_NATUREISOUT")
    add_stock = _number(row, "SOURCE_ADDSTOCK")
    if not _text(row, "SOURCE_NATUREISOUT"):
        return ""
    return "out" if nature_out == 1 and add_stock == 0 else "in"


def _reconcile(raw: list[dict]) -> tuple[list[dict], dict | None]:
    """สอบทานรายล็อตด้วยเครื่องเดียวกับ Stock5 ก่อนเก็บ

    Stock5 เรียก reconcile_distribution ทุกครั้งก่อนเขียนแฟ้มจ่าย ตัวดึงรุ่นแรก
    ของที่นี่ข้ามขั้นนี้ไป สถานะสอบทานจึงว่างทุกแถว และเครื่องคำนวณที่ยืมมาก็ไม่ได้
    ถูกใช้จริง ขั้นนี้ยังทำเครื่องหมายขาเข้าของการโอนว่ารอตรวจ ไม่ให้ปนกับการจ่าย
    """
    if not raw or "SOURCE_ENGINE" not in raw[0]:
        return raw, None
    import pandas as pd

    lot = stock5_engine.load("lot_reconciliation")
    frame = pd.DataFrame(raw)
    frame = frame.astype(object).where(pd.notna(frame), "")
    checked, stats = lot.reconcile_distribution(frame)
    return checked.to_dict("records"), stats


def _deduplicate(raw: list[dict], kind: str) -> list[dict]:
    """ตัดสำเนาที่เหมือนกันทุกช่องจากการเชื่อมตาราง แบบเดียวกับ Stock5

    ถ้าคีย์เดียวกันแต่จำนวนหรือราคาต่างกัน ตัวเดิมของ Stock5 จะหยุดทันที ไม่เลือก
    ตัวเลขแทนผู้ใช้ — ที่นี่หยุดด้วยเหตุผลเดียวกัน
    """
    keys = _DEDUPE_KEYS.get(kind)
    if not raw or keys is None:
        return raw
    import pandas as pd

    identity = stock5_engine.load("transaction_identity")
    frame = pd.DataFrame(raw)
    frame = frame.astype(object).where(pd.notna(frame), "")
    return identity.deduplicate_scoped_extraction(frame, keys).to_dict("records")


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
    """ชื่อยาในฐานข้อมูลมีอักขระตัวแรกซ้ำทุกแถว ทำความสะอาดด้วย name_cleaner"""
    if not raw:
        return ""
    try:
        return name_cleaner.clean_drug_name(raw)
    except Exception:
        try:
            return stock5_engine.load("drug_names").display_drug_name(raw)
        except Exception:
            return raw


def _item_group(main_category: str, group_key: str) -> str:
    """หมวดของรายการเองเชื่อถือได้กว่าตัวกรองที่ใช้ดึง ยกเว้นแถวที่ไม่มีหมวดติดมา"""
    if main_category:
        return categories.group_of(main_category)
    return categories.OTHER if group_key == categories.ALL else group_key


def _item_rows(rows: Iterable[dict], group_key: str = categories.DRUG) -> list[dict[str, Any]]:
    """ทะเบียนรายการที่พบในผลการดึง ใช้ร่วมทุกคลัง

    กลุ่มมาจากหมวดของรายการเอง ไม่ใช่จากตัวกรองที่ใช้ดึง เพราะการดึงรวดเดียวทุกหมวด
    (categories.ALL) จะได้ทั้งยา เวชภัณฑ์ และพัสดุ ปนกันมาในผลลัพธ์เดียว
    ถ้าแถวไหนไม่มีหมวดติดมา จึงค่อยถอยไปใช้กลุ่มของตัวกรอง
    """
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _text(row, "WORKING_CODE")
        if not code or code in seen:
            continue
        name = _display_name(_text(row, "ENGLISHNAME"))
        main_category = _text(row, "MAINCATEGORY")
        seen[code] = {
            "stock_code": code,
            "name": name,
            "trade_name": _display_name(_text(row, "TRADE_NAME")),
            "main_category": main_category,
            "item_group": _item_group(main_category, group_key),
            "base_unit": _text(row, "BASE_UNIT", "BASE_UNIT*", "STDIRUNITCODE"),
            "retired": categories.is_retired_item(name),
        }
    return list(seen.values())


def pull_period(connection, period: str, store: str, kind: str,
                group_key: str = categories.DRUG) -> dict[str, Any]:
    """ดึงงวดเดียว คืนผลสรุป ไม่โยนข้อผิดพลาดออกไปให้ลูปหลักล้ม

    สำหรับคงคลัง period คือวันที่ของภาพ (YYYYMMDD)
    """
    try:
        if kind == SNAPSHOT_KIND:
            # คำสั่งคงคลังไม่ใช้ช่วงวัน แต่ตัวประกอบคำสั่งตรวจรูปแบบวันที่เสมอ
            date_from = date_to = period
        else:
            date_from, date_to = reporting_window.period_bounds(period)
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

    source_rows = len(raw)
    reconciliation = None
    units_sha256 = ""
    if kind == "issue":
        try:
            raw, reconciliation = _reconcile(raw)
            units_sha256 = unit_rules.period_digest(
                (_text(row, "WORKING_CODE") for row in raw),
                unit_rules.rules(), unit_rules.engine_digest())
        except Exception as exc:
            # สอบทานไม่ได้ต้องไม่เก็บ ข้อมูลที่ไม่ผ่านเครื่องเดียวกับ Stock5
            # จะให้ตัวเลขคนละชุดโดยไม่มีใครรู้
            warehouse_db.mark_period_failed(period, store, kind, f"สอบทานรายล็อตไม่สำเร็จ: {exc}")
            return {"period": period, "store": store, "kind": kind, "status": "error",
                    "message": f"สอบทานรายล็อตไม่สำเร็จ: {exc}"}

    try:
        raw = _deduplicate(raw, kind)
        shaped = [_SHAPERS[kind](row) for row in raw]
        shaped = [row for row in shaped if row.get("stock_code")]
        stored = warehouse_db.replace_period(
            period, store, kind, shaped, source_rows=source_rows,
            query_sha256=queries.fingerprint(sql), units_sha256=units_sha256)
        warehouse_db.upsert_items(_item_rows(raw, group_key))
    except Exception as exc:
        # งวดนี้ไม่ถูกเขียน (replace_period เป็นธุรกรรมเดียว) งวดอื่นยังดึงต่อได้
        warehouse_db.mark_period_failed(period, store, kind, f"เก็บข้อมูลไม่สำเร็จ: {exc}")
        return {"period": period, "store": store, "kind": kind, "status": "error",
                "message": f"เก็บข้อมูลไม่สำเร็จ: {exc}"}
    outcome = {"period": period, "store": store, "kind": kind, "status": "success",
               "source_rows": source_rows, "stored_rows": stored["rows"]}
    if reconciliation:
        outcome["verified_rows"] = reconciliation.get("verified_rows", 0)
        outcome["pending_rows"] = reconciliation.get("pending_rows", 0)
    return outcome


#: เหตุผลที่งวดหนึ่งถูกวางแผนดึง — ผู้ใช้ต้องเห็นว่าทำไมต้องอ่านฐานข้อมูลโรงพยาบาลอีก
REASON_MISSING = "ยังไม่มีหรือดึงไม่สำเร็จ"
REASON_QUERY = "คำสั่งดึงเปลี่ยน"
REASON_UNITS = "กติกาหน่วยเปลี่ยน"
REASON_UNITS_UNKNOWN = "ไม่มีบันทึกกติกาหน่วยที่ใช้สอบทาน"
REASON_CURRENT = "เดือนปัจจุบัน ยอดยังเปลี่ยนได้"
REASON_LATE_POSTING = "เดือนก่อน อาจมีเอกสารบันทึกย้อนวัน"
REASON_SNAPSHOT = "คงคลัง ณ วันนี้"

#: เอกสารเข้าระบบช้ากว่าวันที่บนเอกสารได้ พบจริงวันที่ 12 ก.ย. 2569: ใบโอน
#: 69D09127-69D09131 ลงวันที่ 10 ก.ย. 15:11 แต่ยังไม่อยู่ในฐานข้อมูลตอนบ่ายวันที่ 11
#: (1,551,124 หน่วย ฿1,923,985) เดือนที่ผ่านไปแล้วจึงยังเปลี่ยนได้อีกระยะหนึ่ง
#: การดึงซ้ำเฉพาะเดือนปัจจุบันจะไม่มีวันเห็นใบที่ลงวันที่สิ้นเดือนแต่บันทึกต้นเดือนถัดไป
LATE_POSTING_DAYS = 10


def plan(today=None, store_codes: Iterable[str] | None = None,
         kinds: Iterable[str] = ALL_KINDS, years_back: int | None = None,
         include_current: bool = True, recheck_months: int = 0,
         group_key: str = categories.DRUG) -> list[tuple[str, str, str]]:
    """งานที่ต้องดึง = งวดที่ยังไม่มี บวกเดือนปัจจุบันซึ่งยอดยังเปลี่ยนได้"""
    return [item[:3] for item in
            plan_detail(today, store_codes, kinds, years_back, include_current, recheck_months,
                        group_key)]


def plan_detail(today=None, store_codes: Iterable[str] | None = None,
                kinds: Iterable[str] = ALL_KINDS, years_back: int | None = None,
                include_current: bool = True,
                recheck_months: int = 0,
                group_key: str = categories.DRUG) -> list[tuple[str, str, str, str]]:
    """เหมือน plan() แต่บอกเหตุผลของแต่ละงวดด้วย

    group_key ต้องเป็นค่าเดียวกับที่จะใช้ดึงจริง เพราะขั้น "คำสั่งดึงเปลี่ยน" เทียบลายนิ้วมือ
    คำสั่ง ถ้าวางแผนด้วยหมวดหนึ่งแต่ดึงด้วยอีกหมวด ทุกงวดจะถูกวางแผนซ้ำไม่รู้จบ
    """
    years = reporting_window.DEFAULT_FISCAL_YEARS_BACK if years_back is None else years_back
    periods = reporting_window.periods(today, years)
    selected = list(store_codes) if store_codes is not None else stores.active_store_codes()
    requested = list(kinds)
    kinds = [kind for kind in requested if kind in PERIOD_KINDS]
    work = [(period, store, kind, REASON_MISSING)
            for period, store, kind in warehouse_db.missing_periods(periods, selected, kinds)]
    queued = {item[:3] for item in work}
    wanted_periods, wanted_stores = set(periods), set(selected)

    def wanted(period: str, store: str, kind: str) -> bool:
        return ((period, store, kind) not in queued and period in wanted_periods
                and store in wanted_stores and kind in kinds)

    def add(period: str, store: str, kind: str, reason: str) -> None:
        work.append((period, store, kind, reason))
        queued.add((period, store, kind))

    # งวดที่ดึงด้วยคำสั่งรุ่นเก่าต้องดึงใหม่ มิฉะนั้นข้อมูลผิดจากรุ่นก่อนจะค้างอยู่
    # เงียบ ๆ — การดึงรอบแรกเก็บขาเข้าของการโอนไว้ในฐานะการจ่ายของห้องยาย่อย
    for (period, store, kind), stored in warehouse_db.stored_fingerprints().items():
        if not wanted(period, store, kind):
            continue
        try:
            date_from, date_to = reporting_window.period_bounds(period)
            latest = queries.fingerprint(
                queries.build(_QUERY_FOR_KIND[kind], store, date_from, date_to, group_key))
        except Exception:
            continue
        if latest != stored:
            add(period, store, kind, REASON_QUERY)

    # สถานะสอบทานที่เก็บไว้คำนวณด้วยกติกาหน่วย ณ ตอนดึง ถ้าตารางหน่วยถูกเติมภายหลัง
    # ต้องสอบทานใหม่ ไม่เช่นนั้นคลัง 2 จะไม่ตรงกับ Stock5 โดยไม่มีสัญญาณเตือน
    if "issue" in kinds:
        for period, store, reason in _stale_unit_periods(wanted_periods, wanted_stores):
            if wanted(period, store, "issue"):
                add(period, store, "issue", reason)

    if include_current and periods:
        current = periods[-1]
        for store in selected:
            for kind in kinds:
                if (current, store, kind) not in queued:
                    add(current, store, kind, REASON_CURRENT)

    # เดือนก่อนยังเปลี่ยนได้จากเอกสารที่บันทึกย้อนวัน จึงดึงซ้ำในช่วงต้นเดือนถัดไป
    # และดึงลึกกว่านั้นได้ตามต้องการด้วย recheck_months (เช่น งานประจำสัปดาห์)
    months_back = max(recheck_months, 1 if (today or date.today()).day <= LATE_POSTING_DAYS else 0)
    for period in periods[max(0, len(periods) - 1 - months_back):-1]:
        for store in selected:
            for kind in kinds:
                if (period, store, kind) not in queued:
                    add(period, store, kind, REASON_LATE_POSTING)

    # คงคลังคือยอด ณ ตอนดึง จึงถ่ายภาพใหม่ทุกรอบ และไว้ท้ายสุดให้ใกล้เวลาเดียวกับ
    # การเคลื่อนไหวของเดือนปัจจุบันที่เพิ่งดึง
    if SNAPSHOT_KIND in requested:
        day = snapshot_day(today)
        for store in selected:
            work.append((day, store, SNAPSHOT_KIND, REASON_SNAPSHOT))
    return work


def _stale_unit_periods(wanted_periods: set[str],
                        wanted_stores: set[str]) -> list[tuple[str, str, str]]:
    """งวดใบจ่ายที่สอบทานด้วยกติกาหน่วยคนละชุดกับปัจจุบัน หรือไม่รู้ว่าใช้ชุดไหน

    งวดที่ไม่มีบันทึกถูกดึงใหม่ ไม่เดาจากเวลาแก้ไฟล์ — เวลาแก้ไฟล์เปลี่ยนได้โดยที่
    เนื้อหาไม่เปลี่ยน (git checkout) และเนื้อหาเปลี่ยนได้โดยที่เวลาดูเก่า
    """
    rule_map = unit_rules.rules()
    engine = unit_rules.engine_digest()
    codes = warehouse_db.codes_by_period()
    stale = []
    for (period, store), (stored, _pulled_at) in warehouse_db.stored_unit_digests().items():
        if period not in wanted_periods or store not in wanted_stores:
            continue
        current = unit_rules.period_digest(codes.get((period, store), ()), rule_map, engine)
        if not stored:
            stale.append((period, store, REASON_UNITS_UNKNOWN))
        elif stored != current:
            stale.append((period, store, REASON_UNITS))
    return stale


def run(today=None, store_codes: Iterable[str] | None = None,
        kinds: Iterable[str] = ALL_KINDS, years_back: int | None = None,
        limit: int | None = None, pacing: float = PACING_SECONDS,
        progress: Callable[[dict], None] | None = None,
        connection_factory: Callable[[], Any] | None = None,
        recheck_months: int = 0,
        group_key: str = categories.DRUG) -> dict[str, Any]:
    """ดึงตามแผน คืนผลสรุป งวดที่ล้มไม่ทำให้งวดอื่นหยุด

    group_key เลือกขอบเขตหมวด — categories.ALL คือทั้งโรงพยาบาล (ยา เวชภัณฑ์ พัสดุ อื่น ๆ)
    ตาราง periods ไม่มีมิติหมวด การสลับค่านี้จึงเท่ากับดึงทับของเดิม ไม่ใช่ดึงเพิ่ม
    """
    warehouse_db.init_db()
    work = plan(today, store_codes, kinds, years_back, recheck_months=recheck_months,
                group_key=group_key)
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
            outcome = pull_period(connection, period, store, kind, group_key)
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
