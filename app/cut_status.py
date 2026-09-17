"""คลังไหนข้อมูลค้าง — ทั้งคลังที่ใช้ระบบ import และคลังที่เจ้าหน้าที่คีย์เอกสารเอง

ปัญหาที่ผู้ใช้เล่าเอง (16 กันยายน 2569):
    "คลังยาหรือคลังพัสดุยังทำ process เขาไม่เสร็จ เลยไม่มียอดจ่ายมา ปลายทางไม่ได้ import
     ยอดจากคลังใหญ่เข้าคลังย่อย ทำให้ตัดขายของ (import ยอดรวมรายวัน) รายวันไม่ได้"

ผลคือยอดคงคลังของคลังนั้น **สูงกว่าจริง** และไม่มีใครเห็นว่าค้างกี่วัน จนกว่าจะเปิดดูทีละคลัง
ในหน้าจอ SSB หน้านี้ตอบว่า "วันนี้ต้องตามใคร"

**คลังสองแบบ วัดคนละวิธี** (ยืนยันจากข้อมูลจริง 16 ก.ย. 2569)
    - `import` — ห้องจ่ายยา 7 แห่ง (I2 ER 99 O5 O6 P3 SMC) ระบบสร้างเอกสารเลขขึ้นต้นด้วยวันที่
      เช่น `20260907-I2-I/S1` ให้ทุกวันที่มีการจ่าย จึงวัดว่า "วันทำการไหนยังไม่มีเอกสาร"
    - `manual` — คลังใหญ่และคลังพัสดุ (2, 7, 6, PAN, OR, DN …) ไม่มีเอกสารแบบนั้นเลยสักใบ
      เพราะเจ้าหน้าที่คีย์เอกสารเอง จึงวัดว่า "เงียบไปกี่วันทำการ เทียบกับจังหวะปกติของคลังนั้นเอง"
      ใช้จังหวะของตัวเองเป็นเกณฑ์ เพราะบางคลังบันทึกทุกวัน บางคลังบันทึกสัปดาห์ละครั้งเป็นปกติ

**สิ่งที่ตัวเลขนี้ไม่รู้ ต้องบอกผู้ใช้เสมอ**
    - วันหยุดนักขัตฤกษ์: ยังไม่มีตารางวันหยุด จึงตัดเฉพาะเสาร์-อาทิตย์ออก วันหยุดราชการอื่น
      จะโผล่เป็นวันที่ขาดทั้งที่ถูกต้อง (ยังไม่เดาแทนผู้ใช้)
    - คลังที่ยังไม่ได้ดึงข้อมูลเข้าคลังข้อมูลของเรา จะไม่ปรากฏเลย ไม่ใช่ว่าคลังนั้นไม่มีปัญหา
"""
from datetime import date, timedelta
from statistics import median
from typing import NamedTuple

import stores

#: เลขเอกสารของการ import ขึ้นต้นด้วยวันที่ 8 หลัก เช่น 20260907-I2-I/S1
_IMPORT_DOC = "irno LIKE '20%' AND SUBSTR(irno, 1, 8) GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"

METHOD_IMPORT = "import"
METHOD_MANUAL = "manual"

#: ค้างอย่างน้อยเท่านี้วันทำการจึงเตือน — กันไม่ให้คลังที่บันทึกห่าง ๆ เป็นปกติขึ้นเตือนทุกวัน
MIN_BEHIND_DAYS = 2

#: ค้างเกินหนึ่งสัปดาห์ทำการ = หยุดยาว ไม่ใช่แค่ช้า — เส้นที่เราเลือกเอง ไม่ใช่กติกาโรงพยาบาล
STOPPED_DAYS = 5

SEVERITY_STOPPED = "stopped"
SEVERITY_LATE = "late"
SEVERITY_OK = "ok"


class StoreStatus(NamedTuple):
    store: str
    name: str
    method: str
    last_day: str                    # YYYYMMDD วันล่าสุดที่มีข้อมูล
    days_behind: int                 # วันทำการที่ผ่านไปหลังวันล่าสุดนั้น
    expected_gap: int                # จังหวะปกติของคลังนี้ (วันทำการ) — import = 1
    working_days_total: int
    working_days_done: int
    missing_working_days: list[str]  # ใช้กับคลัง import เท่านั้น
    documents: int

    @property
    def needs_attention(self) -> bool:
        """เตือนเฉพาะที่ค้างอยู่ "ตอนนี้" เท่านั้น

        เคยลองนับวันที่ขาดย้อนหลังทั้งช่วงเป็นสัญญาณเตือนด้วย แล้วได้ 9 จาก 14 คลังขึ้นแดง
        ซึ่งไร้ประโยชน์ เพราะเรายังไม่มีตารางวันหยุดราชการ วันหยุดทุกวันจึงนับเป็นวันที่ขาด
        วันที่ขาดย้อนหลังยังแสดงไว้เป็นข้อมูลประกอบ แต่ไม่ทำให้ขึ้นเตือน
        """
        return self.days_behind >= max(MIN_BEHIND_DAYS, self.expected_gap + 1)

    @property
    def severity(self) -> str:
        """แยก "หยุดยาว" ออกจาก "ช้ากว่าปกติ" — สองอย่างนี้ต้องตามคนละแบบ

        ตอนขยายการดึงข้อมูลไปทุกหมวด คลังในหน้านี้เพิ่มจาก 14 เป็น 21 และเข้าเกณฑ์เตือน
        พร้อมกัน 14 คลัง ซึ่งอ่านแล้วเหมือนระบบร้องหมาป่า ทั้งที่ตรวจดูแล้วมีไม่กี่คลัง
        ที่หยุดยาวจริง ๆ ส่วนที่เหลือช้ากว่าปกติแค่หนึ่งถึงสองวันทำการ

        เกณฑ์ "หนึ่งสัปดาห์ทำการ" เป็นเส้นที่เราเลือกเอง เพราะอธิบายได้ในประโยคเดียว
        **ไม่ใช่กติกาของโรงพยาบาล** หน้าจอต้องบอกข้อนี้ไว้เสมอ
        """
        if not self.needs_attention:
            return SEVERITY_OK
        return SEVERITY_STOPPED if self.days_behind >= STOPPED_DAYS else SEVERITY_LATE

    @property
    def reason(self) -> str:
        """คำอธิบายที่ผู้ใช้อ่านแล้วรู้ว่าทำไมถึงขึ้นเตือน — ทุกตัวเลขต้องอธิบายตัวเองได้"""
        if not self.needs_attention:
            return ""
        if self.method == METHOD_IMPORT:
            return f"ยังไม่ได้ตัดมา {self.days_behind} วันทำการ"
        return (f"เงียบมา {self.days_behind} วันทำการ "
                f"ปกติคลังนี้บันทึกทุก ๆ {self.expected_gap} วันทำการ")


def _to_date(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _working_days(start: date, end: date) -> list[date]:
    span = (end - start).days + 1
    return [day for day in (start + timedelta(days=i) for i in range(span)) if day.weekday() < 5]


def _typical_gap(active_days: list[str], working_keys: list[str]) -> int:
    """จังหวะปกติของคลัง — ระยะห่างกลางระหว่างวันทำการที่มีเอกสาร

    ใช้ค่ากลางไม่ใช่ค่าเฉลี่ย เพราะช่วงปิดยาวครั้งเดียว (เช่น สงกรานต์) ไม่ควรทำให้
    เกณฑ์ของทั้งคลังหลวมขึ้น
    """
    order = {day: index for index, day in enumerate(working_keys)}
    positions = sorted(order[day] for day in active_days if day in order)
    if len(positions) < 2:
        return len(working_keys)
    gaps = [b - a for a, b in zip(positions, positions[1:])]
    return max(1, int(median(gaps)))


def latest_data_day(connection) -> str | None:
    """วันล่าสุดที่มีข้อมูลในคลังข้อมูลของเรา — ใช้แทน "วันนี้"

    ต้นทางเป็นสำเนาที่คัดลอกวันละครั้ง ถ้าเทียบกับวันนี้จริง ทุกคลังจะดูค้างทั้งที่
    ยังไม่ถึงรอบคัดลอก
    """
    # ดูแค่สองงวดล่าสุด — เคยอ่านทั้งตาราง 2 ล้านแถว หน้านี้จึงช้าถึง 14 วินาทีหลังดึงทุกหมวด
    # เอกสารของวันไหนก็ตามถูกบันทึกในงวดเดียวกันหรืองวดหลังจากนั้นเสมอ สองงวดล่าสุดจึงพอ
    latest = connection.execute(
        "SELECT MAX(period) FROM issues WHERE LENGTH(period) = 6").fetchone()
    if not latest or not latest[0]:
        return None
    since = _previous_period(latest[0])
    row = connection.execute("""
        SELECT MAX(day) FROM (
            SELECT MAX(SUBSTR(irno, 1, 8)) AS day FROM issues
            WHERE period >= ? AND """ + _IMPORT_DOC + """
            UNION ALL
            SELECT MAX(REPLACE(SUBSTR(issued_at, 1, 10), '-', '')) FROM issues WHERE period >= ?
        )""", (since, since)).fetchone()
    return row[0] if row and row[0] else None


def _previous_period(period: str) -> str:
    year, month = int(period[:4]), int(period[4:6])
    return f"{year - 1:04d}12" if month == 1 else f"{year:04d}{month - 1:02d}"


def collect(connection, window_days: int = 90, as_of: str | None = None) -> dict:
    end_day = as_of or latest_data_day(connection)
    if not end_day:
        return {"as_of": None, "window_days": window_days, "stores": [], "needs_attention": [],
                "reason": "ยังไม่มีข้อมูลการจ่ายในคลังข้อมูลของ ERPLPH"}

    end = _to_date(end_day)
    start = end - timedelta(days=window_days - 1)
    working = _working_days(start, end)
    working_keys = [day.strftime("%Y%m%d") for day in working]
    working_set = set(working_keys)

    rows = connection.execute(f"""
        SELECT store,
               CASE WHEN {_IMPORT_DOC} THEN SUBSTR(irno, 1, 8)
                    ELSE REPLACE(SUBSTR(issued_at, 1, 10), '-', '') END AS day,
               CASE WHEN {_IMPORT_DOC} THEN 1 ELSE 0 END AS is_import,
               COUNT(DISTINCT irno) AS documents
        FROM issues
        WHERE period >= ?
        GROUP BY store, day, is_import
        HAVING day BETWEEN ? AND ?""",
        # เอกสารถูกบันทึกในงวดเดียวกับวันของมันหรือหลังจากนั้นเสมอ กรองงวดก่อนจึงไม่ตกหล่น
        # และไม่ต้องจัดกลุ่มข้อมูลทั้งสองปีงบประมาณก่อนค่อยทิ้ง
        (start.strftime("%Y%m"), start.strftime("%Y%m%d"), end_day)).fetchall()

    tally: dict[str, dict] = {}
    for store, day, is_import, documents in rows:
        entry = tally.setdefault(str(store), {"import": {}, "manual": {}})
        entry["import" if is_import else "manual"][str(day)] = documents

    result = []
    for store, seen in tally.items():
        use_import = bool(seen["import"])
        days = seen["import"] if use_import else seen["manual"]
        if not days:
            continue
        done = sorted(working_set & set(days))
        last_day = max(days)
        behind = len([key for key in working_keys if key > last_day])
        gap = 1 if use_import else _typical_gap(done, working_keys)
        result.append(StoreStatus(
            store=store,
            name=stores.store_name(store),
            method=METHOD_IMPORT if use_import else METHOD_MANUAL,
            last_day=last_day,
            days_behind=behind,
            expected_gap=gap,
            working_days_total=len(working_keys),
            working_days_done=len(done),
            missing_working_days=sorted(working_set - set(days)) if use_import else [],
            documents=sum(days.values()),
        ))

    # คลังที่ต้องตามอยู่บนสุด — คนเปิดหน้านี้มาเพื่อถามว่าวันนี้ต้องตามใคร
    result.sort(key=lambda item: (not item.needs_attention, -item.days_behind,
                                  -len(item.missing_working_days), item.store))
    return {
        "as_of": end_day,
        "from_day": start.strftime("%Y%m%d"),
        "window_days": window_days,
        "working_days": len(working_keys),
        "stores": result,
        "needs_attention": [item for item in result if item.needs_attention],
        "stopped": [item for item in result if item.severity == SEVERITY_STOPPED],
        "late": [item for item in result if item.severity == SEVERITY_LATE],
        "stopped_days": STOPPED_DAYS,
    }
