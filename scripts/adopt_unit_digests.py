"""รับรองกติกาหน่วยให้งวดที่ดึงก่อนมีการบันทึกกติกา — ใช้ครั้งเดียวต่อฐานข้อมูล

งวดที่ดึงก่อนวันที่ 11 ก.ย. 2569 (ค่ำ) ไม่ได้บันทึกว่าสอบทานด้วยกติกาหน่วยชุดไหน
ตัววางแผนจึงจะดึงใหม่ทั้งหมด ถ้าพิสูจน์ได้ว่ากติกาไม่เปลี่ยนตั้งแต่ก่อนการดึง ก็รับรอง
ได้โดยไม่ต้องอ่านฐานข้อมูลโรงพยาบาลซ้ำ

หลักฐานที่ต้องครบทั้งสองข้อ มิฉะนั้นปฏิเสธ
  1. ไฟล์กติกาและโค้ดเครื่องสอบทานของ Stock5 ตรงกับ git HEAD ทุกไบต์
  2. commit ล่าสุดที่แก้ไฟล์เหล่านั้น เกิดก่อนการดึงงวดแรกที่จะรับรอง
ไม่ใช้เวลาแก้ไฟล์เป็นหลักฐาน เพราะ git checkout เปลี่ยนเวลาได้โดยเนื้อหาไม่เปลี่ยน

    python scripts\\adopt_unit_digests.py --pulled-from 2026-09-11T03:30:00+00:00 ^
                                          --pulled-to   2026-09-11T05:45:00+00:00
"""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import stock5_engine  # noqa: E402
import unit_rules  # noqa: E402
import warehouse_db  # noqa: E402

#: ทุกไฟล์ที่มีผลต่อสถานะสอบทาน
EVIDENCE_FILES = tuple(f"app/{name}" for name in unit_rules.ENGINE_FILES) + (
    "app/confirmed_units.py", "new data/baseunit_0369.xlsx")


def _git(home: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(home), *args], capture_output=True,
                          text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


def evidence(pulled_from: datetime, home: Path | None = None,
             git: Callable[..., str] = _git) -> tuple[bool, list[str]]:
    """หลักฐานว่ากติกาที่ใช้ตอนดึง = กติกาปัจจุบัน คืน (ผ่านไหม, คำอธิบาย)"""
    home = home or stock5_engine.stock5_home()
    notes = []
    try:
        tracked = set(git(home, "ls-files", "--", *EVIDENCE_FILES).splitlines())
        dirty = git(home, "status", "--porcelain", "--", *EVIDENCE_FILES)
        last = git(home, "log", "-1", "--format=%H %cI", "--", *EVIDENCE_FILES)
    except (OSError, subprocess.CalledProcessError) as exc:
        return False, [f"อ่าน git ของ Stock5 ไม่ได้: {type(exc).__name__}"]

    missing = [name for name in EVIDENCE_FILES if name not in tracked]
    if missing:
        return False, ["ไฟล์ไม่อยู่ใน git จึงพิสูจน์เนื้อหาย้อนหลังไม่ได้: " + ", ".join(missing)]
    if dirty:
        return False, ["ไฟล์กติกาถูกแก้หลัง commit ล่าสุด:", dirty]
    notes.append("ไฟล์กติกาและเครื่องสอบทานตรงกับ git HEAD ทุกไฟล์")

    commit, _, committed = last.partition(" ")
    if not committed or datetime.fromisoformat(committed) >= pulled_from:
        return False, [f"commit ล่าสุดที่แก้กติกา ({commit[:7]} {committed}) ไม่ได้เกิดก่อนการดึง"]
    notes.append(f"commit ล่าสุดที่แก้กติกา {commit[:7]} เวลา {committed} เกิดก่อนการดึง")
    return True, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pulled-from", required=True, type=datetime.fromisoformat)
    parser.add_argument("--pulled-to", required=True, type=datetime.fromisoformat)
    args = parser.parse_args()

    warehouse_db.init_db()
    ok, notes = evidence(args.pulled_from)
    for note in notes:
        print("  " + note)
    if not ok:
        print("\n  ไม่รับรอง — ให้ pull_warehouse_data.bat ดึงงวดเหล่านี้ใหม่แทน")
        return 1

    rule_map, engine = unit_rules.rules(), unit_rules.engine_digest()
    codes = warehouse_db.codes_by_period()
    chosen = {}
    for key, (stored, pulled_at) in warehouse_db.stored_unit_digests().items():
        if stored or not pulled_at:
            continue
        if args.pulled_from <= datetime.fromisoformat(pulled_at) <= args.pulled_to:
            chosen[key] = unit_rules.period_digest(codes.get(key, ()), rule_map, engine)
    warehouse_db.adopt_unit_digests(chosen)
    print(f"\n  รับรองแล้ว {len(chosen):,} งวด (ดึงระหว่าง {args.pulled_from} ถึง {args.pulled_to})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
