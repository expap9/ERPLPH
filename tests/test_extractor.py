"""ตัวดึงข้อมูลทุกคลัง: ขอบเขต การแยกชนิดเอกสาร และการทนต่อความล้มเหลว

ตัวเลขที่ผู้บริหารเห็นมาจากที่นี่ ความผิดพลาดของขอบเขต (คลังผิด หมวดผิด) หรือ
การนับการโอนเป็นการใช้จริง จะทำให้ยอดทั้งโรงพยาบาลเพี้ยนโดยไม่มีสัญญาณเตือน
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import categories
import extractor
import queries
import stock5_engine
import warehouse_db


ISSUE_COLUMNS = ("WORKING_CODE", "ENGLISHNAME", "TRADE_NAME", "IRNO", "SUFFIX",
                 "SOURCE_MOVEMENT_SUFFIX", "SOURCE_LOTNO", "QTY_DIS", "VALUE",
                 "ISSUEUNITCODE", "DIS_DEPT_GROUP", "SOURCE_MOVEMENT_DATETIME",
                 "SOURCE_DOCUMENTTYPE", "SOURCE_MOS_STATUS", "SOURCE_MOS_REASON",
                 "MAINCATEGORY")


def issue_row(code="1321080", document_type="32", qty=100.0, value=1423.1):
    return (code, "AATORVASTATIN TAB 40 mg", "LLipitor", "69D1", "1", "36", "L1",
            qty, value, "TAB", "2", "2026-08-01 09:00:00", document_type, "VERIFIED", "", "11")


class FakeCursor:
    def __init__(self, rows, columns, fail=None):
        self.description = [(name,) for name in columns]
        self._rows = rows
        self._fail = fail

    def execute(self, sql):
        self.sql = sql
        if self._fail:
            raise self._fail

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows=(), columns=ISSUE_COLUMNS, fail=None):
        self.rows = list(rows)
        self.columns = columns
        self.fail = fail
        self.queries = []

    def cursor(self):
        cursor = FakeCursor(self.rows, self.columns, self.fail)
        self.queries.append(cursor)
        return cursor

    def close(self):
        pass


class ScopeTests(unittest.TestCase):
    """คำสั่งต้องถูกเปลี่ยนขอบเขตครบ ไม่เหลือของ Stock5 ค้างไว้"""

    def setUp(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")

    def test_every_query_kind_is_rescoped(self):
        for kind in ("RECEIPT", "DISTRIBUTION", "INVENTORY"):
            with self.subTest(kind=kind):
                sql = queries.build(kind, "O5", "20260801", "20260901")
                self.assertIn("STORE = 'O5'", sql)
                self.assertNotIn("STORE = '2'", sql, "ยังเหลือคลังของ Stock5")
                self.assertNotIn("[12a-zA-Z]", sql, "ยังเหลือตัวกรองรหัสเดิม")
                self.assertIn("MAINCATEGORY", sql)
                self.assertNotIn("{{", sql, "ยังเหลือ token ที่ยังไม่ได้แทนค่า")

    def test_a_bad_store_code_is_refused_rather_than_injected(self):
        for bad in ("2'; DROP TABLE items--", "", "ยาว-เกิน-สิบตัวอักษร"):
            with self.subTest(store=bad), self.assertRaises(ValueError):
                queries.build("RECEIPT", bad, "20260801", "20260901")

    def test_a_bad_date_is_refused(self):
        for bad in ("2026-08-01", "202608", ""):
            with self.subTest(date=bad), self.assertRaises(ValueError):
                queries.build("RECEIPT", "2", bad, "20260901")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            queries.build("SOMETHING", "2", "20260801", "20260901")


class MovementKindTests(unittest.TestCase):
    """การโอนต้องไม่ถูกนับเป็นการใช้จริง มิฉะนั้นยอดทั้งโรงพยาบาลเกิน 9.5%"""

    def test_document_types_map_to_the_kinds_found_in_the_database(self):
        self.assertEqual(queries.movement_kind("32"), "dispense")
        self.assertEqual(queries.movement_kind("35"), "transfer")
        self.assertEqual(queries.movement_kind("33"), "unclassified")
        self.assertEqual(queries.movement_kind("34"), "unclassified")

    def test_only_dispensing_counts_as_consumption(self):
        self.assertTrue(queries.counts_as_consumption("32"))
        self.assertFalse(queries.counts_as_consumption("35"))

    def test_types_of_unknown_meaning_are_not_counted_yet(self):
        # เก็บข้อมูลไว้ครบ แต่ยังไม่นับจนกว่าจะยืนยันความหมาย
        for document_type in ("33", "34", "99", ""):
            with self.subTest(document_type=document_type):
                self.assertFalse(queries.counts_as_consumption(document_type))


class PullTests(unittest.TestCase):
    def setUp(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "test.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()

    def rows(self, table="issues"):
        with warehouse_db.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]

    def test_a_pull_stores_rows_and_classifies_them(self):
        connection = FakeConnection([issue_row(document_type="32"),
                                     issue_row(code="1021030", document_type="35")])
        result = extractor.pull_period(connection, "202608", "O5", "issue")
        self.assertEqual(result["status"], "success")
        kinds = {row["stock_code"]: row["movement_kind"] for row in self.rows()}
        self.assertEqual(kinds["1321080"], "dispense")
        self.assertEqual(kinds["1021030"], "transfer")

    def test_item_names_use_the_same_cleaner_as_stock5(self):
        extractor.pull_period(FakeConnection([issue_row()]), "202608", "O5", "issue")
        item = self.rows("items")[0]
        self.assertEqual(item["name"], "ATORVASTATIN TAB 40 mg")
        self.assertEqual(item["trade_name"], "Lipitor")

    def test_a_failed_pull_is_recorded_without_raising(self):
        connection = FakeConnection([], fail=Exception("08001"))
        result = extractor.pull_period(connection, "202608", "O5", "issue")
        self.assertEqual(result["status"], "error")
        self.assertFalse(warehouse_db.is_period_complete("202608", "O5", "issue"))

    def test_a_failed_pull_does_not_leak_driver_text(self):
        connection = FakeConnection([], fail=Exception("PWD=secret123;SERVER=10.0.0.1"))
        result = extractor.pull_period(connection, "202608", "O5", "issue")
        self.assertNotIn("secret123", result["message"])

    def test_rows_without_a_stock_code_are_dropped(self):
        connection = FakeConnection([issue_row(), issue_row(code="")])
        result = extractor.pull_period(connection, "202608", "O5", "issue")
        self.assertEqual(result["source_rows"], 2)
        self.assertEqual(result["stored_rows"], 1)

    def test_repulling_replaces_the_period(self):
        extractor.pull_period(FakeConnection([issue_row(), issue_row(code="1021030")]),
                              "202608", "O5", "issue")
        extractor.pull_period(FakeConnection([issue_row()]), "202608", "O5", "issue")
        self.assertEqual(len(self.rows()), 1)


class PlanTests(unittest.TestCase):
    def setUp(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "test.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()

    import datetime as _dt

    def test_the_plan_covers_every_period_and_store_when_empty(self):
        work = extractor.plan(self._dt.date(2026, 9, 11), ["2", "O5"], ["issue"])
        self.assertEqual(len(work), 24 * 2, "24 งวด x 2 คลัง")

    def test_periods_already_held_are_not_planned_again(self):
        # "มีอยู่แล้ว" ต้องหมายถึงดึงด้วยคำสั่งรุ่นปัจจุบัน งวดที่ไม่มีลายนิ้วมือ
        # พิสูจน์ไม่ได้ว่าถูกต้อง จึงถูกดึงใหม่ (ดู FirstRealPullLessonsTests)
        import reporting_window
        date_from, date_to = reporting_window.period_bounds("202608")
        current = queries.fingerprint(queries.build("DISTRIBUTION", "2", date_from, date_to))
        warehouse_db.replace_period("202608", "2", "issue", [], query_sha256=current)
        work = extractor.plan(self._dt.date(2026, 9, 11), ["2"], ["issue"])
        self.assertNotIn(("202608", "2", "issue"), work)

    def test_the_current_month_is_always_replanned(self):
        # เดือนที่ยังไม่ครบ ยอดยังเปลี่ยนได้ จึงต้องดึงซ้ำเสมอ
        warehouse_db.replace_period("202609", "2", "issue", [])
        work = extractor.plan(self._dt.date(2026, 9, 11), ["2"], ["issue"])
        self.assertIn(("202609", "2", "issue"), work)

    def test_a_failed_period_stays_in_the_plan(self):
        warehouse_db.mark_period_failed("202608", "2", "issue", "หมดเวลา")
        work = extractor.plan(self._dt.date(2026, 9, 11), ["2"], ["issue"])
        self.assertIn(("202608", "2", "issue"), work)


class RunTests(unittest.TestCase):
    def setUp(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "test.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()

    import datetime as _dt

    def test_a_run_reports_what_it_stored(self):
        connection = FakeConnection([issue_row()])
        result = extractor.run(self._dt.date(2026, 9, 11), ["O5"], ["issue"], limit=3,
                               pacing=0, connection_factory=lambda: connection)
        self.assertEqual(result["planned"], 3)
        self.assertEqual(result["success"], 3)
        self.assertEqual(result["failed"], 0)

    def test_one_failing_period_does_not_stop_the_rest(self):
        class SometimesFails(FakeConnection):
            calls = 0

            def cursor(self):
                SometimesFails.calls += 1
                fail = Exception("HYT00") if SometimesFails.calls == 2 else None
                return FakeCursor(self.rows, self.columns, fail)

        result = extractor.run(self._dt.date(2026, 9, 11), ["O5"], ["issue"], limit=3,
                               pacing=0, connection_factory=lambda: SometimesFails([issue_row()]))
        self.assertEqual(result["success"], 2)
        self.assertEqual(result["failed"], 1)

    def test_nothing_to_do_is_reported_plainly(self):
        result = extractor.run(self._dt.date(2026, 9, 11), [], ["issue"], pacing=0,
                               connection_factory=lambda: FakeConnection())
        self.assertEqual(result["planned"], 0)
        self.assertEqual(result["success"], 0)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# บทเรียนจากการดึงจริงรอบแรก (11 ก.ย. 2569)
# ---------------------------------------------------------------------------

def lot_level_rows(document_type="32", nature_out="1", add_stock="0", raw_qty=("1900", "1100")):
    """แถวรายล็อตครบฟิลด์ที่ reconcile_distribution ต้องใช้ ตามรูปแบบคำสั่งจ่ายของ Stock5"""
    common = dict(
        HOSP_CODE="TEST", WORKING_CODE="1321080", ENGLISHNAME="AATORVASTATIN TAB 40 mg",
        TRADE_NAME="Lipitor", SOURCE_ENGINE="SSBSTOCK_MOVE_V1", UNIT_PROVENANCE="SSBSTOCK_MOVE_V1",
        SOURCE_SCOPE_VERSION="SSBSTOCK_STORE_V1", SOURCE_STORE="O5",
        SOURCE_DOCUMENTTYPE=document_type, IRNO="O5-0001", SUFFIX="1",
        SOURCE_MOVEMENT_DATETIME="2026-08-08 08:43:53.000",
        SOURCE_PARENT_MOVEMENT_DATETIME="2026-08-08 08:43:53.000",
        SOURCE_PARENT_PRESENT="1", SOURCE_MOVEMENT_PRESENT="1", SOURCE_PARENT_QTY="3000",
        SOURCE_PARENT_UNIT="TAB", SOURCE_PARENT_TO_BASE_FACTOR="1",
        SOURCE_MOVEMENT_UNIT="TAB", SOURCE_ISSUEUNITCODE="TAB", SOURCE_BASE_UNIT="TAB",
        ISSUE_TO_BASE_FACTOR="1", SOURCE_ADDSTOCK=add_stock, SOURCE_NATUREISOUT=nature_out,
        PERIOD_RPT="202608", BASE_UNIT="TAB", PACK_SIZE="1", PACK_COST="1.4231",
        SOURCE_COST_BASIS="SKMOVE_FIFO_RECORDED", ISSUEUNITCODE="TAB", DIS="D1",
        DIS_DEPT_GROUP="2", MAINCATEGORY="11")
    return [
        dict(common, SOURCE_ROW="2", SOURCE_LOTNO="L1", SOURCE_MOVEMENT_SUFFIX="36",
             SOURCE_RAW_QTY=raw_qty[0], QTY_DIS=raw_qty[0], SOURCE_FIFO_VALUE="2703.89", VALUE="2703.89"),
        dict(common, SOURCE_ROW="3", SOURCE_LOTNO="L2", SOURCE_MOVEMENT_SUFFIX="37",
             SOURCE_RAW_QTY=raw_qty[1], QTY_DIS=raw_qty[1], SOURCE_FIFO_VALUE="1565.41", VALUE="1565.41"),
    ]


def connection_for(rows):
    columns = tuple(rows[0].keys())
    return FakeConnection([tuple(row[c] for c in columns) for row in rows], columns)


class FirstRealPullLessonsTests(unittest.TestCase):
    def setUp(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "test.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        warehouse_db.init_db()

    def stored(self):
        with warehouse_db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM issues ORDER BY lot_no")]

    # --- คลังยาหลักล้มทั้ง 24 งวด เพราะการ์ดนับ STORE = '2' ของตัวเองเป็นของเหลือ
    def test_the_main_pharmacy_store_builds_every_query(self):
        for kind in ("RECEIPT", "DISTRIBUTION", "INVENTORY"):
            with self.subTest(kind=kind):
                sql = queries.build(kind, "2", "20260801", "20260901")
                self.assertIn("STORE = '2'", sql)

    def test_the_main_store_keeps_stock5_numbering_filter_so_both_systems_agree(self):
        sql = queries.build("DISTRIBUTION", "2", "20260801", "20260901")
        self.assertIn("iro.IRNO LIKE '[0-9][0-9]D%'", sql)

    # --- ห้องยาย่อยได้ชนิด 35 ล้วน เพราะตัวกรองเลขเอกสารของคลังหลักตัดการจ่ายทิ้ง
    def test_sub_stores_filter_by_document_type_not_by_numbering(self):
        sql = queries.build("DISTRIBUTION", "O5", "20260801", "20260901")
        self.assertNotIn("IRNO LIKE", sql)
        self.assertNotIn("DOCUMENTNO LIKE", sql)
        self.assertIn("iro.DOCUMENTTYPE IN ('32', '33', '34', '35')", sql)

    def test_other_query_kinds_are_left_as_stock5_wrote_them(self):
        sql = queries.build("RECEIPT", "O5", "20260801", "20260901")
        self.assertIn("RECEIVENO LIKE 'M%'", sql)

    # --- ตัวดึงรุ่นแรกไม่ได้เรียก reconcile_distribution สถานะสอบทานจึงว่างทุกแถว
    def test_issue_periods_are_reconciled_with_the_stock5_engine(self):
        result = extractor.pull_period(connection_for(lot_level_rows()), "202608", "O5", "issue")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["verified_rows"], 2)
        self.assertTrue(all(row["check_status"] == "VERIFIED" for row in self.stored()))

    def test_a_quantity_mismatch_is_left_pending_not_counted(self):
        rows = lot_level_rows(raw_qty=("1900", "900"))  # รวม 2,800 แต่รายการหลัก 3,000
        result = extractor.pull_period(connection_for(rows), "202608", "O5", "issue")
        self.assertEqual(result["pending_rows"], 2)
        self.assertTrue(all(row["check_status"] == "PENDING" for row in self.stored()))

    # --- ขาเข้าของการโอนต้องแยกได้ ไม่ปนกับการจ่าย
    def test_outgoing_and_incoming_movements_are_told_apart(self):
        extractor.pull_period(connection_for(lot_level_rows()), "202608", "O5", "issue")
        self.assertTrue(all(row["direction"] == "out" for row in self.stored()))

        extractor.pull_period(connection_for(lot_level_rows("35", nature_out="0", add_stock="1")),
                              "202608", "O5", "issue")
        rows = self.stored()
        self.assertTrue(all(row["direction"] == "in" for row in rows))
        self.assertTrue(all(row["check_status"] == "PENDING" for row in rows),
                        "ขาเข้าต้องไม่ผ่านการสอบทาน จึงไม่ถูกนับเป็นการใช้")

    def test_a_reconciliation_failure_is_recorded_and_nothing_is_stored(self):
        with patch.object(extractor, "_reconcile", side_effect=ValueError("ขาดฟิลด์")):
            result = extractor.pull_period(connection_for(lot_level_rows()), "202608", "O5", "issue")
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.stored(), [])
        self.assertFalse(warehouse_db.is_period_complete("202608", "O5", "issue"))

    # --- ข้อมูลที่ดึงด้วยคำสั่งรุ่นเก่าต้องถูกดึงใหม่ ไม่ค้างอยู่เงียบ ๆ
    import datetime as _dt

    def test_periods_pulled_with_an_older_query_are_planned_again(self):
        warehouse_db.replace_period("202607", "O5", "issue", [], query_sha256="รุ่นเก่า")
        work = extractor.plan(self._dt.date(2026, 9, 11), ["O5"], ["issue"])
        self.assertIn(("202607", "O5", "issue"), work)

    def test_periods_pulled_with_the_current_query_are_left_alone(self):
        date_from, date_to = __import__("reporting_window").period_bounds("202607")
        current = queries.fingerprint(queries.build("DISTRIBUTION", "O5", date_from, date_to))
        warehouse_db.replace_period("202607", "O5", "issue", [], query_sha256=current)
        work = extractor.plan(self._dt.date(2026, 9, 11), ["O5"], ["issue"])
        self.assertNotIn(("202607", "O5", "issue"), work)
