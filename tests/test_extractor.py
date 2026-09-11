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
        warehouse_db.replace_period("202608", "2", "issue", [])
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
