"""ตัวสำรวจใบเบิกคลังย่อย: อ่านอย่างเดียว ค่าที่ค้นเป็นพารามิเตอร์ และตารางที่หายไปไม่ล้มทั้งชุด"""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "probe_substore_requisition", ROOT / "scripts" / "probe_substore_requisition.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

_WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|EXEC|TRUNCATE|GRANT)\b", re.I)

# ข้อมูลสมมุติ ไม่ใช่ใบเบิกจริง
HEADER = (("IRNO", "02-0000-69"), ("DOCUMENTTYPE", 35), ("STORE", "2"), ("CONTRASTORE", "I2"),
          ("DIVISION", "208"), ("DEPT", "02"), ("SECTION", "02"),
          ("REMARKSMEMO", "ผู้ป่วยสมมุติ HN 999999 ใบเบิกทดสอบ"))
LINE = (("IRNO", "02-0000-69"), ("SUFFIX", 1), ("STOCKCODE", "1000000"), ("ISSUEQTY", 200.0))
CONTENT = (("IRNO", "02-0000-69"), ("SUFFIX", 1), ("DOCUMENTTYPE", 35), ("STOCKCODE", "1000000"),
           ("ISSUEQTY", 200.0), ("STORE", "2"), ("CONTRASTORE", "I2"))
COLUMN = (("TABLE_NAME", "SKIROUT"), ("COLUMN_NAME", "ISSUEQTY"), ("DATA_TYPE", "float"))
MOVEMENT = (("STORE", "2"), ("CONTRASTORE", "I2"), ("DOCUMENTTYPE", 35), ("SLIPS", 7254),
            ("LAST_SLIP", datetime(2026, 9, 15)))
CUT_DAY = (("STORE", "I2"), ("CUT_DAY", datetime(2026, 9, 7)), ("SLIPS", 2),
           ("SAMPLE_IRNO", "20260907-I2-I/S1"))
SALES_DOC = (("IRNO", "20260907-I2-I/S1"), ("STORE", "I2"),
             ("UPDATESTOCKDATETIME", datetime(2026, 9, 7, 0, 50)),
             ("APPROVEDATETIME", datetime(2026, 9, 9, 13, 53)),
             ("REMARKSMEMO", "ผู้ป่วยสมมุติ HN 999999"))

MOVE_BY_DOC = (("DOCUMENTNO", "20260908-I2-I/S1"), ("DOCUMENTTYPE", 32), ("ADDSTOCK", 0),
               ("NATUREISOUT", 1), ("STOCKACTCODE", "S01"), ("LINES", 120), ("ITEMS", 90),
               ("QTY", 3400.0))
MOVE_TWICE = (("STOCKCODE", "1000000"), ("DOCS", 3), ("QTY_IN", 10.0), ("QTY_OUT", 25.0),
              ("FIRST_DOC", "20260908-I2-I/S1"), ("LAST_DOC", "WG69-2680"))

# ชื่อหน่วยงานเป็นข้อมูลอ้างอิงขององค์กร ต้องอ่านออกได้ ไม่ใช่ข้อมูลของคน
DEPARTMENT = (("CTRLCODE", 10028), ("CODE", "208"), ("THAINAME", "กลุ่มงานเภสัชกรรม"))
DEPARTMENT_SLIPS = (("DIVISION", "208"), ("DEPT", "02"), ("SECTION", "02"), ("DOCUMENTTYPE", 32),
                    ("SLIPS", 10035), ("STORES", 1), ("LAST_SLIP", datetime(2026, 9, 17)))

CASE = {"numbers": ["02-0000-69"], "codes": ["1000000", "3000000"], "dates": ["2026-09-07"],
        "codes_by_date": {"2026-09-07": ["1000000", "3000000"]},
        "reconcile_store": "I2", "reconcile_day": "2026-09-08"}


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.description = [("X",)]
        self._rows = []

    def execute(self, sql, params=None):
        self.owner.executed.append((sql, params))
        if "dbo.STOCK_LOT" in sql:
            raise RuntimeError("42S02")
        if "INFORMATION_SCHEMA" in sql:
            rows = [COLUMN]
        elif "SYSCONFIG" in sql:
            rows = [DEPARTMENT]
        elif "GROUP BY ir.DIVISION" in sql:
            rows = [DEPARTMENT_SLIPS]
        elif "HAVING COUNT(DISTINCT mv.DOCUMENTNO)" in sql:
            rows = [MOVE_TWICE]
        elif "GROUP BY mv.DOCUMENTNO" in sql:
            rows = [MOVE_BY_DOC]
        elif "SELECT TOP 200 ir.*" in sql:
            rows = [SALES_DOC]
        elif "SELECT TOP 30 ir.*" in sql:
            rows = [SALES_DOC]
        elif "CAST(ir.UPDATESTOCKDATETIME AS DATE)" in sql:
            rows = [CUT_DAY]
        elif "GROUP BY ir.STORE" in sql:
            rows = [MOVEMENT]
        elif "JOIN dbo.SKIR" in sql:
            rows = [CONTENT]
        elif "FROM dbo.SKIROUT" in sql:
            rows = [LINE]
        elif "FROM dbo.SKIR ir" in sql:
            rows = [HEADER]
        else:
            rows = []
        self.description = [(name,) for name, _ in (rows[0] if rows else (("X", None),))]
        self._rows = [tuple(value for _, value in row) for row in rows]

    def fetchmany(self, _limit):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.executed = []
        self.timeout = None

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


class SubstoreRequisitionProbeTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.report = probe.collect_report(lambda: self.connection, CASE, pacing=0)
        self.queries = {q["name"]: q for q in self.report["queries"]}

    def test_every_statement_only_reads_and_values_travel_as_parameters(self):
        self.assertTrue(self.connection.executed)
        for sql, _params in self.connection.executed:
            with self.subTest(sql=sql.strip()[:60]):
                self.assertTrue(sql.strip().upper().startswith("SELECT"))
                self.assertIsNone(_WRITES.search(sql))
                self.assertNotIn("02-0000-69", sql)
                self.assertNotIn("1000000", sql)
                self.assertNotIn("2026-09-07", sql)

    def test_requisition_number_is_searched_with_and_without_the_dashes(self):
        params = next(p for sql, p in self.connection.executed if "FROM dbo.SKIR ir" in sql)
        self.assertIn("02-0000-69", params)
        self.assertIn("02000069", params)

    def test_slip_is_also_searched_by_date_and_item_codes(self):
        params = next(p for sql, p in self.connection.executed if "JOIN dbo.SKIR" in sql)
        self.assertEqual(params[:2], ["2026-09-07", "2026-09-07"])
        self.assertIn("3000000", params)
        rows = self.queries["requisition_by_content_2026-09-07"]["rows"]
        self.assertEqual(rows[0]["CONTRASTORE"], "I2")

    def test_each_slip_is_searched_on_its_own_date(self):
        """ใบที่ได้มาห่างกันสิบปี ถ้าใช้วันเดียวค้นทุกใบจะหาไม่เจอ"""
        case = {"numbers": [], "codes": ["1000000", "1011220"], "dates": ["2016-12-01", "2026-09-07"],
                "codes_by_date": {"2016-12-01": ["1011220"], "2026-09-07": ["1000000"]}}
        names = [q[0] for q in probe.build_queries(case)]
        self.assertIn("requisition_by_content_2016-12-01", names)
        self.assertIn("requisition_by_content_2026-09-07", names)
        old = next(q for q in probe.build_queries(case) if q[0].endswith("2016-12-01"))
        self.assertEqual(old[2], ["2016-12-01", "2016-12-01", "1011220"])

    def test_daily_cut_coverage_is_collected_for_work_item_ka(self):
        """งาน (ก): ต้องรู้ว่าคลังย่อยไหนตัดขายถึงวันไหนแล้ว และช่องวันที่อนุมัติคือคอลัมน์ไหน"""
        cut = self.queries[f"sales_cut_by_store_day_{probe.SALES_DAYS_BACK}d"]
        self.assertEqual(cut["rows"][0]["SAMPLE_IRNO"], "20260907-I2-I/S1")
        sample = self.queries["sales_documents_sample"]["rows"][0]
        self.assertTrue(sample["APPROVEDATETIME"].startswith("2026-09-09"))
        self.assertTrue(sample["UPDATESTOCKDATETIME"].startswith("2026-09-07"))

    def test_import_documents_are_found_by_number_pattern_not_a_guessed_column(self):
        """ยังไม่รู้ชื่อคอลัมน์รหัสรายการ จึงกรองด้วยรูปแบบเลขเอกสารที่เห็นจากหน้าจอจริง"""
        sqls = [sql for sql, _ in self.connection.executed if "[0-9][0-9][0-9][0-9]" in sql]
        self.assertEqual(len(sqls), 2)
        for sql in sqls:
            self.assertNotIn("ขาย", sql)
            self.assertNotIn("SALES", sql.upper())

    def test_one_day_reconciliation_separates_document_kinds(self):
        """ผู้ใช้ถามว่าเอกสาร 'ขาย' รวมโอน/คืน/จ่าย stock ward ไว้แล้วหรือยัง
        ตอบได้จาก SKMOVE ของวันเดียว: เอกสารชนิดไหนกระทบสต๊อกจริงบ้าง ทิศทางไหน"""
        moves = self.queries["reconcile_movement_by_document"]["rows"]
        self.assertEqual(moves[0]["DOCUMENTNO"], "20260908-I2-I/S1")
        self.assertEqual(moves[0]["ADDSTOCK"], 0)
        params = next(p for sql, p in self.connection.executed if "GROUP BY mv.DOCUMENTNO" in sql)
        self.assertEqual(params, ["I2", "2026-09-08", "2026-09-08"])
        twice = self.queries["reconcile_items_touched_twice"]["rows"]
        self.assertEqual(twice[0]["DOCS"], 3)

    def test_a_missing_table_does_not_stop_the_other_checks(self):
        self.assertEqual(self.queries["stock_rows_by_store"]["status"], "error")
        self.assertEqual(self.queries["substore_movement_12m"]["status"], "complete")
        self.assertTrue(self.report["complete"])

    def test_free_text_that_may_name_a_patient_is_never_stored(self):
        text = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn("ผู้ป่วยสมมุติ", text)
        self.assertNotIn("999999", text)

    def test_reference_names_stay_readable_while_patient_text_stays_hidden(self):
        """กฎกันชื่อคนดูคำว่า NAME ในชื่อช่อง จึงเผลอซ่อน TABLE_NAME กับ THAINAME ไปด้วย

        คำสั่งที่อ่านตารางรหัสต้องประกาศช่องอ้างอิงเอง แล้วชื่อหน่วยงานกับชื่อตารางจึงอ่านออก
        โดยที่ช่องหมายเหตุของใบเบิกยังถูกซ่อนเหมือนเดิม
        """
        department = self.queries["department_codes:SSBHOSPITAL"]["rows"][0]
        self.assertEqual(department["THAINAME"], "กลุ่มงานเภสัชกรรม")
        self.assertEqual(self.queries["skir_columns"]["rows"][0]["TABLE_NAME"], "SKIROUT")

        header = self.queries["requisition_header_by_number"]["rows"][0]
        self.assertEqual(header["REMARKSMEMO"]["withheld"], True)

    def test_requisitions_are_counted_by_the_three_department_levels(self):
        row = self.queries["requisition_by_department_12m"]["rows"][0]
        self.assertEqual((row["DIVISION"], row["DEPT"], row["SECTION"]), ("208", "02", "02"))

    def test_case_comes_from_ignored_diagnostics_files(self):
        """ฟอร์มมีช่องเลขที่สองช่อง แต่ละใบกรอกคนละช่อง ต้องอ่านได้ทั้งสองแบบ"""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "substore_requisitions_20260916.json").write_text(json.dumps({
                "documents": [
                    {"requisition_no": "02-1111-69", "date": "2026-09-07",
                     "lines": [{"code": "1221890", "qty": 200}, {"code": "1229850", "qty": 20}]},
                    {"requisition_no": "02-1112-69", "date": "2026-09-07",
                     "lines": [{"code": "3230000", "qty": 100}]},
                ]}, ensure_ascii=False), encoding="utf-8")
            (folder / "substore_transfers_20260916.json").write_text(json.dumps({
                "documents": [
                    {"supply_requisition_no": "20161201-AN-R/S",
                     "requisition_no_slot": "(ว่าง) — ข้อความอธิบาย ไม่ใช่เลขเอกสาร",
                     "date": "2016-12-01", "lines": [{"code": "1011220", "qty": 325}]},
                ]}, ensure_ascii=False), encoding="utf-8")
            case = probe.load_case(folder)
        self.assertEqual(case["numbers"], ["02-1111-69", "02-1112-69", "20161201-AN-R/S"])
        self.assertEqual(case["codes"], ["1011220", "1221890", "1229850", "3230000"])
        self.assertEqual(case["codes_by_date"]["2016-12-01"], ["1011220"])
        self.assertEqual(case["codes_by_date"]["2026-09-07"], ["1221890", "1229850", "3230000"])

    def test_no_case_and_no_numbers_asks_for_input_instead_of_querying(self):
        names = [q[0] for q in probe.build_queries(
            {"numbers": [], "codes": [], "dates": [], "codes_by_date": {}})]
        self.assertNotIn("requisition_header_by_number", names)
        self.assertFalse([n for n in names if n.startswith("requisition_by_content")])
        self.assertIn("skir_columns", names)


if __name__ == "__main__":
    unittest.main()
