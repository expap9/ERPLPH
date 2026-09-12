"""ทำเครื่องหมายให้รายการยา — ข้อมูลที่เจ้าหน้าที่บันทึกเอง ไม่ได้มาจากการดึง

ตอนนี้มีเครื่องหมายเดียว: "บริการผู้ป่วยเฉพาะราย ไม่ได้ซื้อประจำ" รายการที่ติด
เครื่องหมายนี้จะไม่ขึ้นในรายการใกล้หมด เพราะคงคลังเป็นศูนย์คือเรื่องปกติของยากลุ่มนั้น

    python scripts\\mark_item.py --list
    python scripts\\mark_item.py --code 2098120 --by 41850 --note "สั่งตามใบสั่งแพทย์รายคน"
    python scripts\\mark_item.py --code 2098120 --by 41850 --off
    python scripts\\mark_item.py --history 2098120

ต้องระบุ --by ทุกครั้ง เพราะเครื่องหมายเปลี่ยนตัวเลขที่ผู้บริหารเห็น ระบบจึงเก็บว่า
ใครเป็นคนกำหนดและเมื่อไร และประวัติไม่ถูกลบ
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import warehouse_db  # noqa: E402
import work_db  # noqa: E402


def _names(codes) -> dict[str, str]:
    if not codes:
        return {}
    with warehouse_db.connect() as conn:
        return {code: name for code, name in conn.execute(
            "SELECT stock_code, name FROM items")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", help="รหัสรายการ")
    parser.add_argument("--mark", default=work_db.PATIENT_SPECIFIC, choices=list(work_db.MARKS),
                        help="ชนิดเครื่องหมาย")
    parser.add_argument("--by", help="รหัสเจ้าหน้าที่ผู้บันทึก")
    parser.add_argument("--note", default="", help="เหตุผล")
    parser.add_argument("--off", action="store_true", help="ปลดเครื่องหมาย")
    parser.add_argument("--list", action="store_true", help="แสดงรายการที่ติดเครื่องหมายอยู่")
    parser.add_argument("--history", metavar="CODE", nargs="?", const="",
                        help="ประวัติการติด/ปลดเครื่องหมาย")
    args = parser.parse_args()

    if args.history is not None:
        rows = work_db.history(args.history or None)
        print(f"ประวัติ {len(rows)} รายการ (ล่าสุดก่อน)")
        for row in rows:
            print(f"  {row['at']}  {row['action']:<6} {row['stock_code']:<10} "
                  f"โดย {row['actor']:<8} {row['note']}")
        return 0

    if args.list or not args.code:
        found = work_db.marked(args.mark)
        names = _names(found)
        print(f"{work_db.label(args.mark)}: {len(found)} รายการ")
        for code, row in sorted(found.items()):
            print(f"  {code:<10}{names.get(code, '')[:36]:<38}ตั้งโดย {row['set_by']:<8}"
                  f"{row['set_at'][:10]}  {row['note']}")
        if not args.code:
            return 0

    if not args.by:
        print("ต้องระบุ --by รหัสเจ้าหน้าที่ผู้บันทึก")
        return 1

    action = work_db.clear_mark if args.off else work_db.set_mark
    result = action(args.code, args.mark, args.by, args.note)
    verb = "ปลดเครื่องหมาย" if args.off else "ติดเครื่องหมาย"
    print(f"{verb} {work_db.label(args.mark)} ให้ {result['stock_code']} "
          f"โดย {result['actor']} เมื่อ {result['at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
