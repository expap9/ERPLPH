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

CASE = {"numbers": ["02-0000-69"], "codes": ["1000000", "3000000"], "dates": ["2026-09-07"]}


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
        self.assertEqual(self.queries["requisition_by_content"]["rows"][0]["CONTRASTORE"], "I2")

    def test_a_missing_table_does_not_stop_the_other_checks(self):
        self.assertEqual(self.queries["stock_rows_by_store"]["status"], "error")
        self.assertEqual(self.queries["substore_movement_12m"]["status"], "complete")
        self.assertTrue(self.report["complete"])

    def test_free_text_that_may_name_a_patient_is_never_stored(self):
        text = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn("ผู้ป่วยสมมุติ", text)
        self.assertNotIn("999999", text)

    def test_case_comes_from_ignored_diagnostics_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "substore_requisitions_20260916.json").write_text(json.dumps({
                "documents": [
                    {"requisition_no": "02-1111-69", "date": "2026-09-07",
                     "lines": [{"code": "1221890", "qty": 200}, {"code": "1229850", "qty": 20}]},
                    {"requisition_no": "02-1112-69", "date": "2026-09-07",
                     "lines": [{"code": "3230000", "qty": 100}]},
                ]}, ensure_ascii=False), encoding="utf-8")
            case = probe.load_case(folder)
        self.assertEqual(case["numbers"], ["02-1111-69", "02-1112-69"])
        self.assertEqual(case["codes"], ["1221890", "1229850", "3230000"])
        self.assertEqual(case["dates"], ["2026-09-07"])

    def test_no_case_and_no_numbers_asks_for_input_instead_of_querying(self):
        with tempfile.TemporaryDirectory() as tmp:
            queries = probe.build_queries({"numbers": [], "codes": [], "dates": []})
            names = [q[0] for q in queries]
        self.assertNotIn("requisition_header_by_number", names)
        self.assertNotIn("requisition_by_content", names)
        self.assertIn("skir_columns", names)


if __name__ == "__main__":
    unittest.main()
