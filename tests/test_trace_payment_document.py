"""ตัวตามเลขบิล: อ่านอย่างเดียว ไม่เก็บข้อความที่อาจมีข้อมูลผู้ป่วย และบอกได้ว่าค่าบนเอกสารอยู่ช่องไหน"""
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

_spec = importlib.util.spec_from_file_location(
    "trace_payment_document", ROOT / "scripts" / "trace_payment_document.py")
trace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trace)

_WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|EXEC|TRUNCATE|GRANT)\b", re.I)

# ข้อมูลสมมุติ ไม่ใช่เคสจริง
RECEIPT_HEADER = (
    ("RECEIVENO", "R0000-0001"), ("STORE", "5"), ("SUPPLIERINVOICENO", "IV-0000001"),
    ("SUPPLIERINVOICEDATE", datetime(2026, 6, 12)), ("REMARKSMEMO", "ผู้ป่วยสมมุติ HN 999999 ใบตรวจรับ R9999-0001"),
)
RECEIPT_LINE = (
    ("RECEIVENO", "R0000-0001"), ("SUFFIX", 1), ("STOCKCODE", "40000001"), ("RECEIVEQTY", 1.0),
    ("RECEIVEAMT", 43000.0), ("FROMPONO", "P000000-001"),
)


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.description = [("X",)]
        self._rows = []

    def execute(self, sql, params=None):
        self.owner.executed.append((sql, params))
        if "SKPODTL pod" in sql and "rd.FROMPONO" not in sql:
            raise RuntimeError("42S22")
        if "FROM dbo.SKRECVDTL rd" in sql and "JOIN dbo.SKRECV rh" in sql and "SUPPLIERINVOICENO), ' '" in sql:
            rows = [RECEIPT_LINE]
        elif "FROM dbo.SKRECV rh" in sql:
            rows = [RECEIPT_HEADER]
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


CASE = {"fields": [
    {"document": "ใบกำกับภาษี", "label": "เลขบิล", "type": "text", "value": "IV-0000001"},
    {"document": "ใบกำกับภาษี", "label": "ราคาต่อหน่วย", "type": "amount", "value": 43000},
    {"document": "ใบกำกับภาษี", "label": "วันที่บิล", "type": "date", "value": "2026-06-12"},
    {"document": "ใบตรวจรับ", "label": "เลขใบตรวจรับ", "type": "text", "value": "R9999-0001"},
    {"document": "บันทึกขออนุมัติจ่าย", "label": "รหัสอนุมัติ", "type": "text", "value": "BG00000000"},
]}


class TracePaymentDocumentTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.report = trace.collect_trace(lambda: self.connection, "IV 0000001", case=CASE, pacing=0)

    def test_every_statement_only_reads_and_searched_values_travel_as_parameters(self):
        self.assertTrue(self.connection.executed)
        for sql, params in self.connection.executed:
            with self.subTest(sql=sql.strip()[:50]):
                self.assertTrue(sql.strip().upper().startswith("SELECT"))
                self.assertIsNone(_WRITES.search(sql))
                self.assertNotIn("0000001", sql)
                self.assertNotIn("P000000-001", sql)

    def test_invoice_is_matched_without_spaces_or_hyphens(self):
        invoice_params = [params for sql, params in self.connection.executed if "SUPPLIERINVOICENO), ' '" in sql]
        self.assertTrue(invoice_params)
        self.assertTrue(all(params == ["IV0000001"] for params in invoice_params))

    def test_free_text_that_may_name_a_patient_is_never_stored(self):
        text = str(self.report)
        self.assertNotIn("ผู้ป่วยสมมุติ", text)
        self.assertNotIn("999999", text)
        header = next(q for q in self.report["queries"] if q["name"] == "SKRECV")["rows"][0]
        self.assertEqual(header["REMARKSMEMO"]["withheld"], True)
        self.assertIn("R9999-0001", header["REMARKSMEMO"]["contains"])

    def test_the_purchase_order_on_the_receipt_is_followed(self):
        self.assertEqual(self.report["po_numbers"], ["P000000-001"])
        names = [q["name"] for q in self.report["queries"]]
        self.assertIn("SKPO", names)
        self.assertIn("receipts_for_po", names)

    def test_each_document_value_says_where_it_was_found(self):
        found = {field["label"]: field["found_in"] for field in self.report["located"]}
        self.assertEqual(found["เลขบิล"], ["SKRECV.SUPPLIERINVOICENO"])
        self.assertIn("SKRECVDTL.RECEIVEAMT", found["ราคาต่อหน่วย"])
        self.assertEqual(found["วันที่บิล"], ["SKRECV.SUPPLIERINVOICEDATE"])
        self.assertEqual(found["เลขใบตรวจรับ"], ["SKRECV.REMARKSMEMO"])
        self.assertEqual(found["รหัสอนุมัติ"], [])

    def test_one_failing_query_does_not_stop_the_trace(self):
        statuses = {q["name"]: q["status"] for q in self.report["queries"]}
        self.assertEqual(statuses["SKPODTL"], "error")
        self.assertTrue(self.report["complete"])


if __name__ == "__main__":
    unittest.main()
