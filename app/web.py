"""หน้าเว็บของ ERPLPH — หน้าแรกคือ "สถานะการตัดขายของคลังย่อย"

กติกาหน้าจอที่ตกลงกับผู้ใช้ 16 กันยายน 2569
    1. หนึ่งหน้า = หนึ่งคำถามที่คนถามจริงทุกวัน
    2. ทุกตัวเลขกดลงไปหาเอกสารต้นทางได้
    3. ทุกตัวเลขบอกความน่าเชื่อถือของตัวเอง — โดยเฉพาะหน้านี้ที่ต้นทางเป็นสำเนาซึ่ง
       คัดลอกวันละครั้ง ถ้าไม่บอก คนจะเข้าใจว่าเป็นข้อมูลสด
    4. ค้นหาเดียวครอบจักรวาล (ยังไม่ทำในรอบนี้)

อ่านจากฐานข้อมูลของ ERPLPH เท่านั้น ไม่ต่อฐานข้อมูลโรงพยาบาลตอนเปิดหน้า เพื่อให้หน้าเปิดเร็ว
และไม่เพิ่มภาระให้เครื่องต้นทาง (ดู docs/system_design.md ข้อ 2)
"""
from pathlib import Path
import sqlite3
import sys

from flask import Flask, render_template, request

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "app"))

import categories  # noqa: E402
import cut_status  # noqa: E402
import department_usage  # noqa: E402
import departments  # noqa: E402
import stores  # noqa: E402
import warehouse_db  # noqa: E402

WINDOW_CHOICES = (30, 90, 180)
DEFAULT_WINDOW = 90

#: ช่วงเดือนที่หน้ารายหน่วยงานให้เลือก — 12 เดือนคือค่าตั้งต้นเพราะของหลายอย่างเบิกปีละครั้ง
MONTH_CHOICES = (3, 6, 12, 24)
DEFAULT_MONTHS = 12

NO_WAREHOUSE = ("ยังไม่มีคลังข้อมูลของ ERPLPH บนเครื่องนี้ "
                "(warehouse_data/erplph.db) ให้ดึงข้อมูลก่อน")

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


def open_warehouse():
    """เปิดฐานข้อมูลของ ERPLPH แบบอ่านอย่างเดียว — หน้าเว็บไม่มีเหตุผลต้องเขียนอะไรลงคลังข้อมูล"""
    path = Path(warehouse_db.DB_PATH)
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


@app.route("/")
@app.route("/cut-status")
def cut_status_page():
    try:
        window = int(request.args.get("days", DEFAULT_WINDOW))
    except ValueError:
        window = DEFAULT_WINDOW
    if window not in WINDOW_CHOICES:
        window = DEFAULT_WINDOW

    connection = open_warehouse()
    if connection is None:
        return render_template("cut_status.html", report=None, window=window,
                               windows=WINDOW_CHOICES, problem=NO_WAREHOUSE)
    try:
        report = cut_status.collect(connection, window_days=window)
    finally:
        connection.close()
    return render_template("cut_status.html", report=report, window=window,
                           windows=WINDOW_CHOICES, problem=None,
                           warning_days=cut_status.MIN_BEHIND_DAYS)


@app.route("/departments")
def departments_page():
    """แต่ละหน่วยงานเบิกอะไรไปเท่าไร — กดลงไปได้ 3 ชั้น แล้วจบที่รายการของจริง"""
    try:
        months = int(request.args.get("months", DEFAULT_MONTHS))
    except ValueError:
        months = DEFAULT_MONTHS
    if months not in MONTH_CHOICES:
        months = DEFAULT_MONTHS

    # รหัสหน่วยงานมาจาก URL จึงเชื่อไม่ได้ ตัดความยาวและส่งเป็นพารามิเตอร์เสมอ
    parent = departments.path_of(*(request.args.get(name, "")[:8]
                                   for name in ("div", "dept", "section")))
    depth = sum(1 for part in parent if part)
    group = request.args.get("group") or None
    if group not in {found.key for found in categories.GROUPS}:
        group = None
    store = request.args.get("store") or None
    if store is not None and store not in stores.STORES:
        store = None

    connection = open_warehouse()
    if connection is None:
        return render_template("departments.html", problem=NO_WAREHOUSE, months=months,
                               month_choices=MONTH_CHOICES, parent=parent, depth=depth)
    try:
        latest = department_usage.latest_period(connection)
        level = department_usage.LEVELS[min(depth, len(department_usage.LEVELS) - 1)]
        report = department_usage.breakdown(connection, level=level, parent=parent, months=months,
                                            store=store, group=group, latest=latest)
        groups = department_usage.group_totals(connection, parent=parent, months=months,
                                               store=store, latest=latest)
        items = department_usage.items_of(connection, parent=parent, months=months,
                                          store=store, group=group, latest=latest) if depth else []
    finally:
        connection.close()

    trail = [{"name": departments.name_of(*parent[:step + 1]),
              "args": {name: parent[index] for index, name in enumerate(("div", "dept", "section"))
                       if index <= step and parent[index]}}
             for step in range(depth)]
    return render_template(
        "departments.html", problem=None, report=report, groups=groups, items=items,
        months=months, month_choices=MONTH_CHOICES, parent=parent, depth=depth,
        trail=trail, group=group, store=store, name_source=departments.source(),
        store_name=stores.store_name, deepest=len(department_usage.LEVELS))


def main():
    app.run(host="127.0.0.1", port=8090, debug=False)


if __name__ == "__main__":
    main()
