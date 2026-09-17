"""ตัวดึงข้อมูลทุกคลัง: ขอบเขต การแยกชนิดเอกสาร และการทนต่อความล้มเหลว

ตัวเลขที่ผู้บริหารเห็นมาจากที่นี่ ความผิดพลาดของขอบเขต (คลังผิด หมวดผิด) หรือ
การนับการโอนเป็นการใช้จริง จะทำให้ยอดทั้งโรงพยาบาลเพี้ยนโดยไม่มีสัญญาณเตือน
"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import categories
import extractor
import queries
import stock5_engine
import unit_rules
import warehouse_db


ISSUE_COLUMNS = ("WORKING_CODE", "ENGLISHNAME", "TRADE_NAME", "IRNO", "SUFFIX",
                 "SOURCE_MOVEMENT_SUFFIX", "SOURCE_LOTNO", "QTY_DIS", "VALUE",
                 "ISSUEUNITCODE", "DIS_DEPT_GROUP",
                 "DIS_DIVISION", "DIS_DEPT", "DIS_SECTION", "SOURCE_MOVEMENT_DATETIME",
                 "SOURCE_DOCUMENTTYPE", "SOURCE_MOS_STATUS", "SOURCE_MOS_REASON",
                 "MAINCATEGORY")


def current_units(codes=()) -> str:
    """ลายนิ้วมือกติกาหน่วยปัจจุบัน — งวดที่ "มีอยู่แล้ว" ต้องบันทึกค่านี้ไว้เหมือนการดึงจริง"""
    return unit_rules.period_digest(codes, unit_rules.rules(), unit_rules.engine_digest())


def issue_row(code="1321080", document_type="32", qty=100.0, value=1423.1,
              division="208", dept="02", section="02", main_category="11"):
    return (code, "AATORVASTATIN TAB 40 mg", "LLipitor", "69D1", "1", "36", "L1",
            qty, value, "TAB", "2", division, dept, section,
            "2026-08-01 09:00:00", document_type, "VERIFIED", "", main_category)


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

    def test_the_dispense_query_asks_for_the_three_department_levels(self):
        """คลังหลักด้วย — การเพิ่มคอลัมน์ผลลัพธ์ไม่เปลี่ยนแถวหรือยอด จึงยังเทียบกับ Stock5 ได้"""
        for store in ("O5", "2"):
            with self.subTest(store=store):
                sql = queries.build("DISTRIBUTION", store, "20260801", "20260901")
                for column in ("DIS_DIVISION", "DIS_DEPT", "DIS_SECTION"):
                    self.assertIn(column, sql)
                self.assertIn("AS DIS,", sql, "ต้องไม่ทับของเดิมที่ใช้ join รหัสกลุ่มกระทรวง")

    def test_every_query_returns_the_category_it_classifies_items_by(self):
        """เจอตอนดึงจริง 17 ก.ย. 2569: ทะเบียน 8,887 รายการตกไปอยู่กลุ่ม "อื่น ๆ" ทั้งหมด

        คำสั่งของ Stock5 ไม่เคยส่ง MAINCATEGORY ออกมา มันโผล่แค่ในเงื่อนไขกรองหมวด
        พอเลิกกรอง (ดึงทุกหมวด) ค่าก็หายไปเงียบ ๆ โดยไม่มีอะไรล้ม
        """
        for kind in ("RECEIPT", "DISTRIBUTION", "INVENTORY"):
            with self.subTest(kind=kind):
                sql = queries.build(kind, "O5", "20260801", "20260901", categories.ALL)
                self.assertIn("MAINCATEGORY AS MAINCATEGORY", sql.split("FROM")[0])

    def test_a_missing_category_field_stops_the_pull_instead_of_grouping_everything_as_other(self):
        with patch.object(queries, "_statements", return_value={"RECEIPT": "SELECT 1"}), \
                self.assertRaises(ValueError):
            queries.build("RECEIPT", "O5", "20260801", "20260901")

    def test_a_missing_department_field_stops_the_pull_instead_of_reporting_blanks(self):
        """ถ้า Stock5 แก้คำสั่งจนไม่มีช่องหน่วยงาน ต้องล้มให้เห็น ไม่ใช่ได้รายงานแผนกว่าง

        คำสั่งปลอมต้องมีช่องชื่อรายการอยู่ ไม่งั้นจะไปล้มที่ด่านหมวดก่อน แล้วเทสต์นี้
        จะผ่านโดยไม่ได้ตรวจเรื่องหน่วยงานเลย
        """
        without_department = "SELECT LTRIM(RTRIM(sm.ENGLISHNAME)) AS ENGLISHNAME, 1"
        with patch.object(queries, "_statements",
                          return_value={"DISTRIBUTION": without_department}), \
                self.assertRaises(ValueError) as caught:
            queries.build("DISTRIBUTION", "O5", "20260801", "20260901")
        self.assertIn("หน่วยงาน", str(caught.exception))

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

    def test_a_dispense_coming_back_in_is_a_return(self):
        # รับคืนต้องถูกหักจากยอดใช้ ไม่ว่าสถานะสอบทานเป็นอะไร (ขาเข้าไม่เคยผ่าน)
        self.assertTrue(queries.is_return("32", "in"))
        self.assertFalse(queries.is_return("32", "out"))
        self.assertFalse(queries.is_return("35", "in"), "ขาเข้าของการโอนไม่ใช่รับคืน")


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

    def test_the_department_that_asked_is_kept_at_all_three_levels(self):
        """ผู้บริหารขอเห็น "แผนกไหนเบิกอะไร" — รหัสกลุ่มของกระทรวงมีแค่ 1-9 ไม่พอตอบ

        คำสั่งของ Stock5 ส่ง DIS_DEPT_GROUP มาให้ ซึ่งเป็นรหัสกลุ่มสำหรับส่งแฟ้ม
        ไม่ใช่หน่วยงานจริง ถ้าเก็บแค่ค่านั้น รายงานรายแผนกจะรวมทั้งโรงพยาบาลเหลือ 9 แถว
        """
        extractor.pull_period(FakeConnection([issue_row()]), "202608", "O5", "issue")
        row = self.rows()[0]
        self.assertEqual((row["division"], row["dept"], row["section"]), ("208", "02", "02"))
        self.assertEqual(row["department"], "2", "รหัสกลุ่มของกระทรวงต้องยังอยู่ ไม่ถูกทับ")

    def test_pulling_everything_classifies_each_item_by_its_own_category(self):
        """ดึงทีเดียวทุกหมวด ผลลัพธ์จึงมีทั้งยาและพัสดุปนกัน กลุ่มต้องมาจากรายการ ไม่ใช่จากขอบเขต"""
        connection = FakeConnection([issue_row(main_category="11"),
                                     issue_row(code="6010010", main_category="6")])
        extractor.pull_period(connection, "202608", "O5", "issue", categories.ALL)
        groups = {row["stock_code"]: row["item_group"] for row in self.rows("items")}
        self.assertEqual(groups["1321080"], categories.DRUG)
        self.assertEqual(groups["6010010"], categories.MATERIAL)

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
        warehouse_db.replace_period("202608", "2", "issue", [], query_sha256=current,
                                    units_sha256=current_units())
        work = extractor.plan(self._dt.date(2026, 9, 11), ["2"], ["issue"])
        self.assertNotIn(("202608", "2", "issue"), work)

    def test_a_period_pulled_for_every_category_is_not_planned_again(self):
        """วางแผนกับดึงต้องใช้ขอบเขตหมวดเดียวกัน ไม่งั้นทุกงวดจะถูกวางแผนซ้ำไม่รู้จบ

        ขั้นตอน "คำสั่งดึงเปลี่ยน" เทียบลายนิ้วมือคำสั่ง ถ้าวางแผนด้วยหมวดยาแต่ดึงด้วยทุกหมวด
        ลายนิ้วมือจะไม่มีวันตรงกัน และการดึงจะไม่จบสักที
        """
        import reporting_window
        date_from, date_to = reporting_window.period_bounds("202608")
        pulled = queries.fingerprint(
            queries.build("DISTRIBUTION", "2", date_from, date_to, categories.ALL))
        warehouse_db.replace_period("202608", "2", "issue", [], query_sha256=pulled,
                                    units_sha256=current_units())
        work = extractor.plan(self._dt.date(2026, 9, 11), ["2"], ["issue"],
                              group_key=categories.ALL)
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
        warehouse_db.replace_period("202607", "O5", "issue", [], query_sha256=current,
                                    units_sha256=current_units())
        work = extractor.plan(self._dt.date(2026, 9, 11), ["O5"], ["issue"])
        self.assertNotIn(("202607", "O5", "issue"), work)


# ---------------------------------------------------------------------------
# สถานะสอบทานต้องตามตารางหน่วยของ Stock5 ให้ทัน
#
# สถานะ VERIFIED/PENDING คำนวณตอนดึงแล้วเก็บไว้ ถ้าผู้ใช้เติม baseunit_0369.xlsx
# ภายหลัง Stock5 จะใช้กติกาใหม่ แต่ที่นี่ยังเป็นของเดิม — คลัง 2 ของสองระบบจะไม่ตรงกัน
# ---------------------------------------------------------------------------

class UnitRuleFreshnessTests(unittest.TestCase):
    import datetime as _dt
    TODAY = _dt.date(2026, 9, 11)
    PERIOD = ("202608", "O5", "issue")

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

        self.rules = {"1321080": "กติกาเดิม", "9999999": "กติกาของยาอื่น"}
        self.engine = "เครื่องสอบทานรุ่นเดิม"
        for name, value in (("rules", lambda: dict(self.rules)),
                            ("engine_digest", lambda: self.engine)):
            patcher = patch.object(unit_rules, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pull()

    def pull(self):
        result = extractor.pull_period(connection_for(lot_level_rows()), *self.PERIOD)
        self.assertEqual(result["status"], "success")

    def planned(self):
        return extractor.plan(self.TODAY, ["O5"], ["issue"], include_current=False)

    def make_legacy(self):
        """จำลองงวดที่ดึงก่อนมีลายนิ้วมือกติกาหน่วย"""
        with warehouse_db.connect() as conn:
            conn.execute("UPDATE periods SET units_sha256 = ''")
            conn.commit()

    def test_a_pulled_issue_period_records_the_unit_rules_it_was_checked_with(self):
        stored = warehouse_db.period_status(*self.PERIOD)["units_sha256"]
        self.assertEqual(stored, unit_rules.period_digest(["1321080"], self.rules, self.engine))

    def test_unchanged_rules_leave_the_period_alone(self):
        self.assertNotIn(self.PERIOD, self.planned())

    def test_filling_in_units_for_an_item_in_the_period_replans_it(self):
        self.rules["1321080"] = "เติมหน่วยแล้ว"
        self.assertIn(self.PERIOD, self.planned())

    def test_a_first_rule_for_an_item_in_the_period_replans_it(self):
        del self.rules["1321080"]
        self.pull()
        self.rules["1321080"] = "เพิ่งเติม"
        self.assertIn(self.PERIOD, self.planned())

    def test_rules_for_items_outside_the_period_do_not_force_a_repull(self):
        # เติมหน่วยยาหนึ่งตัว ต้องไม่ทำให้ดึงใหม่ทั้ง 888 งวด
        self.rules["9999999"] = "เปลี่ยนกติกา"
        self.rules["5555555"] = "รหัสใหม่ในตาราง"
        self.assertNotIn(self.PERIOD, self.planned())

    def test_a_change_to_the_checking_engine_replans_the_period(self):
        self.engine = "เครื่องสอบทานรุ่นใหม่"
        self.assertIn(self.PERIOD, self.planned())

    def test_the_plan_says_why_a_period_is_pulled_again(self):
        self.rules["1321080"] = "เติมหน่วยแล้ว"
        reasons = {item[:3]: item[3] for item in
                   extractor.plan_detail(self.TODAY, ["O5"], ["issue"], include_current=False)}
        self.assertEqual(reasons[self.PERIOD], extractor.REASON_UNITS)

    def test_a_repull_brings_the_period_up_to_date(self):
        self.rules["1321080"] = "เติมหน่วยแล้ว"
        self.pull()
        self.assertNotIn(self.PERIOD, self.planned())

    def test_periods_without_a_recorded_rule_set_are_pulled_again(self):
        # ไม่เดาจากเวลาแก้ไฟล์: git checkout เปลี่ยนเวลาได้ทั้งที่เนื้อหาเดิม
        self.make_legacy()
        reasons = {item[:3]: item[3] for item in
                   extractor.plan_detail(self.TODAY, ["O5"], ["issue"], include_current=False)}
        self.assertEqual(reasons[self.PERIOD], extractor.REASON_UNITS_UNKNOWN)

    def test_adoption_fills_only_periods_without_a_recorded_rule_set(self):
        recorded = warehouse_db.period_status(*self.PERIOD)["units_sha256"]
        warehouse_db.adopt_unit_digests({self.PERIOD[:2]: "ทับ"})
        self.assertEqual(warehouse_db.period_status(*self.PERIOD)["units_sha256"], recorded)

        self.make_legacy()
        warehouse_db.adopt_unit_digests({self.PERIOD[:2]: recorded})
        self.assertNotIn(self.PERIOD, self.planned())

    def test_an_unreadable_unit_table_stops_planning_before_reading_the_hospital_database(self):
        with patch.object(unit_rules, "rules", side_effect=ValueError("ตารางหน่วยอ่านไม่ได้")):
            with self.assertRaises(ValueError):
                self.planned()
            # ใบรับไม่ใช้ตารางหน่วย จึงยังวางแผนได้
            extractor.plan(self.TODAY, ["O5"], ["receipt"])


# ---------------------------------------------------------------------------
# คงคลัง: ภาพ ณ วันที่ดึง วันละหนึ่งภาพต่อคลัง
# ---------------------------------------------------------------------------

BALANCE_COLUMNS = ("HOSP_CODE", "WORKING_CODE", "ENGLISHNAME", "TRADE_NAME", "QTY_ONHAND",
                   "SOURCE_STOCK_VALUE", "SOURCE_STORE", "SOURCE_SCOPE_VERSION", "SOURCE_LOTNO",
                   "LOTNO", "BASE_UNIT", "EXPIRE_DATE", "SOURCE_DATE_LAST_IN")


def balance_row(code="1321080", lot="L1", qty=500.0, value=711.55):
    return ("EA0010672", code, "AATORVASTATIN TAB 40 mg", "LLipitor", qty, value, "O5",
            "SSBSTOCK_STORE_V1", lot, lot, "TAB", "20271231", "20260801")


class SnapshotTests(unittest.TestCase):
    import datetime as _dt
    TODAY = _dt.date(2026, 9, 11)
    DAY = "20260911"

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

    def snap(self, rows, day=DAY, store="O5", **kwargs):
        connection = FakeConnection(rows, BALANCE_COLUMNS)
        return extractor.pull_period(connection, day, store, "balance", **kwargs)

    def balances(self):
        with warehouse_db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM balances ORDER BY period, lot_no")]

    def test_every_run_ends_with_a_snapshot_of_every_store(self):
        work = extractor.plan(self.TODAY, ["2", "O5"])
        self.assertEqual(work[-2:], [(self.DAY, "2", "balance"), (self.DAY, "O5", "balance")])
        self.assertEqual(len(work), 24 * 2 * 2 + 2)

    def test_a_snapshot_only_plan_reads_nothing_else(self):
        self.assertEqual(extractor.plan(self.TODAY, ["O5"], ["balance"]),
                         [(self.DAY, "O5", "balance")])

    def test_a_snapshot_is_stored_under_its_day(self):
        result = self.snap([balance_row(), balance_row(lot="L2", qty=20.0, value=28.46)])
        self.assertEqual(result["status"], "success")
        rows = self.balances()
        self.assertEqual([(r["period"], r["lot_no"], r["qty"]) for r in rows],
                         [(self.DAY, "L1", 500.0), (self.DAY, "L2", 20.0)])
        self.assertEqual(warehouse_db.latest_snapshots(), {"O5": self.DAY})

    def test_pulling_again_the_same_day_replaces_that_days_snapshot(self):
        self.snap([balance_row(), balance_row(lot="L2")])
        self.snap([balance_row(qty=480.0)])
        self.assertEqual([(r["lot_no"], r["qty"]) for r in self.balances()], [("L1", 480.0)])

    def test_earlier_days_are_kept_for_trends(self):
        self.snap([balance_row(qty=600.0)], day="20260910")
        self.snap([balance_row(qty=500.0)])
        self.assertEqual([(r["period"], r["qty"]) for r in self.balances()],
                         [("20260910", 600.0), (self.DAY, 500.0)])
        self.assertEqual(warehouse_db.latest_snapshots(), {"O5": self.DAY})

    # --- หลักเดียวกับ Stock5: สำเนาซ้ำตัดทิ้งได้ แต่คีย์เดียวกันตัวเลขต่างต้องหยุด
    def test_identical_join_copies_are_collapsed(self):
        result = self.snap([balance_row(), balance_row()])
        self.assertEqual(result["stored_rows"], 1)

    def test_one_lot_with_two_different_quantities_stops_the_snapshot(self):
        result = self.snap([balance_row(qty=500.0), balance_row(qty=5000.0)])
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.balances(), [])
        self.assertEqual(warehouse_db.period_status(self.DAY, "O5", "balance")["status"], "failed")

    def test_the_database_refuses_to_pick_between_conflicting_rows(self):
        # ด่านที่สอง ถ้าแถวซ้ำหลุดมาถึงฐานข้อมูล INSERT OR REPLACE ต้องไม่เลือกให้เงียบ ๆ
        rows = [{"stock_code": "1321080", "lot_no": "L1", "qty": 1.0},
                {"stock_code": "1321080", "lot_no": "L1", "qty": 2.0}]
        with self.assertRaises(ValueError):
            warehouse_db.replace_period(self.DAY, "O5", "balance", rows)

    def test_a_storage_failure_is_recorded_and_does_not_stop_the_run(self):
        with patch.object(warehouse_db, "replace_period", side_effect=OSError("disk full")):
            result = self.snap([balance_row()])
        self.assertEqual(result["status"], "error")
        self.assertEqual(warehouse_db.period_status(self.DAY, "O5", "balance")["status"], "failed")

    # --- ทะเบียนรายการ
    def test_a_snapshot_does_not_blank_what_the_item_register_already_knows(self):
        warehouse_db.upsert_items([{"stock_code": "1321080", "name": "ATORVASTATIN TAB 40 mg",
                                    "main_category": "11", "item_group": "drug", "base_unit": "TAB"}])
        self.snap([balance_row()])  # คำสั่งคงคลังไม่มีคอลัมน์หมวด
        with warehouse_db.connect() as conn:
            item = dict(conn.execute("SELECT * FROM items").fetchone())
        self.assertEqual(item["main_category"], "11")

    def test_items_take_their_group_from_the_pull_not_a_constant(self):
        self.snap([balance_row()], group_key=categories.MEDICAL_SUPPLY)
        with warehouse_db.connect() as conn:
            group = conn.execute("SELECT item_group FROM items").fetchone()[0]
        self.assertEqual(group, categories.MEDICAL_SUPPLY)


# ---------------------------------------------------------------------------
# เอกสารบันทึกย้อนวัน: เดือนที่ผ่านไปแล้วยังเปลี่ยนได้
#
# 12 ก.ย. 2569 พบใบโอน 69D09127-69D09131 ลงวันที่ 10 ก.ย. 15:11 (1,551,124 หน่วย
# ฿1,923,985) ซึ่งยังไม่อยู่ในฐานข้อมูลตอนบ่ายวันที่ 11 ถ้าใบแบบนี้ลงวันที่สิ้นเดือน
# แต่บันทึกต้นเดือนถัดไป การดึงซ้ำเฉพาะเดือนปัจจุบันจะไม่มีวันเห็น
# ---------------------------------------------------------------------------

class LatePostingTests(unittest.TestCase):
    import datetime as _dt

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
        for period in ("202607", "202608", "202609", "202610"):
            self.hold(period)

    def hold(self, period, store="O5", kind="issue"):
        """งวดที่ดึงครบแล้วด้วยคำสั่งและกติกาหน่วยปัจจุบัน"""
        import reporting_window

        date_from, date_to = reporting_window.period_bounds(period)
        current = queries.fingerprint(queries.build("DISTRIBUTION", store, date_from, date_to))
        warehouse_db.replace_period(period, store, kind, [], query_sha256=current,
                                    units_sha256=current_units())

    def planned(self, day, **kwargs):
        return extractor.plan(day, ["O5"], ["issue"], **kwargs)

    def test_early_in_the_month_the_previous_month_is_pulled_again(self):
        work = self.planned(self._dt.date(2026, 10, 3))
        self.assertIn(("202609", "O5", "issue"), work)
        self.assertIn(("202610", "O5", "issue"), work)

    def test_the_plan_says_it_is_about_late_postings(self):
        reasons = {item[:3]: item[3] for item in
                   extractor.plan_detail(self._dt.date(2026, 10, 3), ["O5"], ["issue"])}
        self.assertEqual(reasons[("202609", "O5", "issue")], extractor.REASON_LATE_POSTING)

    def test_later_in_the_month_the_previous_month_is_left_alone(self):
        work = self.planned(self._dt.date(2026, 10, 20))
        self.assertNotIn(("202609", "O5", "issue"), work)

    def test_a_deeper_recheck_can_be_asked_for(self):
        # งานประจำสัปดาห์: กันใบที่ลงวันที่เก่ากว่าหนึ่งเดือน
        work = self.planned(self._dt.date(2026, 10, 20), recheck_months=3)
        for period in ("202609", "202608", "202607"):
            with self.subTest(period=period):
                self.assertIn((period, "O5", "issue"), work)

    def test_the_window_start_is_never_crossed(self):
        work = self.planned(self._dt.date(2026, 10, 3), recheck_months=99)
        self.assertTrue(all(period >= "202510" for period, _s, _k in work))


class UnitRuleDigestTests(unittest.TestCase):
    """ลายนิ้วมือต้องเปลี่ยนเมื่อการแปลงหน่วยเปลี่ยนเท่านั้น"""

    ENTRY = {"content_unit": "MG", "qty_per_container": 80.0, "container_unit": "PFS",
             "pack_size": 1.0, "ir_unit": "PFS"}

    def fake_engine(self, special, confirmed=None):
        modules = {"special_units": SimpleNamespace(get_special_units=lambda: special),
                   "confirmed_units": SimpleNamespace(CONFIRMED_UNITS=confirmed or {})}
        return patch.object(unit_rules.stock5_engine, "load", lambda name: modules[name])

    def test_moving_a_row_in_the_unit_table_does_not_change_the_rule(self):
        with self.fake_engine({"2091520": dict(self.ENTRY, source_row=5, clean_name="IXEKIZUMAB")}):
            before = unit_rules.rules()
        with self.fake_engine({"2091520": dict(self.ENTRY, source_row=9, clean_name="IXEKIZUMAB 80")}):
            after = unit_rules.rules()
        self.assertEqual(before, after)

    def test_changing_a_conversion_changes_the_rule(self):
        with self.fake_engine({"2091520": dict(self.ENTRY)}):
            before = unit_rules.rules()
        with self.fake_engine({"2091520": dict(self.ENTRY, qty_per_container=40.0)}):
            after = unit_rules.rules()
        self.assertNotEqual(before["2091520"], after["2091520"])

    def test_confirmed_units_written_with_sets_can_be_fingerprinted(self):
        confirmed = {"2185030": {"label": "ขวด", "issue_sql_units": {"BX30"}}}
        with self.fake_engine({}, confirmed):
            self.assertIn("2185030", unit_rules.rules())

    def test_the_engine_fingerprint_ignores_line_endings(self):
        digests = []
        for ending in (b"\n", b"\r\n"):
            with tempfile.TemporaryDirectory() as home:
                app_dir = Path(home) / "app"
                app_dir.mkdir()
                for name in unit_rules.ENGINE_FILES:
                    (app_dir / name).write_bytes(b"line one" + ending + b"line two" + ending)
                unit_rules.engine_digest.cache_clear()
                with patch.object(unit_rules.stock5_engine, "stock5_home", lambda: Path(home)):
                    digests.append(unit_rules.engine_digest())
        unit_rules.engine_digest.cache_clear()
        self.assertEqual(digests[0], digests[1])

    def test_the_real_stock5_unit_table_can_be_fingerprinted(self):
        if not stock5_engine.engine_available():
            self.skipTest("ยังไม่ได้ติดตั้ง Stock5 ข้างโปรเจกต์นี้")
        found = unit_rules.rules()
        self.assertTrue(found)
        self.assertTrue(all(isinstance(value, str) and len(value) == 64 for value in found.values()))


if __name__ == "__main__":
    unittest.main()
