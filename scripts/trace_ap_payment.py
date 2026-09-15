"""บิลที่รู้เลขอยู่แล้ว ฝ่ายบัญชีตั้งหนี้และจ่ายเงินแล้วหรือยัง — อ่านจากบัญชีเจ้าหนี้ของ SSB

สำรวจ 15 ก.ย. 2569 (check_procurement_finance.bat) พบว่าตารางบัญชีเจ้าหนี้ที่มีข้อมูลจริงอยู่ใน
SSBBACKOFFICE (APINV 59,467 · APINVPAY 57,073 · APCHQ 163,237 แถว) ไม่ใช่ SSBGL ซึ่งว่างทั้งหมด
ส่วน SSBGL48 และ SSBWEL มีข้อมูลเช่นกันแต่ยังไม่รู้ว่าเป็นของปีไหน/กองทุนไหน และ TP_AP_INVBAL
(ตารางอายุหนี้) มี 0 แถว ใช้ไม่ได้

ตัวนี้ตอบ 3 ข้อด้วยเคสจริง ก่อนตัดสินว่า ERPLPH จะอ่านบัญชีเจ้าหนี้หรือไม่:
    1. ตารางไหนยังใช้งานอยู่ (วันที่ใบแจ้งหนี้/วันที่จ่ายล่าสุดของแต่ละฐาน)
    2. บิลที่รู้เลขแล้ว (diagnostics/payment_case_*.json และ payment_batch_*.json) พบในบัญชี
       เจ้าหนี้ไหม ผูกด้วยเลขใบแจ้งหนี้ เลขใบรับ หรือเลขใบสั่งซื้อ และมีรายการจ่ายเงินหรือยัง
    3. ค่าบนบันทึกขออนุมัติจ่ายเงินที่หาไม่พบใน SSBSTOCK (เช่น รหัส BG ภาษีหัก ณ ที่จ่าย) อยู่ช่องไหน

อ่านอย่างเดียว ชื่อฐานข้อมูลมาจากรายการคงที่ในไฟล์นี้ ค่าที่ค้นส่งเป็นพารามิเตอร์ ข้อความยาวและช่อง
ชื่อ/หมายเหตุไม่ถูกเก็บค่า (ใช้กติกาเดียวกับ trace_payment_document.py)
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
from trace_payment_document import compact, invoice_key, json_value, locate, safe_value  # noqa: E402

DATABASES = ("SSBBACKOFFICE", "SSBGL48", "SSBWEL")
PACING_SECONDS = 0.5
MAX_PAIRS = 200
KEY_COLUMNS = {"APCODE", "INVOICENO", "SUPPLIERINVOICENO", "RECEIVENO", "PONO", "CHEQUENO",
               "PAYMENTDATETIME", "INVOICEDATETIME"}
_INVOICE_KEY_SQL = "REPLACE(REPLACE(UPPER(i.SUPPLIERINVOICENO), ' ', ''), '-', '')"


def load_known_cases(directory: Path) -> list[dict]:
    """เคสจริงที่เก็บไว้แล้ว (ไม่ขึ้น git) — ไม่ฝังเลขเอกสารจริงไว้ในโค้ด"""
    cases = []
    for path in sorted(directory.glob("payment_case_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(dict(
            source=path.name, invoice=str(data.get("invoice") or path.stem[len("payment_case_"):]),
            receive_numbers=[], po_numbers=[str(p) for p in data.get("po_numbers", [])],
            fields=list(data.get("fields", []))))
    for path in sorted(directory.glob("payment_batch_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for line in data.get("lines", []):
            cases.append(dict(
                source=path.name, invoice=str(line.get("invoice_no") or ""),
                receive_numbers=[str(line["receive_no"])] if line.get("receive_no") else [],
                po_numbers=[str(line["po_no"])] if line.get("po_no") else [],
                amount=line.get("amount"), fields=[]))
    return cases


def freshness_queries(db: str) -> list[tuple]:
    return [
        (f"{db}.APINV:freshness", f"""
            SELECT BUDGETYEAR, COUNT(*) AS INVOICES,
                   MIN(INVOICEDATETIME) AS FIRST_INVOICE, MAX(INVOICEDATETIME) AS LAST_INVOICE
            FROM {db}.dbo.APINV WITH (NOLOCK)
            GROUP BY BUDGETYEAR ORDER BY BUDGETYEAR""", [], 100),
        (f"{db}.APINVPAY:freshness", f"""
            SELECT BUDGETYEAR, COUNT(*) AS PAYMENTS,
                   MIN(PAYMENTDATETIME) AS FIRST_PAYMENT, MAX(PAYMENTDATETIME) AS LAST_PAYMENT
            FROM {db}.dbo.APINVPAY WITH (NOLOCK)
            GROUP BY BUDGETYEAR ORDER BY BUDGETYEAR""", [], 100),
    ]


def invoice_match_query(db, invoice_keys, receive_numbers, po_numbers):
    clauses, params = [], []
    for expression, values in ((_INVOICE_KEY_SQL, invoice_keys), ("i.RECEIVENO", receive_numbers),
                               ("i.PONO", po_numbers)):
        if values:
            clauses.append(f"{expression} IN ({', '.join('?' for _ in values)})")
            params += list(values)
    if not clauses:
        return None
    return (f"{db}.APINV", f"""
        SELECT TOP 200 i.* FROM {db}.dbo.APINV i WITH (NOLOCK)
        WHERE {' OR '.join(clauses)}
        ORDER BY i.INVOICEDATETIME""", params, 200)


def payment_query(db, pairs):
    pairs = list(pairs)[:MAX_PAIRS]
    if not pairs:
        return None
    where = " OR ".join("(p.APCODE = ? AND p.INVOICENO = ?)" for _ in pairs)
    return (f"{db}.APINVPAY", f"""
        SELECT TOP 500 p.* FROM {db}.dbo.APINVPAY p WITH (NOLOCK)
        WHERE {where}
        ORDER BY p.PAYMENTDATETIME""", [value for pair in pairs for value in pair], 500)


def cheque_query(db, cheques):
    cheques = list(cheques)[:MAX_PAIRS]
    if not cheques:
        return None
    return (f"{db}.APCHQ", f"""
        SELECT TOP 200 c.* FROM {db}.dbo.APCHQ c WITH (NOLOCK)
        WHERE c.CHEQUENO IN ({', '.join('?' for _ in cheques)})""", cheques, 200)


def _key_value(value):
    value = json_value(value)
    return value.strip() if isinstance(value, str) else value


def _run(conn, name, sql, params, limit, terms):
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
        # ค่าที่ใช้ตามต่อและสรุปผลต้องได้จากค่าจริง ก่อนกติกาตัดข้อความ
        check["_keys"] = [{col.upper(): _key_value(val) for col, val in zip(columns, row)
                           if col.upper() in KEY_COLUMNS} for row in raw[:limit]]
    except Exception as exc:
        check.update(status="error", error=error_summary(exc))
    finally:
        if cursor is not None:
            cursor.close()
    return check


def case_status(cases, queries) -> list[dict]:
    """คำตอบต่อบิล — แยกให้เห็นว่าจับคู่ด้วยเลขอะไร เพราะจับด้วยเลขใบสั่งซื้ออย่างเดียวอาจเป็นบิลอื่น
    ของใบสั่งซื้อเดียวกัน (ใบสั่งซื้อหนึ่งใบส่งมอบได้หลายงวด)"""
    by_name = {q["name"]: q for q in queries}
    results = []
    for case in cases:
        wanted_invoice = invoice_key(case.get("invoice"))
        receives = {compact(x) for x in case.get("receive_numbers", [])} - {""}
        pos = {compact(x) for x in case.get("po_numbers", [])} - {""}
        entry = dict(source=case.get("source"), invoice=case.get("invoice"),
                     receive_numbers=case.get("receive_numbers", []),
                     po_numbers=case.get("po_numbers", []), by_database={})
        strong = paid = False
        for db in DATABASES:
            invoices = []
            for row in by_name.get(f"{db}.APINV", {}).get("_keys", []):
                basis = [label for label, hit in (
                    ("เลขใบแจ้งหนี้", bool(wanted_invoice) and invoice_key(row.get("SUPPLIERINVOICENO")) == wanted_invoice),
                    ("เลขใบรับ", compact(row.get("RECEIVENO")) in receives),
                    ("เลขใบสั่งซื้อ", compact(row.get("PONO")) in pos)) if hit]
                if basis:
                    invoices.append({**row, "matched_by": basis})
            payments = [row for row in by_name.get(f"{db}.APINVPAY", {}).get("_keys", [])
                        if any(compact(row.get("APCODE")) == compact(inv.get("APCODE"))
                               and compact(row.get("INVOICENO")) == compact(inv.get("INVOICENO"))
                               for inv in invoices if inv["matched_by"] != ["เลขใบสั่งซื้อ"])]
            if invoices:
                entry["by_database"][db] = {"invoices": invoices, "payments": payments}
                strong |= any(inv["matched_by"] != ["เลขใบสั่งซื้อ"] for inv in invoices)
                paid |= bool(payments)
        if paid:
            entry["answer"] = "พบรายการจ่ายเงิน"
        elif strong:
            entry["answer"] = "พบใบแจ้งหนี้ ไม่พบรายการจ่ายเงิน"
        elif entry["by_database"]:
            entry["answer"] = "พบเฉพาะจากเลขใบสั่งซื้อ (อาจเป็นบิลอื่นของใบสั่งซื้อเดียวกัน)"
        else:
            entry["answer"] = "ไม่พบในบัญชีเจ้าหนี้"
        results.append(entry)
    return results


def collect_report(open_connection, cases, pacing=PACING_SECONDS):
    fields = [field for case in cases for field in case.get("fields", [])]
    receives = sorted({str(x).strip() for c in cases for x in c.get("receive_numbers", [])} - {""})
    pos = sorted({str(x).strip() for c in cases for x in c.get("po_numbers", [])} - {""})
    invoice_keys = sorted({invoice_key(c.get("invoice")) for c in cases} - {""})
    terms = sorted({str(f["value"]) for f in fields if f.get("type", "text") == "text"}
                   | {str(c.get("invoice")) for c in cases if c.get("invoice")} | set(receives) | set(pos))
    report = dict(
        report_type="AP_PAYMENT_TRACE_V1", generated_at=datetime.now().isoformat(),
        connected=False, complete=False, databases=list(DATABASES), cases_loaded=len(cases),
        purpose="บิลที่รู้เลขแล้ว ตั้งหนี้และจ่ายเงินแล้วหรือยัง ในบัญชีเจ้าหนี้ของ SSB (อ่านอย่างเดียว)",
        queries=[], cases=[], located=[], errors=[])
    conn = None
    try:
        conn = open_connection()
        conn.timeout = 120
        report["connected"] = True

        def run(item):
            if item is None:
                return {}
            if report["queries"]:
                time.sleep(pacing)
            check = _run(conn, *item, terms)
            report["queries"].append(check)
            return check

        for db in DATABASES:
            for item in freshness_queries(db):
                run(item)
            invoices = run(invoice_match_query(db, invoice_keys, receives, pos))
            pairs = sorted({(row["APCODE"], row["INVOICENO"]) for row in invoices.get("_keys", [])
                            if row.get("APCODE") and row.get("INVOICENO")})
            payments = run(payment_query(db, pairs))
            run(cheque_query(db, sorted({row["CHEQUENO"] for row in payments.get("_keys", [])
                                         if row.get("CHEQUENO")})))
        report["complete"] = True
    except Exception as exc:
        report["errors"].append(error_summary(exc))
    finally:
        if conn is not None:
            conn.close()
    report["cases"] = case_status(cases, report["queries"])
    report["located"] = locate(fields, [q for q in report["queries"] if not q["name"].endswith(":freshness")])
    for query in report["queries"]:
        query.pop("_keys", None)
    return report


def save_report(report, directory=None):
    directory = Path(directory or ROOT / "diagnostics")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("ap_payment_trace_" + datetime.now().strftime("%Y%m%d_%H%M%S_")
                        + uuid4().hex[:8] + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path


def print_summary(report):
    queries = {q["name"]: q for q in report.get("queries", [])}
    print("\n" + "=" * 74)
    print("   บิลเหล่านี้ ตั้งหนี้และจ่ายเงินแล้วหรือยัง (บัญชีเจ้าหนี้ SSB)")
    print("=" * 74)
    for db in DATABASES:
        print(f"\n[{db}]")
        for table, stamp in (("APINV", "LAST_INVOICE"), ("APINVPAY", "LAST_PAYMENT")):
            query = queries.get(f"{db}.{table}:freshness")
            if query is None:
                continue
            if query["status"] == "error":
                print(f"    {table:<9} อ่านไม่สำเร็จ: {query['error']['message']}")
                continue
            latest = max((str(r.get(stamp) or "") for r in query["rows"]), default="")
            years = ", ".join(str(r.get("BUDGETYEAR")) for r in query["rows"][-3:])
            print(f"    {table:<9} ล่าสุด {latest[:10] or '-'}  ปีงบล่าสุด: {years or '-'}")
    print("\n    บิล → คำตอบ")
    for case in report.get("cases", []):
        paid_on = sorted({str(p.get("PAYMENTDATETIME") or "")[:10]
                          for db in case["by_database"].values() for p in db["payments"]} - {""})
        suffix = f"  วันที่จ่าย {', '.join(paid_on)}" if paid_on else ""
        print(f"      {str(case.get('invoice')):<20} {case['answer']}{suffix}")
    found = [f for f in report.get("located", []) if f["found_in"]]
    if found:
        print("\n    ค่าบนเอกสาร → พบในบัญชีเจ้าหนี้")
        for field in found:
            print(f"      {field.get('label', ''):<30} {str(field.get('value')):<18} {', '.join(field['found_in'])}")
    if report.get("errors"):
        print("\n[!] ข้อผิดพลาด:", report["errors"])
    print("\n" + "=" * 74)


def main(argv=None):
    parser = argparse.ArgumentParser(description="บิลที่รู้เลข จ่ายเงินแล้วหรือยัง (อ่านอย่างเดียว)")
    parser.add_argument("--invoice", action="append", default=[], help="เลขใบแจ้งหนี้เพิ่มเติม (ใส่ซ้ำได้)")
    parser.add_argument("--receive", action="append", default=[], help="เลขใบรับเพิ่มเติม")
    parser.add_argument("--po", action="append", default=[], help="เลขใบสั่งซื้อเพิ่มเติม")
    args = parser.parse_args(argv)

    cases = load_known_cases(ROOT / "diagnostics")
    cases += [dict(source="command line", invoice=i, receive_numbers=[], po_numbers=[], fields=[])
              for i in args.invoice]
    if args.receive or args.po:
        cases.append(dict(source="command line", invoice="", receive_numbers=args.receive,
                          po_numbers=args.po, fields=[]))
    if not cases:
        print("ไม่พบเคสใน diagnostics/ และไม่ได้ระบุเลขเอกสาร")
        return 1
    print(f"ตรวจ {len(cases)} บิลในบัญชีเจ้าหนี้ ({', '.join(DATABASES)}) อ่านอย่างเดียว", flush=True)
    report = collect_report(lambda: connect(load_config(), timeout=15), cases)
    path = save_report(report)
    print_summary(report)
    print(f"บันทึกผลไว้ที่: {path}")
    return 0 if report.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
