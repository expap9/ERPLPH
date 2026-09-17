"""คลังย่อยเบิกของจากคลังใหญ่ — ใบเบิกหนึ่งใบอยู่ตรงไหนใน SSB และคลังย่อยมียอดคงคลังไหม

ผู้ใช้ส่งใบเบิกจริง 2 ใบ (16 ก.ย. 2569) จาก "หน่วยจ่ายยาผู้ป่วยใน" ถึง "คลังยาและเวชภัณฑ์"
พิมพ์จาก SSB เอง (`Skirform_ใบขอเบิก4สี.rpt`) เลขที่ `02-1332-69` และ `02-1333-69`

ที่รู้แล้วจากการสำรวจ 11 ก.ย. 2569 (`transfer_vs_dispense`): ชนิดเอกสาร 35 = โอนระหว่างคลัง
(มีคลังคู่ `SKIR.CONTRASTORE`) · ชนิด 32 = จ่ายให้หน่วยเบิก · คู่ `2 → I2` มี 7,254 ใบใน 12 เดือน
ซึ่งตรงกับทิศทางของเอกสารชุดนี้

ตัวนี้ตอบสิ่งที่ยังไม่รู้ ก่อนออกแบบหน้าคลังย่อย:
    1. เลขบนกระดาษ (`02-1332-69`) คือ `SKIR.IRNO` ตรง ๆ หรือเป็นเลขคนละชุด
    2. ถ้าไม่ตรง ใบเดียวกันอยู่แถวไหน (ค้นจากวันที่ + คลัง + รหัสยาบนใบ)
    3. `SKIR`/`SKIROUT` แยก "จำนวนที่ขอเบิก" กับ "จำนวนที่จ่ายจริง" ไหม (ช่องจ่ายบนฟอร์มว่างอยู่)
    4. SSB เก็บยอดคงคลังรายคลังย่อยจริงไหม (`STOCK_LOT` มีแถวของคลัง `I2` ฯลฯ หรือเปล่า)
    5. คลังย่อยที่ยังเคลื่อนไหวจริงใน 12 เดือนมีคลังไหนบ้าง (ใช้ปรับ `app/stores.py`)

อ่านอย่างเดียว ค่าที่ค้นส่งเป็นพารามิเตอร์ ช่องหมายเหตุ/ชื่อ ไม่ถูกเก็บค่า
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from database import connect, error_summary, load_config  # noqa: E402
from trace_payment_document import compact, json_value, safe_value  # noqa: E402

PACING_SECONDS = 0.5
CASE_FILE_PATTERN = "substore_*.json"
NUMBER_FIELDS = ("requisition_no", "supply_requisition_no")
MONTHS_BACK = 12
SALES_DAYS_BACK = 60

#: ช่องที่เป็นข้อมูลอ้างอิงขององค์กร ไม่ใช่ข้อมูลคน — ต้องประกาศเป็นรายคำสั่ง ไม่งั้นกฎกัน
#: ชื่อคนที่มองหาคำว่า NAME จะไปซ่อน TABLE_NAME กับ THAINAME ด้วย (เจอตอนรัน 17 ก.ย. 2569)
SCHEMA_COLUMNS = ("TABLE_NAME", "COLUMN_NAME", "TABLE_CATALOG")
DEPARTMENT_COLUMNS = ("THAINAME", "ENGNAME", "SHORTNAME")

#: วันที่และคลังสำหรับ "กระทบยอดวันเดียว" — เลือก 8 ก.ย. 2569 ที่คลัง I2 เพราะจากภาพหน้าจอ
#: วันนั้นมีครบทั้ง 5 ทาง: เอกสารขาย (20260908-I2-I/S1) · เบิกจ่ายให้หน่วยเบิก (I769090xx) ·
#: โอนออก (I7T6909005/S1, I7T6909007/S1) · รับของภายใน (WG69-2680 ถึง WG69-2694) ·
#: และรับจากคลังใหญ่ จึงใช้ตรวจว่าเอกสารแต่ละชนิดกระทบสต๊อกอย่างไร ซ้ำกันหรือไม่
RECONCILE_STORE = "I2"
RECONCILE_DAY = "2026-09-08"


def load_case(directory: Path) -> dict:
    """ใบเบิก/ใบโอนจริงเก็บอยู่ใน diagnostics/ (ไม่ขึ้น git) ไม่ฝังเลขเอกสารจริงไว้ในโค้ด

    ฟอร์มมีช่องเลขที่สองช่อง ("เลขที่ใบเบิกหรือใบส่งคืน" กับ "เลขที่ใบเบิกพัสดุ") แต่ละใบ
    กรอกคนละช่อง จึงเก็บทั้งสองแบบ และรวมรหัสยาแยกตามวันที่ของใบนั้น เพราะใบที่ได้มา
    ห่างกันสิบปี ใช้วันเดียวค้นแทนกันไม่ได้
    """
    numbers, codes, by_date = [], [], {}
    for path in sorted(directory.glob(CASE_FILE_PATTERN)):
        data = json.loads(path.read_text(encoding="utf-8"))
        for document in data.get("documents", []):
            for field in NUMBER_FIELDS:
                value = str(document.get(field) or "").strip()
                if value and not value.startswith("("):
                    numbers.append(value)
            day = str(document.get("date") or "").strip()
            for line in document.get("lines", []):
                if not line.get("code"):
                    continue
                code = str(line["code"])
                codes.append(code)
                if day:
                    by_date.setdefault(day, set()).add(code)
    return {
        "numbers": sorted(set(numbers)),
        "codes": sorted(set(codes)),
        "dates": sorted(by_date),
        "codes_by_date": {day: sorted(values) for day, values in sorted(by_date.items())},
        "reconcile_store": RECONCILE_STORE,
        "reconcile_day": RECONCILE_DAY,
    }


def _run(conn, name, sql, params, limit, terms=(), plain_columns=()):
    check = dict(name=name, sql=sql, status="pending", rows=[])
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params) if params else cursor.execute(sql)
        columns = [c[0] for c in cursor.description]
        raw = cursor.fetchmany(limit + 1)
        check.update(
            status="truncated" if len(raw) > limit else "complete", row_limit=limit,
            rows=[{col: safe_value(col, val, terms, plain_columns) for col, val in zip(columns, row)}
                  for row in raw[:limit]])
    except Exception as exc:
        check.update(status="error", error=error_summary(exc))
    finally:
        if cursor is not None:
            cursor.close()
    return check


def build_queries(case: dict) -> list[tuple]:
    numbers = case["numbers"]
    plain = [compact(n).replace("-", "") for n in numbers]
    queries = []

    if numbers:
        marks = ", ".join("?" for _ in numbers)
        plain_marks = ", ".join("?" for _ in plain)
        queries.append(("requisition_header_by_number", f"""
            SELECT TOP 20 ir.* FROM dbo.SKIR ir WITH (NOLOCK)
            WHERE ir.IRNO IN ({marks})
               OR REPLACE(REPLACE(UPPER(ir.IRNO), ' ', ''), '-', '') IN ({plain_marks})""",
            numbers + plain, 20))
        queries.append(("requisition_lines_by_number", f"""
            SELECT TOP 200 iro.* FROM dbo.SKIROUT iro WITH (NOLOCK)
            WHERE iro.IRNO IN ({marks})
               OR REPLACE(REPLACE(UPPER(iro.IRNO), ' ', ''), '-', '') IN ({plain_marks})
            ORDER BY iro.IRNO, iro.SUFFIX""", numbers + plain, 200))

    # ถ้าเลขบนกระดาษไม่ใช่ IRNO ต้องหาใบเดียวกันจากเนื้อหาแทน: วันเดียวกันกับใบนั้น
    # และรหัสยาที่อยู่บนใบนั้น (แยกคำสั่งตามวัน เพราะใบที่ได้มาห่างกันสิบปี)
    for day, day_codes in case.get("codes_by_date", {}).items():
        code_marks = ", ".join("?" for _ in day_codes)
        queries.append((f"requisition_by_content_{day}", f"""
            SELECT TOP 200 iro.IRNO, iro.SUFFIX, iro.DOCUMENTTYPE, iro.STOCKCODE, iro.ISSUEQTY,
                   iro.ISSUEUNITCODE, iro.LOTNO, iro.UPDATESTOCKDATETIME,
                   ir.STORE, ir.CONTRASTORE, ir.DIVISION, ir.DEPT, ir.[SECTION]
            FROM dbo.SKIROUT iro WITH (NOLOCK)
            JOIN dbo.SKIR ir WITH (NOLOCK)
              ON iro.IRNO = ir.IRNO AND iro.DOCUMENTTYPE = ir.DOCUMENTTYPE
            WHERE iro.UPDATESTOCKDATETIME >= ? AND iro.UPDATESTOCKDATETIME < DATEADD(day, 1, ?)
              AND iro.STOCKCODE IN ({code_marks})
            ORDER BY iro.IRNO, iro.SUFFIX""", [day, day] + list(day_codes), 200))

    queries.append(("skir_columns", """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME IN ('SKIR', 'SKIROUT')
        ORDER BY TABLE_NAME, ORDINAL_POSITION""", [], 400, SCHEMA_COLUMNS))

    # คลังย่อยมีคงคลังของตัวเองไหม — ถ้ามีแถวใน STOCK_LOT แปลว่าติดตามรายคลังได้จริง
    queries.append(("stock_rows_by_store", """
        SELECT sl.STORE, COUNT(*) AS LOT_ROWS, COUNT(DISTINCT sl.STOCKCODE) AS ITEMS
        FROM dbo.STOCK_LOT sl WITH (NOLOCK)
        GROUP BY sl.STORE ORDER BY COUNT(*) DESC""", [], 100))

    # ชื่อคอลัมน์วันที่ยืนยันจาก probe_transfer_vs_dispense.py ที่รันผ่านจริงแล้ว
    # (UPDATESTOCKDATETIME ไม่ใช่ ISSUEDATETIME)
    queries.append(("substore_movement_12m", f"""
        SELECT ir.STORE, ir.CONTRASTORE, ir.DOCUMENTTYPE, COUNT(*) AS SLIPS,
               MAX(ir.UPDATESTOCKDATETIME) AS LAST_SLIP
        FROM dbo.SKIR ir WITH (NOLOCK)
        WHERE ir.UPDATESTOCKDATETIME >= DATEADD(month, -{MONTHS_BACK}, GETDATE())
        GROUP BY ir.STORE, ir.CONTRASTORE, ir.DOCUMENTTYPE
        ORDER BY COUNT(*) DESC""", [], 300))

    # งาน (ก) "วันไหนคลังย่อยยังตัดขายไม่ได้" — เอกสารที่ระบบสร้างตอน import ยอดใช้ยา
    # ใช้เลขรูปแบบ YYYYMMDD-คลัง-I/S<ลำดับ> (เห็นจากหน้าจอจริง 16 ก.ย. 2569 เช่น
    # 20260907-I2-I/S1) จึงกรองด้วยรูปแบบเลขได้โดยไม่ต้องรู้ชื่อคอลัมน์รหัสรายการก่อน
    queries.append((f"sales_cut_by_store_day_{SALES_DAYS_BACK}d", f"""
        SELECT ir.STORE, CAST(ir.UPDATESTOCKDATETIME AS DATE) AS CUT_DAY,
               COUNT(*) AS SLIPS, MIN(ir.IRNO) AS SAMPLE_IRNO
        FROM dbo.SKIR ir WITH (NOLOCK)
        WHERE ir.IRNO LIKE '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-%'
          AND ir.UPDATESTOCKDATETIME >= DATEADD(day, -{SALES_DAYS_BACK}, GETDATE())
        GROUP BY ir.STORE, CAST(ir.UPDATESTOCKDATETIME AS DATE)
        ORDER BY ir.STORE, CAST(ir.UPDATESTOCKDATETIME AS DATE)""", [], 1000))

    # ทุกคอลัมน์ของเอกสารกลุ่มนี้ เพื่อหาว่าช่อง "วันที่อนุมัติ" กับ "รหัสรายการ" บนหน้าจอ
    # คือคอลัมน์ไหนจริง ๆ (ยังไม่รู้ จึงดึงทั้งแถวมาดู ไม่เดาชื่อ)
    queries.append(("sales_documents_sample", f"""
        SELECT TOP 30 ir.* FROM dbo.SKIR ir WITH (NOLOCK)
        WHERE ir.IRNO LIKE '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-%'
          AND ir.UPDATESTOCKDATETIME >= DATEADD(day, -{SALES_DAYS_BACK}, GETDATE())
        ORDER BY ir.UPDATESTOCKDATETIME DESC""", [], 30))

    # ตารางรหัสหน่วยงาน — ภาพหน้าจอ 17 ก.ย. 2569 เห็นโครง 3 ชั้น กลุ่มงาน[203] > งาน[02] > ส่วนย่อย
    # ต้องหาให้เจอว่าเก็บที่ไหน ไม่งั้นรายงานรายแผนกจะมีแต่รหัส ไม่มีชื่อ
    queries.append(("department_tables_by_column", """
        SELECT TABLE_CATALOG, TABLE_NAME, COUNT(*) AS MATCHED_COLUMNS
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE COLUMN_NAME IN ('DIVISION', 'DEPT', 'SECTION', 'DIVISIONCODE', 'DEPTCODE')
        GROUP BY TABLE_CATALOG, TABLE_NAME
        HAVING COUNT(*) >= 2
        ORDER BY COUNT(*) DESC, TABLE_NAME""", [], 200, SCHEMA_COLUMNS))

    # ตารางรหัสหน่วยงานอยู่ใน SYSCONFIG — ยืนยันจากการรัน 17 ก.ย. 2569
    #   CTRLCODE 10028 = กลุ่มงาน (DIVISION) รหัส 3 หลัก เช่น 208 = กลุ่มงานเภสัชกรรม
    #   CTRLCODE 10029 = งาน (DEPT) ช่อง CODE รวมรหัสแม่ไว้ด้วย รูปแบบ '203   02'
    #   CTRLCODE 10030 = ส่วนย่อย (SECTION) — ยังไม่ยืนยัน ดึงมาดูพร้อมกัน
    # SSBHOSPITAL กับ SSBSTOCK ให้ชื่อไม่เหมือนกันในรหัสเดียวกัน จึงต้องดึงทั้งสองฐานมาเทียบ
    for database in ("SSBHOSPITAL", "SSBSTOCK"):
        queries.append((f"department_codes:{database}", f"""
            SELECT CTRLCODE, CODE, LTRIM(RTRIM(THAINAME)) AS THAINAME
            FROM {database}.dbo.SYSCONFIG WITH (NOLOCK)
            WHERE CTRLCODE IN (10028, 10029, 10030)
            ORDER BY CTRLCODE, CODE""", [], 1500, DEPARTMENT_COLUMNS))

    # ใบเบิกกรอกรหัสหน่วยงานมาครบแค่ไหน และหน่วยไหนเบิกมากที่สุด — นี่คือหน้า "รายแผนก"
    queries.append(("requisition_by_department_12m", """
        SELECT ir.DIVISION, ir.DEPT, ir.SECTION, ir.DOCUMENTTYPE,
               COUNT(*) AS SLIPS, COUNT(DISTINCT ir.STORE) AS STORES,
               MAX(ir.UPDATESTOCKDATETIME) AS LAST_SLIP
        FROM dbo.SKIR ir WITH (NOLOCK)
        WHERE ir.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
        GROUP BY ir.DIVISION, ir.DEPT, ir.SECTION, ir.DOCUMENTTYPE
        ORDER BY COUNT(*) DESC""", [], 600))

    # ชื่อหมวดสินค้าของโรงพยาบาลเอง — ผู้ใช้สั่ง 17 ก.ย. 2569 ให้ดูได้ "ทุกประเภท ยา อาหาร พัสดุ
    # งานจ้าง งานก่อสร้าง วัสดุคอมพิวเตอร์" ตอนนี้ชื่อกลุ่มในหน้าจออนุมานจากชื่อรายการตัวอย่าง
    # ต้องหาตารางชื่อหมวดจริงแทน — ตารางรหัสใน SYSCONFIG ชุดไหนมีครบทั้ง 03, 7, 9, 11, 4
    # (ค่า MAINCATEGORY ที่พบจริง) น่าจะเป็นตารางชื่อหมวด
    for database in ("SSBSTOCK", "SSBHOSPITAL"):
        queries.append((f"category_names_in_sysconfig:{database}", f"""
            SELECT CTRLCODE, CODE, LTRIM(RTRIM(THAINAME)) AS THAINAME
            FROM {database}.dbo.SYSCONFIG WITH (NOLOCK)
            WHERE CTRLCODE IN (
                SELECT CTRLCODE FROM {database}.dbo.SYSCONFIG WITH (NOLOCK)
                WHERE LTRIM(RTRIM(CODE)) IN ('03', '7', '9', '11', '4')
                GROUP BY CTRLCODE HAVING COUNT(DISTINCT LTRIM(RTRIM(CODE))) = 5)
            ORDER BY CTRLCODE, CODE""", [], 800, DEPARTMENT_COLUMNS))

    # ทะเบียนรายการมีช่องจัดกลุ่มชั้นรองไหม (หมวดย่อย / ประเภท) — ใช้แยกงานก่อสร้างออกจากงานจ้าง
    queries.append(("stock_master_grouping_columns", """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'STOCK_MASTER'
          AND (COLUMN_NAME LIKE '%CATEGORY%' OR COLUMN_NAME LIKE '%GROUP%'
               OR COLUMN_NAME LIKE '%TYPE%' OR COLUMN_NAME LIKE '%CLASS%')
        ORDER BY ORDINAL_POSITION""", [], 100, SCHEMA_COLUMNS))

    # งานก่อสร้างกับวัสดุคอมพิวเตอร์อยู่หมวดไหน และซื้อ/จ้างไปเท่าไรใน 12 เดือน
    queries.append(("named_work_by_category_12m", """
        SELECT kind.KIND, sm.MAINCATEGORY, COUNT(DISTINCT sm.STOCKCODE) AS ITEMS,
               COUNT(DISTINCT rd.RECEIVENO) AS RECEIPTS, SUM(rd.RECEIVEAMT) AS AMOUNT,
               MIN(rd.STORE) AS FIRST_STORE, MAX(rd.STORE) AS LAST_STORE
        FROM dbo.STOCK_MASTER sm WITH (NOLOCK)
        CROSS APPLY (SELECT CASE
            WHEN sm.ENGLISHNAME LIKE N'%ก่อสร้าง%' THEN 'construction'
            WHEN sm.ENGLISHNAME LIKE N'%ปรับปรุง%' THEN 'renovation'
            WHEN sm.ENGLISHNAME LIKE N'%คอมพิวเตอร์%' OR sm.ENGLISHNAME LIKE '%computer%'
                 OR sm.ENGLISHNAME LIKE N'%หมึก%' OR sm.ENGLISHNAME LIKE '%toner%' THEN 'computer'
            WHEN sm.ENGLISHNAME LIKE N'%จ้าง%' THEN 'hire'
            END AS KIND) kind
        LEFT JOIN dbo.SKRECVDTL rd WITH (NOLOCK)
               ON rd.STOCKCODE = sm.STOCKCODE
              AND rd.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
        WHERE kind.KIND IS NOT NULL
        GROUP BY kind.KIND, sm.MAINCATEGORY
        ORDER BY kind.KIND, SUM(rd.RECEIVEAMT) DESC""", [], 200))

    store, day = case.get("reconcile_store"), case.get("reconcile_day")
    if store and day:
        # กระทบยอดวันเดียว — ตอบคำถามว่าเอกสาร "ขาย" รวมอะไรไว้แล้วบ้าง
        # SKMOVE คือบัญชีเคลื่อนไหวสต๊อกตัวจริง ถ้าเอกสารชนิดไหนไม่ปรากฏที่นี่ แปลว่าไม่กระทบสต๊อก
        queries.append(("reconcile_documents", """
            SELECT TOP 200 ir.* FROM dbo.SKIR ir WITH (NOLOCK)
            WHERE ir.STORE = ?
              AND ir.UPDATESTOCKDATETIME >= ? AND ir.UPDATESTOCKDATETIME < DATEADD(day, 1, ?)
            ORDER BY ir.UPDATESTOCKDATETIME""", [store, day, day], 200))

        queries.append(("reconcile_movement_by_document", """
            SELECT mv.DOCUMENTNO, mv.DOCUMENTTYPE, mv.ADDSTOCK, mv.NATUREISOUT,
                   mv.STOCKACTCODE, COUNT(*) AS LINES,
                   COUNT(DISTINCT mv.STOCKCODE) AS ITEMS, SUM(mv.UPDATEQTY) AS QTY
            FROM dbo.SKMOVE mv WITH (NOLOCK)
            WHERE mv.STORE = ?
              AND mv.UPDATESTOCKDATETIME >= ? AND mv.UPDATESTOCKDATETIME < DATEADD(day, 1, ?)
            GROUP BY mv.DOCUMENTNO, mv.DOCUMENTTYPE, mv.ADDSTOCK, mv.NATUREISOUT, mv.STOCKACTCODE
            ORDER BY mv.DOCUMENTNO""", [store, day, day], 500))

        # ยาตัวเดียวกันที่เคลื่อนไหวหลายเอกสารในวันเดียว = จุดที่ต้องดูว่านับซ้ำหรือไม่
        queries.append(("reconcile_items_touched_twice", """
            SELECT TOP 100 mv.STOCKCODE, COUNT(DISTINCT mv.DOCUMENTNO) AS DOCS,
                   SUM(CASE WHEN mv.ADDSTOCK = 1 THEN mv.UPDATEQTY ELSE 0 END) AS QTY_IN,
                   SUM(CASE WHEN mv.ADDSTOCK = 1 THEN 0 ELSE mv.UPDATEQTY END) AS QTY_OUT,
                   MIN(mv.DOCUMENTNO) AS FIRST_DOC, MAX(mv.DOCUMENTNO) AS LAST_DOC
            FROM dbo.SKMOVE mv WITH (NOLOCK)
            WHERE mv.STORE = ?
              AND mv.UPDATESTOCKDATETIME >= ? AND mv.UPDATESTOCKDATETIME < DATEADD(day, 1, ?)
            GROUP BY mv.STOCKCODE
            HAVING COUNT(DISTINCT mv.DOCUMENTNO) > 1
            ORDER BY COUNT(DISTINCT mv.DOCUMENTNO) DESC""", [store, day, day], 100))
    return queries


def collect_report(open_connection, case: dict, pacing=PACING_SECONDS) -> dict:
    terms = sorted(set(case["numbers"]) | set(case["codes"]))
    report = dict(
        report_type="SUBSTORE_REQUISITION_V1", generated_at=datetime.now().isoformat(),
        connected=False, complete=False, case=case,
        purpose="ใบเบิกของคลังย่อยอยู่ตรงไหนใน SSB และคลังย่อยมียอดคงคลังของตัวเองไหม (อ่านอย่างเดียว)",
        queries=[], errors=[])
    conn = None
    try:
        conn = open_connection()
        conn.timeout = 120
        report["connected"] = True
        for item in build_queries(case):
            if report["queries"]:
                time.sleep(pacing)
            name, sql, params, limit, *reference = item
            report["queries"].append(
                _run(conn, name, sql, params, limit, terms, reference[0] if reference else ()))
        report["complete"] = True
    except Exception as exc:
        report["errors"].append(error_summary(exc))
    finally:
        if conn is not None:
            conn.close()
    return report


def save_report(report, directory=None):
    directory = Path(directory or ROOT / "diagnostics")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("substore_requisition_" + datetime.now().strftime("%Y%m%d_%H%M%S_")
                        + uuid4().hex[:8] + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path


def print_summary(report):
    queries = {q["name"]: q for q in report.get("queries", [])}
    print("\n" + "=" * 74)
    print("   ใบเบิกของคลังย่อย อยู่ตรงไหนใน SSB")
    print("=" * 74)

    header = queries.get("requisition_header_by_number")
    if header and header["status"] != "error":
        if header["rows"]:
            print(f"\n[1] เลขบนกระดาษคือ SKIR.IRNO ตรง ๆ — พบ {len(header['rows'])} ใบ")
            for row in header["rows"]:
                print(f"    IRNO={row.get('IRNO')} ชนิด={row.get('DOCUMENTTYPE')} "
                      f"คลัง={row.get('STORE')} คลังคู่={row.get('CONTRASTORE')} "
                      f"หน่วยเบิก={row.get('DIVISION')}-{row.get('DEPT')}-{row.get('SECTION')}")
        else:
            print("\n[1] ไม่พบเลขบนกระดาษใน SKIR.IRNO — เป็นเลขคนละชุด ดูข้อ 2")

    for name, content in queries.items():
        if not name.startswith("requisition_by_content_") or content["status"] == "error":
            continue
        print(f"\n[2] ค้นจากรหัสยาบนใบ วันที่ {name.rsplit('_', 1)[-1]}: {len(content['rows'])} บรรทัด")
        seen = {}
        for row in content["rows"]:
            key = (row.get("IRNO"), row.get("STORE"), row.get("CONTRASTORE"), row.get("DOCUMENTTYPE"))
            seen[key] = seen.get(key, 0) + 1
        for (irno, store, contra, doctype), lines in sorted(seen.items(), key=lambda x: -x[1])[:12]:
            print(f"    IRNO={irno} คลัง={store} -> {contra} ชนิด={doctype} {lines} บรรทัด")

    columns = queries.get("skir_columns")
    if columns and columns["status"] != "error":
        wanted = [r for r in columns["rows"]
                  if any(k in str(r.get("COLUMN_NAME", "")).upper()
                         for k in ("QTY", "APPROVE", "REQUEST", "ISSUE"))]
        print(f"\n[3] ช่องจำนวน/อนุมัติใน SKIR, SKIROUT ({len(wanted)} ช่อง)")
        for row in wanted[:20]:
            print(f"    {row.get('TABLE_NAME')}.{row.get('COLUMN_NAME')} ({row.get('DATA_TYPE')})")

    stock = queries.get("stock_rows_by_store")
    if stock and stock["status"] != "error":
        print(f"\n[4] ยอดคงคลังรายคลัง (STOCK_LOT) — {len(stock['rows'])} คลัง")
        for row in stock["rows"][:15]:
            print(f"    คลัง {str(row.get('STORE')):<5} {row.get('LOT_ROWS'):>8} แถว  "
                  f"{row.get('ITEMS'):>6} รายการ")

    movement = queries.get("substore_movement_12m")
    if movement and movement["status"] != "error":
        print(f"\n[5] ใบเบิก/โอน 12 เดือน แยกคลัง (10 อันดับแรกจาก {len(movement['rows'])})")
        for row in movement["rows"][:10]:
            print(f"    {str(row.get('STORE')):<5} -> {str(row.get('CONTRASTORE') or '-'):<5} "
                  f"ชนิด {row.get('DOCUMENTTYPE')}  {row.get('SLIPS'):>7} ใบ  "
                  f"ล่าสุด {str(row.get('LAST_SLIP') or '')[:10]}")

    cut = queries.get(f"sales_cut_by_store_day_{SALES_DAYS_BACK}d")
    if cut and cut["status"] != "error":
        by_store: dict[str, list[str]] = {}
        for row in cut["rows"]:
            by_store.setdefault(str(row.get("STORE") or "?"), []).append(str(row.get("CUT_DAY") or "")[:10])
        today = datetime.now().date()
        print(f"\n[6] วันที่คลังย่อยตัดขาย (เอกสาร import) ย้อนหลัง {SALES_DAYS_BACK} วัน — {len(by_store)} คลัง")
        for store, days in sorted(by_store.items()):
            latest = max(days) if days else ""
            try:
                behind = (today - datetime.strptime(latest, "%Y-%m-%d").date()).days
            except ValueError:
                behind = None
            line = f"    คลัง {store:<5} ตัดแล้ว {len(set(days)):>3} วัน  ล่าสุด {latest or '-'}"
            if behind is not None:
                line += f"  (ค้าง {behind} วัน)" + ("  <-- ค้าง" if behind >= 2 else "")
            print(line)

    sample = queries.get("sales_documents_sample")
    if sample and sample["status"] == "complete" and sample["rows"]:
        columns = list(sample["rows"][0].keys())
        date_like = [c for c in columns if "DATE" in c.upper() or "TIME" in c.upper()]
        print(f"\n[7] คอลัมน์ของเอกสาร import ({len(columns)} ช่อง) — ช่องวันที่ที่มี: {', '.join(date_like)}")
        first = sample["rows"][0]
        for name in date_like:
            print(f"    {name} = {first.get(name)}")

    moves = queries.get("reconcile_movement_by_document")
    if moves and moves["status"] != "error":
        groups: dict[tuple, dict] = {}
        for row in moves["rows"]:
            number = str(row.get("DOCUMENTNO") or "")
            kind = ("ขาย (เลขวันที่)" if number[:8].isdigit()
                    else "รับของภายใน (WG)" if number.startswith("WG")
                    else "โอนออก (I7T)" if number.startswith("I7T")
                    else "เบิกจ่าย (I7)" if number.startswith("I7")
                    else "อื่น ๆ")
            key = (kind, row.get("ADDSTOCK"), row.get("STOCKACTCODE"))
            entry = groups.setdefault(key, {"docs": set(), "lines": 0, "qty": 0.0})
            entry["docs"].add(number)
            entry["lines"] += row.get("LINES") or 0
            entry["qty"] += float(row.get("QTY") or 0)
        print(f"\n[8] กระทบยอดวันเดียว คลัง {RECONCILE_STORE} วันที่ {RECONCILE_DAY}")
        print(f"    {'ชนิดเอกสาร':<20}{'เข้า/ออก':>9}{'ACT':>6}{'ใบ':>6}{'บรรทัด':>8}{'จำนวนรวม':>14}")
        for (kind, add, act), entry in sorted(groups.items()):
            direction = "เข้า" if add == 1 else "ออก"
            print(f"    {kind:<20}{direction:>9}{str(act or '-'):>6}"
                  f"{len(entry['docs']):>6}{entry['lines']:>8}{entry['qty']:>14,.2f}")

    twice = queries.get("reconcile_items_touched_twice")
    if twice and twice["status"] != "error":
        print(f"\n[9] ยาที่เคลื่อนไหวหลายเอกสารในวันเดียว ({len(twice['rows'])} รายการ) — จุดที่ต้องดูว่านับซ้ำไหม")
        for row in twice["rows"][:10]:
            print(f"    {str(row.get('STOCKCODE')):<10} {row.get('DOCS')} ใบ  "
                  f"เข้า {float(row.get('QTY_IN') or 0):>10,.2f}  ออก {float(row.get('QTY_OUT') or 0):>10,.2f}")

    tables = queries.get("department_tables_by_column")
    if tables and tables["status"] != "error":
        print(f"\n[10] ตารางที่มีคอลัมน์รหัสหน่วยงานตั้งแต่ 2 ช่องขึ้นไป ({len(tables['rows'])} ตาราง)")
        for row in tables["rows"][:15]:
            print(f"    {row.get('TABLE_CATALOG')}.{row.get('TABLE_NAME')} "
                  f"({row.get('MATCHED_COLUMNS')} ช่อง)")

    for name, found in queries.items():
        if not name.startswith("department_codes:") or found["status"] == "error":
            continue
        database = name.split(":", 1)[1]
        levels = {}
        for row in found["rows"]:
            levels.setdefault(row.get("CTRLCODE"), []).append(row)
        print(f"\n[11] ตารางรหัสหน่วยงานใน {database}.SYSCONFIG ({len(found['rows'])} แถว)")
        for ctrlcode in sorted(levels, key=lambda code: (code is None, code)):
            rows = levels[ctrlcode]
            label = {10028: "กลุ่มงาน", 10029: "งาน", 10030: "ส่วนย่อย"}.get(ctrlcode, "ไม่ทราบชั้น")
            print(f"    CTRLCODE {ctrlcode} = {label} ({len(rows)} รหัส)")
            for row in rows[:6]:
                print(f"        [{row.get('CODE')}] {row.get('THAINAME')}")

    by_department = queries.get("requisition_by_department_12m")
    if by_department and by_department["status"] != "error":
        rows = by_department["rows"]
        filled = sum(1 for row in rows if str(row.get("DIVISION") or "").strip())
        print(f"\n[12] ใบเบิก 12 เดือนแยกตามหน่วยงาน ({len(rows)} กลุ่ม "
              f"กรอกรหัสกลุ่มงานมา {filled} กลุ่ม)")
        for row in rows[:15]:
            code = "-".join(str(row.get(part) or "").strip() or "?"
                            for part in ("DIVISION", "DEPT", "SECTION"))
            print(f"    {code:<14} ชนิด {row.get('DOCUMENTTYPE')}  "
                  f"{row.get('SLIPS'):>7,} ใบ  {row.get('STORES')} คลัง")

    for query in report.get("queries", []):
        if query["status"] == "error":
            print(f"\n[!] {query['name']}: {query['error']['message']}")
    print("\n" + "=" * 74)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ใบเบิกคลังย่อยใน SSB (อ่านอย่างเดียว)")
    parser.add_argument("--number", action="append", default=[], help="เลขที่ใบเบิกเพิ่มเติม")
    args = parser.parse_args(argv)

    case = load_case(ROOT / "diagnostics")
    case["numbers"] = sorted(set(case["numbers"]) | set(args.number))
    if not case["numbers"] and not case["codes"]:
        print(f"ไม่พบแฟ้มใบเบิกใน diagnostics/ ({CASE_FILE_PATTERN}) และไม่ได้ระบุเลขที่ใบเบิก")
        return 1
    print(f"ตรวจใบเบิก {len(case['numbers'])} ใบ ({len(case['codes'])} รหัสยา) อ่านอย่างเดียว", flush=True)
    report = collect_report(lambda: connect(load_config(), timeout=15), case)
    path = save_report(report)
    print_summary(report)
    print(f"บันทึกผลไว้ที่: {path}")
    return 0 if report.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
