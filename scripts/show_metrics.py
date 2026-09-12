"""รายงานตัวชี้วัดจากฐานข้อมูลของ ERPLPH — ไม่แตะฐานข้อมูลโรงพยาบาล

    python scripts\\show_metrics.py                ทั้งโรงพยาบาล
    python scripts\\show_metrics.py --store O5     เฉพาะคลังเดียว
    python scripts\\show_metrics.py --json         ส่งออกเป็น JSON

ทั้งโรงพยาบาลกับรายคลังใช้สูตรคนละแบบโดยตั้งใจ ดู app/metrics.py
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import metrics  # noqa: E402
import stores  # noqa: E402
import warehouse_db  # noqa: E402


def _baht(value: float) -> str:
    return f"{value:,.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", help="รหัสคลัง เว้นว่าง = ทั้งโรงพยาบาล")
    parser.add_argument("--json", action="store_true", help="ส่งออกเป็น JSON")
    parser.add_argument("--top", type=int, default=8, help="แสดงกี่บรรทัดในแต่ละรายการ")
    args = parser.parse_args()

    conn = warehouse_db.connect()
    try:
        scope = [args.store] if args.store else None
        found = metrics.summary(args.store, conn=conn)
        if args.json:
            print(json.dumps(found, ensure_ascii=False, indent=2))
            return 0

        days = sorted(set(found["as_of"].values()))
        title = stores.store_name(args.store) if args.store else "ทั้งโรงพยาบาล"
        print("=" * 78)
        print(f"   ตัวชี้วัดคลังยา — {title}")
        print(f"   ข้อมูลคงคลัง ณ {', '.join(days) if days else 'ยังไม่มีภาพคงคลัง'}")
        print("=" * 78)

        held, used = found["stock"], found["consumption"]
        print(f"\n[1] มูลค่าคงคลัง {_baht(held['value'])} บาท  ({held['lots']:,} ล็อต)")
        print(f"    ในนั้นหมดอายุแล้ว {_baht(held['expired_value'])} บาท "
              f"({held['expired_lots']:,} ล็อต)")
        if not args.store:
            for row in held["by_store"][:args.top]:
                print(f"      {row['store']:<5}{stores.store_name(row['store'])[:28]:<30}"
                      f"{row['lots']:>6,} ล็อต{row['value']:>18,.2f}")

        label = "ยอดใช้สุทธิ (จ่ายผู้ป่วย หักรับคืน)" if not args.store \
            else "ยอดออกสุทธิ (จ่าย + โอนออก หักรับคืน)"
        print(f"\n[2] {label}")
        for row in used["months"]:
            print(f"      {row['period']}  จ่าย {row['issued']:>16,.2f}  "
                  f"รับคืน {row['returned']:>13,.2f}  สุทธิ {row['net']:>16,.2f}")
        print(f"    เฉลี่ย {_baht(used['average'])} บาท/เดือน "
              f"(จาก {used['covered_months']} เดือนที่มีข้อมูลครบ)")

        remaining = found["months_of_stock"]
        print(f"\n[3] เหลือพอใช้ {remaining:.2f} เดือน" if remaining is not None
              else "\n[3] เหลือพอใช้ — คำนวณไม่ได้ ไม่มียอดใช้ในช่วงนี้")

        print(f"\n[4] ใกล้หมดอายุ (เกณฑ์ {metrics.EXPIRY_WARNING_MONTHS} เดือน "
              f"วิกฤตที่ {metrics.EXPIRY_CRITICAL_MONTHS} เดือน)")
        names = {"expired": "หมดอายุแล้ว", "critical": "ภายใน 3 เดือน",
                 "warning": "3-6 เดือน", "no_expiry_date": "ไม่ระบุวันหมดอายุ"}
        for key, label in names.items():
            bucket = found["expiring"][key]
            print(f"      {label:<18}{bucket['lots']:>6,} ล็อต{bucket['value']:>18,.2f}")
        for lot in metrics.expiring_lots(conn, scope, limit=args.top):
            mark = "หมดอายุแล้ว" if lot["expired"] else "ใกล้หมดอายุ"
            print(f"      {lot['store']:<5}{lot['stock_code']:<9}{lot['name'][:28]:<30}"
                  f"{lot['value']:>12,.2f}  {mark} {lot['expire_date']}")

        dormant = metrics.dormant(conn, scope)
        print(f"\n[5] ของค้างนิ่ง (ไม่เคลื่อนไหวออก {metrics.DORMANT_MONTHS} เดือน) "
              f"{len(dormant):,} รายการ  {_baht(sum(row['value'] for row in dormant))} บาท")
        for row in dormant[:args.top]:
            print(f"      {row['store']:<5}{row['stock_code']:<9}{row['name'][:28]:<30}"
                  f"{row['qty']:>10,.0f}{row['value']:>14,.2f}")

        low = metrics.low_stock(conn, scope)
        print(f"\n[6] ใกล้หมด (เหลือน้อยกว่า {metrics.LOW_STOCK_MONTHS:g} เดือน) "
              f"{len(low['items']):,} รายการ")
        for row in low["items"][:args.top]:
            print(f"      {row['store']:<5}{row['stock_code']:<9}{row['name'][:28]:<30}"
                  f"เหลือ {row['months_left']:>5.2f} เดือน  ใช้เดือนละ {row['monthly_use']:>12,.2f}")
        if low["without_value"]:
            print(f"    รายการที่ระบบไม่ได้ลงมูลค่าไว้ {len(low['without_value']):,} รายการ "
                  "คิดเป็นเดือนไม่ได้ ต้องดูจำนวนแทน")
        print("\n" + "=" * 78)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
