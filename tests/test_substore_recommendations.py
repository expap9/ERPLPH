from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import substores


class TestSubstoreRecommendations(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
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
                main_category TEXT, sub_category TEXT, common_name TEXT, retired INTEGER DEFAULT 0
            )
        """)

        # Items
        self.conn.execute("INSERT INTO items VALUES ('1001', 'PARACETAMOL 500MG', 'ยา', '01', '', 'PARACETAMOL', 0)")
        self.conn.execute("INSERT INTO items VALUES ('1002', 'AMOXICILLIN 500MG', 'ยา', '01', '', 'AMOXICILLIN', 0)")
        self.conn.execute("INSERT INTO items VALUES ('1003', 'OMEPRAZOLE 20MG', 'ยา', '01', '', 'OMEPRAZOLE', 0)")
        self.conn.execute("INSERT INTO items VALUES ('60105001', 'หมึก HP Laser Jet', 'พัสดุ', '6', '', '', 0)")

        # Balances in I2 for period 202609:
        self.conn.execute("INSERT INTO balances VALUES ('202609', 'I2', '1002', 'L02', 50.0, 150.0, 'CAP', '20270430', '20260901')")
        self.conn.execute("INSERT INTO balances VALUES ('202609', 'I2', '1003', 'L03', 1000.0, 2000.0, 'TAB', '20270430', '20260901')")

        # 3-Month Issues
        for p in ('202606', '202607', '202608'):
            self.conn.execute("INSERT INTO issues VALUES (?, 'I2', 'D01', '1', '', '1001', 'L01', 300.0, 150.0, 'TAB', '', '', '32', '', 'VERIFIED', '', 'out', '102', '01', '01')", (p,))
            self.conn.execute("INSERT INTO issues VALUES (?, 'I2', 'D02', '1', '', '1002', 'L02', 300.0, 900.0, 'CAP', '', '', '32', '', 'VERIFIED', '', 'out', '102', '01', '01')", (p,))
            self.conn.execute("INSERT INTO issues VALUES (?, 'I2', 'D03', '1', '', '1003', 'L03', 200.0, 400.0, 'TAB', '', '', '32', '', 'VERIFIED', '', 'out', '102', '01', '01')", (p,))

        # Supply issues (Toner 60105001)
        self.conn.execute("INSERT INTO issues VALUES ('202608', '1', 'SUP01', '1', '', '60105001', 'L01', 10.0, 25000.0, 'BOX', '', '', '32', '', 'VERIFIED', '', 'out', '208', '02', '02')")

        # Pending transfer for 1001 from store 2 to I2
        self.conn.execute("INSERT INTO issues VALUES ('202609', '2', 'GTF6909-001', '1', '', '1001', 'L01', 200.0, 100.0, 'TAB', '', '2026-09-17 08:30', '35', '', 'VERIFIED', '', 'out', '208', '02', '02')")

    def tearDown(self):
        self.conn.close()

    def test_reorder_recommendations(self):
        res = substores.get_requisition_recommendations(self.conn, 'I2', target_mos=1.0)
        self.assertEqual(res['store_code'], 'I2')
        self.assertEqual(res['target_mos'], 1.0)
        self.assertEqual(res['total_items'], 2)
        self.assertEqual(res['stockout_count'], 1)
        self.assertEqual(res['critical_count'], 1)

        items_map = {it['stock_code']: it for it in res['items']}
        item1 = items_map['1001']
        self.assertEqual(item1['urgency'], 'STOCKOUT')
        self.assertEqual(item1['on_hand_qty'], 0.0)
        self.assertEqual(item1['amc_qty'], 300.0)
        self.assertEqual(item1['suggested_qty'], 300.0)

        item2 = items_map['1002']
        self.assertEqual(item2['urgency'], 'CRITICAL')
        self.assertEqual(item2['on_hand_qty'], 50.0)
        self.assertEqual(item2['suggested_qty'], 250.0)

    def test_category_all_items(self):
        data = substores.get_category_all_items(self.conn, 'toner', months=18)
        self.assertEqual(data['scope'], 'toner')
        self.assertEqual(data['total_items'], 1)
        self.assertEqual(data['items'][0]['stock_code'], '60105001')
        self.assertEqual(data['items'][0]['value'], 25000.0)

    def test_category_all_depts(self):
        data = substores.get_category_all_depts(self.conn, 'toner', months=18)
        self.assertEqual(data['scope'], 'toner')
        self.assertEqual(data['total_depts'], 1)
        self.assertEqual(data['depts'][0]['dept_code'], '208-02-02')


if __name__ == '__main__':
    unittest.main()
