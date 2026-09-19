"""ตัด Stock รายวันห้องยา ต้องใช้วันที่จริงล่าสุดที่มีข้อมูล ไม่ใช่นาฬิกาเครื่อง

เจอจริง 19 ก.ย. 2569: หลังรัน pull_warehouse_data.bat รอบแรกบนเซิร์ฟเวอร์ใหม่
ทุกยอดขึ้น 0 หมด เพราะฟังก์ชันเดิมยึด datetime.now() เป็น "วันนี้"/"เมื่อวาน" ตรง ๆ
แต่คลังข้อมูลต้นทาง (SSB) เป็นสำเนา restore รายวัน ข้อมูลของวันจริงตามปฏิทินยังไม่
มาถึงเลยสักแถว ทำให้ดูเหมือนของหมดคลังทั้งที่จริงแค่ข้อมูลยังไม่มา
"""
import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import substores  # noqa: E402
import warehouse_db  # noqa: E402


class PharmacyDailyStockCutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "erplph.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()
        self.conn = warehouse_db.connect()
        self.addCleanup(self.conn.close)

        # ข้อมูลจริงล่าสุดที่มีคือ 5 วันก่อน "วันนี้" ตามนาฬิกาเครื่อง (จำลอง SSB ตามหลัง)
        real_today = datetime.date.today()
        self.stale_day = real_today - datetime.timedelta(days=5)
        self.stale_prev_day = self.stale_day - datetime.timedelta(days=1)
        stale_bal_period = self.stale_prev_day.strftime("%Y%m%d")

        self.conn.execute(
            "INSERT INTO items (stock_code, name, base_unit) VALUES ('1091350', 'OXALIPLATIN INJ 150 MG', 'VIAL')")
        # ยกมาจากงวดคงคลังจริงล่าสุด (ไม่ใช่ 'เมื่อวาน' ตามนาฬิกาเครื่อง)
        self.conn.execute(
            "INSERT INTO balances (period, store, stock_code, lot_no, qty, value) VALUES (?, '99', '1091350', 'L01', 370.0, 593850.0)",
            (stale_bal_period,))
        # ใช้เมื่อวาน (เทียบกับวันจริงล่าสุดที่มีข้อมูล ไม่ใช่เมื่อวานตามปฏิทินจริง)
        self.conn.execute(
            "INSERT INTO issues (period, store, irno, stock_code, qty, value, unit, issued_at, "
            "document_type, direction, check_status) VALUES ('202609', '99', 'D01', '1091350', 20.0, 32100.0, "
            "'VIAL', ?, '32', 'out', 'VERIFIED')",
            (self.stale_prev_day.strftime("%Y-%m-%d") + " 10:00:00",))
        # จ่ายวันนี้ (เทียบกับวันจริงล่าสุด)
        self.conn.execute(
            "INSERT INTO issues (period, store, irno, stock_code, qty, value, unit, issued_at, "
            "document_type, direction, check_status) VALUES ('202609', '99', 'D02', '1091350', 5.0, 8025.0, "
            "'VIAL', ?, '32', 'out', 'VERIFIED')",
            (self.stale_day.strftime("%Y-%m-%d") + " 09:00:00",))
        self.conn.commit()

    def test_uses_the_actual_latest_data_day_not_the_wall_clock(self):
        result = substores.get_pharmacy_daily_stock_cut(self.conn, "99")
        kpis = result["kpis"]

        self.assertEqual(kpis["as_of_today"], self.stale_day.strftime("%Y-%m-%d"),
                         "ต้องใช้วันจริงล่าสุดที่มีข้อมูล ไม่ใช่วันนี้ตามนาฬิกาเครื่อง")
        self.assertTrue(kpis["data_is_stale"], "ข้อมูลตามหลังวันจริง ต้องปักธงบอกตรง ๆ")
        self.assertEqual(kpis["total_items"], 1)

        row = result["data"][0]
        self.assertEqual(row["stock_code"], "1091350")
        self.assertEqual(row["prev_bal"], 370.0, "ต้องดึงยอดยกมาจากงวดคงคลังจริงล่าสุด ไม่ใช่ 0")
        self.assertEqual(row["yesterday_use"], 20.0)
        self.assertEqual(row["today_use"], 5.0)
        self.assertEqual(row["current_on_hand"], 370.0 - 5.0, "ยกมา + รับวันนี้(0) - จ่ายวันนี้")
        self.assertNotEqual(row["status"], "🚨 ยาหมดคลัง", "มีของเหลืออยู่ ต้องไม่ขึ้นว่าของหมดคลัง")

    def test_flags_fresh_data_as_not_stale(self):
        """ถ้าข้อมูลจริง ๆ ตรงกับวันนี้ตามนาฬิกาเครื่อง ต้องไม่ขึ้นเตือนว่าข้อมูลเก่า"""
        self.conn.execute("DELETE FROM balances")
        self.conn.execute("DELETE FROM issues")
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        self.conn.execute(
            "INSERT INTO balances (period, store, stock_code, lot_no, qty, value) VALUES (?, '99', '1091350', 'L01', 100.0, 1000.0)",
            (yesterday.strftime("%Y%m%d"),))
        self.conn.execute(
            "INSERT INTO issues (period, store, irno, stock_code, qty, value, unit, issued_at, "
            "document_type, direction, check_status) VALUES ('202609', '99', 'D03', '1091350', 1.0, 10.0, "
            "'VIAL', ?, '32', 'out', 'VERIFIED')",
            (today.strftime("%Y-%m-%d") + " 08:00:00",))
        self.conn.commit()

        result = substores.get_pharmacy_daily_stock_cut(self.conn, "99")
        self.assertFalse(result["kpis"]["data_is_stale"])
        self.assertEqual(result["kpis"]["as_of_today"], today.strftime("%Y-%m-%d"))

    def test_falls_back_to_wall_clock_when_there_is_no_data_at_all(self):
        """คลังว่างเปล่าสนิท (ยังไม่เคยดึงอะไรเลย) ต้องไม่ error แค่คืนรายการว่าง"""
        self.conn.execute("DELETE FROM balances")
        self.conn.execute("DELETE FROM issues")
        self.conn.commit()
        result = substores.get_pharmacy_daily_stock_cut(self.conn, "99")
        self.assertEqual(result["kpis"]["total_items"], 0)
        self.assertTrue(result["kpis"]["data_is_stale"])


if __name__ == "__main__":
    unittest.main()
