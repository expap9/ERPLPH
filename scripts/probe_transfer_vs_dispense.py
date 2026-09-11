"""แยก "จ่ายให้ผู้ป่วย" ออกจาก "โอนระหว่างคลัง" — ต้องรู้ก่อนรวมยอดทั้งโรงพยาบาล

ถ้าไม่แยก ยาที่คลังยาจ่ายให้ห้องยาผู้ป่วยนอกจะถูกนับสองครั้ง: เป็นการจ่ายของ
คลังต้นทาง และเป็นการรับของคลังปลายทาง ยอดรวมทั้งโรงพยาบาลจะเบิ้ลทันที

ยังไม่ทราบว่าฐานข้อมูลแยกสองอย่างนี้ด้วยฟิลด์ไหน ตัวเลือกที่เป็นไปได้คือ
SKIR.CONTRASTORE (คลังคู่ตรงข้าม) หรือ SKIR.DOCUMENTTYPE (ชนิดเอกสาร)
probe นี้อ่านข้อมูลจริงมาตอบ แทนการเดาแล้วรวมยอดผิด

อ่านอย่างเดียว: SELECT พร้อม WITH (NOLOCK) เท่านั้น ไม่แก้ไขข้อมูลใด ๆ
"""
from datetime import date, datetime
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import stock5_engine

# ใช้การตั้งค่าฐานข้อมูลและตัวสรุปข้อผิดพลาดของ Stock5 โดยไม่แก้ไฟล์เดิม
stock5_engine.install()
from db_extractor import get_connection, load_config  # noqa: E402
from export_mos_schema import error_summary  # noqa: E402


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def get_queries():
    return [
        # 1. ชนิดเอกสารทั้งหมด พร้อมดูว่ามีคลังคู่ตรงข้ามหรือไม่
        ("document_types", """
            SELECT ir.DOCUMENTTYPE,
                   CASE WHEN LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) = ''
                        THEN 'ไม่มีคลังคู่' ELSE 'มีคลังคู่' END AS HAS_CONTRA,
                   COUNT(*) AS SLIPS,
                   COUNT(DISTINCT ir.STORE) AS STORES,
                   MIN(ir.IRNO) AS SAMPLE_IRNO,
                   MIN(ir.REMARKSMEMO) AS SAMPLE_MEMO
            FROM dbo.SKIR ir WITH (NOLOCK)
            WHERE ir.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
            GROUP BY ir.DOCUMENTTYPE,
                     CASE WHEN LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) = ''
                          THEN 'ไม่มีคลังคู่' ELSE 'มีคลังคู่' END
            ORDER BY SLIPS DESC""", (), 100),

        # 2. คู่คลังต้นทาง-ปลายทางที่พบบ่อย ถ้า CONTRASTORE คือคลังจริงแปลว่าโอนภายใน
        ("store_pairs", """
            SELECT ir.STORE AS FROM_STORE,
                   LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) AS TO_STORE,
                   ir.DOCUMENTTYPE,
                   COUNT(*) AS SLIPS,
                   COUNT(DISTINCT iro.STOCKCODE) AS ITEMS
            FROM dbo.SKIR ir WITH (NOLOCK)
            JOIN dbo.SKIROUT iro WITH (NOLOCK)
                 ON iro.IRNO = ir.IRNO AND iro.DOCUMENTTYPE = ir.DOCUMENTTYPE
            WHERE ir.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
              AND LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) <> ''
            GROUP BY ir.STORE, LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))), ir.DOCUMENTTYPE
            ORDER BY SLIPS DESC""", (), 200),

        # 3. หน่วยเบิกที่รับยาไป ถ้าเป็นหอผู้ป่วย/คลินิก แปลว่าจ่ายให้ผู้ป่วย
        ("requesting_units", """
            SELECT TOP 100 ir.STORE, ir.DIVISION, ir.DEPT, ir.[SECTION],
                   ir.DOCUMENTTYPE,
                   CASE WHEN LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) = ''
                        THEN 'ไม่มีคลังคู่' ELSE 'มีคลังคู่' END AS HAS_CONTRA,
                   COUNT(*) AS SLIPS
            FROM dbo.SKIR ir WITH (NOLOCK)
            WHERE ir.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
            GROUP BY ir.STORE, ir.DIVISION, ir.DEPT, ir.[SECTION], ir.DOCUMENTTYPE,
                     CASE WHEN LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) = ''
                          THEN 'ไม่มีคลังคู่' ELSE 'มีคลังคู่' END
            ORDER BY SLIPS DESC""", (), 100),

        # 4. พิสูจน์การนับซ้ำ: การจ่ายที่มีคลังคู่ ไปโผล่เป็นการรับของอีกคลังหรือไม่
        ("transfer_appears_as_receipt", """
            SELECT TOP 50 ir.IRNO, ir.STORE AS FROM_STORE, ir.CONTRASTORE AS TO_STORE,
                   iro.STOCKCODE, iro.ISSUEQTY,
                   rd.RECEIVENO, rd.STORE AS RECEIVED_IN, rd.RECEIVEQTY
            FROM dbo.SKIR ir WITH (NOLOCK)
            JOIN dbo.SKIROUT iro WITH (NOLOCK)
                 ON iro.IRNO = ir.IRNO AND iro.DOCUMENTTYPE = ir.DOCUMENTTYPE
            LEFT JOIN dbo.SKRECVDTL rd WITH (NOLOCK)
                 ON rd.STOCKCODE = iro.STOCKCODE
                AND rd.STORE = ir.CONTRASTORE
                AND CAST(rd.UPDATESTOCKDATETIME AS DATE) = CAST(iro.UPDATESTOCKDATETIME AS DATE)
            WHERE ir.UPDATESTOCKDATETIME >= DATEADD(month, -3, GETDATE())
              AND LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) <> ''
            ORDER BY ir.IRNO DESC""", (), 50),

        # 5. ปริมาณแยกตามชนิด เพื่อประเมินว่ายอดจะเบิ้ลเท่าไรถ้าไม่แยก
        ("volume_by_kind", """
            SELECT CASE WHEN LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) = ''
                        THEN 'จ่ายให้หน่วยเบิก' ELSE 'โอนระหว่างคลัง' END AS KIND,
                   COUNT(*) AS LINES,
                   COUNT(DISTINCT ir.IRNO) AS SLIPS,
                   COUNT(DISTINCT iro.STOCKCODE) AS ITEMS
            FROM dbo.SKIR ir WITH (NOLOCK)
            JOIN dbo.SKIROUT iro WITH (NOLOCK)
                 ON iro.IRNO = ir.IRNO AND iro.DOCUMENTTYPE = ir.DOCUMENTTYPE
            WHERE ir.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
            GROUP BY CASE WHEN LTRIM(RTRIM(ISNULL(ir.CONTRASTORE, ''))) = ''
                          THEN 'จ่ายให้หน่วยเบิก' ELSE 'โอนระหว่างคลัง' END""", (), 20),

        # 6. ใบรับก็อาจมีคลังต้นทาง ถ้ามีก็ระบุการรับจากการโอนได้จากฝั่งรับเช่นกัน
        ("receipt_types", """
            SELECT rh.PURCHASETYPECODE,
                   COUNT(*) AS SLIPS,
                   COUNT(DISTINCT rh.STORE) AS STORES,
                   MIN(rh.RECEIVENO) AS SAMPLE_RCV,
                   MIN(rh.SUPPLIERCODE) AS SAMPLE_SUPPLIER
            FROM dbo.SKRECV rh WITH (NOLOCK)
            WHERE rh.UPDATESTOCKDATETIME >= DATEADD(month, -12, GETDATE())
            GROUP BY rh.PURCHASETYPECODE
            ORDER BY SLIPS DESC""", (), 60),
    ]


def collect_report(connect, progress=None):
    report = dict(
        report_type="TRANSFER_VS_DISPENSE_V1",
        generated_at=datetime.now().isoformat(),
        connected=False,
        complete=False,
        purpose="แยกการจ่ายให้ผู้ป่วยออกจากการโอนระหว่างคลัง ก่อนรวมยอดทั้งโรงพยาบาล (อ่านอย่างเดียว)",
        queries=[],
        errors=[],
    )
    conn = None
    try:
        conn = connect()
        conn.timeout = 180
        report["connected"] = True
        for name, sql, params, limit in get_queries():
            check = dict(name=name, sql=sql, parameters=list(params), status="pending", rows=[])
            cursor = None
            try:
                cursor = conn.cursor()
                cursor.execute(sql, *params)
                columns = [c[0] for c in cursor.description]
                rows = cursor.fetchmany(limit + 1)
                check.update(
                    status="truncated" if len(rows) > limit else "complete",
                    row_limit=limit,
                    rows=[dict(zip(columns, (json_value(v) for v in r))) for r in rows[:limit]],
                )
            except Exception as exc:
                check.update(status="error", error=error_summary(exc))
            finally:
                if cursor is not None:
                    cursor.close()
            report["queries"].append(check)
            if progress:
                progress(name, check["status"], len(check["rows"]))
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
    path = directory / ("transfer_vs_dispense_" + datetime.now().strftime("%Y%m%d_%H%M%S_")
                        + uuid4().hex[:8] + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path


def print_summary(report):
    rows = {q["name"]: q["rows"] for q in report.get("queries", [])}
    print("\n" + "=" * 74)
    print("   แยกการจ่ายให้ผู้ป่วย ออกจากการโอนระหว่างคลัง")
    print("=" * 74)

    volume = rows.get("volume_by_kind", [])
    if volume:
        total = sum(int(r.get("LINES") or 0) for r in volume)
        print("\n[1] ปริมาณ 12 เดือน แยกตามว่ามีคลังคู่ตรงข้ามหรือไม่")
        for r in volume:
            lines = int(r.get("LINES") or 0)
            print("    %-20s %10s บรรทัด  %8s ใบ  %6s รายการ  %.1f%%" % (
                r.get("KIND"), f"{lines:,}", f"{int(r.get('SLIPS') or 0):,}",
                f"{int(r.get('ITEMS') or 0):,}", lines / total * 100 if total else 0))

    types = rows.get("document_types", [])
    if types:
        print("\n[2] ชนิดเอกสารจ่าย (DOCUMENTTYPE)")
        print("    %-12s %-14s %9s %7s  %s" % ("ชนิด", "คลังคู่", "ใบ", "คลัง", "ตัวอย่างหมายเหตุ"))
        for r in types[:15]:
            print("    %-12s %-14s %9s %7s  %s" % (
                r.get("DOCUMENTTYPE"), r.get("HAS_CONTRA"), f"{int(r.get('SLIPS') or 0):,}",
                r.get("STORES"), str(r.get("SAMPLE_MEMO") or "")[:30]))

    pairs = rows.get("store_pairs", [])
    if pairs:
        print("\n[3] คู่คลังที่โอนกันบ่อย (ถ้า TO_STORE เป็นรหัสคลังจริง = โอนภายใน)")
        for r in pairs[:12]:
            print("    %-6s -> %-6s  ชนิด %-6s %8s ใบ  %5s รายการ" % (
                r.get("FROM_STORE"), r.get("TO_STORE"), r.get("DOCUMENTTYPE"),
                f"{int(r.get('SLIPS') or 0):,}", f"{int(r.get('ITEMS') or 0):,}"))

    proof = rows.get("transfer_appears_as_receipt", [])
    if proof:
        matched = [r for r in proof if r.get("RECEIVENO")]
        print("\n[4] พิสูจน์การนับซ้ำ: สุ่ม %d การโอน พบใบรับคู่กัน %d รายการ"
              % (len(proof), len(matched)))
        for r in matched[:5]:
            print("    จ่าย %s คลัง %s -> %s  %s x%s   รับเป็น %s ที่คลัง %s x%s" % (
                r.get("IRNO"), r.get("FROM_STORE"), r.get("TO_STORE"), r.get("STOCKCODE"),
                r.get("ISSUEQTY"), r.get("RECEIVENO"), r.get("RECEIVED_IN"), r.get("RECEIVEQTY")))
        if matched:
            print("    -> ยืนยันว่าถ้านับรวมทั้งสองฝั่ง ยอดจะเบิ้ล")

    receipts = rows.get("receipt_types", [])
    if receipts:
        print("\n[5] ชนิดการรับ (PURCHASETYPECODE) — ใช้แยกรับจากการซื้อกับรับจากการโอน")
        for r in receipts[:12]:
            print("    %-10s %9s ใบ  %3s คลัง  ผู้ขายตัวอย่าง %s" % (
                r.get("PURCHASETYPECODE"), f"{int(r.get('SLIPS') or 0):,}",
                r.get("STORES"), r.get("SAMPLE_SUPPLIER")))

    if report.get("errors"):
        print("\n[!] ข้อผิดพลาด:", report["errors"])
    print("\n" + "=" * 74)


def main():
    print("สำรวจการแยกจ่ายผู้ป่วย/โอนภายใน (อ่านอย่างเดียว ไม่แก้ไขฐานข้อมูล)", flush=True)
    report = collect_report(
        lambda: get_connection(load_config(), timeout=15),
        progress=lambda name, status, count: print(f"  {name}: {status}, {count} แถว", flush=True),
    )
    path = save_report(report)
    print_summary(report)
    print(f"บันทึกผลไว้ที่: {path}")
    return 0 if report.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
