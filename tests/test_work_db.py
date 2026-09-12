"""เครื่องหมายที่เจ้าหน้าที่กำหนดเอง: ต้องรู้ว่าใครกำหนด และประวัติต้องไม่หาย

เครื่องหมายเหล่านี้เปลี่ยนความหมายของตัวเลขที่ผู้บริหารเห็น ยาที่ติดเครื่องหมาย
"บริการผู้ป่วยเฉพาะราย" จะหายจากรายการใกล้หมด ถ้าใครติดผิดแล้วไม่มีประวัติ
จะไม่มีทางรู้ว่าทำไมยาตัวนั้นไม่เคยขึ้นเตือน
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import work_db  # noqa: E402


class MarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "work.db")):
            patcher = patch.object(work_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        work_db.init_db()

    def test_a_mark_records_who_set_it_and_when(self):
        work_db.set_mark("2098120", work_db.PATIENT_SPECIFIC, "41850", "สั่งตามใบสั่งแพทย์")
        found = work_db.marked()
        self.assertIn("2098120", found)
        self.assertEqual(found["2098120"]["set_by"], "41850")
        self.assertEqual(found["2098120"]["note"], "สั่งตามใบสั่งแพทย์")
        self.assertTrue(found["2098120"]["set_at"])

    def test_clearing_a_mark_keeps_the_history(self):
        work_db.set_mark("2098120", work_db.PATIENT_SPECIFIC, "41850")
        work_db.clear_mark("2098120", work_db.PATIENT_SPECIFIC, "41851", "กลับมาซื้อประจำ")
        self.assertEqual(work_db.marked(), {})
        actions = [row["action"] for row in work_db.history("2098120")]
        self.assertEqual(actions, ["clear", "set"])
        self.assertEqual(work_db.history("2098120")[0]["actor"], "41851")

    def test_marking_twice_updates_rather_than_duplicates(self):
        work_db.set_mark("2098120", work_db.PATIENT_SPECIFIC, "41850", "เหตุผลแรก")
        work_db.set_mark("2098120", work_db.PATIENT_SPECIFIC, "41852", "เหตุผลใหม่")
        found = work_db.marked()
        self.assertEqual(len(found), 1)
        self.assertEqual(found["2098120"]["set_by"], "41852")
        self.assertEqual(len(work_db.history("2098120")), 2)

    def test_a_change_without_a_person_is_refused(self):
        with self.assertRaises(ValueError):
            work_db.set_mark("2098120", work_db.PATIENT_SPECIFIC, "")

    def test_an_unknown_mark_is_refused(self):
        with self.assertRaises(ValueError):
            work_db.set_mark("2098120", "something_else", "41850")

    def test_a_missing_code_is_refused(self):
        with self.assertRaises(ValueError):
            work_db.set_mark("  ", work_db.PATIENT_SPECIFIC, "41850")

    def test_reading_before_anything_is_written_is_empty_not_an_error(self):
        with patch.object(work_db, "DB_PATH", Path(self.temp.name) / "missing.db"):
            self.assertEqual(work_db.marked(), {})
            self.assertEqual(work_db.history(), [])

    def test_the_label_is_the_wording_users_see(self):
        self.assertEqual(work_db.label(work_db.PATIENT_SPECIFIC),
                         "บริการผู้ป่วยเฉพาะราย ไม่ได้ซื้อประจำ")


if __name__ == "__main__":
    unittest.main()
