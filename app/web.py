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
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote, urlencode

from flask import Flask, Response, redirect, render_template, request, send_from_directory, url_for
from markupsafe import Markup, escape

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "app"))

import categories  # noqa: E402
import cut_status  # noqa: E402
import department_usage  # noqa: E402
import departments  # noqa: E402
import executive_analytics  # noqa: E402
import his_login  # noqa: E402
import metrics  # noqa: E402
import overview  # noqa: E402
import stock5_api  # noqa: E402
import stores  # noqa: E402
import substores  # noqa: E402
import warehouse_db  # noqa: E402

WINDOW_CHOICES = (30, 90, 180)
DEFAULT_WINDOW = 90

#: ช่วงเดือนที่ให้เลือก — 12 หรือ 18 เดือนตามความต้องการดูข้อมูลย้อนหลัง
MONTH_CHOICES = (3, 6, 12, 18, 24)
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
    """เปิดฐานข้อมูลของ ERPLPH แบบอ่านอย่างเดียว พร้อมตั้งค่า Pragmas Concurrency (WAL + busy_timeout)"""
    path = Path(warehouse_db.DB_PATH)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    executive_analytics.apply_sqlite_concurrency_pragmas(conn)
    return conn


def get_current_user():
    """ดึงข้อมูลผู้ใช้ที่ล็อกอินอยู่ในปัจจุบันจาก session cookie"""
    token = request.cookies.get(his_login.SESSION_COOKIE_NAME) or request.cookies.get(his_login.ALT_COOKIE_NAME, "")
    session = his_login.get_session(token)
    return his_login.public_user(session)


@app.context_processor
def inject_global_data():
    conn = open_warehouse()
    if conn is None:
        freshness = {
            "last_sync_thai": "ยังไม่มีฐานข้อมูล",
            "badge_class": "slow",
            "badge_label": "ไม่มีข้อมูล",
        }
    else:
        try:
            freshness = executive_analytics.get_data_freshness_status(conn)
        finally:
            conn.close()
    return dict(data_freshness=freshness, current_user=get_current_user())


@app.before_request
def enforce_login_for_pages():
    """ตรวจสอบการเข้าสู่ระบบสำหรับหน้าเว็บ HTML ของระบบ

    - ในโหมดทดสอบ (app.testing) ปล่อยผ่านเพื่อให้ Unit Test ทำงานได้
    - หน้าจอ Angular SPA (/<path>) มี authGuard คุมทางฝั่งหน้าบ้านอยู่แล้ว
    - ปล่อยผ่าน API และ static assets ทั้งหมด
    - หน้า HTML ที่เรนเดอร์จากฝั่งเซิร์ฟเวอร์ (เช่น /substores, /departments) หากยังไม่ล็อกอิน
      จะส่งต่อไปยังหน้า /login พร้อมจำ returnUrl ไว้กลับมาหน้าเดิม
    """
    if app.testing or os.environ.get("ERPLPH_DISABLE_AUTH", "").lower() in ("1", "true", "yes"):
        return None

    path = request.path
    if (
        path.startswith("/api/")
        or path in ("/", "/login")
        or "." in path
        or path.startswith("/dashboard")
        or path.startswith("/drugs/")
        or path.startswith("/drug-search")
        or path.startswith("/purchasing")
        or path.startswith("/procurement-plan")
        or path.startswith("/monitor")
    ):
        return None

    if get_current_user() is None:
        target = request.full_path if request.query_string else request.path
        return redirect("/login?returnUrl=" + quote(target, safe="/:?=&"))
    return None


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
    """แต่ละหน่วยงานเบิกอะไรไปเท่าไร — หน้าจอแบ่งครึ่ง: ซ้าย=ข้อมูลหน่วยเบิก ขวา=ของที่เบิกและใครเบิกมากที่สุด"""
    months = _months()
    # รหัสหน่วยงานระดับเจาะลึก (hierarchy breadcrumb)
    parent = departments.path_of(*(request.args.get(name, "")[:8]
                                   for name in ("div", "dept", "section")))
    depth = sum(1 for part in parent if part)
    group, store = _group(), _store()

    selected_raw = (request.args.get("selected") or "").strip()[:24]

    connection = open_warehouse()
    if connection is None:
        return render_template(
            "departments.html", problem=NO_WAREHOUSE, months=months,
            month_choices=MONTH_CHOICES, parent=parent, depth=depth,
            trail=[], groups=[], items=[], top_reqs=[],
            target_summary=department_usage.TargetSummary(0.0, 0, 0, 0),
            target_groups=[], target_code="", target_name="",
            target_full_name="", selected_code="", active="departments")
    try:
        latest = department_usage.latest_period(connection)
        level = department_usage.LEVELS[min(depth, len(department_usage.LEVELS) - 1)]
        report = department_usage.breakdown(connection, level=level, parent=parent, months=months,
                                            store=store, group=group, latest=latest)
        groups = department_usage.group_totals(connection, parent=parent, months=months,
                                               store=store, latest=latest)

        # กำหนดหน่วยงานเป้าหมายที่จะแสดงรายละเอียดในฝั่งขวา
        selected_path = None
        if selected_raw == "all":
            selected_path = ()
        elif selected_raw:
            parts = [p.strip() for p in selected_raw.split("-") if p.strip()][:3]
            selected_path = departments.path_of(*(parts + [""] * (3 - len(parts))))
        elif depth > 0:
            selected_path = parent
        elif report["rows"]:
            selected_path = report["rows"][0].path
        else:
            selected_path = ()

        target_path = selected_path
        target_code = "-".join([p for p in target_path if p])
        if any(target_path):
            target_name = departments.name_of(*target_path)
            target_full_name = departments.full_name(*target_path)
        else:
            target_name = "ทั้งโรงพยาบาล"
            target_full_name = "ภาพรวมทั้งโรงพยาบาล"

        target_summary = department_usage.target_summary(
            connection, parent=target_path, months=months, store=store, group=group, latest=latest)
        target_groups = department_usage.group_totals(
            connection, parent=target_path, months=months, store=store, latest=latest)
        top_reqs = department_usage.top_requisitioners(
            connection, parent=target_path, months=months, limit=10, store=store, group=group, latest=latest)
        items = department_usage.items_of(
            connection, parent=target_path, months=months, store=store, group=group, latest=latest, limit=50)
    finally:
        connection.close()

    trail = [{"name": departments.name_of(*parent[:step + 1]),
              "args": {name: parent[index] for index, name in enumerate(("div", "dept", "section"))
                       if index <= step and parent[index]}}
             for step in range(depth)]
    return render_template(
        "departments.html", problem=None, report=report, groups=groups, items=items,
        top_reqs=top_reqs, target_summary=target_summary, target_groups=target_groups,
        target_path=target_path, target_code=target_code, target_name=target_name,
        target_full_name=target_full_name, selected_code=target_code,
        months=months, month_choices=MONTH_CHOICES, parent=parent, depth=depth,
        trail=trail, group=group, store=store, name_source=departments.source(),
        deepest=len(department_usage.LEVELS), active="departments")


# --------------------------------------------------------------------------- แดชบอร์ดผู้บริหารและธรรมาภิบาล

@app.route("/top10")
def top10_page():
    store = _store()
    months = _months()
    group = _group()
    conn = open_warehouse()
    if conn is None:
        return render_template("top10.html", problem=NO_WAREHOUSE, active="top10")
    try:
        overdue = executive_analytics.top10_overdue_pos(conn, group=group)
        fast_moving = executive_analytics.top10_fast_moving(conn, store=store, months=months, group=group)
        frequent = executive_analytics.top10_frequent_purchases(conn, store=store, months=months, group=group)
        high_val = executive_analytics.top10_highest_value(conn, store=store, group=group)
        top_depts = executive_analytics.top10_top_requisitioning_depts(conn, months=months, group=group)

        active_stores = [s for s in stores.STORES.values() if s.active]
        sel_store = store or "2"
        store_items = executive_analytics.top10_items_by_store(sel_store, conn, months=months, group=group)
        store_reqs = executive_analytics.top10_requisitioners_by_store(sel_store, conn, months=months, group=group)
    finally:
        conn.close()

    return render_template(
        "top10.html", problem=None, active="top10", store=store, selected_store=sel_store,
        months=months, month_choices=MONTH_CHOICES, stores=active_stores,
        group=group, group_choices=categories.GROUPS,
        overdue=overdue, fast_moving=fast_moving, frequent=frequent, high_val=high_val,
        top_depts=top_depts, store_items=store_items, store_reqs=store_reqs)


@app.route("/procure-to-pay")
@app.route("/purchasing")
def procure_to_pay_page():
    conn = open_warehouse()
    if conn is None:
        return render_template("procure_to_pay.html", problem=NO_WAREHOUSE, active="p2p")
    try:
        pipeline = executive_analytics.procure_to_pay_pipeline(conn)
    finally:
        conn.close()
    return render_template("procure_to_pay.html", problem=None, active="p2p", **pipeline)


@app.route("/substores")
def substores_page():
    raw_store = (request.args.get("store") or "ALL").strip()
    is_ward = substores.is_ward(raw_store)
    current_tab = (request.args.get("tab") or "substores").strip()

    try:
        raw_months = int(request.args.get("months", 18))
    except (TypeError, ValueError):
        raw_months = 18
    months = raw_months if raw_months in MONTH_CHOICES else 18

    conn = open_warehouse()
    if conn is None:
        return render_template(
            "substores.html", problem=NO_WAREHOUSE, active="substores",
            current_store=raw_store, is_ward=is_ward, months=months,
            month_choices=MONTH_CHOICES, current_tab=current_tab,
            pharmacy_stores=substores.PHARMACY_SUBSTORES,
            clinical_stores=substores.CLINICAL_SUBSTORES,
            wards=substores.WARDS,
            kpis={}, transfers_received=[], pending_transfers=[],
            amc_mos_list=[], expiring_list=[], ward_dispensations=[],
            ward_info={}, ward_kpis={}, ward_sources=[], ward_top_items=[],
            ward_monthly_trend={}, substore_monthly_trend={}, leaders_data={})

    try:
        leaders_data = {}
        if current_tab == "leaders":
            leaders_data = substores.get_top_requisition_leaders_dashboard(conn, months=months)

        is_all_stores = raw_store.upper() in ("ALL", "TOTAL", "HOSPITAL")
        if is_all_stores:
            h_data = substores.get_hospital_all_stores_analytics(conn, months=months)
            kpis = h_data["kpis"]
            all_groups = h_data["groups_data"]
            hospital_top_items = h_data["top_items"]
            substore_monthly_trend = h_data["monthly_trend"]
            amc_mos_list = substores.get_amc_and_mos_list(conn, "ALL", limit=100)
            reorder_recommendations = substores.get_requisition_recommendations(conn, "ALL", target_mos=1.0)
            expiring_data = substores.get_expiring_medicines(conn, "ALL", limit=100)
            ward_dispensations = substores.get_ward_dispensations(conn, "ALL", limit=25, months=months)
            transfers_received = substores.get_transfers_received(conn, "ALL", limit=50, months=months)
            pending_transfers = substores.get_pending_transfers(conn, "ALL", months=12)
            return render_template(
                "substores.html", problem=None, active="substores",
                current_store="ALL", is_ward=False, is_all_stores=True,
                current_tab=current_tab, leaders_data=leaders_data,
                months=months, month_choices=MONTH_CHOICES,
                pharmacy_stores=substores.PHARMACY_SUBSTORES,
                clinical_stores=substores.CLINICAL_SUBSTORES,
                wards=substores.WARDS,
                kpis=kpis, all_groups=all_groups, hospital_top_items=hospital_top_items,
                transfers_received=transfers_received, pending_transfers=pending_transfers,
                amc_mos_list=amc_mos_list, reorder_recommendations=reorder_recommendations,
                expiring_data=expiring_data, ward_dispensations=ward_dispensations,
                substore_monthly_trend=substore_monthly_trend)
        elif is_ward:
            ward_info = substores.get_ward_info(raw_store)
            ward_data = substores.get_ward_analytics(conn, ward_info["dept_code"], months=months)
            ward_monthly_trend = substores.get_ward_monthly_trend(conn, ward_info["dept_code"], months=months)
            pending_transfers = substores.get_pending_transfers(conn, ward_info["dept_code"], months=12)
            return render_template(
                "substores.html", problem=None, active="substores",
                current_store=ward_info["code"], is_ward=True, is_all_stores=False,
                current_tab=current_tab, leaders_data=leaders_data,
                ward_info=ward_info,
                ward_kpis=ward_data["kpis"],
                ward_sources=ward_data["source_stores"],
                ward_top_items=ward_data["top_items"],
                ward_monthly_trend=ward_monthly_trend,
                pending_transfers=pending_transfers,
                reorder_recommendations={},
                months=months, month_choices=MONTH_CHOICES,
                pharmacy_stores=substores.PHARMACY_SUBSTORES,
                clinical_stores=substores.CLINICAL_SUBSTORES,
                wards=substores.WARDS,
                kpis={})
        else:
            store_code = raw_store.upper()
            valid_codes = [s["code"] for s in substores.PHARMACY_SUBSTORES + substores.CLINICAL_SUBSTORES]
            if store_code not in valid_codes:
                store_code = "I2"
            kpis = substores.get_substore_kpis(conn, store_code, months=months)
            transfers_received = substores.get_transfers_received(conn, store_code, limit=50, months=months)
            pending_transfers = substores.get_pending_transfers(conn, store_code, months=12)
            amc_mos_list = substores.get_amc_and_mos_list(conn, store_code, limit=100)
            reorder_recommendations = substores.get_requisition_recommendations(conn, store_code, target_mos=1.0)
            expiring_data = substores.get_expiring_medicines(conn, store_code, limit=100)
            ward_dispensations = substores.get_ward_dispensations(conn, store_code, limit=20, months=months)
            substore_monthly_trend = substores.get_substore_monthly_trend(conn, store_code, months=months)
            return render_template(
                "substores.html", problem=None, active="substores",
                current_store=store_code, is_ward=False, is_all_stores=False,
                current_tab=current_tab, leaders_data=leaders_data,
                months=months, month_choices=MONTH_CHOICES,
                pharmacy_stores=substores.PHARMACY_SUBSTORES,
                clinical_stores=substores.CLINICAL_SUBSTORES,
                wards=substores.WARDS,
                kpis=kpis, transfers_received=transfers_received,
                pending_transfers=pending_transfers, amc_mos_list=amc_mos_list,
                reorder_recommendations=reorder_recommendations,
                expiring_data=expiring_data, ward_dispensations=ward_dispensations,
                substore_monthly_trend=substore_monthly_trend)
    finally:
        conn.close()


@app.route("/api/substores/item-detail")
def api_substore_item_detail():
    """ดึงข้อมูลเจาะลึกของยาหรือเวชภัณฑ์สำหรับคลังย่อยหรือวอร์ด (JSON API สำหรับ Modal & Search)"""
    store = (request.args.get("store") or "I2").strip()
    code = (request.args.get("code") or request.args.get("q") or "").strip()
    if not code:
        return {"status": "error", "message": "ไม่ได้ระบุรหัสหรือชื่อสินค้า"}, 400
    conn = open_warehouse()
    if conn is None:
        return {"status": "error", "message": "ฐานข้อมูลไม่พร้อมใช้งาน"}, 503
    try:
        # หากระบุเป็นชื่อ หรือค้นหา ให้หา stock_code ที่ตรงที่สุด
        row = conn.execute("SELECT stock_code FROM items WHERE stock_code = ?", [code]).fetchone()
        if not row:
            row = conn.execute("""
                SELECT stock_code FROM items 
                WHERE name LIKE ? OR trade_name LIKE ? OR stock_code LIKE ?
                ORDER BY CASE WHEN retired = 0 THEN 0 ELSE 1 END, LENGTH(name) ASC
                LIMIT 1
            """, [f"%{code}%", f"%{code}%", f"%{code}%"]).fetchone()
        target_code = row[0] if row else code
        data = substores.get_item_substore_detail(conn, store, target_code)
    finally:
        conn.close()
    return {"status": "success", "data": data}


@app.route("/api/substores/category-items")
def api_substores_category_items():
    """ดึงรายการเบิกจ่ายทั้งหมดในหมวดหรือคลังย่อย (สำหรับกดดูรายละเอียดได้ทั้งหมด)"""
    scope = (request.args.get("scope") or request.args.get("key") or "paper").strip().lower()
    months = int(request.args.get("months") or 18)
    conn = open_warehouse()
    if conn is None:
        return {"status": "error", "message": "ฐานข้อมูลไม่พร้อมใช้งาน"}, 503
    try:
        data = substores.get_category_all_items(conn, scope, months=months, limit=500)
    finally:
        conn.close()
    return {"status": "success", "data": data}


@app.route("/api/substores/category-depts")
def api_substores_category_depts():
    """ดึงหน่วยงานที่เบิกทั้งหมดในหมวดหรือคลังย่อย (สำหรับกดดูรายละเอียดได้ทั้งหมด)"""
    scope = (request.args.get("scope") or request.args.get("key") or "paper").strip().lower()
    months = int(request.args.get("months") or 18)
    conn = open_warehouse()
    if conn is None:
        return {"status": "error", "message": "ฐานข้อมูลไม่พร้อมใช้งาน"}, 503
    try:
        data = substores.get_category_all_depts(conn, scope, months=months, limit=200)
    finally:
        conn.close()
    return {"status": "success", "data": data}


@app.route("/api/substores/dept-items")
def api_substores_dept_items():
    """ดึงรายการที่หน่วยงานใดหน่วยงานหนึ่งเบิกไปทั้งหมด (สำหรับกดดูรายละเอียดการเบิกของหน่วยงาน)"""
    dept_code = (request.args.get("dept") or request.args.get("dept_code") or "").strip()
    scope = (request.args.get("scope") or request.args.get("key") or "").strip().lower()
    months = int(request.args.get("months") or 18)
    if not dept_code:
        return {"status": "error", "message": "ไม่ได้ระบุรหัสหน่วยงาน"}, 400
    parts = dept_code.split("-")
    div = parts[0] if len(parts) > 0 else ""
    dept = parts[1] if len(parts) > 1 else ""
    sec = parts[2] if len(parts) > 2 else ""

    conn = open_warehouse()
    if conn is None:
        return {"status": "error", "message": "ฐานข้อมูลไม่พร้อมใช้งาน"}, 503
    try:
        data = substores.get_dept_requisition_breakdown(conn, months=months, div=div, dept=dept, sec=sec, scope=scope)
    finally:
        conn.close()
    return {"status": "success", "data": data}


@app.route("/api/requisition-leaders")
def api_requisition_leaders():
    """ดึงข้อมูลจัดอันดับใครเบิกอะไรเยอะสุด (Requisition Leaders) สำหรับแดชบอร์ดผู้บริหาร"""
    months_str = request.args.get("months", "18")
    try:
        months = int(months_str)
    except (ValueError, TypeError):
        months = 18
    conn = open_warehouse()
    if conn is None:
        return {"status": "error", "message": NO_WAREHOUSE}, 503
    try:
        data = substores.get_top_requisition_leaders_dashboard(conn, months=months)
        return {"status": "success", "data": data}
    finally:
        conn.close()


@app.route("/api/requisition-leaders/dept-breakdown")
def api_requisition_leaders_dept_breakdown():
    """ดึงรายละเอียดรายการที่หน่วยงานเบิกทั้งหมดในหมวดหรือห้องยานั้น ๆ"""
    months_str = request.args.get("months", "18")
    try:
        months = int(months_str)
    except (ValueError, TypeError):
        months = 18
    div = (request.args.get("div") or "").strip()
    dept = (request.args.get("dept") or "").strip()
    sec = (request.args.get("sec") or "").strip()
    dept_code = (request.args.get("dept_code") or "").strip()
    scope = (request.args.get("scope") or "").strip()

    if not div and dept_code:
        parts = dept_code.split("-")
        div = parts[0] if len(parts) > 0 else ""
        dept = parts[1] if len(parts) > 1 else ""
        sec = parts[2] if len(parts) > 2 else ""
    elif not div and "-" in dept:
        parts = dept.split("-")
        div = parts[0] if len(parts) > 0 else ""
        dept = parts[1] if len(parts) > 1 else ""
        sec = parts[2] if len(parts) > 2 else ""

    conn = open_warehouse()
    if conn is None:
        return {"status": "error", "message": NO_WAREHOUSE}, 503
    try:
        data = substores.get_dept_requisition_breakdown(
            conn, months=months, div=div, dept=dept, sec=sec, scope=scope
        )
        return {"status": "success", "data": data}
    finally:
        conn.close()


@app.route("/projects")
def projects_page():
    conn = open_warehouse()
    if conn is None:
        return render_template("projects.html", problem=NO_WAREHOUSE, active="projects")
    try:
        summary = executive_analytics.services_maintenance_projects_summary(conn)
        high_repairs = executive_analytics.high_repair_cost_assets(conn)
        warranties = executive_analytics.detect_warranty_overlap(conn)
    finally:
        conn.close()
    return render_template("projects.html", problem=None, active="projects",
                           high_repairs=high_repairs, warranties=warranties, **summary)


@app.route("/savings")
def savings_page():
    conn = open_warehouse()
    if conn is None:
        return render_template("savings.html", problem=NO_WAREHOUSE, active="savings")
    try:
        data = executive_analytics.hospital_savings_opportunities(conn)
        near_expiry = executive_analytics.detect_near_expiry_returns(conn)
    finally:
        conn.close()
    return render_template("savings.html", problem=None, active="savings",
                           near_expiry=near_expiry, **data)


@app.route("/governance")
def governance_page():
    conn = open_warehouse()
    if conn is None:
        return render_template("governance.html", problem=NO_WAREHOUSE, active="governance")
    try:
        split_pos = executive_analytics.detect_split_po_risks(conn)
        consignment = executive_analytics.get_stock_with_consignment_separation(conn)
        actions = executive_analytics.get_action_required_inbox(conn)
    finally:
        conn.close()
    return render_template("governance.html", problem=None, active="governance",
                           split_pos=split_pos, consignment=consignment, actions=actions)


@app.route("/executive-print")
def executive_print_page():
    conn = open_warehouse()
    if conn is None:
        return render_template("executive_print.html", problem=NO_WAREHOUSE, active="print")
    try:
        sheet = executive_analytics.get_executive_summary_sheet(conn)
    finally:
        conn.close()
    return render_template("executive_print.html", problem=None, active="print", **sheet)


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
        substores.get_top_requisition_leaders_dashboard(connection, months=18)
    finally:
        connection.close()


def main():
    import threading
    import auto_sync
    auto_sync.start_scheduler()
    threading.Thread(target=warm_cache, daemon=True).start()
    try:
        from waitress import serve
    except ImportError:
        app.run(host="127.0.0.1", port=8090, debug=False, threaded=True)
    else:
        serve(app, host="127.0.0.1", port=8090, threads=8)


if __name__ == "__main__":
    main()
