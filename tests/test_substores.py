from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import substores


class TestSubstores(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""
            CREATE TABLE issues (
                period TEXT, store TEXT, irno TEXT, suffix TEXT, movement_key TEXT,
                stock_code TEXT, lot_no TEXT, qty REAL, value REAL, unit TEXT,
                department TEXT, issued_at TEXT, document_type TEXT, movement_kind TEXT,
                check_status TEXT, check_reason TEXT, direction TEXT,
                division TEXT, dept TEXT, section TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE balances (
                period TEXT, store TEXT, stock_code TEXT, lot_no TEXT,
                qty REAL, value REAL, unit TEXT, expire_date TEXT, last_in_date TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE items (
                stock_code TEXT PRIMARY KEY, name TEXT, item_group TEXT,
                main_category TEXT, sub_category TEXT, common_name TEXT
            )
        """)

        # Sample data
        self.conn.execute("INSERT INTO items VALUES ('1001', 'PARACETAMOL 500MG', 'ยา', '01', '', 'PARACETAMOL')")
        self.conn.execute("INSERT INTO items VALUES ('1002', 'AMOXICILLIN 500MG', 'ยา', '01', '', 'AMOXICILLIN')")

        # Balances in I2 (1001 expires Nov 2026, 1002 expires Apr 2027 within 8m)
        self.conn.execute("INSERT INTO balances VALUES ('202609', 'I2', '1001', 'L01', 500.0, 250.0, 'TAB', '20261130', '20260901')")
        self.conn.execute("INSERT INTO balances VALUES ('202609', 'I2', '1002', 'L02', 100.0, 300.0, 'CAP', '20270430', '20260901')")

        # Transfers in to I2 (doc 35 in)
        self.conn.execute("INSERT INTO issues VALUES ('202608', 'I2', 'TF01', '1', '', '1001', 'L01', 500.0, 250.0, 'TAB', '', '', '35', '', 'VERIFIED', '', 'in', '208', '02', '02')")

        # Dispenses out from I2 (doc 32 out)
        # Period 202606, 202607, 202608 (3 months)
        self.conn.execute("INSERT INTO issues VALUES ('202606', 'I2', 'DSP01', '1', '', '1001', 'L01', 300.0, 150.0, 'TAB', '', '', '32', '', 'VERIFIED', '', 'out', '102', '01', '01')")
        self.conn.execute("INSERT INTO issues VALUES ('202607', 'I2', 'DSP02', '1', '', '1001', 'L01', 300.0, 150.0, 'TAB', '', '', '32', '', 'VERIFIED', '', 'out', '102', '01', '01')")
        self.conn.execute("INSERT INTO issues VALUES ('202608', 'I2', 'DSP03', '1', '', '1001', 'L01', 300.0, 150.0, 'TAB', '', '', '32', '', 'VERIFIED', '', 'out', '102', '01', '01')")
        # Unfinished current month
        self.conn.execute("INSERT INTO issues VALUES ('202609', 'I2', 'OPEN01', '1', '', '1001', 'L01', 10.0, 5.0, 'TAB', '', '', '32', '', 'VERIFIED', '', 'out', '102', '01', '01')")

    def tearDown(self):
        self.conn.close()

    def test_kpis_calculation(self):
        kpis = substores.get_substore_kpis(self.conn, "I2", latest="202609")
        self.assertEqual(kpis["stock_value"], 550.0)
        self.assertEqual(kpis["stock_items"], 2)
        self.assertEqual(kpis["monthly_amc"], 150.0)
        self.assertEqual(kpis["trans_in_val"], 250.0)
        self.assertEqual(kpis["disp_val"], 450.0)
        self.assertEqual(kpis["expiring_items"], 1)

    def test_transfers_received(self):
        trans = substores.get_transfers_received(self.conn, "I2")
        self.assertEqual(len(trans), 1)
        self.assertEqual(trans[0]["stock_code"], "1001")
        self.assertEqual(trans[0]["value"], 250.0)

    def test_amc_and_mos_list(self):
        items = substores.get_amc_and_mos_list(self.conn, "I2")
        self.assertTrue(len(items) >= 2)
        p_item = [x for x in items if x["stock_code"] == "1001"][0]
        self.assertEqual(p_item["amc_qty"], 300.0)
        self.assertAlmostEqual(p_item["mos"], 500.0 / 300.0, places=1)

    def test_expiring_medicines(self):
        exp = substores.get_expiring_medicines(self.conn, "I2")
        self.assertEqual(len(exp), 2)
        # First one is 20261130
        self.assertEqual(exp[0]["stock_code"], "1001")
        self.assertIn("2026", exp[0]["expire_date"])

    def test_ward_dispensations(self):
        wards = substores.get_ward_dispensations(self.conn, "I2")
        self.assertEqual(len(wards), 1)
        self.assertEqual(wards[0]["value"], 450.0)
        self.assertEqual(wards[0]["slips"], 3)


if __name__ == "__main__":
    unittest.main()
