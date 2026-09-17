"""หน้าเว็บของ ERPLPH — ดูข้อมูลคลังแบบ Stock5 แต่ครบทุกประเภทของ และไม่มีส่วนส่งกระทรวง

กติกาหน้าจอที่ตกลงกับผู้ใช้ 16 กันยายน 2569
    1. หนึ่งหน้า = หนึ่งคำถามที่คนถามจริงทุกวัน
    2. ทุกตัวเลขกดลงไปหาเอกสารต้นทางได้
    3. ทุกตัวเลขบอกความน่าเชื่อถือของตัวเอง — โดยเฉพาะหน้านี้ที่ต้นทางเป็นสำเนาซึ่ง
       คัดลอกวันละครั้ง ถ้าไม่บอก คนจะเข้าใจว่าเป็นข้อมูลสด
    4. ค้นหาเดียวครอบจักรวาล — ช่องค้นหาบนแถบเมนูทุกหน้า

ผู้ใช้สั่ง 17 ก.ย. 2569: "อยากได้การดูข้อมูลเหมือน stock5 ครับ แต่ว่าตัดส่วนของข้อมูลส่งกระทรวงออก
และดูได้ทุกอย่าง ทุกประเภทของ ยา อาหาร พัสดุ งานจ้าง งานก่อสร้าง วัสดุคอมพิวเตอร์ทุกอย่าง"

    /                 ภาพรวม (Dashboard ของ Stock5)
    /items            ค้นหารายการ (ค้นหาและตรวจสอบข้อมูล ของ Stock5)
    /items/<รหัส>     รายละเอียดรายการ (รายละเอียดเวชภัณฑ์ ของ Stock5)
    /lists/<ชนิด>     รายการที่ประกอบเป็นตัวเลขบนการ์ด — ใกล้หมดอายุ ค้างนิ่ง
    /departments      แต่ละแผนกเบิกอะไรไปเท่าไร
    /cut-status       คลังไหนข้อมูลค้าง

อ่านจากฐานข้อมูลของ ERPLPH เท่านั้น ไม่ต่อฐานข้อมูลโรงพยาบาลตอนเปิดหน้า เพื่อให้หน้าเปิดเร็ว
และไม่เพิ่มภาระให้เครื่องต้นทาง (ดู docs/system_design.md ข้อ 2)
"""
from datetime import date
import math
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlencode

from flask import Flask, Response, render_template, request, send_from_directory
from markupsafe import Markup, escape

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "app"))

import categories  # noqa: E402
import cut_status  # noqa: E402
import department_usage  # noqa: E402
import departments  # noqa: E402
import metrics  # noqa: E402
import overview  # noqa: E402
import stock5_api  # noqa: E402
import stores  # noqa: E402
import warehouse_db  # noqa: E402

WINDOW_CHOICES = (30, 90, 180)
DEFAULT_WINDOW = 90

#: ช่วงเดือนที่ให้เลือก — 12 เดือนคือค่าตั้งต้นเพราะของหลายอย่างเบิกปีละครั้ง
MONTH_CHOICES = (3, 6, 12, 24)
DEFAULT_MONTHS = 12

#: รายการตามการ์ดแสดงได้ไม่เกินนี้ เกินแล้วบอกว่าตัดไว้ ไม่ให้หน้าหนักจนเปิดไม่ขึ้น
LIST_LIMIT = 500

NO_WAREHOUSE = ("ยังไม่มีคลังข้อมูลของ ERPLPH บนเครื่องนี้ "
                "(warehouse_data/erplph.db) ให้ดึงข้อมูลก่อน")

THAI_MONTHS = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.register_blueprint(stock5_api.bp)

#: หน้าจอ Angular ที่ยกมาจาก Stock5 (frontend/ build ออกมาที่นี่) — แบบระบบข้อ 3 "Angular เหมือน Stock5"
ANGULAR_DIST = BASE_DIR / "app" / "angular_dist"


def open_warehouse():
    """เปิดฐานข้อมูลของ ERPLPH แบบอ่านอย่างเดียว — หน้าเว็บไม่มีเหตุผลต้องเขียนอะไรลงคลังข้อมูล"""
    path = Path(warehouse_db.DB_PATH)
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


# --------------------------------------------------------------------------- ตัวช่วยแสดงผล

def money(value) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "—"


def price(value) -> str:
    """ราคาต่อหน่วยบางรายการต่ำกว่าหนึ่งบาท ปัดทิ้งแล้วจะเห็นเป็นศูนย์"""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.2f}" if abs(number) < 1000 else f"{number:,.0f}"


def short_money(value) -> str:
    """ตัวเลขใหญ่ในการ์ด — ผู้บริหารอ่าน "1,585.4 ล้าน" ได้เร็วกว่าเลขสิบหลัก"""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:,.1f} ล้าน"
    if abs(number) >= 1_000:
        return f"{number / 1_000:,.1f} พัน"
    return f"{number:,.0f}"


def nice_ceiling(value) -> float:
    """เพดานแกนที่เป็นตัวเลขกลม ๆ หารสี่ลงตัว"""
    number = float(value or 0)
    if number <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(number))
    for step in (1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10):
        if step * magnitude >= number:
            return step * magnitude
    return 10 * magnitude


def thai_period(period) -> str:
    text = str(period or "")
    if len(text) < 6 or not text[:6].isdigit():
        return text
    return f"{THAI_MONTHS[int(text[4:6]) - 1]} {(int(text[:4]) + 543) % 100:02d}"


def thai_day(day) -> str:
    text = str(day or "").replace("-", "")[:8]
    if len(text) < 8 or not text.isdigit():
        return ""
    return f"{int(text[6:8])} {THAI_MONTHS[int(text[4:6]) - 1]} {(int(text[:4]) + 543) % 100:02d}"


def expiry_badge(day) -> Markup:
    """วันหมดอายุพร้อมระดับ — ใช้ข้อความกำกับเสมอ ไม่ใช้สีอย่างเดียว"""
    text = str(day or "")
    if len(text) < 8:
        return Markup("—")
    today = date.today().strftime("%Y%m%d")
    critical = metrics._shift(today, metrics.EXPIRY_CRITICAL_MONTHS)
    warning = metrics._shift(today, metrics.EXPIRY_WARNING_MONTHS)
    label = escape(thai_day(text))
    if text < today:
        return Markup(f'{label} <span class="pill late">หมดอายุแล้ว</span>')
    if text < critical:
        return Markup(f'{label} <span class="pill late">ไม่ถึง 3 เดือน</span>')
    if text < warning:
        return Markup(f'{label} <span class="pill slow">3–6 เดือน</span>')
    return label


def query(**values) -> str:
    return urlencode({key: value for key, value in values.items() if value not in (None, "")})


app.jinja_env.globals.update(
    money=money, price=price, short_money=short_money, nice_ceiling=nice_ceiling,
    thai_period=thai_period, thai_day=thai_day, expiry_badge=expiry_badge, query=query,
    store_name=stores.store_name, group_name=categories.group_name)
app.jinja_env.filters["store_name"] = stores.store_name


# --------------------------------------------------------------------------- อ่านตัวเลือกจาก URL

def _months() -> int:
    try:
        months = int(request.args.get("months", DEFAULT_MONTHS))
    except ValueError:
        return DEFAULT_MONTHS
    return months if months in MONTH_CHOICES else DEFAULT_MONTHS


def _group() -> str | None:
    group = request.args.get("group") or None
    return group if group in categories.GROUP_BY_KEY else None


def _store() -> str | None:
    store = (request.args.get("store") or "").strip()
    return store if store in stores.STORES else None


def _search_text() -> str:
    return (request.args.get("q") or "").strip()[:60]


# --------------------------------------------------------------------------- หน้า

@app.route("/overview")
def overview_page():
    """ภาพรวมทั้งโรงพยาบาล — เทียบได้กับหน้า Dashboard ของ Stock5"""
    months, group, store = _months(), _group(), _store()
    context = dict(active="overview", months=months, group=group, store=store,
                   month_choices=MONTH_CHOICES, group_choices=categories.GROUPS)
    connection = open_warehouse()
    if connection is None:
        return render_template("overview.html", problem=NO_WAREHOUSE, **context)
    try:
        latest = overview.latest_period(connection)
        table = overview.group_table(connection, months=months, store=store, latest=latest)
        head = overview.headline(connection, months=months, store=store, group=group, latest=latest)
        trend = overview.monthly(connection, months=months, store=store, group=group, latest=latest)
        receipts = overview.receipt_coverage(connection, months=months)
        store_choices = [code for (code,) in connection.execute(
            "SELECT store FROM balances GROUP BY store ORDER BY SUM(value) DESC")]
    finally:
        connection.close()
    if not latest:
        return render_template("overview.html", problem=table["reason"], **context)

    def link(**changes):
        values = {"months": months, "group": group, "store": store, **changes}
        if values["months"] == DEFAULT_MONTHS:
            values["months"] = None
        return "/overview?" + query(**values)

    order = [found.key for found in categories.GROUPS]
    rows = sorted(table["rows"], key=lambda row: order.index(row["key"]))
    stock_parts = [{"key": row["key"], "name": row["name"], "value": row["stock"]} for row in rows]
    used_parts = [{"key": row["key"], "name": row["name"], "value": row["used"]} for row in rows]
    return render_template(
        "overview.html", problem=None, table=table, head=head, trend=trend, receipts=receipts,
        store_choices=[code for code in store_choices if stores.is_active(code)][:14], link=link,
        stock_parts=stock_parts, stock_total=sum(part["value"] for part in stock_parts),
        used_parts=used_parts, used_total=sum(max(part["value"], 0) for part in used_parts),
        **context)


@app.route("/items")
def items_page():
    """ค้นหารายการทุกประเภท — ค้นด้วยรหัสหรือชื่อ หรือเลือกกลุ่มเพื่อดูรายการเด่น"""
    text, group, store = _search_text(), _group(), _store()
    context = dict(active="items", search_text=text, group=group, store=store,
                   months=DEFAULT_MONTHS, group_choices=categories.GROUPS,
                   limit=overview.SEARCH_LIMIT)
    connection = open_warehouse()
    if connection is None:
        return render_template("items.html", problem=NO_WAREHOUSE, rows=[], **context)
    try:
        rows = overview.search(connection, text, group=group, store=store)
        receipts = overview.receipt_coverage(connection)
        catalogue_size = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        connection.close()
    return render_template("items.html", problem=None, rows=rows, receipts=receipts,
                           catalogue_size=catalogue_size, **context)


@app.route("/items/<path:code>")
def item_page(code):
    """รายละเอียดรายการ — แบบหน้ารายละเอียดเวชภัณฑ์ของ Stock5 ใช้ได้กับของทุกประเภท"""
    code = (code or "").strip()[:30]
    context = dict(active="items", code=code, d=None)
    connection = open_warehouse()
    if connection is None:
        return render_template("item.html", problem=NO_WAREHOUSE, **context)
    try:
        detail = overview.item(connection, code)
        receipts = overview.receipt_coverage(connection)
    finally:
        connection.close()
    status = 200 if detail else 404
    return render_template("item.html", problem=None, receipts=receipts,
                           **{**context, "d": detail}), status


LISTS = {
    "expiring": dict(
        title="ใกล้หมดอายุ / หมดอายุแล้ว",
        subtitle="ล็อตที่หมดอายุแล้ว หรือจะหมดภายใน 6 เดือน เรียงตามระดับ แล้วตามมูลค่า",
        explanation=("นับจากภาพคงคลังล่าสุดของแต่ละคลัง · ระดับ \"ไม่ถึง 3 เดือน\" และ \"3–6 เดือน\" "
                     "ตามเกณฑ์ที่ผู้ใช้กำหนด 12 ก.ย. 2569 · ล็อตที่ระบบไม่ได้บันทึกวันหมดอายุ "
                     "และล็อตที่ไม่เหลือทั้งจำนวนและมูลค่า ไม่อยู่ในรายการนี้")),
    "dormant": dict(
        title="ของค้างนิ่ง",
        subtitle="ยังมีอยู่ในคลัง แต่ไม่มีการจ่ายหรือโอนออกเลยใน 6 เดือน เรียงตามมูลค่า",
        explanation=("นับการเคลื่อนไหวออกทุกแบบ ทั้งจ่ายและโอน เพราะคลังหลักจ่ายออกด้วยการโอนเป็นหลัก · "
                     "ครุภัณฑ์ที่รอส่งมอบก็อาจอยู่ในรายการนี้ ต้องดูประกอบกับกลุ่มของ")),
}


@app.route("/lists/<kind>")
def list_page(kind):
    """รายการที่ประกอบเป็นตัวเลขบนการ์ดของหน้าภาพรวม"""
    if kind not in LISTS:
        kind = "expiring"
    group, store = _group(), _store()
    context = dict(active="lists", kind=kind, group=group, store=store,
                   group_choices=categories.GROUPS, **LISTS[kind])
    connection = open_warehouse()
    if connection is None:
        return render_template("lists.html", problem=NO_WAREHOUSE, rows=[], **context)
    try:
        if kind == "expiring":
            rows = overview.expiring(connection, store=store, group=group, limit=LIST_LIMIT + 1)
        else:
            rows = overview.dormant(connection, store=store, group=group, limit=LIST_LIMIT + 1)
    finally:
        connection.close()
    return render_template("lists.html", problem=None, rows=rows[:LIST_LIMIT],
                           truncated=len(rows) > LIST_LIMIT, **context)


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
        return render_template("cut_status.html", report=None, window=window, active="cut",
                               windows=WINDOW_CHOICES, problem=NO_WAREHOUSE)
    try:
        report = cut_status.collect(connection, window_days=window)
    finally:
        connection.close()
    return render_template("cut_status.html", report=report, window=window, active="cut",
                           windows=WINDOW_CHOICES, problem=None,
                           warning_days=cut_status.MIN_BEHIND_DAYS)


@app.route("/departments")
def departments_page():
    """แต่ละหน่วยงานเบิกอะไรไปเท่าไร — กดลงไปได้ 3 ชั้น แล้วจบที่รายการของจริง"""
    months = _months()
    # รหัสหน่วยงานมาจาก URL จึงเชื่อไม่ได้ ตัดความยาวและส่งเป็นพารามิเตอร์เสมอ
    parent = departments.path_of(*(request.args.get(name, "")[:8]
                                   for name in ("div", "dept", "section")))
    depth = sum(1 for part in parent if part)
    group, store = _group(), _store()

    connection = open_warehouse()
    if connection is None:
        return render_template("departments.html", problem=NO_WAREHOUSE, months=months,
                               month_choices=MONTH_CHOICES, parent=parent, depth=depth,
                               active="departments")
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
        deepest=len(department_usage.LEVELS), active="departments")


def _angular_index():
    index = ANGULAR_DIST / "index.html"
    if not index.is_file():
        return Response("ยังไม่ได้ build หน้าจอ — รัน build_frontend.bat", status=503,
                        mimetype="text/plain; charset=utf-8")
    html = index.read_text(encoding="utf-8")
    prefix = request.script_root.rstrip("/")
    if prefix:
        html = html.replace('<base href="/">', f'<base href="{prefix}/">')
    response = Response(html, mimetype="text/html")
    # index.html ชี้ไปที่ไฟล์ bundle ชุดใหม่ทุกครั้งที่ build ถ้าเบราว์เซอร์จำไว้ หน้าจอใหม่จะไม่ขึ้น
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.route("/")
def angular_root():
    return _angular_index()


@app.route("/<path:asset_path>")
def angular_assets(asset_path):
    """ไฟล์ของหน้าจอ Angular และเส้นทางฝั่งเบราว์เซอร์ (เช่น /dashboard /drugs/1021030)"""
    if asset_path.startswith("api/"):
        return {"status": "error", "message": "ไม่พบ API นี้"}, 404
    candidate = ANGULAR_DIST / asset_path
    if candidate.is_file() and ANGULAR_DIST in candidate.resolve().parents:
        return send_from_directory(ANGULAR_DIST, asset_path)
    return _angular_index()


def warm_cache():
    """คำนวณหน้าแดชบอร์ดและทะเบียนค้นหาไว้ก่อน — ครั้งแรกใช้หลายวินาที ไม่ให้ผู้ใช้คนแรกต้องรอ"""
    connection = open_warehouse()
    if connection is None:
        return
    try:
        stock5_api._cached(("summary", stock5_api.TARGET_DAYS, stock5_api.USAGE_MONTHS,
                            stock5_api.EXPIRY_DAYS, None, None),
                           lambda: stock5_api.build_summary(connection))
        stock5_api._cached(("catalog", None, None), lambda: stock5_api.build_catalog(connection))
    finally:
        connection.close()


def main():
    import threading
    threading.Thread(target=warm_cache, daemon=True).start()
    try:
        from waitress import serve
    except ImportError:
        app.run(host="127.0.0.1", port=8090, debug=False, threaded=True)
    else:
        serve(app, host="127.0.0.1", port=8090, threads=8)


if __name__ == "__main__":
    main()
