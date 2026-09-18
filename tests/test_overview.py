"""ภาพรวมทุกประเภทของ แบบ Stock5 — ตัวเลขที่ผู้บริหารเห็นบนหน้าแรก

เรื่องที่ต้องไม่พลาด
    1. ตัวเลขยอดใช้ต้องตรงกับหน้ารายแผนกเสมอ ไม่งั้นผู้บริหารเห็นสองตัวเลขแล้วไม่เชื่อทั้งคู่
    2. การโอนระหว่างคลังไม่ใช่การใช้ (ทั้งโรงพยาบาล) แต่เป็นการใช้ของคลังต้นทาง (รายคลัง)
    3. เดือนล่าสุดที่ยังไม่จบไม่ถูกนับรวม และขาออกที่สอบทานไม่ผ่านไม่ถูกนับ
    4. ครุภัณฑ์และงานจ้างไม่มีเดือนคงคลัง
    5. หน้าเว็บเปิดได้ทุกหน้า และไม่ต่อฐานข้อมูลโรงพยาบาล
"""
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import categories  # noqa: E402
import department_usage  # noqa: E402
import overview  # noqa: E402
import warehouse_db  # noqa: E402

SNAPSHOT = "20260917"
TODAY = date.today()


def day_from_today(days: int) -> str:
    return (TODAY + timedelta(days=days)).strftime("%Y%m%d")


ITEMS = [
    {"stock_code": "1000", "name": "PARACETAMOL TAB 500 MG", "main_category": "11"},
    {"stock_code": "6000", "name": "กระดาษถ่ายเอกสาร A4", "main_category": "6"},
    {"stock_code": "7000", "name": "เครื่องเอกซเรย์", "main_category": "7"},
    {"stock_code": "9000", "name": "จ้างทำความสะอาด", "main_category": "9"},
    {"stock_code": "3000", "name": "นมผงทารก", "main_category": "03"},
]


def issue(irno, code, value, *, kind="32", direction="out", status="VERIFIED", qty=1.0,
          division="208", dept="02"):
    return {"irno": irno, "suffix": "1", "movement_key": irno, "stock_code": code, "qty": qty,
            "value": value, "unit": "PCS", "division": division, "dept": dept,
            "document_type": kind, "movement_kind": {"32": "dispense", "35": "transfer"}[kind],
            "direction": direction, "check_status": status, "issued_at": "2026-08-10 09:00"}


def receipt(rcv_no, code, value, supplier="ออินโดไชน่า เฮลท์", qty=10.0, day="20260805"):
    return {"rcv_no": rcv_no, "suffix": "1", "stock_code": code, "qty": qty, "value": value,
            "unit": "PCS", "unit_price": value / qty, "po_no": "PO1", "supplier": supplier,
            "rcv_date": day}


def build_warehouse():
    """ข้อมูลจำลองขนาดเล็ก — ทุกตัวเลขในเทสต์คิดมือได้"""
    warehouse_db.init_db()
    warehouse_db.upsert_items(ITEMS)
    for period in ("202607", "202608"):
        warehouse_db.replace_period(period, "I2", "issue", [])
        warehouse_db.replace_period(period, "2", "issue", [])
    warehouse_db.replace_period("202608", "I2", "issue", [
        issue("A1", "1000", 1000.0),
        issue("A2", "1000", 200.0, direction="in", status="PENDING"),       # ใบคืน หัก
        issue("A3", "1000", 777.0, status="PENDING"),                        # สอบทานไม่ผ่าน ไม่นับ
        issue("A4", "6000", 500.0, division="305", dept="01"),
        issue("A5", "7000", 30000.0, division="210", dept="01"),
        issue("A6", "3000", 90.0, division="306", dept="01"),
    ])
    warehouse_db.replace_period("202608", "2", "issue", [
        issue("T1", "1000", 5000.0, kind="35"),                              # โอนออกจากคลังหลัก
        issue("T2", "1000", 5000.0, kind="35", direction="in", status="PENDING"),
    ])
    # เดือนล่าสุดยังไม่จบ ต้องไม่นับในยอด 12 เดือน แต่ต้องขึ้นในกราฟ
    warehouse_db.replace_period("202609", "I2", "issue", [issue("OPEN", "1000", 4321.0)])
    warehouse_db.replace_period("202608", "2", "receipt", [receipt("M1", "1000", 1100.0)])
    warehouse_db.replace_period("202608", "1", "receipt", [
        receipt("RA1", "9000", 25000.0, supplier="บริษัทรับจ้าง"),
        receipt("GN1", "7000", 30000.0, supplier="บริษัทเครื่องมือ"),
    ])
    warehouse_db.replace_period(SNAPSHOT, "I2", "balance", [
        {"stock_code": "1000", "lot_no": "L1", "qty": 100, "value": 400.0, "unit": "TAB",
         "expire_date": day_from_today(-10)},                                # หมดอายุแล้ว
        {"stock_code": "1000", "lot_no": "L2", "qty": 50, "value": 200.0, "unit": "TAB",
         "expire_date": day_from_today(400)},
        {"stock_code": "6000", "lot_no": "", "qty": 0, "value": 0.0, "unit": "RM",
         "expire_date": day_from_today(-5000)},                              # ล็อตว่างเก่า ต้องไม่ขึ้น
    ])
    warehouse_db.replace_period(SNAPSHOT, "2", "balance", [
        {"stock_code": "6000", "lot_no": "P1", "qty": 20, "value": 1000.0, "unit": "RM",
         "expire_date": day_from_today(30)},                                 # ไม่ถึง 3 เดือน
        {"stock_code": "7000", "lot_no": "", "qty": 1, "value": 50000.0, "unit": "EG"},
    ])


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "erplph.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        build_warehouse()
        self.conn = sqlite3.connect(warehouse_db.DB_PATH)
        self.addCleanup(self.conn.close)

    def groups(self, **kwargs):
        return {row["key"]: row for row in overview.group_table(self.conn, **kwargs)["rows"]}

    # --- ยอดใช้
    def test_hospital_use_counts_verified_dispensing_net_of_returns_and_nothing_else(self):
        drug = self.groups()[categories.DRUG]
        self.assertEqual(drug["used"], 800.0, "1,000 − คืน 200 · ไม่นับ PENDING 777 · ไม่นับโอน 5,000")

    def test_the_unfinished_latest_month_is_not_counted_but_is_charted(self):
        self.assertEqual(self.groups()[categories.DRUG]["used"], 800.0, "ใบ OPEN ของ 202609 ต้องไม่นับ")
        trend = overview.monthly(self.conn)
        self.assertTrue(trend[-1]["partial"])
        self.assertEqual(trend[-1]["period"], "202609")
        self.assertEqual(trend[-1]["used"], 4321.0)

    def test_the_total_matches_the_department_page_exactly(self):
        total = sum(row["used"] for row in overview.group_table(self.conn)["rows"])
        self.assertEqual(total, department_usage.breakdown(self.conn)["total_net"])

    def test_a_single_store_counts_its_transfers_out_as_use(self):
        """คลังหลักจ่ายออกด้วยการโอน ถ้าไม่นับ คลังหลักจะดูเหมือนไม่ได้ใช้อะไรเลย"""
        self.assertEqual(self.groups(store="2")[categories.DRUG]["used"], 5000.0)

    # --- กลุ่มของ
    def test_every_kind_of_thing_the_user_named_has_its_own_row(self):
        rows = self.groups()
        for key in (categories.DRUG, categories.MATERIAL, categories.EQUIPMENT,
                    categories.HIRE, categories.FOOD):
            self.assertIn(key, rows)
        self.assertEqual(rows[categories.HIRE]["received"], 25000.0, "งานจ้างเห็นได้จากใบรับเท่านั้น")

    def test_equipment_and_hired_work_have_no_months_of_stock(self):
        rows = self.groups()
        self.assertIsNone(rows[categories.EQUIPMENT]["months_of_stock"])
        self.assertIsNone(rows[categories.HIRE]["months_of_stock"])
        self.assertIsNotNone(rows[categories.DRUG]["months_of_stock"])

    # --- การ์ด
    def test_headline_splits_expired_from_expiring_soon(self):
        head = overview.headline(self.conn)
        self.assertEqual(head["stock_value"], 51600.0)
        self.assertEqual(head["expired"], 400.0)
        self.assertEqual(head["expiring_critical"], 1000.0)

    def test_headline_can_be_narrowed_to_one_group(self):
        head = overview.headline(self.conn, group=categories.MATERIAL)
        self.assertEqual(head["stock_value"], 1000.0)
        self.assertEqual(head["used"], 500.0)

    def test_the_headline_does_not_use_a_key_that_jinja_reads_as_a_method(self):
        """head.items ใน Jinja คือเมธอดของ dict หน้าแรกเคยล้มเพราะชื่อนี้"""
        self.assertNotIn("items", overview.headline(self.conn))

    # --- รายการ
    def test_expiring_list_leaves_out_empty_lots_and_leads_with_what_already_expired(self):
        rows = overview.expiring(self.conn)
        self.assertEqual([row["level"] for row in rows], ["expired", "critical"])
        self.assertNotIn("6000", [row["stock_code"] for row in rows if row["store"] == "I2"])

    def test_dormant_lists_stock_with_no_movement_out(self):
        codes = {(row["store"], row["stock_code"]) for row in overview.dormant(self.conn)}
        self.assertIn(("2", "7000"), codes)
        self.assertNotIn(("I2", "1000"), codes, "มีการจ่ายออกในงวด 202608 ไม่ใช่ของค้างนิ่ง")

    # --- ค้นหา
    def test_search_finds_by_name_or_code_across_all_groups(self):
        self.assertEqual([row["stock_code"] for row in overview.search(self.conn, "กระดาษ")], ["6000"])
        self.assertEqual([row["stock_code"] for row in overview.search(self.conn, "700")], ["7000"])
        self.assertEqual(overview.search(self.conn, "paracetamol")[0]["group"], "ยา")

    def test_search_finds_by_po_no_or_supplier(self):
        # ค้นหาด้วยเลข PO
        rows = overview.search(self.conn, "PO1")
        codes = [row["stock_code"] for row in rows]
        self.assertIn("1000", codes)
        self.assertEqual(rows[0]["match_po"], "PO1")

        # ค้นหาด้วยชื่อผู้ขาย
        sup_rows = overview.search(self.conn, "อินโดไชน่า")
        sup_codes = [row["stock_code"] for row in sup_rows]
        self.assertIn("1000", sup_codes)

    def test_choosing_a_group_without_text_lists_that_group(self):
        rows = overview.search(self.conn, "", group=categories.HIRE)
        self.assertEqual([row["stock_code"] for row in rows], ["9000"])
        self.assertEqual(rows[0]["received"], 25000.0)

    def test_an_empty_search_returns_nothing_instead_of_the_whole_catalogue(self):
        self.assertEqual(overview.search(self.conn, "   "), [])

    # --- รายละเอียดรายการ
    def test_item_detail_gathers_stock_use_vendors_and_departments(self):
        detail = overview.item(self.conn, "1000")
        self.assertEqual(detail["stock_value"], 600.0)
        self.assertEqual([entry["store"] for entry in detail["stock_by_store"]], ["I2"])
        self.assertEqual(detail["used_total"], 800.0)
        self.assertEqual(detail["vendors"][0]["supplier"], "อินโดไชน่า เฮลท์",
                         "ชื่อผู้ขายต้องตัดอักษรซ้ำและช่องว่างพิเศษแบบเดียวกับชื่อยา")
        self.assertEqual(detail["taken_by"][0]["division"], "208")
        self.assertEqual(detail["taken_by"][0]["value"], 800.0)
        self.assertTrue(detail["months"][-1]["partial"])

    def test_transfers_are_shown_apart_from_use_on_the_item_page(self):
        last_closed = next(row for row in overview.item(self.conn, "1000")["months"]
                      if row["period"] == "202608")
        self.assertEqual(last_closed["used"], 800.0)
        self.assertEqual(last_closed["transferred"], 5000.0)

    def test_an_unknown_item_returns_nothing(self):
        self.assertIsNone(overview.item(self.conn, "ไม่มีรหัสนี้"))

    def test_receipt_coverage_says_when_only_the_pharmacy_has_receipts(self):
        self.assertTrue(overview.receipt_coverage(self.conn)["complete"])
        self.conn.execute("DELETE FROM receipts WHERE store <> '2'")
        self.assertFalse(overview.receipt_coverage(self.conn)["complete"])


class OverviewPageTests(unittest.TestCase):
    """หน้าเว็บทุกหน้าต้องเปิดได้กับข้อมูลรูปจริง — หน้าแรกเคยล้มเพราะชื่อคีย์ items"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "erplph.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        import web
        self.web = web
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def get(self, path):
        return self.client.get(path)

    def test_every_page_opens_with_real_shaped_data(self):
        build_warehouse()
        for path in ("/overview", "/overview?group=material", "/overview?store=2", "/overview?months=24",
                     "/overview?group=hire",
                     "/items?q=กระดาษ", "/items?group=equipment", "/items",
                     "/items/1000", "/items/9000", "/lists/expiring", "/lists/dormant?group=equipment",
                     "/departments", "/cut-status"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[-400:])

    def test_the_first_page_shows_every_group_and_warns_when_receipts_are_partial(self):
        build_warehouse()
        body = self.get("/overview").get_data(as_text=True)
        for name in ("ยา", "พัสดุ", "ครุภัณฑ์", "งานจ้างและบริการ", "อาหารและโภชนาการ"):
            self.assertIn(name, body)
        self.assertNotIn("ยอดรับ (ยอดซื้อ/จ้าง) ยังไม่ครบ", body)
        with sqlite3.connect(warehouse_db.DB_PATH) as conn:
            conn.execute("DELETE FROM receipts WHERE store <> '2'")
        self.assertIn("ยอดรับ (ยอดซื้อ/จ้าง) ยังไม่ครบ", self.get("/overview").get_data(as_text=True))

    def test_an_unknown_item_is_a_404_page_not_a_crash(self):
        build_warehouse()
        self.assertEqual(self.get("/items/ไม่มีรหัสนี้").status_code, 404)

    def test_values_from_the_address_bar_are_refused_not_trusted(self):
        build_warehouse()
        for path in ("/overview?group=ไม่มี", "/overview?store=' OR 1=1--", "/overview?months=-1",
                     "/items?q=' OR '1'='1", "/items?q=" + "ก" * 500, "/lists/อะไรก็ได้"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 200)
        with sqlite3.connect(warehouse_db.DB_PATH) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], len(ITEMS))

    def test_pages_explain_a_missing_warehouse_instead_of_crashing(self):
        for path in ("/overview", "/items?q=x", "/items/1000", "/lists/expiring"):
            with self.subTest(path=path):
                self.assertIn("ยังไม่มีคลังข้อมูล", self.get(path).get_data(as_text=True))

    def test_pages_never_open_the_hospital_database(self):
        build_warehouse()
        with mock.patch("database.connect", side_effect=AssertionError("ห้ามต่อฐานโรงพยาบาล")):
            for path in ("/overview", "/items?q=กระดาษ", "/items/1000", "/lists/dormant"):
                with self.subTest(path=path):
                    self.assertIn(self.get(path).status_code, (200, 404))


if __name__ == "__main__":
    unittest.main()
