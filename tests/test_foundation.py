"""รากฐานของ ERPLPH: ทะเบียนคลัง การแบ่งกลุ่ม ฐานข้อมูลรายงวด และการยืม engine

เฟส 1 ขยายขอบเขตจากคลังเดียวเป็นทุกคลัง ข้อผิดพลาดที่นี่จะทำให้ตัวเลขของทั้ง
โรงพยาบาลเพี้ยนโดยไม่มีสัญญาณเตือน จึงตรึงพฤติกรรมสำคัญไว้ตั้งแต่ต้น
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import categories
import stock5_engine
import stores
import warehouse_db


class StoreRegistryTests(unittest.TestCase):
    def test_every_store_seen_in_the_survey_has_a_name(self):
        # รหัสเหล่านี้ถือของจริงอยู่ตามผลสำรวจ 11 ก.ย. 2569
        for code in ("1", "2", "3", "6", "7", "8", "20", "99", "O5", "O6", "O7",
                     "I2", "OR", "OR1", "CL", "LAB", "SMC", "P3", "PAN", "ER", "1R"):
            with self.subTest(store=code):
                self.assertNotEqual(stores.store_name(code), code,
                                    f"คลัง {code} ยังไม่มีชื่อในทะเบียน")

    def test_retired_stores_keep_their_name_for_history(self):
        # ข้อมูลย้อนหลังยังอ้างถึงคลังเก่า รายงานต้องแปลชื่อได้แม้เลิกใช้แล้ว
        for code in ("O1", "O2", "O3", "O4", "I1", "P2", "PCU", "AN"):
            with self.subTest(store=code):
                self.assertFalse(stores.is_active(code))
                self.assertIn("เดิม", stores.store_name(code))

    def test_an_unknown_store_is_kept_not_dropped(self):
        # คลังที่เพิ่งเปิดใหม่ต้องไม่หายไปจากรายงานเพราะยังไม่มีในทะเบียน
        self.assertTrue(stores.is_active("ZZ"))
        self.assertEqual(stores.store_name("ZZ"), "ZZ")

    def test_lookup_ignores_surrounding_space_and_case(self):
        self.assertEqual(stores.store_name(" o5 "), stores.store_name("O5"))

    def test_the_pharmacy_store_is_the_one_stock5_reports(self):
        self.assertEqual(stores.PHARMACY_MAIN_STORE, "2")
        self.assertEqual(stores.store_name("2"), "คลังยาและเวชภัณฑ์")


class CategoryTests(unittest.TestCase):
    def test_the_four_groups_match_the_survey(self):
        cases = {
            "10": "ยา", "11": "ยา", "12": "ยา", "14": "ยา", "17": "ยา",
            "2": "เวชภัณฑ์มิใช่ยา", "3": "เวชภัณฑ์มิใช่ยา",
            "4": "พัสดุ", "5": "พัสดุ", "6": "พัสดุ", "8": "พัสดุ",
            # หมวด 7 แยกออกมาเมื่อ 17 ก.ย. 2569 — ราคาต่อหน่วยกลาง 34,500 บาท หน่วยนับ EG
            # เทียบกับหมวด 4 ที่ 2,200 และหมวด 6 ที่ 170 เป็นของลงทุน ไม่ใช่ของใช้สิ้นเปลือง
            "7": "ครุภัณฑ์",
            # หมวด 03 แยกออกมาเมื่อ 17 ก.ย. 2569 — เป็นอาหารล้วน 360 รหัส 19.7 ล้านบาท/ปี
            # และเป็น 90% ของยอดกลุ่มงานโภชนศาสตร์ เรียก "อื่น ๆ" แล้วอ่านผิดแน่
            "03": "อาหารและโภชนาการ",
            "9": "อื่น ๆ",
        }
        for category, expected in cases.items():
            with self.subTest(category=category):
                self.assertEqual(categories.group_name(categories.group_of(category)), expected)

    def test_an_unknown_category_falls_into_other_rather_than_vanishing(self):
        self.assertEqual(categories.group_of("ไม่เคยเห็น"), categories.OTHER)
        self.assertEqual(categories.group_of(None), categories.OTHER)

    def test_retired_items_are_recognised_by_their_name(self):
        self.assertTrue(categories.is_retired_item("((ยกเลิก) ถังขยะพลาสติก"))
        self.assertTrue(categories.is_retired_item("((ใช้ 90104002 แทน)จ้างถ่ายเอกสาร"))
        self.assertFalse(categories.is_retired_item("PARACETAMOL TAB 500 MG"))

    def test_the_drug_filter_names_every_drug_category(self):
        clause = categories.sql_category_filter(categories.DRUG)
        for category in ("10", "11", "12", "14", "17"):
            self.assertIn(f"'{category}'", clause)

    def test_the_other_filter_excludes_all_named_categories(self):
        clause = categories.sql_category_filter(categories.OTHER)
        self.assertIn("NOT IN", clause)
        for category in ("11", "2", "6"):
            self.assertIn(f"'{category}'", clause)

    def test_pulling_everything_puts_no_category_condition_in_the_query(self):
        """ทุกแผนกต้องเห็นภาพ ไม่ใช่เฉพาะยา — ตัวกรองหมวดจึงต้องหายไปจริง ๆ ไม่ใช่ขยายรายชื่อ"""
        self.assertEqual(categories.sql_category_filter(categories.ALL), "1 = 1")
        self.assertNotIn(categories.ALL, [group.key for group in categories.GROUPS],
                         "all ไม่ใช่กลุ่มของรายการ เป็นแค่ขอบเขตการดึง")

    def test_the_group_expression_covers_every_group(self):
        expression = categories.sql_group_expression()
        for group in categories.GROUPS:
            if group.categories:
                self.assertIn(f"'{group.key}'", expression)


class WarehouseDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "test.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()

    def issue_rows(self, count=2):
        return [{
            "irno": "69D0001", "suffix": str(index), "movement_key": str(30 + index),
            "stock_code": "1321080", "lot_no": f"L{index}", "qty": 100 + index,
            "value": 1000.0 + index, "unit": "TAB", "department": "2",
            "issued_at": "2026-08-01 09:00", "check_status": "VERIFIED", "check_reason": "",
        } for index in range(count)]

    def count(self, table):
        with warehouse_db.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_a_period_is_stored_and_reported_complete(self):
        result = warehouse_db.replace_period("202608", "2", "issue", self.issue_rows())
        self.assertEqual(result["rows"], 2)
        self.assertTrue(warehouse_db.is_period_complete("202608", "2", "issue"))

    def test_repulling_a_period_replaces_it_instead_of_adding_to_it(self):
        warehouse_db.replace_period("202608", "2", "issue", self.issue_rows(3))
        warehouse_db.replace_period("202608", "2", "issue", self.issue_rows(1))
        self.assertEqual(self.count("issues"), 1, "งวดเดิมต้องถูกแทนที่ ไม่ใช่สะสมทับ")

    def test_one_store_does_not_disturb_another(self):
        warehouse_db.replace_period("202608", "2", "issue", self.issue_rows(2))
        warehouse_db.replace_period("202608", "O5", "issue", self.issue_rows(3))
        warehouse_db.replace_period("202608", "2", "issue", self.issue_rows(1))
        self.assertEqual(self.count("issues"), 4, "การดึงคลังหนึ่งใหม่ต้องไม่ลบของอีกคลัง")

    def test_only_missing_periods_are_reported_as_work_to_do(self):
        warehouse_db.replace_period("202608", "2", "issue", self.issue_rows())
        missing = warehouse_db.missing_periods(["202607", "202608"], ["2", "O5"], ["issue"])
        self.assertNotIn(("202608", "2", "issue"), missing)
        self.assertIn(("202607", "2", "issue"), missing)
        self.assertIn(("202608", "O5", "issue"), missing)

    def test_a_failed_pull_is_recorded_and_not_counted_as_complete(self):
        warehouse_db.mark_period_failed("202607", "O5", "issue", "ต่อฐานข้อมูลไม่ได้")
        self.assertFalse(warehouse_db.is_period_complete("202607", "O5", "issue"))
        self.assertIn(("202607", "O5", "issue"),
                      warehouse_db.missing_periods(["202607"], ["O5"], ["issue"]))

    def test_a_failed_pull_leaves_earlier_good_data_alone(self):
        warehouse_db.replace_period("202608", "2", "issue", self.issue_rows(2))
        warehouse_db.mark_period_failed("202608", "2", "issue", "หมดเวลา")
        self.assertEqual(self.count("issues"), 2, "ข้อมูลที่ดึงสำเร็จแล้วต้องไม่ถูกลบ")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            warehouse_db.replace_period("202608", "2", "ไม่รู้จัก", [])

    def test_the_item_registry_updates_rather_than_duplicates(self):
        row = {"stock_code": "1321080", "name": "ATORVASTATIN", "main_category": "11",
               "item_group": categories.DRUG, "base_unit": "TAB"}
        warehouse_db.upsert_items([row])
        warehouse_db.upsert_items([dict(row, name="ATORVASTATIN TAB 40 mg")])
        self.assertEqual(self.count("items"), 1)
        with warehouse_db.connect() as conn:
            stored = conn.execute("SELECT name FROM items").fetchone()["name"]
        self.assertEqual(stored, "ATORVASTATIN TAB 40 mg")

    def test_coverage_reports_what_is_actually_held(self):
        warehouse_db.replace_period("202608", "2", "issue", self.issue_rows())
        report = warehouse_db.coverage()
        self.assertIn("2", report["stores"])
        self.assertTrue(report["exists"])


class BorrowedEngineTests(unittest.TestCase):
    """Stock5 ต้องถูกอ่านเท่านั้น ห้ามแก้ และห้ามยืมส่วนที่ส่งข้อมูลกระทรวง"""

    def test_the_calculation_engine_can_be_borrowed(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")
        self.assertEqual(stock5_engine.load("lot_reconciliation").ENGINE, "SSBSTOCK_MOVE_V1")
        self.assertEqual(stock5_engine.load("monitor_units").unit("TABLET"), "TAB")

    def test_modules_outside_the_list_are_refused(self):
        for name in ("moph_api_sender", "server", "db_extractor", "master_db"):
            with self.subTest(module=name), self.assertRaises(ValueError):
                stock5_engine.load(name)

    def test_a_missing_stock5_says_so_plainly(self):
        with patch.object(stock5_engine, "stock5_home", return_value=Path("/ไม่มีที่นี่")):
            self.assertFalse(stock5_engine.engine_available())
            with self.assertRaises(FileNotFoundError):
                stock5_engine.install()


if __name__ == "__main__":
    unittest.main()


class ReportingWindowTests(unittest.TestCase):
    """ช่วงรายงานต้องเป็นปีงบประมาณเหมือน Stock5 เพื่อให้ตัวเลขสองระบบเทียบกันได้"""

    import datetime as _dt

    def setUp(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")
        global reporting_window
        import reporting_window

    def date(self, text):
        return self._dt.date.fromisoformat(text)

    def test_the_window_opens_on_1_october(self):
        for day in ("2026-09-11", "2026-09-30"):
            with self.subTest(day=day):
                self.assertEqual(reporting_window.describe(self.date(day))["date_from"], "20241001")

    def test_the_window_rolls_when_a_new_fiscal_year_begins(self):
        before = reporting_window.describe(self.date("2026-09-30"))
        after = reporting_window.describe(self.date("2026-10-01"))
        self.assertEqual(before["fiscal_year"], "2569")
        self.assertEqual(after["fiscal_year"], "2570")
        self.assertEqual(after["date_from"], "20251001")

    def test_the_window_never_grows_beyond_two_fiscal_years(self):
        for year in range(2026, 2031):
            for month in (1, 4, 7, 10):
                day = self._dt.date(year, month, 1)
                with self.subTest(day=day.isoformat()):
                    months = reporting_window.describe(day)["months"]
                    self.assertLessEqual(months, 25)
                    self.assertGreaterEqual(months, 12)

    def test_the_current_month_is_excluded_from_closed_periods(self):
        # เดือนที่ยังไม่ครบใช้คำนวณอัตราเบิกจ่ายไม่ได้ เหมือนกติกาของ Stock5
        today = self.date("2026-09-11")
        self.assertIn("202609", reporting_window.periods(today))
        self.assertNotIn("202609", reporting_window.closed_periods(today))
        self.assertEqual(reporting_window.closed_periods(today)[-1], "202608")

    def test_periods_run_without_gaps(self):
        every = reporting_window.periods(self.date("2026-09-11"))
        self.assertEqual(every[0], "202410")
        self.assertEqual(every[-1], "202609")
        self.assertEqual(len(every), len(set(every)), "ต้องไม่มีงวดซ้ำ")

    def test_period_bounds_wrap_the_year_correctly(self):
        self.assertEqual(reporting_window.period_bounds("202608"), ("20260801", "20260901"))
        self.assertEqual(reporting_window.period_bounds("202612"), ("20261201", "20270101"))


class DatabaseConfigTests(unittest.TestCase):
    """ERPLPH ต้องตั้งค่าฐานข้อมูลเองได้ และต้องไม่ทำรหัสผ่านหลุดไปกับรายงาน"""

    def setUp(self):
        global database
        import database

    def test_config_falls_back_to_stock5_without_copying_the_file(self):
        config = database.load_config()
        self.assertIn("db_host", config)
        described = database.describe_config()
        self.assertIn("inherited_from_stock5", described)

    def test_the_password_never_appears_in_the_description(self):
        # รายงานที่ส่งให้ผู้พัฒนาผ่านค่านี้ ต้องไม่มีรหัสผ่านติดไป
        described = database.describe_config()
        self.assertNotIn("db_pass", described)
        self.assertIsInstance(described["password_set"], bool)

    def test_a_missing_host_is_refused_before_connecting(self):
        with self.assertRaises(ValueError):
            database.connect({"db_host": "", "db_name": "X", "db_user": "u", "db_pass": "p"})

    def test_driver_messages_are_not_copied_into_reports(self):
        # ข้อความจากไดรเวอร์อาจมีสตริงการเชื่อมต่อซึ่งมีรหัสผ่านอยู่
        leaky = Exception("PWD=secret123;SERVER=10.0.0.1")
        summary = database.error_summary(leaky)
        self.assertNotIn("secret123", summary["message"])
        self.assertIsNone(summary["sqlstate"])

    def test_known_sqlstates_get_a_readable_explanation(self):
        for state in ("08001", "28000", "42S22", "42S02", "HYT00"):
            with self.subTest(sqlstate=state):
                summary = database.error_summary(Exception(state))
                self.assertEqual(summary["sqlstate"], state)
                self.assertNotIn("ไม่เก็บข้อความจากไดรเวอร์", summary["message"])


class SchemaUpgradeTests(unittest.TestCase):
    """ฐานข้อมูลที่สร้างไว้ก่อนต้องอัปเกรดได้ โดยข้อมูลเดิมไม่หาย"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "old.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def build_old_database(self):
        """สร้างตาราง issues แบบรุ่นก่อน ที่ยังไม่มีคอลัมน์ชนิดเอกสาร"""
        with warehouse_db.connect() as conn:
            conn.execute("""
                CREATE TABLE issues (
                    period TEXT NOT NULL, store TEXT NOT NULL, irno TEXT NOT NULL,
                    suffix TEXT NOT NULL DEFAULT '', movement_key TEXT NOT NULL DEFAULT '',
                    stock_code TEXT NOT NULL, lot_no TEXT DEFAULT '', qty REAL DEFAULT 0,
                    value REAL DEFAULT 0, unit TEXT DEFAULT '', department TEXT DEFAULT '',
                    issued_at TEXT DEFAULT '', check_status TEXT DEFAULT '',
                    check_reason TEXT DEFAULT '',
                    PRIMARY KEY (period, store, irno, suffix, stock_code, movement_key))""")
            conn.execute("INSERT INTO issues (period, store, irno, stock_code, qty)"
                         " VALUES ('202607', '2', '69D9', '1321080', 42)")
            conn.commit()

    def test_missing_columns_are_added_to_an_existing_database(self):
        self.build_old_database()
        applied = warehouse_db.init_db()
        self.assertIn("issues.document_type", applied)
        self.assertIn("issues.movement_kind", applied)
        # คลังข้อมูลจริงมี 507 MB การเพิ่มช่องหน่วยงานต้องไม่บังคับให้ลบทิ้งแล้วดึงใหม่
        for level in ("division", "dept", "section"):
            self.assertIn(f"issues.{level}", applied)

    def test_existing_rows_survive_the_upgrade(self):
        self.build_old_database()
        warehouse_db.init_db()
        with warehouse_db.connect() as conn:
            row = conn.execute("SELECT qty, movement_kind FROM issues").fetchone()
        self.assertEqual(row["qty"], 42)
        self.assertEqual(row["movement_kind"], "")

    def test_running_the_upgrade_twice_changes_nothing_more(self):
        self.build_old_database()
        warehouse_db.init_db()
        self.assertEqual(warehouse_db.init_db(), [], "ครั้งที่สองต้องไม่มีอะไรให้เติม")

    def test_a_fresh_database_needs_no_upgrade(self):
        self.assertEqual(warehouse_db.init_db(), [])
