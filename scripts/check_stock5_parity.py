"""เทียบคลัง 2 ของ ERPLPH กับชุดข้อมูลที่ Stock5 ใช้อยู่ ทีละบรรทัด — อ่านอย่างเดียวทั้งสองฝั่ง

คลัง 2 ใช้คำสั่งดึงของ Stock5 ตามเดิมทุกตัวอักษรและเครื่องสอบทานตัวเดียวกัน ตัวเลข
จึงต้องตรงกันทุกบรรทัด ถ้าไม่ตรง แปลว่าฝั่งใดฝั่งหนึ่งดึงหรือสอบทานคนละแบบ — เป็น
สัญญาณเตือนเดียวที่มีว่าตัวเลขทั้งโรงพยาบาลอาจเพี้ยน

เดือนปัจจุบันต่างกันได้ ถ้าสองระบบดึงคนละเวลา (ยังมีการจ่ายเพิ่มระหว่างวัน)
แต่เดือนที่ปิดแล้วต้องตรงกันทุกบรรทัด

    python scripts\\check_stock5_parity.py
"""
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import stock5_engine  # noqa: E402
import stores  # noqa: E402
import warehouse_db  # noqa: E402

MAIN = stores.PHARMACY_MAIN_STORE


def _number(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _stock5_run(home: Path) -> tuple[dict, Path]:
    analytics = home / "analytics_data"
    active = json.loads((analytics / "active.json").read_text(encoding="utf-8"))
    return active, analytics / "runs" / active["run_id"]


def _issue_key(irno, suffix, code, movement) -> tuple[str, str, str, str]:
    return (str(irno), str(suffix), str(code), str(movement))


def stock5_issues(run: Path) -> dict[tuple, tuple]:
    rows = json.loads((run / "DISTRIBUTION.json").read_text(encoding="utf-8"))
    found = {}
    for row in rows:
        period = str(row.get("PERIOD_RPT") or "")[:6] or \
            str(row.get("SOURCE_MOVEMENT_DATETIME", ""))[:7].replace("-", "")
        key = _issue_key(row.get("IRNO"), row.get("SUFFIX"), row.get("WORKING_CODE"),
                         row.get("SOURCE_MOVEMENT_SUFFIX"))
        found[key] = (period, row.get("SOURCE_MOS_STATUS", ""),
                      round(_number(row.get("QTY_DIS")), 6), round(_number(row.get("VALUE")), 4))
    return found


def erplph_issues(conn: sqlite3.Connection) -> dict[tuple, tuple]:
    return {
        _issue_key(irno, suffix, code, movement): (period, status, round(qty, 6), round(value, 4))
        for period, irno, suffix, code, movement, status, qty, value in conn.execute(
            "SELECT period, irno, suffix, stock_code, movement_key, check_status, qty, value "
            "FROM issues WHERE store = ?", (MAIN,))
    }


def compare(erplph: dict, stock5: dict, periods: set[str]) -> dict[str, dict[str, int]]:
    """ความต่างรายเดือน เฉพาะงวดที่ทั้งสองฝั่งครอบคลุม"""
    diff: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for key in set(erplph) | set(stock5):
        a, b = erplph.get(key), stock5.get(key)
        period = (a or b)[0]
        if period not in periods:
            continue
        if b is None:
            diff[period]["มีแต่ ERPLPH"] += 1
        elif a is None:
            diff[period]["มีแต่ Stock5"] += 1
        elif a[1] != b[1]:
            diff[period]["สถานะสอบทานต่าง"] += 1
        elif abs(a[2] - b[2]) > 1e-6 or abs(a[3] - b[3]) > 0.005:
            diff[period]["จำนวนหรือมูลค่าต่าง"] += 1
    return diff


def main() -> int:
    home = stock5_engine.stock5_home()
    try:
        active, run = _stock5_run(home)
    except (OSError, ValueError, KeyError) as exc:
        print(f"  อ่านชุดข้อมูลของ Stock5 ไม่ได้ ({type(exc).__name__}) — ต้องดึงข้อมูลใน Stock5 ก่อน")
        return 1

    conn = sqlite3.connect(f"file:{warehouse_db.DB_PATH}?mode=ro", uri=True)
    s5, er = stock5_issues(run), erplph_issues(conn)
    er_periods = {row[0] for row in conn.execute(
        "SELECT period FROM periods WHERE store = ? AND kind = 'issue' AND status = 'complete'",
        (MAIN,))}
    s5_periods = {value[0] for value in s5.values()}
    shared = er_periods & s5_periods
    current = max(shared) if shared else ""

    print("=" * 70)
    print("   ERPLPH ↔ Stock5 — คลัง 2 ต้องตรงกันทุกบรรทัด")
    print("=" * 70)
    print(f"  Stock5 ชุด {active.get('run_id')}  ดึงเสร็จ {active.get('completed_at')}")
    print(f"  งวดที่เทียบ {len(shared)} งวด ({min(shared, default='-')} ถึง {current or '-'})")

    diff = compare(er, s5, shared)
    closed_problems = {p: d for p, d in diff.items() if p != current}
    totals = defaultdict(lambda: [0, 0.0, 0, 0.0])
    for source, rows in (("s5", s5), ("er", er)):
        for period, status, _qty, value in rows.values():
            if period in shared:
                bucket = totals[(source, period)]
                bucket[0] += 1
                bucket[1] += value
    print(f"\n  {'งวด':<7} {'บรรทัด S5':>10} {'บรรทัด ER':>10} {'มูลค่า S5':>18} {'มูลค่า ER':>18}  ผล")
    for period in sorted(shared):
        a, b = totals[("s5", period)], totals[("er", period)]
        note = "ตรง" if period not in diff else ", ".join(f"{k} {v:,}" for k, v in diff[period].items())
        if period == current and period in diff:
            note += "  (เดือนปัจจุบัน ดึงคนละเวลา)"
        print(f"  {period:<7} {a[0]:>10,} {b[0]:>10,} {a[1]:>18,.2f} {b[1]:>18,.2f}  {note}")

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "stock5_run": active.get("run_id"),
        "periods": sorted(shared),
        "closed_period_differences": {p: dict(d) for p, d in closed_problems.items()},
        "current_period": current,
        "current_period_differences": dict(diff.get(current, {})),
        "matched": not closed_problems,
    }
    out = ROOT / "diagnostics" / f"stock5_parity_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    if closed_problems:
        print(f"  ไม่ตรง {len(closed_problems)} งวดที่ปิดแล้ว — ห้ามใช้ตัวเลขทั้งโรงพยาบาลจนกว่าจะหาสาเหตุ")
    else:
        print("  ตรงกันทุกบรรทัดในงวดที่ปิดแล้ว")
    print(f"  รายงาน: {out}")
    print("=" * 70)
    return 1 if closed_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
