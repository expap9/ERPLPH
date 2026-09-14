"""ตามเลขบิลหนึ่งใบเข้าไปใน SSB ว่าเอกสารจ่ายเงินชุดนั้น ระบบบันทึกช่องไหนไว้บ้าง

งานการเงินจ่ายเงินได้เมื่อเอกสารครบชุด ซึ่งผูกกันด้วยเลขบิล (เคสแรก IV-2606063, 14 ก.ย. 2569)
    1. ใบกำกับภาษี / ใบส่งของของบริษัท
    2. ใบตรวจรับพัสดุของกลุ่มงานพัสดุ
    3. บันทึกขออนุมัติจ่ายเงินของกลุ่มงานการเงิน
ก่อนออกแบบหน้าจ่ายเงิน ต้องรู้ว่าช่องไหนมีใน SSB (ดึงมาได้) และช่องไหนมีแต่บนกระดาษ
(เจ้าหน้าที่ต้องกรอก) ตามกติกา "ไม่เดา"

อ่านอย่างเดียว เฉพาะแถวของบิลนี้และใบสั่งซื้อที่อ้างถึง ข้อความยาวและช่องหมายเหตุไม่ถูก
เก็บลงรายงาน เพราะใบตรวจรับวัสดุผู้ป่วยเฉพาะรายมีชื่อผู้ป่วยและ HN เก็บแค่ว่ามีข้อความ
ยาวเท่าไร และมีเลขเอกสารที่ค้นอยู่ในนั้นไหม
"""
import argparse
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

TABLES = ("SKRECV", "SKRECVDTL", "SKPO", "SKPODTL")
PACING_SECONDS = 0.5
MAX_PO_NUMBERS = 20

#: ช่องที่อาจมีชื่อคน HN หรือข้อความอิสระ ไม่เก็บค่า
WITHHELD_COLUMNS = re.compile(
    r"MEMO|REMARK|NOTE|DESC|DETAIL|COMMENT|NAME|PATIENT|HN|CID|CARD|ADDRESS|TEL|PHONE", re.I)
#: ข้อความยาวกว่านี้ไม่เก็บค่า ไม่ว่าชื่อช่องจะเป็นอะไร
MAX_PLAIN_TEXT = 40

_INVOICE_MATCH = "REPLACE(REPLACE(UPPER(rh.SUPPLIERINVOICENO), ' ', ''), '-', '') = ?"


def compact(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def invoice_key(invoice) -> str:
    """เลขบิลที่ใช้เทียบ ไม่สนช่องว่างและขีด เพราะแต่ละคนพิมพ์ต่างกัน"""
    return compact(invoice).replace("-", "")


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def safe_value(column: str, value, terms=()):
    """ค่าที่เก็บลงรายงานได้ ข้อความที่อาจมีข้อมูลผู้ป่วยเหลือแค่ความยาวและเลขเอกสารที่พบ"""
    value = json_value(value)
    if not isinstance(value, str):
        return value
    text = value.strip()
    if WITHHELD_COLUMNS.search(column) or len(text) > MAX_PLAIN_TEXT:
        found = [term for term in terms if compact(term) and compact(term) in compact(text)]
        return {"withheld": True, "length": len(text), "contains": found}
    return text


def trace_queries(po_numbers=()):
    """คำสั่งอ่านทั้งหมด ค่าที่ค้นส่งเป็นพารามิเตอร์ ไม่ต่อลงในคำสั่ง"""
    tables = ", ".join(f"'{name}'" for name in TABLES)
    queries = [
        ("columns", f"""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME IN ({tables})
            ORDER BY TABLE_NAME, ORDINAL_POSITION""", "none", 800),
        ("SKRECV", f"""
            SELECT TOP 50 rh.* FROM dbo.SKRECV rh WITH (NOLOCK)
            WHERE {_INVOICE_MATCH}""", "invoice", 50),
        ("SKRECVDTL", f"""
            SELECT TOP 500 rd.* FROM dbo.SKRECVDTL rd WITH (NOLOCK)
            JOIN dbo.SKRECV rh WITH (NOLOCK) ON rd.RECEIVENO = rh.RECEIVENO AND rd.STORE = rh.STORE
            WHERE {_INVOICE_MATCH}
            ORDER BY rd.RECEIVENO, rd.SUFFIX""", "invoice", 500),
    ]
    if po_numbers:
        marks = ", ".join("?" for _ in po_numbers)
        queries += [
            ("SKPO", f"""
                SELECT TOP 50 po.* FROM dbo.SKPO po WITH (NOLOCK)
                WHERE po.PONO IN ({marks})""", "po", 50),
            ("SKPODTL", f"""
                SELECT TOP 500 pod.* FROM dbo.SKPODTL pod WITH (NOLOCK)
                WHERE pod.PONO IN ({marks})
                ORDER BY pod.PONO, pod.SUFFIX""", "po", 500),
            # ใบสั่งซื้อเดียวส่งหลายงวดได้ บิลอื่นของใบสั่งซื้อเดียวกันบอกว่ายังค้างจ่ายอะไรอีก
            ("receipts_for_po", f"""
                SELECT TOP 500 rd.RECEIVENO, rd.STORE, rd.SUFFIX, rd.STOCKCODE, rd.RECEIVEQTY,
                       rd.FROMPONO, rh.SUPPLIERINVOICENO, rh.SUPPLIERINVOICEDATE, rh.RECEIVEDATETIME
                FROM dbo.SKRECVDTL rd WITH (NOLOCK)
                LEFT JOIN dbo.SKRECV rh WITH (NOLOCK) ON rd.RECEIVENO = rh.RECEIVENO AND rd.STORE = rh.STORE
                WHERE rd.FROMPONO IN ({marks})
                ORDER BY rd.RECEIVENO, rd.SUFFIX""", "po", 500),
        ]
    return queries


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
            rows=[{col: safe_value(col, val, terms) for col, val in zip(columns, row)}
                  for row in raw[:limit]])
        # เลขใบสั่งซื้อใช้ตามต่อ ต้องได้จากค่าจริงก่อนตัดข้อความ
        check["_po_numbers"] = sorted({
            str(dict(zip(columns, row)).get("FROMPONO") or "").strip() for row in raw[:limit]
        } - {"", "*"})
    except Exception as exc:
        check.update(status="error", error=error_summary(exc))
    finally:
        if cursor is not None:
            cursor.close()
    return check


def _numeric(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
        return float(value)
    return None


def matches(field: dict, value) -> bool:
    wanted, kind = field.get("value"), field.get("type", "text")
    if isinstance(value, dict) and value.get("withheld"):
        return kind == "text" and str(wanted) in value.get("contains", [])
    if kind == "amount":
        number = _numeric(value)
        return number is not None and abs(number - float(wanted)) < 0.005
    if kind == "date":
        return isinstance(value, str) and value.startswith(str(wanted))
    text, target = compact(value), compact(wanted)
    return bool(target) and (text == target or (len(target) >= 6 and target in text))


def locate(fields, queries) -> list[dict]:
    """ค่าบนเอกสารแต่ละช่อง พบในตารางและคอลัมน์ไหนของ SSB"""
    located = []
    for field in fields:
        found = sorted({
            f"{query['name']}.{column}"
            for query in queries if query["name"] != "columns"
            for row in query.get("rows", [])
            for column, value in row.items() if matches(field, value)
        })
        located.append({**field, "found_in": found})
    return located


def collect_trace(open_connection, invoice, po_numbers=(), case=None, pacing=PACING_SECONDS):
    fields = list((case or {}).get("fields", []))
    terms = sorted({str(f["value"]) for f in fields if f.get("type", "text") == "text"} | {invoice})
    report = dict(
        report_type="PAYMENT_DOCUMENT_TRACE_V1", invoice=invoice,
        generated_at=datetime.now().isoformat(), connected=False, complete=False,
        purpose="ช่องของเอกสารจ่ายเงินชุดหนึ่ง อยู่ในตารางไหนของ SSB (อ่านอย่างเดียว)",
        queries=[], located=[], errors=[])
    conn = None
    try:
        conn = open_connection()
        conn.timeout = 120
        report["connected"] = True

        def run(queries, params_for):
            for name, sql, param_kind, limit in queries:
                if report["queries"]:
                    time.sleep(pacing)
                report["queries"].append(_run(conn, name, sql, params_for(param_kind), limit, terms))

        key = invoice_key(invoice)
        run(trace_queries(), lambda kind: [key] if kind == "invoice" else None)

        followed = {str(p).strip() for p in po_numbers if str(p).strip()}
        for query in report["queries"]:
            followed |= set(query.get("_po_numbers", []))
        followed = sorted(followed)[:MAX_PO_NUMBERS]
        report["po_numbers"] = followed
        if followed:
            run(trace_queries(followed)[3:], lambda kind: list(followed) if kind == "po" else None)
        report["complete"] = True
    except Exception as exc:
        report["errors"].append(error_summary(exc))
    finally:
        if conn is not None:
            conn.close()
    for query in report["queries"]:
        query.pop("_po_numbers", None)
    report["located"] = locate(fields, report["queries"])
    return report


def case_path(invoice) -> Path:
    safe = re.sub(r"[^A-Za-z0-9-]", "_", str(invoice))
    return ROOT / "diagnostics" / f"payment_case_{safe}.json"


def load_case(path: Path):
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def save_report(report, directory=None):
    directory = Path(directory or ROOT / "diagnostics")
    directory.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9-]", "_", str(report.get("invoice")))
    path = directory / (f"payment_trace_{safe}_" + datetime.now().strftime("%Y%m%d_%H%M%S_")
                        + uuid4().hex[:8] + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path


def print_summary(report):
    queries = {q["name"]: q for q in report.get("queries", [])}
    print("\n" + "=" * 74)
    print(f"   เอกสารจ่ายเงินของบิล {report.get('invoice')} อยู่ตรงไหนใน SSB")
    print("=" * 74)
    for name, title in (("SKRECV", "หัวใบรับ"), ("SKRECVDTL", "บรรทัดใบรับ"), ("SKPO", "หัวใบสั่งซื้อ"),
                        ("SKPODTL", "บรรทัดใบสั่งซื้อ"), ("receipts_for_po", "ใบรับทุกใบของใบสั่งซื้อนี้")):
        query = queries.get(name)
        if query is None:
            print(f"    {title:<28} ไม่ได้ค้น (ไม่พบเลขใบสั่งซื้อ)")
        elif query["status"] == "error":
            print(f"    {title:<28} อ่านไม่สำเร็จ: {query['error']['message']}")
        else:
            print(f"    {title:<28} {len(query['rows']):>4} แถว")
    if report.get("po_numbers"):
        print(f"    ใบสั่งซื้อที่ตามต่อ: {', '.join(report['po_numbers'])}")

    if report.get("located"):
        print("\n    ค่าบนเอกสาร → พบใน SSB")
        current = None
        for field in report["located"]:
            if field.get("document") != current:
                current = field.get("document")
                print(f"\n    [{current}]")
            where = ", ".join(field["found_in"]) if field["found_in"] else "ไม่พบ (มีแต่บนกระดาษ หรือเก็บที่อื่น)"
            print(f"      {field.get('label', ''):<30} {str(field.get('value')):<22} {where}")
    else:
        print("\n    ไม่มีแฟ้มค่าบนเอกสาร (diagnostics/payment_case_<เลขบิล>.json) จึงแสดงแค่แถวที่พบ")

    if report.get("errors"):
        print("\n[!] ข้อผิดพลาด:", report["errors"])
    print("\n" + "=" * 74)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ตามเลขบิลหนึ่งใบเข้าไปใน SSB (อ่านอย่างเดียว)")
    parser.add_argument("invoice", help="เลขบิล / เลขใบกำกับภาษีของบริษัท เช่น IV-2606063")
    parser.add_argument("--po", action="append", default=[], help="เลขใบสั่งซื้อที่รู้อยู่แล้ว (ใส่ซ้ำได้)")
    parser.add_argument("--case", help="แฟ้มค่าบนเอกสาร (ค่าเริ่มต้น diagnostics/payment_case_<เลขบิล>.json)")
    args = parser.parse_args(argv)

    case = load_case(Path(args.case) if args.case else case_path(args.invoice))
    print(f"ตามบิล {args.invoice} ใน SSB (อ่านอย่างเดียว)", flush=True)
    report = collect_trace(lambda: connect(load_config(), timeout=15), args.invoice,
                           po_numbers=args.po or (case or {}).get("po_numbers", []), case=case)
    path = save_report(report)
    print_summary(report)
    print(f"บันทึกผลไว้ที่: {path}")
    return 0 if report.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
