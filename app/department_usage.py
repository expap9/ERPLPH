"""แต่ละหน่วยงานเบิกอะไรไปเท่าไร — ภาพรวมทั้งโรงพยาบาล กดลงไปได้ถึงรายการ

โจทย์จากผู้ใช้ 16 ก.ย. 2569:
    "ถ้าเรามาทาง ERPLPH ก็ต้องทุกแผนกใน รพ. นะครับ หอผู้ป่วย งานซักฟอก งานบริหาร
     งานการเงิน บลา ๆ ๆ ๆ ที่เขาเบิกของไปใช้ ก็ต้องเห็นภาพนะครับ
     (เช่น ใช้กระดาษเยอะก็ paperless, ชุดทำเเผลสัมพันธ์กับผู้ป่วยไหม)"

**นับอะไร** — เฉพาะเอกสารชนิด 32 "จ่ายให้หน่วยเบิก" ที่ออกจากคลังจริง (`direction = 'out'`)
เพราะนั่นคือของที่หน่วยงานรับไป การโอนระหว่างคลัง (ชนิด 35) ไม่นับ ของยังอยู่ในคลังของ
โรงพยาบาล และจะถูกเบิกอีกทอดที่ปลายทาง ถ้านับจะเป็นการนับซ้ำ

**หักคืน** — ใบคืนเป็นเอกสารชนิด 32 ทิศทางเข้า (`direction = 'in'`) กระทบยอดจริงวันที่
8 ก.ย. 2569 ที่คลัง `I2` ยืนยันว่าใบขายกับใบคืนเป็นคนละใบ (รหัส `S02` กับ `R02`)
ยอดสุทธิจึงต้องหักเอง ระบบไม่ได้หักมาให้ (ดู docs/substore.md §3.6)

**สิ่งที่ตัวเลขนี้ไม่รู้ ต้องบอกผู้ใช้เสมอ**
    - หน่วยงานที่ "เบิก" ไม่ใช่หน่วยงานที่ "ใช้" เสมอไป — ห้องยาเบิกแทนหอผู้ป่วย
      กลุ่มงานเภสัชกรรมจึงมียอดสูงที่สุดโดยธรรมชาติ ไม่ได้แปลว่าใช้เองหมด
    - ของที่ถึงหอผู้ป่วยแล้วถือว่าใช้หมดทันทีในสายตาระบบ ซึ่งไม่จริง (floor stock มองไม่เห็น)
    - มูลค่าเป็นต้นทุนที่ระบบบันทึกไว้ตอนตัดจ่าย ไม่ใช่ราคาขายหรือราคาที่เรียกเก็บผู้ป่วย
"""
from typing import Any, Iterable, NamedTuple

import categories
import departments

#: ชนิดเอกสาร "จ่ายให้หน่วยเบิก" — ชนิดเดียวที่แปลว่าของถูกเบิกไปใช้จริง
DISPENSE_TYPE = "32"

#: ระดับที่หน้าจอเจาะได้
LEVELS = (departments.DIVISION, departments.DEPT, departments.SECTION)

_LEVEL_COLUMNS = {departments.DIVISION: ("division",),
                  departments.DEPT: ("division", "dept"),
                  departments.SECTION: ("division", "dept", "section")}


class DepartmentRow(NamedTuple):
    path: tuple[str, str, str]
    name: str
    full_name: str
    issued: float
    returned: float
    slips: int
    items: int
    stores: int

    @property
    def net(self) -> float:
        return self.issued - self.returned

    @property
    def has_children(self) -> bool:
        return bool(departments.children_of(*self.path[:2])) if not self.path[2] else False


class ItemRow(NamedTuple):
    stock_code: str
    name: str
    group: str
    qty: float
    unit: str
    net: float
    slips: int


def _period_clause(months: int, latest: str) -> tuple[str, list[Any]]:
    """ช่วงงวดที่นับ — นับถอยหลังจากงวดล่าสุดที่มีข้อมูล ไม่ใช่จากวันนี้

    ต้นทางเป็นสำเนาที่คัดลอกวันละครั้ง และเดือนปัจจุบันยังไม่จบ การนับจากวันนี้จะทำให้
    เดือนสุดท้ายดูตกลงเสมอ
    """
    year, month = int(latest[:4]), int(latest[4:6])
    total = year * 12 + (month - 1) - (months - 1)
    first = f"{total // 12:04d}{total % 12 + 1:02d}"
    return "period >= ? AND period <= ?", [first, latest]


def latest_period(conn) -> str:
    row = conn.execute("SELECT MAX(period) FROM issues WHERE LENGTH(period) = 6").fetchone()
    return (row[0] if row else "") or ""


def _scope(months: int, latest: str, path: tuple[str, ...], store: str | None,
           group: str | None, alias: str = "") -> tuple[list[str], list[Any]]:
    """เงื่อนไขร่วมของทุกคำสั่งในหน้านี้ — alias ใส่เมื่อคำสั่งนั้น join ตารางอื่นด้วย"""
    at = f"{alias}." if alias else ""
    clause, values = _period_clause(months, latest)
    where = [clause.replace("period", f"{at}period"), f"{at}document_type = ?"]
    values.append(DISPENSE_TYPE)
    for column, value in zip(("division", "dept", "section"), path):
        if value:
            where.append(f"{at}{column} = ?")
            values.append(value)
    if store:
        where.append(f"{at}store = ?")
        values.append(store)
    if group:
        where.append(f"{at}stock_code IN (SELECT stock_code FROM items WHERE item_group = ?)")
        values.append(group)
    return where, values


def breakdown(conn, level: str = departments.DIVISION, parent: Iterable[str] = (),
              months: int = 12, store: str | None = None,
              group: str | None = None, latest: str | None = None) -> dict[str, Any]:
    """ยอดเบิกแยกตามหน่วยงานหนึ่งชั้น ภายใต้หน่วยงานแม่ที่ระบุ"""
    if level not in _LEVEL_COLUMNS:
        raise ValueError(f"ระดับหน่วยงานไม่ถูกต้อง: {level}")
    latest = latest or latest_period(conn)
    if not latest:
        return {"rows": [], "level": level, "parent": tuple(parent), "latest": "",
                "months": months, "total_net": 0.0, "reason": "ยังไม่มีข้อมูลใบเบิกในคลังข้อมูล"}

    columns = _LEVEL_COLUMNS[level]
    path = departments.path_of(*list(parent)[:3] + [""] * (3 - len(list(parent)[:3])))
    where, values = _scope(months, latest, path[:len(columns) - 1], store, group)
    # ชั้นที่กำลังดูต้องมีค่า ไม่งั้นแถว "ยังไม่ระบุ" จะปนกับหน่วยงานจริง
    grouped = ", ".join(columns)
    rows = conn.execute(
        f"SELECT {grouped}, "
        "       COALESCE(SUM(CASE WHEN direction = 'out' THEN value ELSE 0 END), 0), "
        "       COALESCE(SUM(CASE WHEN direction = 'in'  THEN value ELSE 0 END), 0), "
        "       COUNT(DISTINCT irno), COUNT(DISTINCT stock_code), COUNT(DISTINCT store) "
        f"FROM issues WHERE {' AND '.join(where)} "
        f"GROUP BY {grouped} ORDER BY 1", values).fetchall()

    found = []
    for row in rows:
        key = departments.path_of(*(list(row[:len(columns)]) + [""] * (3 - len(columns))))
        issued, returned, slips, items, store_count = row[len(columns):]
        found.append(DepartmentRow(key, departments.name_of(*key), departments.full_name(*key),
                                   issued, returned, slips, items, store_count))
    found.sort(key=lambda entry: entry.net, reverse=True)
    return {"rows": found, "level": level, "parent": path, "latest": latest, "months": months,
            "total_net": sum(entry.net for entry in found), "reason": ""}


def items_of(conn, parent: Iterable[str], months: int = 12, limit: int = 40,
             store: str | None = None, group: str | None = None,
             latest: str | None = None) -> list[ItemRow]:
    """รายการที่หน่วยงานนี้เบิกมากที่สุด — ปลายทางของการกดเจาะ"""
    latest = latest or latest_period(conn)
    if not latest:
        return []
    path = departments.path_of(*list(parent)[:3] + [""] * (3 - len(list(parent)[:3])))
    where, values = _scope(months, latest, path, store, group, alias="i")
    rows = conn.execute(
        "SELECT i.stock_code, COALESCE(m.name, ''), COALESCE(m.item_group, ''), "
        "       COALESCE(SUM(CASE WHEN i.direction = 'out' THEN i.qty ELSE -i.qty END), 0), "
        "       COALESCE(MAX(i.unit), ''), "
        "       COALESCE(SUM(CASE WHEN i.direction = 'out' THEN i.value ELSE -i.value END), 0), "
        "       COUNT(DISTINCT i.irno) "
        "FROM issues i LEFT JOIN items m ON m.stock_code = i.stock_code "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY i.stock_code ORDER BY 6 DESC LIMIT ?", values + [limit]).fetchall()
    return [ItemRow(code, name, categories.group_name(group_key) if group_key else "",
                    qty, unit, net, slips)
            for code, name, group_key, qty, unit, net, slips in rows]


def group_totals(conn, parent: Iterable[str] = (), months: int = 12,
                 store: str | None = None, latest: str | None = None) -> list[dict[str, Any]]:
    """แยกตามกลุ่มของ ยา / เวชภัณฑ์ / พัสดุ / อื่น ๆ — ตอบว่าหน่วยงานนี้ใช้อะไรเป็นหลัก"""
    latest = latest or latest_period(conn)
    if not latest:
        return []
    path = departments.path_of(*list(parent)[:3] + [""] * (3 - len(list(parent)[:3])))
    where, values = _scope(months, latest, path, store, None, alias="i")
    rows = conn.execute(
        "SELECT COALESCE(m.item_group, ''), "
        "       COALESCE(SUM(CASE WHEN i.direction = 'out' THEN i.value ELSE -i.value END), 0) "
        "FROM issues i LEFT JOIN items m ON m.stock_code = i.stock_code "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY 1 ORDER BY 2 DESC", values).fetchall()
    return [{"key": key, "name": categories.group_name(key) if key else "ยังไม่ระบุหมวด",
             "net": net} for key, net in rows]
