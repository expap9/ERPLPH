"""ดึงข้อมูลทุกคลังเข้าฐานข้อมูลของ ERPLPH

รันซ้ำได้เสมอ ครั้งแรกจะดึงทั้งช่วงปีงบประมาณ ครั้งถัดไปดึงเฉพาะงวดที่ยังขาด
กับเดือนปัจจุบันซึ่งยอดยังเปลี่ยนได้

    python scripts\\pull_warehouse_data.py                  ทุกคลัง
    python scripts\\pull_warehouse_data.py --store 2 O5     เฉพาะคลังที่ระบุ
    python scripts\\pull_warehouse_data.py --limit 10       ทดลองสิบงวดแรก
    python scripts\\pull_warehouse_data.py --plan-only      ดูแผนโดยไม่ดึงจริง
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import extractor  # noqa: E402
import stores  # noqa: E402
import warehouse_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", nargs="*", help="รหัสคลัง เว้นว่าง = ทุกคลังที่ใช้งานอยู่")
    parser.add_argument("--kind", nargs="*", default=list(extractor.PERIOD_KINDS),
                        choices=["receipt", "issue", "balance"])
    parser.add_argument("--limit", type=int, help="ดึงไม่เกินกี่งวด สำหรับทดลอง")
    parser.add_argument("--pacing", type=float, default=extractor.PACING_SECONDS,
                        help="พักกี่วินาทีระหว่างคำสั่ง")
    parser.add_argument("--plan-only", action="store_true", help="แสดงแผนโดยไม่ดึงจริง")
    args = parser.parse_args()

    warehouse_db.init_db()
    print("=" * 70)
    print("   ERPLPH — ดึงข้อมูลคลังเข้าฐานข้อมูล")
    print("=" * 70)
    try:
        work = extractor.plan_detail(store_codes=args.store, kinds=args.kind)
    except Exception as exc:
        # ส่วนใหญ่คือตารางหน่วยของ Stock5 อ่านไม่ได้ ถ้าดึงต่อ ทุกงวดใบจ่ายจะล้มอยู่ดี
        print(f"\n  วางแผนไม่สำเร็จ ยังไม่ได้อ่านฐานข้อมูลโรงพยาบาล: {exc}")
        return 1

    print(f"  งวดที่ต้องดึง {len(work):,} งวด")
    by_reason: dict[str, int] = {}
    for *_ignored, reason in work:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    for reason, count in sorted(by_reason.items(), key=lambda item: -item[1]):
        print(f"    - {reason:<32} {count:>5,} งวด")
    if args.limit:
        print(f"  จำกัดรอบนี้ {args.limit:,} งวด")
    by_store: dict[str, int] = {}
    for _period, store, _kind, _reason in work:
        by_store[store] = by_store.get(store, 0) + 1
    for store, count in sorted(by_store.items(), key=lambda item: -item[1])[:10]:
        print(f"    {store:<5} {stores.store_name(store)[:30]:<32} {count:>4} งวด")

    if args.plan_only:
        print("\n  (--plan-only: ยังไม่ได้ดึงข้อมูลจริง)")
        return 0
    if not work:
        print("\n  ข้อมูลครบแล้ว ไม่มีอะไรต้องดึง")
        return 0

    print("\n  เริ่มดึง อ่านอย่างเดียว ไม่แก้ไขฐานข้อมูลโรงพยาบาล\n")

    def show(outcome: dict) -> None:
        if outcome["status"] == "success":
            print("    %-8s %-5s %-8s %6s แถว" % (
                outcome["period"], outcome["store"], outcome["kind"],
                f"{outcome.get('stored_rows', 0):,}"), flush=True)
        else:
            print("    %-8s %-5s %-8s ล้มเหลว: %s" % (
                outcome["period"], outcome["store"], outcome["kind"],
                outcome.get("message", "")[:44]), flush=True)

    result = extractor.run(store_codes=args.store, kinds=args.kind, limit=args.limit,
                           pacing=args.pacing, progress=show)

    print("\n" + "=" * 70)
    print("  สำเร็จ %s งวด  ล้มเหลว %s งวด  รวม %s แถว" % (
        f"{result['success']:,}", f"{result['failed']:,}", f"{result['rows']:,}"))
    if result.get("error"):
        print("  หยุดกลางคัน:", result["error"])
    coverage = warehouse_db.coverage()
    print("  ฐานข้อมูล: %s" % coverage["database"])
    print("  คลังที่มีข้อมูลแล้ว %d คลัง  ทะเบียนรายการ %s รายการ" % (
        len(coverage["stores"]), f"{coverage['items']:,}"))
    print("=" * 70)
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
