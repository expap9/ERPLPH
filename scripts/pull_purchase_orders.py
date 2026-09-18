"""ดึงใบสั่งซื้อจริง (SKPO/SKPODTL) เข้าฐานข้อมูลของ ERPLPH

ตอบคำถามที่ยังไม่มีข้อมูลจริงมาก่อน (หน้า "คลังใหญ่ & จัดซื้อ" เคยแสดงยอด PO
สั่งซื้อและสถานะจ่ายเงินที่คำนวณเดาขึ้นเอง เพราะยังไม่เคยดึงตารางนี้)

    python scripts\\pull_purchase_orders.py                 ย้อนหลัง 18 เดือน (ค่าเริ่มต้น)
    python scripts\\pull_purchase_orders.py --months 24     ย้อนหลัง 24 เดือน
    python scripts\\pull_purchase_orders.py --plan-only     นับจำนวนแถวโดยไม่ดึงจริง
    python scripts\\pull_purchase_orders.py --limit 500     ทดลองไม่เกิน 500 แถว

ที่มาของข้อมูล (สำรวจแล้ว 15 ก.ย. 2569 — ดู diagnostics/procurement_finance_*.json,
scripts/probe_procurement_finance.py):
    - SKPO / SKPODTL อยู่ใน SSBSTOCK (ฐานเดียวกับที่ ERPLPH ดึงคลัง/ใบรับ/ใบเบิกอยู่แล้ว)
    - ชื่อผู้ขาย (APMASTER) อยู่ใน SSBBACKOFFICE.dbo.APMASTER ตามที่ Stock5 เองก็ใช้ตารางนี้
      (ดู config/table_mappings.json ของ Stock5 — เชื่อมด้วย APMASTER.APCODE = SKPO.SUPPLIERCODE)

ยังไม่มีในรอบนี้: สถานะจ่ายเงินจริง (AP/บัญชี) — APMASTER เป็นแค่ทะเบียนชื่อผู้ขาย
ไม่ใช่ตารางรายการจ่ายเงิน ต้องสำรวจ SSBBACKOFFICE เพิ่มถ้าจะทำต่อ

รหัส SKPODTL.POSTATUS เก็บดิบไว้เฉยๆ ไม่ตีความ (สำรวจเจอแค่ 0 กับ 2 ยังไม่ได้ถาม
ผู้ใช้ว่าแต่ละเลขคืออะไร) — "อนุมัติแล้วหรือยัง" ใช้ APPROVEDATETIME IS NOT NULL
แทน เพราะเป็นข้อเท็จจริงตรงจากข้อมูล ไม่ใช่การตีความรหัส

อ่านอย่างเดียวจากฐานข้อมูลโรงพยาบาล ไม่แก้ไขข้อมูลใด ๆ
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from database import connect, error_summary, load_config  # noqa: E402
import name_cleaner  # noqa: E402
import warehouse_db  # noqa: E402

#: ย้อนหลังกี่เดือนถ้าไม่ระบุ — เท่ากับตัวเลือกช่วงเวลายาวสุดที่หน้าเว็บมีให้เลือก (18/24 เดือน)
DEFAULT_MONTHS = 18

QUERY = """
    SELECT
        po.PONO, pod.SUFFIX, pod.STORE, pod.STOCKCODE, pod.LOTNO,
        po.SUPPLIERCODE, LTRIM(RTRIM(ap.THAINAME)) AS SUPPLIER_NAME,
        pod.REQUESTQTY, pod.LOTQTY, pod.LOTPRICE, pod.AMT, pod.POSTATUS,
        pod.DIVISION, pod.DEPT, pod.SECTION,
        po.ISSUEDATETIME, po.APPROVEDATETIME, po.DUEDATETIME, po.LASTRECEIVEDATETIME,
        po.CONTRACTNO
    FROM dbo.SKPODTL pod WITH (NOLOCK)
    JOIN dbo.SKPO po WITH (NOLOCK) ON pod.PONO = po.PONO
    LEFT JOIN SSBBACKOFFICE.dbo.APMASTER ap WITH (NOLOCK) ON ap.APCODE = po.SUPPLIERCODE
    WHERE po.ISSUEDATETIME >= ? AND po.ISSUEDATETIME < ?
    ORDER BY po.ISSUEDATETIME
"""


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _iso(value) -> str:
    """datetime จากไดรเวอร์ -> ข้อความ ISO เทียบช่วงวันที่ตรงกันได้ (เก็บว่าง = ไม่มีวันที่จริง)"""
    if value is None:
        return ""
    try:
        return value.isoformat(sep=" ")
    except AttributeError:
        return str(value)


def fetch_rows(conn, date_from: datetime, date_to: datetime, limit: int | None = None):
    cursor = conn.cursor()
    cursor.execute(QUERY, (date_from, date_to))
    columns = [c[0] for c in cursor.description]
    fetched = cursor.fetchmany(limit) if limit else cursor.fetchall()
    cursor.close()
    rows = []
    for raw in fetched:
        row = dict(zip(columns, raw))
        rows.append({
            "po_no": _text(row["PONO"]),
            "suffix": int(_number(row["SUFFIX"])),
            "store": _text(row["STORE"]),
            "stock_code": _text(row["STOCKCODE"]),
            "lot_no": _text(row["LOTNO"]),
            "supplier_code": _text(row["SUPPLIERCODE"]),
            "supplier_name": name_cleaner.clean_vendor_name(_text(row["SUPPLIER_NAME"])),
            "request_qty": _number(row["REQUESTQTY"]),
            "lot_qty": _number(row["LOTQTY"]),
            "lot_price": _number(row["LOTPRICE"]),
            "amount": _number(row["AMT"]),
            "postatus": int(row["POSTATUS"]) if row["POSTATUS"] is not None else None,
            "division": _text(row["DIVISION"]),
            "dept": _text(row["DEPT"]),
            "section": _text(row["SECTION"]),
            "issue_datetime": _iso(row["ISSUEDATETIME"]),
            "approve_datetime": _iso(row["APPROVEDATETIME"]),
            "due_datetime": _iso(row["DUEDATETIME"]),
            "last_receive_datetime": _iso(row["LASTRECEIVEDATETIME"]),
            "contract_no": _text(row["CONTRACTNO"]),
        })
    return rows


_COLUMNS = ("po_no", "suffix", "store", "stock_code", "lot_no", "supplier_code",
            "supplier_name", "request_qty", "lot_qty", "lot_price", "amount", "postatus",
            "division", "dept", "section", "issue_datetime", "approve_datetime",
            "due_datetime", "last_receive_datetime", "contract_no", "pulled_at")


def store_rows(rows: list[dict], date_from: datetime, date_to: datetime) -> int:
    """แทนที่ข้อมูลในช่วงวันที่นี้ทั้งหมดหรือไม่แทนเลย (เหมือนงวดอื่นของ ERPLPH)"""
    pulled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    placeholders = ", ".join("?" for _ in _COLUMNS)
    statement = f"INSERT OR REPLACE INTO purchase_orders ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
    from_str, to_str = date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d")
    with warehouse_db.connect() as conn:
        conn.execute(
            "DELETE FROM purchase_orders WHERE issue_datetime >= ? AND issue_datetime < ?",
            (from_str, to_str))
        conn.executemany(statement, [
            [row[c] if c != "pulled_at" else pulled_at for c in _COLUMNS] for row in rows
        ])
        conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS, metavar="N",
                        help=f"ย้อนหลังกี่เดือนนับจากวันนี้ (ค่าเริ่มต้น {DEFAULT_MONTHS})")
    parser.add_argument("--limit", type=int, help="ดึงไม่เกินกี่แถว สำหรับทดลอง")
    parser.add_argument("--plan-only", action="store_true", help="แสดงจำนวนแถวโดยไม่เขียนฐานข้อมูล")
    args = parser.parse_args()

    warehouse_db.init_db()
    print("=" * 70)
    print("   ERPLPH — ดึงใบสั่งซื้อจริง (SKPO/SKPODTL)")
    print("=" * 70)

    date_to = datetime.now()
    date_from = date_to - timedelta(days=30 * args.months)
    print(f"  ช่วงวันที่ออกใบสั่งซื้อ: {date_from:%Y-%m-%d} ถึง {date_to:%Y-%m-%d} ({args.months} เดือน)")

    try:
        conn = connect(load_config(), timeout=180)
    except Exception as exc:
        print(f"\n  เชื่อมต่อฐานข้อมูลโรงพยาบาลไม่สำเร็จ: {error_summary(exc)}")
        return 1

    try:
        rows = fetch_rows(conn, date_from, date_to, limit=args.limit)
    except Exception as exc:
        print(f"\n  ดึงข้อมูลไม่สำเร็จ: {error_summary(exc)}")
        return 1
    finally:
        conn.close()

    po_count = len({r["po_no"] for r in rows})
    total_amount = sum(r["amount"] for r in rows)
    print(f"  พบ {len(rows):,} บรรทัด จาก {po_count:,} ใบสั่งซื้อ รวมมูลค่า {total_amount:,.2f} บาท")

    if args.plan_only:
        print("\n  (--plan-only: ยังไม่ได้เขียนฐานข้อมูลจริง)")
        return 0

    stored = store_rows(rows, date_from, date_to)
    print(f"\n  บันทึกแล้ว {stored:,} บรรทัด ลงตาราง purchase_orders")

    with warehouse_db.connect() as check_conn:
        approved = check_conn.execute(
            "SELECT COUNT(DISTINCT po_no) FROM purchase_orders "
            "WHERE approve_datetime != '' AND issue_datetime >= ? AND issue_datetime < ?",
            (date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d"))).fetchone()[0]
        top_suppliers = check_conn.execute(
            "SELECT supplier_name, COUNT(DISTINCT po_no), SUM(amount) FROM purchase_orders "
            "WHERE issue_datetime >= ? AND issue_datetime < ? AND supplier_name != '' "
            "GROUP BY supplier_name ORDER BY SUM(amount) DESC LIMIT 5",
            (date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d"))).fetchall()
    print(f"  อนุมัติแล้ว {approved:,} / {po_count:,} ใบสั่งซื้อ (มี APPROVEDATETIME)")
    if top_suppliers:
        print("\n  ผู้ขาย 5 รายที่มีมูลค่าสั่งซื้อสูงสุดในช่วงนี้")
        for name, pos, amount in top_suppliers:
            print(f"    {name[:40]:<42} {pos:>4} PO   ฿{amount:>16,.2f}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
