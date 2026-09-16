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
CASE_FILE_PATTERN = "substore_requisitions_*.json"
MONTHS_BACK = 12


def load_case(directory: Path) -> dict:
    """ใบเบิกจริงเก็บอยู่ใน diagnostics/ (ไม่ขึ้น git) ไม่ฝังเลขเอกสารจริงไว้ในโค้ด"""
    numbers, codes, dates = [], [], []
    for path in sorted(directory.glob(CASE_FILE_PATTERN)):
        data = json.loads(path.read_text(encoding="utf-8"))
        for document in data.get("documents", []):
            if document.get("requisition_no"):
                numbers.append(str(document["requisition_no"]))
            if document.get("date"):
                dates.append(str(document["date"]))
            for line in document.get("lines", []):
                if line.get("code"):
                    codes.append(str(line["code"]))
    return {"numbers": sorted(set(numbers)), "codes": sorted(set(codes)), "dates": sorted(set(dates))}


def _run(conn, name, sql, params, limit, terms=()):
    check = dict(name=name, sql=sql, status="pending", rows=[])
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params) if params else cursor.execute(sql)
        columns = [c[0] for c in cursor.description]
        raw = cursor.fetchmany(limit + 1)
        check.update(
            status="truncated" if len(raw) > limit else "complete", row_limit=limit,
            rows=[{col: safe_value(col, val, terms) for col, val in zip(columns, row)} for row in raw[:limit]])
    except Exception as exc:
        check.update(status="error", error=error_summary(exc))
    finally:
        if cursor is not None:
            cursor.close()
    return check


def build_queries(case: dict) -> list[tuple]:
    numbers = case["numbers"]
    plain = [compact(n).replace("-", "") for n in numbers]
    codes = case["codes"]
    day = case["dates"][0] if case["dates"] else None
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

    # ถ้าเลขบนกระดาษไม่ใช่ IRNO ต้องหาใบเดียวกันจากเนื้อหาแทน: วันเดียวกัน คลังยา (2)
    # ปลายทางคลังย่อย และรหัสยาที่อยู่บนใบ
    if day and codes:
        code_marks = ", ".join("?" for _ in codes)
        queries.append(("requisition_by_content", f"""
            SELECT TOP 200 iro.IRNO, iro.SUFFIX, iro.DOCUMENTTYPE, iro.STOCKCODE, iro.ISSUEQTY,
                   iro.ISSUEUNITCODE, iro.LOTNO, iro.UPDATESTOCKDATETIME,
                   ir.STORE, ir.CONTRASTORE, ir.DIVISION, ir.DEPT, ir.[SECTION]
            FROM dbo.SKIROUT iro WITH (NOLOCK)
            JOIN dbo.SKIR ir WITH (NOLOCK)
              ON iro.IRNO = ir.IRNO AND iro.DOCUMENTTYPE = ir.DOCUMENTTYPE
            WHERE iro.UPDATESTOCKDATETIME >= ? AND iro.UPDATESTOCKDATETIME < DATEADD(day, 1, ?)
              AND iro.STOCKCODE IN ({code_marks})
            ORDER BY iro.IRNO, iro.SUFFIX""", [day, day] + codes, 200))

    queries.append(("skir_columns", """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME IN ('SKIR', 'SKIROUT')
        ORDER BY TABLE_NAME, ORDINAL_POSITION""", [], 400))

    # คลังย่อยมีคงคลังของตัวเองไหม — ถ้ามีแถวใน STOCK_LOT แปลว่าติดตามรายคลังได้จริง
    queries.append(("stock_rows_by_store", """
        SELECT sl.STORE, COUNT(*) AS LOT_ROWS, COUNT(DISTINCT sl.STOCKCODE) AS ITEMS
        FROM dbo.STOCK_LOT sl WITH (NOLOCK)
        GROUP BY sl.STORE ORDER BY COUNT(*) DESC""", [], 100))

    queries.append(("substore_movement_12m", f"""
        SELECT ir.STORE, ir.CONTRASTORE, ir.DOCUMENTTYPE, COUNT(*) AS SLIPS,
               MAX(ir.ISSUEDATETIME) AS LAST_SLIP
        FROM dbo.SKIR ir WITH (NOLOCK)
        WHERE ir.ISSUEDATETIME >= DATEADD(month, -{MONTHS_BACK}, GETDATE())
        GROUP BY ir.STORE, ir.CONTRASTORE, ir.DOCUMENTTYPE
        ORDER BY COUNT(*) DESC""", [], 300))
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
            report["queries"].append(_run(conn, *item, terms))
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

    content = queries.get("requisition_by_content")
    if content and content["status"] != "error":
        print(f"\n[2] ค้นจากวันที่+รหัสยาบนใบ: {len(content['rows'])} บรรทัด")
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
