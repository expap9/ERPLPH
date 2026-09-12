"""ชั้นคำนวณตัวชี้วัด: สูตรต้องเปลี่ยนตามขอบเขต และเกณฑ์ต้องเป็นไปตามที่ผู้ใช้กำหนด

ตัวเลขเหล่านี้คือสิ่งที่ผู้บริหารเห็น การนับการโอนเป็นการใช้ หรือการลืมหักรับคืน
ทำให้ยอดเพี้ยนเป็นเท่าตัวโดยไม่มีสัญญาณเตือน
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import metrics  # noqa: E402
import warehouse_db  # noqa: E402

TODAY = date.today()
DAY = TODAY.strftime("%Y%m%d")
THIS_MONTH = TODAY.strftime("%Y%m")


def month_before(months: int) -> str:
    total = TODAY.year * 12 + (TODAY.month - 1) - months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def in_days(days: int) -> str:
    return (TODAY + timedelta(days=days)).strftime("%Y%m%d")


class MetricsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "test.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()
        self.conn = warehouse_db.connect()
        self.addCleanup(self.conn.close)

    def hold(self, store, code, qty, value, lot="L1", expire="", day=DAY, unit=""):
        """คงคลังหนึ่งล็อต พร้อมบันทึกว่างวดภาพนั้นดึงสำเร็จแล้ว"""
        warehouse_db.replace_period(day, store, "balance", [
            {"stock_code": code, "lot_no": lot, "qty": qty, "value": value,
             "expire_date": expire, "unit": unit},
        ] + self.existing_lots(store, day), source_rows=1)

    def existing_lots(self, store, day):
        rows = self.conn.execute(
            "SELECT stock_code, lot_no, qty, value, expire_date, unit FROM balances "
            "WHERE store = ? AND period = ?", (store, day)).fetchall()
        return [{"stock_code": code, "lot_no": lot, "qty": qty, "value": value,
                 "expire_date": expire, "unit": unit}
                for code, lot, qty, value, expire, unit in rows]

    def move(self, store, code, period, value, kind="dispense", direction="out",
             status="VERIFIED", qty=1.0, key="1", unit=""):
        with warehouse_db.connect() as conn:
            conn.execute(
                "INSERT INTO issues (period, store, irno, suffix, movement_key, stock_code, "
                "qty, value, unit, movement_kind, direction, check_status) "
                "VALUES (?, ?, ?, '1', ?, ?, ?, ?, ?, ?, ?, ?)",
                (period, store, f"{code}-{period}-{key}", key, code, qty, value, unit, kind,
                 direction, status))
            conn.commit()


class StockValueTests(MetricsTestCase):
    def test_stock_is_reported_with_the_expired_part_on_its_own_line(self):
        self.hold("2", "A", 10, 1000.0, lot="good", expire=in_days(400))
        self.hold("2", "B", 5, 250.0, lot="old", expire=in_days(-30))
        found = metrics.stock(self.conn)
        self.assertEqual(found["value"], 1250.0)
        self.assertEqual(found["expired_value"], 250.0)
        self.assertEqual(found["expired_lots"], 1)

    def test_only_the_latest_snapshot_of_each_store_is_counted(self):
        older = (TODAY - timedelta(days=1)).strftime("%Y%m%d")
        self.hold("2", "A", 10, 999.0, day=older)
        self.hold("2", "A", 8, 800.0)
        self.assertEqual(metrics.stock(self.conn)["value"], 800.0)

    def test_a_store_scope_leaves_other_stores_out(self):
        self.hold("2", "A", 10, 1000.0)
        self.hold("O5", "A", 10, 2000.0)
        self.assertEqual(metrics.stock(self.conn, ["O5"])["value"], 2000.0)
        self.assertEqual(metrics.stock(self.conn)["value"], 3000.0)


class ConsumptionTests(MetricsTestCase):
    def setUp(self):
        super().setUp()
        self.period = month_before(1)
        self.move("2", "A", self.period, 1_000_000.0, kind="transfer")   # คลังหลักโอนให้ห้องยา
        self.move("O5", "A", self.period, 1_000_000.0, kind="dispense")  # ห้องยาจ่ายผู้ป่วย
        self.move("O5", "A", self.period, 100_000.0, kind="dispense", direction="in",
                  status="PENDING", key="2")                            # ผู้ป่วยคืนยา

    def test_the_hospital_total_counts_dispensing_only_and_nets_returns(self):
        found = metrics.consumption(self.conn, until=THIS_MONTH)
        self.assertEqual(found["average"] * len(found["months"]), 900_000.0)

    def test_adding_up_every_store_would_double_the_hospital_figure(self):
        # เหตุผลที่สูตรต้องเปลี่ยนตามขอบเขต ไม่ใช่แค่กรองข้อมูล
        per_store = sum(metrics.consumption(self.conn, store, until=THIS_MONTH)["average"]
                        for store in ("2", "O5"))
        hospital = metrics.consumption(self.conn, until=THIS_MONTH)["average"]
        self.assertAlmostEqual(per_store - hospital, 1_000_000.0, places=2)

    def test_the_average_divides_by_the_months_the_system_holds_data_for(self):
        # ยาที่ใช้เดือนเดียวใน 6 เดือน ต้องไม่ถูกมองว่าใช้เดือนละเท่ากับทั้งก้อน
        for index in range(1, 7):
            warehouse_db.replace_period(month_before(index), "O5", "issue", [], source_rows=0)
        # เขียนงวดแล้วแถวของงวดนั้นถูกลบ จึงใส่การเคลื่อนไหวหลังบันทึกงวด
        self.move("O5", "A", self.period, 900_000.0, key="7")
        found = metrics.consumption(self.conn, "O5", until=THIS_MONTH)
        self.assertEqual(found["covered_months"], 6)
        self.assertAlmostEqual(found["average"], 900_000.0 / 6)

    def test_a_store_counts_its_transfers_out_as_outflow(self):
        found = metrics.consumption(self.conn, "2", until=THIS_MONTH)
        self.assertEqual(found["months"][0]["net"], 1_000_000.0)

    def test_only_verified_lines_are_counted(self):
        self.move("O5", "A", self.period, 500_000.0, status="PENDING", key="9")
        found = metrics.consumption(self.conn, until=THIS_MONTH)
        self.assertEqual(found["months"][0]["issued"], 1_000_000.0)

    def test_the_unfinished_month_is_left_out_of_the_average(self):
        self.move("O5", "A", THIS_MONTH, 5_000_000.0, key="3")
        found = metrics.consumption(self.conn, until=THIS_MONTH)
        self.assertTrue(all(row["period"] != THIS_MONTH for row in found["months"]))

    def test_months_of_stock_needs_something_to_divide_by(self):
        self.assertEqual(metrics.months_of_stock(900.0, 300.0), 3.0)
        self.assertIsNone(metrics.months_of_stock(900.0, 0.0))


class ExpiryTests(MetricsTestCase):
    def setUp(self):
        super().setUp()
        self.hold("2", "A", 1, 100.0, lot="expired", expire=in_days(-1))
        self.hold("2", "B", 1, 200.0, lot="soon", expire=in_days(40))
        self.hold("2", "C", 1, 300.0, lot="later", expire=in_days(150))
        self.hold("2", "D", 1, 400.0, lot="safe", expire=in_days(400))
        self.hold("2", "E", 1, 500.0, lot="blank", expire="")

    def test_the_three_month_line_is_separated_from_the_six_month_line(self):
        found = metrics.expiring(self.conn)
        self.assertEqual(found["expired"]["value"], 100.0)
        self.assertEqual(found["critical"]["value"], 200.0)
        self.assertEqual(found["warning"]["value"], 300.0)

    def test_stock_that_lasts_beyond_the_warning_is_not_listed(self):
        codes = {row["stock_code"] for row in metrics.expiring_lots(self.conn)}
        self.assertEqual(codes, {"A", "B", "C"})

    def test_lots_without_an_expiry_date_are_reported_not_assumed_safe(self):
        self.assertEqual(metrics.expiring(self.conn)["no_expiry_date"]["value"], 500.0)


class DormantTests(MetricsTestCase):
    def test_an_item_that_only_moves_by_transfer_is_not_dormant(self):
        # ความผิดพลาดจริงตอนคำนวณร่าง: นับเฉพาะการจ่ายผู้ป่วย ทำให้คลังหลัก
        # กลายเป็นของค้างนิ่ง 44 ล้านบาท ทั้งที่จ่ายออกด้วยการโอนตลอด
        self.hold("2", "A", 10, 1000.0)
        self.move("2", "A", month_before(1), 5000.0, kind="transfer")
        self.assertEqual(metrics.dormant(self.conn, until=THIS_MONTH), [])

    def test_stock_with_no_outflow_in_the_window_is_dormant(self):
        self.hold("2", "B", 10, 2000.0)
        self.move("2", "B", month_before(12), 5000.0)
        found = metrics.dormant(self.conn, until=THIS_MONTH)
        self.assertEqual([row["stock_code"] for row in found], ["B"])

    def test_an_item_with_no_value_left_is_not_listed(self):
        self.hold("2", "C", 10, 0.0)
        self.assertEqual(metrics.dormant(self.conn, until=THIS_MONTH), [])


class LowStockTests(MetricsTestCase):
    def use(self, store, code, monthly_value, months=6, start=1):
        for index in range(start, start + months):
            self.move(store, code, month_before(index), monthly_value, key=f"u{index}")

    def test_an_item_below_one_month_is_listed_with_how_long_it_lasts(self):
        self.hold("O5", "A", 10, 1000.0)
        self.use("O5", "A", 6000.0)
        found = metrics.low_stock(self.conn, ["O5"], until=THIS_MONTH)
        self.assertEqual([row["stock_code"] for row in found["items"]], ["A"])
        self.assertAlmostEqual(found["items"][0]["months_left"], 1000.0 / 6000.0)

    def test_an_item_that_ran_out_is_listed_first_even_though_it_has_no_lot_left(self):
        # ของที่หมดเกลี้ยงไม่มีอยู่ในภาพคงคลัง ถ้าดูแต่คงคลังจะไม่เห็นรายการที่ด่วนที่สุด
        self.hold("O5", "A", 10, 1000.0)
        self.use("O5", "A", 6000.0)
        self.use("O5", "GONE", 5000.0)
        found = metrics.low_stock(self.conn, ["O5"], until=THIS_MONTH)
        self.assertEqual(found["items"][0]["stock_code"], "GONE")
        self.assertTrue(found["items"][0]["out_of_stock"])

    def test_an_item_with_plenty_left_is_not_listed(self):
        self.hold("O5", "B", 100, 100_000.0)
        self.use("O5", "B", 6000.0)
        self.assertEqual(metrics.low_stock(self.conn, ["O5"], until=THIS_MONTH)["items"], [])

    def test_returns_reduce_the_monthly_use(self):
        self.hold("O5", "C", 10, 1000.0)
        self.move("O5", "C", month_before(1), 12_000.0)
        self.move("O5", "C", month_before(1), 11_400.0, direction="in", status="PENDING", key="2")
        # ใช้สุทธิ 600 ใน 6 เดือน = 100 ต่อเดือน คงคลัง 1,000 จึงอยู่ได้สิบเดือน
        self.assertEqual(metrics.low_stock(self.conn, ["O5"], until=THIS_MONTH)["items"], [])

    def test_stock_in_another_store_counts_for_the_hospital_but_not_for_the_store(self):
        self.hold("O5", "D", 1, 100.0)
        self.hold("2", "D", 500, 500_000.0)
        self.use("O5", "D", 6000.0)
        self.assertEqual([row["stock_code"] for row in
                          metrics.low_stock(self.conn, ["O5"], until=THIS_MONTH)["items"]], ["D"])
        self.assertEqual(metrics.low_stock(self.conn, until=THIS_MONTH)["items"], [])

    def test_an_item_nobody_uses_is_not_called_low(self):
        self.hold("O5", "E", 1, 10.0)
        self.assertEqual(metrics.low_stock(self.conn, ["O5"], until=THIS_MONTH)["items"], [])

    def test_a_retired_code_is_kept_off_the_list_to_order(self):
        with warehouse_db.connect() as conn:
            conn.execute("INSERT INTO items (stock_code, name, retired) VALUES ('OLD', '(ยกเลิก) ยาเก่า', 1)")
            conn.commit()
        self.use("O5", "OLD", 6000.0)
        found = metrics.low_stock(self.conn, ["O5"], until=THIS_MONTH)
        self.assertEqual(found["items"], [])
        self.assertEqual([row["stock_code"] for row in found["retired_in_use"]], ["OLD"])

    def test_an_item_without_a_price_is_measured_in_quantity_when_units_match(self):
        # วัคซีนที่ได้รับจัดสรรมีจำนวนแต่ไม่มีมูลค่า ยังวัดได้ถ้าหน่วยตรงกัน
        self.hold("2", "EPI2001", 50, 0.0, unit="DOSE")
        for index in range(1, 7):
            self.move("2", "EPI2001", month_before(index), 0.0, qty=100.0, unit="DOSE",
                      key=f"v{index}")
        found = metrics.low_stock(self.conn, ["2"], until=THIS_MONTH)
        self.assertEqual([row["stock_code"] for row in found["items"]], ["EPI2001"])
        self.assertEqual(found["items"][0]["basis"], "qty")
        self.assertAlmostEqual(found["items"][0]["months_left"], 0.5)

    def test_an_item_without_a_price_and_with_different_units_is_reported_not_guessed(self):
        # หน่วยคงคลังเป็นขวด หน่วยจ่ายเป็นโดส ระบบไม่แปลงหน่วยเอง
        self.hold("2", "EPI2002", 50, 0.0, unit="VIAL")
        self.move("2", "EPI2002", month_before(1), 0.0, qty=100.0, unit="DOSE")
        found = metrics.low_stock(self.conn, ["2"], until=THIS_MONTH)
        self.assertEqual(found["items"], [])
        self.assertEqual([row["stock_code"] for row in found["without_value"]], ["EPI2002"])


class SummaryTests(MetricsTestCase):
    def test_the_summary_answers_every_question_the_executive_asked(self):
        self.hold("O5", "A", 10, 1000.0, expire=in_days(-5))
        self.move("O5", "A", month_before(1), 6000.0)
        found = metrics.summary(conn=self.conn)
        for key in ("stock", "consumption", "months_of_stock", "expiring", "dormant_value",
                    "low_stock_count", "as_of"):
            self.assertIn(key, found)
        self.assertEqual(found["stock"]["expired_value"], 1000.0)
        self.assertEqual(found["scope"], "ทั้งโรงพยาบาล")

    def test_an_empty_database_reports_zero_rather_than_failing(self):
        found = metrics.summary(conn=self.conn)
        self.assertEqual(found["stock"]["value"], 0)
        self.assertIsNone(found["months_of_stock"])


if __name__ == "__main__":
    unittest.main()
