"""หาว่าข้อมูลที่ผู้บริหารถาม อยู่ตรงไหนในฐานข้อมูลโรงพยาบาล — อ่านโครงสร้างเป็นหลัก

ตัวชี้วัดที่ผู้บริหารต้องการ (11 ก.ย. 2569) ส่วนใหญ่คำนวณได้จากข้อมูลคลังที่ดึงอยู่แล้ว
แต่มีสามเรื่องที่ยังไม่รู้ว่าข้อมูลอยู่ที่ไหน
    1. ซื้อแล้วจ่ายเงินหรือยัง   — น่าจะอยู่ในระบบบัญชี (SSBBACKOFFICE หรือฐานอื่น)
    2. ครุภัณฑ์                 — ทะเบียนครุภัณฑ์อาจแยกจากคลัง
    3. จ้างเหมา ก่อสร้าง งานช่าง  — คำสั่งใบรับของ Stock5 ระบุว่า "ไม่มีเลขที่คุมสัญญา
                                   ในระบบต้นทางแล้ว" จึงต้องหาที่อยู่ใหม่
และต้องรู้รูปแบบเลขใบรับของทุกคลัง เพราะตัวกรอง RECEIVENO LIKE 'M%' ของ Stock5
ใช้ได้กับคลัง 2 เท่านั้น

อ่านอย่างเดียว: รายชื่อฐานข้อมูล ตาราง คอลัมน์ จำนวนแถว และยอดรวมรายคลัง×หมวด
ไม่อ่านข้อมูลรายบรรทัด ไม่แก้ไขข้อมูลใด ๆ
"""
from datetime import date, datetime
from decimal import Decimal
import json
import math
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from database import connect, error_summary, load_config  # noqa: E402
import categories  # noqa: E402

#: คำในชื่อคอลัมน์ที่บ่งบอกการเงิน สัญญา หรือครุภัณฑ์ — ใช้คัดคอลัมน์ที่น่าสนใจ
KEYWORDS = ("PAY", "PAID", "INVOICE", "VOUCHER", "VCH", "CHEQUE", "CHQ", "BILL",
            "APCODE", "RECEIVENO", "PONO", "CONTRACT", "ASSET", "DEPREC", "WARRANT",
            "BUDGET", "DEBT", "CREDIT", "TAXINV", "INSTAL")

#: ตารางคลังที่ต้องรู้ทุกคอลัมน์ เพื่อหาช่องเลขใบแจ้งหนี้ การจ่ายเงิน หรือสัญญา
STOCK_TABLES = ("SKPO", "SKPODTL", "SKRECV", "SKRECVDTL", "SKTMPPO", "SKTMPPODTL")

_SAFE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
PACING_SECONDS = 0.5


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _keyword_filter(column: str) -> str:
    return " OR ".join(f"{column} LIKE '%{word}%'" for word in KEYWORDS)


def safe_database_name(name: object) -> str | None:
    """ชื่อฐานข้อมูลที่จะใส่ในคำสั่ง ต้องเป็นตัวอักษรธรรมดาเท่านั้น"""
    text = str(name or "")
    return text if _SAFE_NAME.fullmatch(text) else None


def base_queries():
    tables = ", ".join(f"'{name}'" for name in STOCK_TABLES)
    return [
        ("current_database", "SELECT DB_NAME() AS DATABASE_NAME", 1),

        ("databases", """
            SELECT name AS DATABASE_NAME, state_desc AS STATE,
                   HAS_DBACCESS(name) AS CAN_READ
            FROM sys.databases WHERE database_id > 4 ORDER BY name""", 200),

        ("categories", """
            SELECT MAINCATEGORY, COUNT(*) AS ITEMS,
                   MIN(LTRIM(RTRIM(ENGLISHNAME))) AS SAMPLE_NAME
            FROM dbo.STOCK_MASTER WITH (NOLOCK)
            GROUP BY MAINCATEGORY ORDER BY ITEMS DESC""", 100),

        # ใบสั่งซื้อ 12 เดือน รายคลัง×หมวด — ครุภัณฑ์และจ้างเหมาอยู่ในระบบคลังด้วยหรือไม่
        ("po_by_store_category", """
            SELECT po.STORE, sm.MAINCATEGORY,
                   COUNT(*) AS LINES, COUNT(DISTINCT po.PONO) AS POS,
                   SUM(pod.REQUESTQTY * pod.LOTPRICE) AS AMOUNT,
                   MIN(po.PONO) AS FIRST_PONO, MAX(po.PONO) AS LAST_PONO
            FROM dbo.SKPODTL pod WITH (NOLOCK)
            JOIN dbo.SKPO po WITH (NOLOCK) ON pod.PONO = po.PONO
            LEFT JOIN dbo.STOCK_MASTER sm WITH (NOLOCK) ON pod.STOCKCODE = sm.STOCKCODE
            WHERE po.ISSUEDATETIME >= DATEADD(month, -12, GETDATE())
            GROUP BY po.STORE, sm.MAINCATEGORY
            ORDER BY AMOUNT DESC""", 400),

        # ใบรับ 12 เดือน รายคลัง×หมวด พร้อมรูปแบบเลขใบรับ แทนตัวกรอง 'M%' ของคลัง 2
        ("receipt_by_store_category", """
            SELECT rd.STORE, sm.MAINCATEGORY,
                   COUNT(*) AS LINES, COUNT(DISTINCT rd.RECEIVENO) AS RECEIPTS,
                   SUM(rd.RECEIVEAMT) AS AMOUNT,
                   MIN(rd.RECEIVENO) AS FIRST_RECEIVENO, MAX(rd.RECEIVENO) AS LAST_RECEIVENO,
                   SUM(CASE WHEN rd.RECEIVENO LIKE 'M%' THEN 1 ELSE 0 END) AS LINES_NUMBERED_M,
                   SUM(CASE WHEN ISNULL(rd.FROMPONO, '') IN ('', '*') THEN 1 ELSE 0 END) AS LINES_WITHOUT_PO
            FROM dbo.SKRECVDTL rd WITH (NOLOCK)
            LEFT JOIN dbo.STOCK_MASTER sm WITH (NOLOCK) ON rd.STOCKCODE = sm.STOCKCODE
            WHERE rd.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
            GROUP BY rd.STORE, sm.MAINCATEGORY
            ORDER BY AMOUNT DESC""", 400),

        ("po_status", """
            SELECT POSTATUS, COUNT(*) AS POS, MIN(PONO) AS SAMPLE_PONO,
                   SUM(CASE WHEN APPROVEDATETIME IS NULL THEN 1 ELSE 0 END) AS NOT_APPROVED
            FROM dbo.SKPO WITH (NOLOCK)
            WHERE ISSUEDATETIME >= DATEADD(month, -12, GETDATE())
            GROUP BY POSTATUS ORDER BY POS DESC""", 50),

        ("stock_table_columns", f"""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME IN ({tables})
            ORDER BY TABLE_NAME, ORDINAL_POSITION""", 800),
    ]


def database_queries(database: str):
    """โครงสร้างของฐานข้อมูลหนึ่ง: ตารางพร้อมจำนวนแถว และคอลัมน์ที่ชื่อเข้าข่าย"""
    name = safe_database_name(database)
    if name is None:
        raise ValueError(f"ชื่อฐานข้อมูลไม่ปลอดภัย: {database!r}")
    return [
        (f"tables:{name}", f"""
            SELECT t.name AS TABLE_NAME, SUM(p.rows) AS ROW_COUNT
            FROM [{name}].sys.tables t
            JOIN [{name}].sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
            GROUP BY t.name ORDER BY t.name""", 4000),
        (f"keyword_columns:{name}", f"""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM [{name}].INFORMATION_SCHEMA.COLUMNS
            WHERE {_keyword_filter('COLUMN_NAME')}
            ORDER BY TABLE_NAME, COLUMN_NAME""", 3000),
    ]


def _run(conn, name, sql, limit):
    check = dict(name=name, sql=sql, status="pending", rows=[])
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchmany(limit + 1)
        check.update(
            status="truncated" if len(rows) > limit else "complete", row_limit=limit,
            rows=[dict(zip(columns, (json_value(v) for v in r))) for r in rows[:limit]])
    except Exception as exc:
        check.update(status="error", error=error_summary(exc))
    finally:
        if cursor is not None:
            cursor.close()
    return check


def collect_report(open_connection, progress=None, pacing=PACING_SECONDS):
    report = dict(
        report_type="PROCUREMENT_FINANCE_SCOPE_V1",
        generated_at=datetime.now().isoformat(),
        connected=False, complete=False,
        purpose="หาที่อยู่ของสถานะการจ่ายเงิน ครุภัณฑ์ และสัญญาจ้าง (อ่านโครงสร้างอย่างเดียว)",
        queries=[], errors=[])
    conn = None
    try:
        conn = open_connection()
        conn.timeout = 180
        report["connected"] = True

        def run(name, sql, limit):
            if report["queries"]:
                time.sleep(pacing)
            check = _run(conn, name, sql, limit)
            report["queries"].append(check)
            if progress:
                progress(name, check["status"], len(check["rows"]))
            return check

        for name, sql, limit in base_queries():
            run(name, sql, limit)

        # ฐานข้อมูลอื่นที่บัญชีนี้อ่านได้ — ระบบบัญชีและครุภัณฑ์น่าจะอยู่ในนั้น
        listed = next(q for q in report["queries"] if q["name"] == "databases")
        for row in listed["rows"]:
            name = safe_database_name(row.get("DATABASE_NAME"))
            if name and row.get("CAN_READ") in (1, "1", True):
                for query_name, sql, limit in database_queries(name):
                    run(query_name, sql, limit)
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
    path = directory / ("procurement_finance_" + datetime.now().strftime("%Y%m%d_%H%M%S_")
                        + uuid4().hex[:8] + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def print_summary(report):
    queries = {q["name"]: q for q in report.get("queries", [])}
    print("\n" + "=" * 74)
    print("   ข้อมูลจัดซื้อ การเงิน ครุภัณฑ์ และสัญญาจ้าง อยู่ที่ไหน")
    print("=" * 74)

    databases = queries.get("databases", {}).get("rows", [])
    if databases:
        print("\n[1] ฐานข้อมูลบนเซิร์ฟเวอร์")
        for row in databases:
            tables = queries.get(f"tables:{row.get('DATABASE_NAME')}", {})
            state = ("อ่านได้ %s ตาราง" % f"{len(tables.get('rows', [])):,}"
                     if tables.get("status") in ("complete", "truncated")
                     else "อ่านไม่ได้" if row.get("CAN_READ") not in (1, "1", True) else "ผิดพลาด")
            print(f"    {str(row.get('DATABASE_NAME')):<28} {state}")

    for kind, title in (("po_by_store_category", "ใบสั่งซื้อ"), ("receipt_by_store_category", "ใบรับ")):
        rows = queries.get(kind, {}).get("rows", [])
        if not rows:
            continue
        by_group: dict[str, list[float]] = {}
        for row in rows:
            bucket = by_group.setdefault(categories.group_of(row.get("MAINCATEGORY")), [0, 0.0])
            bucket[0] += _number(row.get("LINES"))
            bucket[1] += _number(row.get("AMOUNT"))
        print(f"\n[2] {title} 12 เดือน แยก 4 กลุ่ม")
        for group, (lines, amount) in sorted(by_group.items(), key=lambda item: -item[1][1]):
            print(f"    {categories.group_name(group):<18} {lines:>10,.0f} บรรทัด  ฿{amount:>18,.2f}")

    receipts = queries.get("receipt_by_store_category", {}).get("rows", [])
    if receipts:
        print("\n[3] เลขใบรับรายคลัง (ตัวกรอง 'M%' ของ Stock5 ครอบคลุมแค่ไหน)")
        stores: dict[str, list] = {}
        for row in receipts:
            entry = stores.setdefault(row.get("STORE"), [0, 0, row.get("FIRST_RECEIVENO")])
            entry[0] += _number(row.get("LINES"))
            entry[1] += _number(row.get("LINES_NUMBERED_M"))
        for store, (lines, numbered_m, sample) in sorted(stores.items(), key=lambda s: -s[1][0])[:20]:
            print(f"    {str(store):<6} {lines:>8,.0f} บรรทัด  เลข M {numbered_m / lines * 100 if lines else 0:5.1f}%  ตัวอย่าง {sample}")

    print("\n[4] คอลัมน์ที่ชื่อเกี่ยวกับการเงิน/สัญญา/ครุภัณฑ์ (ตารางที่พบบ่อยสุด)")
    for name, check in queries.items():
        if not name.startswith("keyword_columns:"):
            continue
        counts: dict[str, int] = {}
        for row in check.get("rows", []):
            counts[row.get("TABLE_NAME")] = counts.get(row.get("TABLE_NAME"), 0) + 1
        top = sorted(counts.items(), key=lambda item: -item[1])[:8]
        if top:
            print(f"    {name.split(':', 1)[1]}: " + ", ".join(f"{t}({n})" for t, n in top))

    failed = [q["name"] for q in report.get("queries", []) if q["status"] == "error"]
    if failed:
        print(f"\n[!] อ่านไม่สำเร็จ {len(failed)} คำสั่ง: {', '.join(failed[:8])}")
    if report.get("errors"):
        print("\n[!] ข้อผิดพลาด:", report["errors"])
    print("\n" + "=" * 74)


def main():
    print("สำรวจที่อยู่ข้อมูลจัดซื้อ การเงิน ครุภัณฑ์ สัญญาจ้าง (อ่านโครงสร้างอย่างเดียว)", flush=True)
    report = collect_report(
        lambda: connect(load_config(), timeout=15),
        progress=lambda name, status, count: print(f"  {name}: {status}, {count} แถว", flush=True))
    path = save_report(report)
    print_summary(report)
    print(f"บันทึกผลไว้ที่: {path}")
    return 0 if report.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
