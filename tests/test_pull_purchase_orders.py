"""ทดสอบการดึงใบสั่งซื้อจริง (SKPO/SKPODTL) โดยไม่ต้องต่อฐานข้อมูลโรงพยาบาลจริง

จำลองแถวที่ pyodbc คืนกลับมา (คอลัมน์ตรงตามที่สำรวจแล้วใน
diagnostics/procurement_finance_*.json) แล้วตรวจว่า:
    1. แถวถูกแปลงร่างและทำความสะอาดชื่อผู้ขายถูกต้อง
    2. เขียนลง purchase_orders แล้วอ่านกลับมาตรงกัน
    3. ดึงซ้ำในช่วงเดิมแทนที่ของเก่าทั้งหมด ไม่ทิ้งแถวเก่าปนไว้ (เหมือนงวดอื่นของ ERPLPH)
"""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

import warehouse_db  # noqa: E402
import pull_purchase_orders as ppo  # noqa: E402


class FakeCursor:
    """จำลอง pyodbc cursor คืนแถวตามคอลัมน์ที่สำรวจจริงจาก SKPO/SKPODTL/APMASTER"""

    description = [(name,) for name in (
        "PONO", "SUFFIX", "STORE", "STOCKCODE", "LOTNO", "SUPPLIERCODE", "SUPPLIER_NAME",
        "REQUESTQTY", "LOTQTY", "LOTPRICE", "AMT", "POSTATUS",
        "DIVISION", "DEPT", "SECTION",
        "ISSUEDATETIME", "APPROVEDATETIME", "DUEDATETIME", "LASTRECEIVEDATETIME", "CONTRACTNO",
    )]

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params):
        return self

    def fetchall(self):
        return self._rows

    def fetchmany(self, limit):
        return self._rows[:limit]

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._rows)


def _row(po_no="P1", suffix=1, store="2", stock_code="1000", supplier="บริษัท บบบทดสอบ จำกัด",
         approved=True):
    return (
        po_no, suffix, store, stock_code, "L01", "V001", supplier,
        100.0, 100.0, 12.5, 1250.0, 2,
        "208", "02", "02",
        datetime(2026, 6, 1), datetime(2026, 6, 2) if approved else None,
        datetime(2026, 6, 15), None, "",
    )


class PullPurchaseOrdersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "erplph.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()

    def test_fetch_rows_cleans_and_maps_columns(self):
        conn = FakeConn([_row()])
        rows = ppo.fetch_rows(conn, datetime(2026, 1, 1), datetime(2026, 12, 31))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["po_no"], "P1")
        self.assertEqual(row["store"], "2")
        self.assertEqual(row["amount"], 1250.0)
        self.assertEqual(row["postatus"], 2)
        self.assertEqual(row["approve_datetime"], "2026-06-02 00:00:00")
        self.assertNotEqual(row["supplier_name"], "", "ต้องได้ชื่อผู้ขายที่ทำความสะอาดแล้ว ไม่ใช่ว่างเปล่า")

    def test_unapproved_po_has_empty_approve_datetime(self):
        conn = FakeConn([_row(po_no="P2", approved=False)])
        rows = ppo.fetch_rows(conn, datetime(2026, 1, 1), datetime(2026, 12, 31))
        self.assertEqual(rows[0]["approve_datetime"], "",
                          "ยังไม่อนุมัติต้องเป็นค่าว่าง ไม่ใช่การเดาว่าอนุมัติแล้ว")

    def test_store_rows_writes_and_replaces_window(self):
        date_from, date_to = datetime(2026, 1, 1), datetime(2026, 12, 31)
        rows_first = [dict(po_no="P1", suffix=1, store="2", stock_code="1000", lot_no="L01",
                            supplier_code="V001", supplier_name="บริษัท ก จำกัด",
                            request_qty=10.0, lot_qty=10.0, lot_price=5.0, amount=50.0,
                            postatus=2, division="208", dept="02", section="02",
                            issue_datetime="2026-06-01", approve_datetime="2026-06-02",
                            due_datetime="", last_receive_datetime="", contract_no="")]
        ppo.store_rows(rows_first, date_from, date_to)

        with warehouse_db.connect() as conn:
            stored = conn.execute("SELECT po_no, amount, supplier_name FROM purchase_orders").fetchall()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["po_no"], "P1")
        self.assertEqual(stored[0]["amount"], 50.0)

        # ดึงซ้ำในช่วงเดียวกันด้วยข้อมูลใหม่ (PO เดิมถูกยกเลิก/ไม่มีในรอบใหม่) ต้องแทนที่ทั้งหมด
        rows_second = [dict(po_no="P2", suffix=1, store="2", stock_code="1000", lot_no="L02",
                             supplier_code="V002", supplier_name="บริษัท ข จำกัด",
                             request_qty=5.0, lot_qty=5.0, lot_price=8.0, amount=40.0,
                             postatus=0, division="208", dept="02", section="02",
                             issue_datetime="2026-07-01", approve_datetime="",
                             due_datetime="", last_receive_datetime="", contract_no="")]
        ppo.store_rows(rows_second, date_from, date_to)

        with warehouse_db.connect() as conn:
            stored = conn.execute("SELECT po_no FROM purchase_orders").fetchall()
        self.assertEqual([r["po_no"] for r in stored], ["P2"],
                          "ดึงซ้ำช่วงเดิมต้องแทนที่ของเก่าทั้งหมด ไม่ใช่เพิ่มปนกัน")

    def test_store_rows_does_not_touch_other_date_windows(self):
        date_from_a, date_to_a = datetime(2026, 1, 1), datetime(2026, 6, 30)
        date_from_b, date_to_b = datetime(2026, 7, 1), datetime(2026, 12, 31)
        ppo.store_rows([dict(po_no="A1", suffix=1, store="2", stock_code="1000", lot_no="",
                              supplier_code="", supplier_name="", request_qty=1.0, lot_qty=1.0,
                              lot_price=1.0, amount=1.0, postatus=2, division="", dept="", section="",
                              issue_datetime="2026-03-01", approve_datetime="2026-03-02",
                              due_datetime="", last_receive_datetime="", contract_no="")],
                       date_from_a, date_to_a)
        ppo.store_rows([dict(po_no="B1", suffix=1, store="2", stock_code="1000", lot_no="",
                              supplier_code="", supplier_name="", request_qty=1.0, lot_qty=1.0,
                              lot_price=1.0, amount=1.0, postatus=2, division="", dept="", section="",
                              issue_datetime="2026-09-01", approve_datetime="2026-09-02",
                              due_datetime="", last_receive_datetime="", contract_no="")],
                       date_from_b, date_to_b)
        with warehouse_db.connect() as conn:
            stored = {r["po_no"] for r in conn.execute("SELECT po_no FROM purchase_orders")}
        self.assertEqual(stored, {"A1", "B1"}, "ดึงคนละช่วงวันที่ต้องไม่ลบของช่วงอื่นทิ้ง")


if __name__ == "__main__":
    unittest.main()
