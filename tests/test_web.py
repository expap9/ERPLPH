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
    connection.execute("CREATE TABLE issues (store TEXT, irno TEXT, issued_at TEXT, stock_code TEXT)")
    for store, (days_done, skip_recent) in stores_days.items():
        day, skipped, added = NEWEST_DAY, 0, 0
        while added < days_done:
            if day.weekday() < 5:
                if skipped < skip_recent:
                    skipped += 1
                else:
                    key = day.strftime("%Y%m%d")
                    connection.execute(
                        "INSERT INTO issues VALUES (?, ?, ?, ?)",
                        (store, f"{key}-{store}-I/S1", f"{key[:4]}-{key[4:6]}-{key[6:8]}", "1"))
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
            return self.client.get("/" + query)

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


if __name__ == "__main__":
    unittest.main()
