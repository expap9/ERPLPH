"""คลังไหนข้อมูลค้าง: แยกวันหยุดออก วัดคลังสองแบบคนละวิธี และไม่เทียบกับวันนี้"""
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import cut_status  # noqa: E402

SCHEMA = "CREATE TABLE issues (store TEXT, irno TEXT, issued_at TEXT, stock_code TEXT)"
START, END = date(2026, 6, 15), date(2026, 9, 9)   # จันทร์ ถึง พุธ
WINDOW = (END - START).days + 1


def build(rows):
    connection = sqlite3.connect(":memory:")
    connection.execute(SCHEMA)
    connection.executemany("INSERT INTO issues VALUES (?, ?, ?, ?)", rows)
    return connection


def working_days(start=START, end=END):
    span = (end - start).days + 1
    return [(start + timedelta(days=i)).strftime("%Y%m%d")
            for i in range(span) if (start + timedelta(days=i)).weekday() < 5]


def import_rows(store, days):
    """เอกสารที่ระบบสร้างตอน import — เลขขึ้นต้นด้วยวันที่"""
    return [(store, f"{day}-{store}-I/S1", f"{day[:4]}-{day[4:6]}-{day[6:8]} 00:50", "1000000")
            for day in days]


def manual_rows(store, days, prefix="I7"):
    """เอกสารที่เจ้าหน้าที่คีย์เอง — เลขไม่ขึ้นต้นด้วยวันที่"""
    return [(store, f"{prefix}{index:07d}", f"{day[:4]}-{day[4:6]}-{day[6:8]} 08:16", "1000000")
            for index, day in enumerate(days, start=1)]


class ImportStoreTests(unittest.TestCase):
    def test_weekend_gaps_are_not_counted_as_behind(self):
        report = cut_status.collect(build(import_rows("I2", working_days())), window_days=WINDOW)
        entry = report["stores"][0]
        self.assertEqual(entry.method, cut_status.METHOD_IMPORT)
        self.assertEqual(entry.missing_working_days, [])
        self.assertEqual(entry.days_behind, 0)
        self.assertFalse(entry.needs_attention)

    def test_an_old_missing_day_is_shown_but_does_not_raise_an_alarm(self):
        """ยังไม่มีตารางวันหยุดราชการ ถ้าวันที่ขาดย้อนหลังทำให้ขึ้นเตือน เกือบทุกคลังจะแดง"""
        days = working_days()
        skipped = days[10]
        report = cut_status.collect(build(import_rows("SMC", [d for d in days if d != skipped])),
                                    window_days=WINDOW)
        entry = report["stores"][0]
        self.assertEqual(entry.missing_working_days, [skipped])
        self.assertEqual(entry.days_behind, 0)
        self.assertFalse(entry.needs_attention)

    def test_a_store_behind_right_now_is_flagged_with_a_reason(self):
        days = working_days()
        rows = import_rows("I2", days) + import_rows("SMC", days[:-4])
        report = cut_status.collect(build(rows), window_days=WINDOW)
        entry = next(item for item in report["stores"] if item.store == "SMC")
        self.assertEqual(entry.days_behind, 4)
        self.assertTrue(entry.needs_attention)
        self.assertIn("ยังไม่ได้ตัดมา 4 วันทำการ", entry.reason)

    def test_documents_keyed_by_staff_do_not_count_as_a_cut(self):
        """I76909054 กับ WG69-2680 เป็นเอกสารที่คนคีย์ ไม่ใช่การ import"""
        days = working_days()
        rows = import_rows("I2", days[:-3]) + manual_rows("I2", days[-3:])
        report = cut_status.collect(build(rows), window_days=WINDOW)
        entry = report["stores"][0]
        self.assertEqual(entry.method, cut_status.METHOD_IMPORT)
        self.assertEqual(entry.days_behind, 3)


class ManualStoreTests(unittest.TestCase):
    def test_store_without_import_documents_is_measured_by_its_own_rhythm(self):
        report = cut_status.collect(build(manual_rows("2", working_days())), window_days=WINDOW)
        entry = report["stores"][0]
        self.assertEqual(entry.method, cut_status.METHOD_MANUAL)
        self.assertEqual(entry.expected_gap, 1)
        self.assertEqual(entry.missing_working_days, [])
        self.assertFalse(entry.needs_attention)

    def test_a_store_that_posts_weekly_is_not_flagged_for_a_four_day_gap(self):
        """คลังทันตกรรมบันทึกห่าง ๆ เป็นปกติ ไม่ควรขึ้นเตือนทุกวัน"""
        weekly = working_days()[::5]
        report = cut_status.collect(build(manual_rows("DN", weekly)), window_days=WINDOW)
        entry = report["stores"][0]
        self.assertEqual(entry.expected_gap, 5)
        self.assertLessEqual(entry.days_behind, 5)
        self.assertFalse(entry.needs_attention)

    def test_a_daily_store_going_quiet_is_flagged(self):
        """ต้องมีคลังอื่นที่ข้อมูลมาถึงวันล่าสุดด้วย ไม่งั้น "วันล่าสุดของข้อมูล" จะเท่ากับ
        วันสุดท้ายของคลังที่เงียบเอง แล้วจะไม่มีวันขึ้นว่าค้าง"""
        days = working_days()
        rows = import_rows("I2", days) + manual_rows("2", days[:-6], prefix="M6")
        report = cut_status.collect(build(rows), window_days=WINDOW)
        entry = next(item for item in report["stores"] if item.store == "2")
        self.assertEqual(entry.days_behind, 6)
        self.assertTrue(entry.needs_attention)
        self.assertIn("เงียบมา", entry.reason)
        self.assertIn("ปกติคลังนี้บันทึกทุก ๆ 1 วันทำการ", entry.reason)


class ReportShapeTests(unittest.TestCase):
    def test_stores_needing_attention_come_first(self):
        days = working_days()
        rows = import_rows("I2", days) + manual_rows("2", days[:-8], prefix="M6")
        report = cut_status.collect(build(rows), window_days=WINDOW)
        self.assertEqual(report["stores"][0].store, "2")
        self.assertEqual([item.store for item in report["needs_attention"]], ["2"])

    def test_measured_against_the_newest_data_not_today(self):
        report = cut_status.collect(build(import_rows("I2", ["20260105", "20260106"])),
                                    window_days=30)
        self.assertEqual(report["as_of"], "20260106")
        self.assertEqual(report["stores"][0].days_behind, 0)

    def test_store_code_is_translated_to_its_thai_name(self):
        report = cut_status.collect(build(import_rows("I2", ["20260909"])), window_days=5)
        self.assertEqual(report["stores"][0].name, "ห้องจ่ายยาผู้ป่วยใน")

    def test_empty_database_says_why_instead_of_showing_zero(self):
        report = cut_status.collect(build([]), window_days=30)
        self.assertEqual(report["stores"], [])
        self.assertIn("ยังไม่มีข้อมูล", report["reason"])


if __name__ == "__main__":
    unittest.main()
