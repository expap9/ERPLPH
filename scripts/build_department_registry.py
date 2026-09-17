"""สร้างตารางเทียบรหัสหน่วยงาน จากผลการสำรวจล่าสุด

หน้าเว็บอ่านจากฐานข้อมูลของ ERPLPH เท่านั้น ไม่ต่อ SQL Server ตอนเปิดหน้า ชื่อหน่วยงาน
จึงต้องมีสำเนาเก็บไว้ในโปรเจกต์ ตัวนี้แปลงผลของ `check_substore_requisition.bat`
(ไฟล์ใน diagnostics/ ซึ่งไม่ขึ้น git) เป็น `config/departments.json` ซึ่งขึ้น git ได้
เพราะเป็นผังองค์กร ไม่ใช่ข้อมูลผู้ป่วย

    python scripts\\build_department_registry.py

รูปแบบรหัสใน SYSCONFIG (ยืนยันจากข้อมูลจริง 17 ก.ย. 2569)
    CTRLCODE 10028  '208'             กลุ่มงาน
    CTRLCODE 10029  '209   01'        กลุ่มงาน(กว้าง 6) + งาน
    CTRLCODE 10030  '209   07    01'  กลุ่มงาน(6) + งาน(6) + ส่วนย่อย
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import departments  # noqa: E402
import stock5_engine  # noqa: E402

#: ฐานที่ยึดเป็นหลัก — ใบเบิกอยู่ใน SSBSTOCK จึงใช้ชื่อจากฐานเดียวกัน
#: สองฐานให้ชื่อไม่ตรงกันในหลายรหัส ดู docs/departments.md
PREFERRED_DATABASE = "SSBSTOCK"
QUERY_PREFIX = "department_codes:"


def newest_report(directory: Path) -> Path:
    reports = sorted(directory.glob("substore_requisition_*.json"))
    if not reports:
        raise FileNotFoundError(
            f"ไม่พบผลการสำรวจใน {directory} — รัน check_substore_requisition.bat ก่อน")
    return reports[-1]


def rows_from(report: dict, database: str) -> list[dict]:
    for query in report.get("queries", []):
        if query.get("name") == QUERY_PREFIX + database and query.get("status") != "error":
            return query.get("rows") or []
    raise ValueError(f"ผลการสำรวจไม่มีตารางรหัสหน่วยงานของ {database}")


def clean_name(raw: object) -> str:
    """ชื่อในฐานข้อมูลมีอักขระตัวแรกซ้ำเหมือนชื่อยา ใช้ตัวตัดเดียวกับ Stock5"""
    text = str(raw or "").strip()
    try:
        return stock5_engine.load("drug_names").display_drug_name(text)
    except Exception:
        return text


def build(rows: list[dict], database: str) -> dict:
    entries = []
    for row in rows:
        level = departments.LEVEL_BY_CTRLCODE.get(row.get("CTRLCODE"))
        if level is None:
            continue
        code = str(row.get("CODE") or "")
        name = clean_name(row.get("THAINAME"))
        if not name:
            continue
        entries.append({"level": level, "path": list(departments.split_code(code)), "name": name})
    entries.sort(key=lambda entry: (entry["path"], entry["level"]))
    return {"source": f"{database}.dbo.SYSCONFIG", "levels": dict(departments.LEVEL_BY_CTRLCODE),
            "entries": entries}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    database = argv[0] if argv else PREFERRED_DATABASE
    report_path = newest_report(ROOT / "diagnostics")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    registry = build(rows_from(report, database), database)

    target = ROOT / departments.REGISTRY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    counts: dict[str, int] = {}
    for entry in registry["entries"]:
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1
    print(f"อ่านจาก {report_path.name}")
    print(f"เขียน   {target}")
    for level, count in counts.items():
        print(f"    {level:<9} {count:>5,} รหัส")
    retired = sum(1 for entry in registry["entries"] if departments.is_retired(entry["name"]))
    print(f"    ในจำนวนนี้เป็นรหัสที่เลิกใช้แล้ว {retired:,} รหัส")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
