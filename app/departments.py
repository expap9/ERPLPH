"""หน่วยงานที่ขอเบิกของ — รหัส 3 ชั้นของโรงพยาบาล

ใบเบิกทุกใบใน `SKIR` มีรหัสหน่วยงานอยู่แล้ว 3 ช่อง `DIVISION` / `DEPT` / `SECTION`
(เช่น `208-02-02`) และมีการกรอกครบทุกใบ — ตรวจจริง 17 ก.ย. 2569 ใบเบิก 95,248 ใบ
ใน 12 เดือน ไม่มีใบไหนเว้นว่างเลย

ชื่อของแต่ละรหัสอยู่ใน `SYSCONFIG` ของ SSB แต่หน้าเว็บไม่ต่อ SQL Server ตอนเปิดหน้า
จึงเก็บสำเนาไว้ที่ `config/departments.json` สร้างใหม่ได้ด้วย
`scripts/build_department_registry.py`

ที่นี่ไม่เดาชื่อ — รหัสที่ไม่มีในสำเนาจะคืนรหัสเดิมกลับไป ไม่ใช่ทิ้งแถวนั้น
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = "config/departments.json"

DIVISION = "division"
DEPT = "dept"
SECTION = "section"

#: ชั้นของหน่วยงานใน SYSCONFIG — ยืนยันจากข้อมูลจริง 17 ก.ย. 2569
LEVEL_BY_CTRLCODE = {10028: DIVISION, 10029: DEPT, 10030: SECTION}

#: ช่อง CODE ของ SYSCONFIG รวมรหัสแม่ไว้ด้วย โดยเติมช่องว่างให้แต่ละชั้นกว้าง 6 ตัวอักษร
#:     '208'             -> ('208', '', '')
#:     '209   01'        -> ('209', '01', '')
#:     '209   07    01'  -> ('209', '07', '01')
_LEVEL_WIDTH = 6

#: รหัสที่เลิกใช้ถูกทำเครื่องหมายไว้ในชื่อ ไม่มีช่องสถานะแยก เหมือนทะเบียนรายการ
#: ตัวอย่างจริง: '((ยกเลิก)งานบริหารและธุรการ' · '((ยกเลิกใช้ 30704 แทน)กลุ่มนโยบายและแผนงาน'
RETIRED_MARKERS = ("ยกเลิก", "ยกเเลิก", "ไม่ใช้")


class Department(NamedTuple):
    path: tuple[str, str, str]
    level: str
    name: str

    @property
    def retired(self) -> bool:
        return is_retired(self.name)


def split_code(code: object) -> tuple[str, str, str]:
    """แยกช่อง CODE ของ SYSCONFIG เป็นสามชั้น"""
    text = str(code or "")
    return (text[:_LEVEL_WIDTH].strip(),
            text[_LEVEL_WIDTH:_LEVEL_WIDTH * 2].strip(),
            text[_LEVEL_WIDTH * 2:].strip())


def is_retired(name: object) -> bool:
    text = str(name or "")
    return any(marker in text for marker in RETIRED_MARKERS)


def path_of(division: object, dept: object = "", section: object = "") -> tuple[str, str, str]:
    """คีย์มาตรฐานของหน่วยงาน — ตัดช่องว่างและยุบชั้นที่ว่างให้เป็นสตริงว่าง"""
    levels = [str(value or "").strip() for value in (division, dept, section)]
    # ชั้นล่างไม่มีความหมายถ้าชั้นบนว่าง เช่น ('', '02', '') ระบุหน่วยงานไม่ได้
    for index, value in enumerate(levels):
        if not value:
            levels[index:] = [""] * (len(levels) - index)
            break
    return tuple(levels)  # type: ignore[return-value]


@functools.lru_cache(maxsize=1)
def registry(path: str | None = None) -> dict:
    """สำเนาตารางรหัส คืนโครงว่างถ้ายังไม่ได้สร้าง — หน้าเว็บต้องเปิดได้เสมอ"""
    target = Path(path) if path else ROOT / REGISTRY_PATH
    if not target.is_file():
        return {"source": "", "entries": []}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"source": "", "entries": []}
    return loaded if isinstance(loaded, dict) else {"source": "", "entries": []}


@functools.lru_cache(maxsize=1)
def _by_path() -> dict[tuple[str, str, str], Department]:
    found: dict[tuple[str, str, str], Department] = {}
    for entry in registry().get("entries", []):
        try:
            key = path_of(*entry["path"])
            found[key] = Department(key, entry["level"], entry["name"])
        except (KeyError, TypeError):
            continue
    return found


def source() -> str:
    """ฐานข้อมูลที่ชื่อมาจาก — ต้องแสดงบนหน้าจอ เพราะสองฐานให้ชื่อไม่ตรงกัน"""
    return str(registry().get("source") or "")


def name_of(division: object, dept: object = "", section: object = "") -> str:
    """ชื่อหน่วยงาน คืนรหัสเดิมถ้าไม่รู้จัก เพื่อไม่ให้แถวนั้นหายจากรายงาน"""
    key = path_of(division, dept, section)
    found = _by_path().get(key)
    if found is not None:
        return found.name

    # 1. ชั้น section ไม่พบ ให้ลองดูชื่อชั้น dept
    if key[2] and key[1]:
        parent_dept = _by_path().get(path_of(key[0], key[1], ""))
        if parent_dept is not None:
            return f"{parent_dept.name} ({key[2]})"

    # 2. ชั้น dept ไม่พบ ให้ลองดูชื่อชั้น division
    if key[0]:
        parent_div = _by_path().get(path_of(key[0], "", ""))
        if parent_div is not None:
            sub = "-".join(part for part in key[1:] if part)
            return f"{parent_div.name} ({sub})" if sub else parent_div.name

    return "-".join(part for part in key if part) or "(ไม่ระบุหน่วยงาน)"


def full_name(division: object, dept: object = "", section: object = "") -> str:
    """ชื่อเต็มไล่จากกลุ่มงานลงมา เช่น 'กลุ่มการพยาบาล › งานจ่ายกลาง › จ่ายกลาง OPD'"""
    key = path_of(division, dept, section)
    parts = []
    for depth in range(1, len(key) + 1):
        if not key[depth - 1]:
            break
        parts.append(name_of(*key[:depth]))
    return " › ".join(parts) if parts else "(ไม่ระบุหน่วยงาน)"


def children_of(division: object = "", dept: object = "") -> list[Department]:
    """หน่วยงานชั้นถัดไปใต้รหัสที่ให้มา ว่างเปล่า = กลุ่มงานทั้งหมด"""
    parent = path_of(division, dept)
    depth = sum(1 for part in parent if part)
    return sorted(
        (found for key, found in _by_path().items()
         if sum(1 for part in key if part) == depth + 1 and key[:depth] == parent[:depth]),
        key=lambda found: found.path)


def reload() -> None:
    """ลืมสำเนาที่อ่านไว้ — ใช้ในเทสต์และหลังสร้างตารางใหม่"""
    registry.cache_clear()
    _by_path.cache_clear()
