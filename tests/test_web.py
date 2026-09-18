"""หน้าเว็บต้องเปิดได้จริง — เทสต์ชุดนี้เกิดหลังหน้าแรกพังเพราะชื่อค่าคงที่ที่แก้แล้วลืมตามไปแก้

ชั้นคำนวณมีเทสต์อยู่แล้ว แต่ไม่มีอะไรตรวจว่าหน้าเว็บเรียกใช้ชื่อถูกไหม จึงต้องมีเทสต์ที่
เรนเดอร์หน้าจริงด้วย ไม่ใช่แค่ทดสอบตัวเลข
"""
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import web  # noqa: E402


NEWEST_DAY = date(2026, 9, 9)


def make_warehouse(path: Path, stores_days: dict[str, tuple[int, int]]):
    """คลังข้อมูลจำลอง — ต่อคลังกำหนด (จำนวนวันทำการที่มีเอกสาร, จำนวนวันทำการล่าสุดที่ข้ามไป)

    ต้องข้ามวันล่าสุดได้ ไม่งั้นทุกคลังจะจบวันเดียวกันและไม่มีคลังไหนค้างเลย
    """
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE issues (store TEXT, irno TEXT, issued_at TEXT, stock_code TEXT, "
                       "period TEXT)")
    for store, (days_done, skip_recent) in stores_days.items():
        day, skipped, added = NEWEST_DAY, 0, 0
        while added < days_done:
            if day.weekday() < 5:
                if skipped < skip_recent:
                    skipped += 1
                else:
                    key = day.strftime("%Y%m%d")
                    connection.execute(
                        "INSERT INTO issues VALUES (?, ?, ?, ?, ?)",
                        (store, f"{key}-{store}-I/S1", f"{key[:4]}-{key[4:6]}-{key[6:8]}", "1",
                         key[:6]))
                    added += 1
            day -= timedelta(days=1)
    connection.commit()
    connection.close()


class WebPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "erplph.db"
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def render(self, query=""):
        with mock.patch.object(web.warehouse_db, "DB_PATH", self.db):
            return self.client.get("/cut-status" + query)

    def test_page_renders_with_real_shaped_data(self):
        make_warehouse(self.db, {"I2": (40, 0), "SMC": (35, 5)})
        response = self.render()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("ห้องจ่ายยาผู้ป่วยใน", body)
        self.assertIn("ห้องจ่ายยาเมตตา", body)
        self.assertIn("ข้อมูลถึงวันที่", body)

    def test_store_that_is_behind_is_shown_with_its_reason(self):
        make_warehouse(self.db, {"I2": (40, 0), "SMC": (35, 5)})
        body = self.render().get_data(as_text=True)
        self.assertIn("ยังไม่ได้ตัดมา 5 วันทำการ", body)

    def test_window_choice_is_limited_to_the_offered_values(self):
        make_warehouse(self.db, {"I2": (40, 0)})
        self.assertEqual(self.render("?days=30").status_code, 200)
        self.assertEqual(self.render("?days=99999").status_code, 200)
        self.assertEqual(self.render("?days=ไม่ใช่ตัวเลข").status_code, 200)

    def test_missing_warehouse_explains_itself_instead_of_crashing(self):
        response = self.render()
        self.assertEqual(response.status_code, 200)
        self.assertIn("ยังไม่มีคลังข้อมูล", response.get_data(as_text=True))

    def test_page_does_not_open_the_hospital_database(self):
        """หน้าเว็บต้องอ่านจากฐานของ ERPLPH เท่านั้น ไม่ต่อฐานโรงพยาบาลตอนเปิดหน้า"""
        make_warehouse(self.db, {"I2": (10, 0)})
        with mock.patch("database.connect", side_effect=AssertionError("ห้ามต่อฐานโรงพยาบาล")):
            self.assertEqual(self.render().status_code, 200)


def make_department_warehouse(path: Path):
    """คลังข้อมูลจำลองของหน้ารายแผนก — คนละรูปกับหน้าคลังค้าง จึงแยกกันสร้าง"""
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE issues (period TEXT, store TEXT, irno TEXT, suffix TEXT, "
        "movement_key TEXT, stock_code TEXT, qty REAL, value REAL, unit TEXT, "
        "division TEXT, dept TEXT, section TEXT, document_type TEXT, direction TEXT, "
        "issued_at TEXT, check_status TEXT)")
    connection.execute("CREATE TABLE items (stock_code TEXT, name TEXT, main_category TEXT, "
                       "item_group TEXT)")
    connection.execute(
        "INSERT INTO issues VALUES ('202608','I2','A1','1','','1000',10,1000.0,'TAB',"
        "'208','02','','32','out','2026-08-08','VERIFIED')")
    connection.execute(
        "INSERT INTO issues VALUES ('202609','I2','A2','1','','1000',1,100.0,'TAB',"
        "'208','02','','32','out','2026-09-08','VERIFIED')")
    connection.execute("INSERT INTO items VALUES ('1000', 'PARACETAMOL', '11', 'drug')")
    connection.commit()
    connection.close()


class DepartmentPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "erplph.db"
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def render(self, query=""):
        with mock.patch.object(web.warehouse_db, "DB_PATH", self.db):
            return self.client.get("/departments" + query)

    def test_the_page_renders_with_real_shaped_data(self):
        make_department_warehouse(self.db)
        response = self.render()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("แต่ละแผนกเบิกอะไรไปเท่าไร", body)
        self.assertIn("208", body)

    def test_drilling_into_a_department_keeps_working(self):
        make_department_warehouse(self.db)
        self.assertEqual(self.render("?div=208").status_code, 200)
        self.assertEqual(self.render("?div=208&dept=02").status_code, 200)
        self.assertEqual(self.render("?div=208&dept=02&section=01").status_code, 200)

    def test_values_from_the_address_bar_cannot_reach_the_database_as_sql(self):
        make_department_warehouse(self.db)
        for bad in ("?div=208'; DROP TABLE issues--", "?months=ไม่ใช่ตัวเลข", "?months=99999",
                    "?group=ไม่มีกลุ่มนี้", "?store=ไม่มีคลังนี้", "?div=" + "9" * 500):
            with self.subTest(query=bad):
                self.assertEqual(self.render(bad).status_code, 200)
        with mock.patch.object(web.warehouse_db, "DB_PATH", self.db):
            connection = sqlite3.connect(self.db)
            self.addCleanup(connection.close)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 2,
                "ตารางต้องยังอยู่ครบ ไม่ถูกลบด้วยค่าจากช่อง URL")

    def test_a_missing_warehouse_explains_itself_instead_of_crashing(self):
        response = self.render()
        self.assertEqual(response.status_code, 200)
        self.assertIn("ยังไม่มีคลังข้อมูล", response.get_data(as_text=True))

    def test_the_page_does_not_open_the_hospital_database(self):
        make_department_warehouse(self.db)
        with mock.patch("database.connect", side_effect=AssertionError("ห้ามต่อฐานโรงพยาบาล")):
            self.assertEqual(self.render().status_code, 200)

    def test_departments_split_screen_and_selected_parameter(self):
        make_department_warehouse(self.db)
        response = self.render("?selected=208")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("departments-split", body)
        self.assertIn("ใครเบิกมากที่สุด", body)
        self.assertIn("ข้อมูลของที่เบิก", body)
        self.assertIn("dept-search-input", body)


if __name__ == "__main__":
    unittest.main()

