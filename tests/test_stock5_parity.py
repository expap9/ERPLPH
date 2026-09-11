"""ตัวเทียบกับ Stock5 ต้องจับความต่างได้ทุกแบบ และไม่ตื่นตูมกับเดือนปัจจุบัน"""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

_spec = importlib.util.spec_from_file_location("check_stock5_parity",
                                               ROOT / "scripts" / "check_stock5_parity.py")
parity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity)

KEY = ("69D0001", "1", "1321080", "36")
LINE = ("202608", "VERIFIED", 100.0, 1423.1)


class CompareTests(unittest.TestCase):
    def test_identical_lines_have_no_difference(self):
        self.assertEqual(parity.compare({KEY: LINE}, {KEY: LINE}, {"202608"}), {})

    def test_a_line_missing_on_either_side_is_reported(self):
        self.assertIn("มีแต่ ERPLPH", parity.compare({KEY: LINE}, {}, {"202608"})["202608"])
        self.assertIn("มีแต่ Stock5", parity.compare({}, {KEY: LINE}, {"202608"})["202608"])

    def test_a_different_check_status_is_reported(self):
        pending = ("202608", "PENDING", 100.0, 1423.1)
        diff = parity.compare({KEY: LINE}, {KEY: pending}, {"202608"})
        self.assertIn("สถานะสอบทานต่าง", diff["202608"])

    def test_a_one_satang_difference_is_reported(self):
        off = ("202608", "VERIFIED", 100.0, 1423.11)
        self.assertIn("จำนวนหรือมูลค่าต่าง", parity.compare({KEY: LINE}, {KEY: off}, {"202608"})["202608"])

    def test_periods_only_one_side_covers_are_not_compared(self):
        # Stock5 อาจตั้งช่วงรายงานต่างออกไป งวดนอกช่วงร่วมไม่ใช่ความผิดพลาด
        self.assertEqual(parity.compare({KEY: LINE}, {}, {"202609"}), {})


if __name__ == "__main__":
    unittest.main()
