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


class BalanceDifferenceTests(unittest.TestCase):
    """ล็อตหมดเป็นเรื่องปกติ รหัสที่หายทั้งรหัสคือสัญญาณว่าขอบเขตต่างกัน"""

    STOCK = {("1321080", "L1"): (100.0, 142.31), ("1321080", "L2"): (50.0, 71.15)}

    def test_identical_stock_has_no_difference(self):
        found = parity.balance_difference(self.STOCK, dict(self.STOCK))
        self.assertEqual(found["identical_lots"], 2)
        self.assertEqual(found["codes_only_in_stock5"], [])
        self.assertEqual(found["lots_only_in_stock5"], 0)

    def test_a_lot_used_up_is_not_reported_as_a_missing_code(self):
        # ตัดจ่ายแบบเข้าก่อนออกก่อน ล็อตเก่าหมดแล้วหายไป ยานี้ยังมีของอยู่
        later = {("1321080", "L2"): (50.0, 71.15)}
        found = parity.balance_difference(self.STOCK, later)
        self.assertEqual(found["lots_only_in_stock5"], 1)
        self.assertEqual(found["codes_only_in_stock5"], [])

    def test_a_code_missing_on_one_side_is_reported(self):
        found = parity.balance_difference(self.STOCK, {("2321030", "L9"): (10.0, 30.0)})
        self.assertEqual(found["codes_only_in_stock5"], ["1321080"])
        self.assertEqual(found["codes_only_in_erplph"], ["2321030"])

    def test_a_new_lot_received_after_the_other_pull_is_counted_separately(self):
        later = dict(self.STOCK)
        later[("1321080", "L3")] = (200.0, 284.62)
        found = parity.balance_difference(self.STOCK, later)
        self.assertEqual(found["lots_only_in_erplph"], 1)
        self.assertEqual(found["codes_only_in_erplph"], [])


if __name__ == "__main__":
    unittest.main()
