"""ตัวตรวจบัญชีเจ้าหนี้: อ่านอย่างเดียว ค่าที่ค้นเป็นพารามิเตอร์ ไม่เก็บข้อความอิสระ และตอบว่าบิลจ่ายแล้วหรือยัง"""
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

_spec = importlib.util.spec_from_file_location("trace_ap_payment", ROOT / "scripts" / "trace_ap_payment.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)

_WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|EXEC|TRUNCATE|GRANT)\b", re.I)

# ข้อมูลสมมุติ ไม่ใช่เคสจริง
INVOICE_ROW = (("APCODE", "A0001 "), ("INVOICENO", "INV-1"), ("SUPPLIERINVOICENO", "IV-0000001"),
               ("RECEIVENO", "R0000-0001"), ("PONO", "P000000-001"),
               ("INVOICEDATETIME", datetime(2026, 6, 20)), ("REMARK", "ผู้ป่วยสมมุติ HN 999999"))
OTHER_BILL_SAME_PO = (("APCODE", "A0001"), ("INVOICENO", "INV-2"), ("SUPPLIERINVOICENO", "IV-0000999"),
                      ("RECEIVENO", "R0000-0999"), ("PONO", "P000000-001"),
                      ("INVOICEDATETIME", datetime(2026, 7, 1)), ("REMARK", ""))
PAYMENT_ROW = (("APCODE", "A0001"), ("INVOICENO", "INV-1"), ("CHEQUENO", "CQ0001"),
               ("PAYMENTDATETIME", datetime(2026, 8, 5)), ("PAYMENTBATCHNO", "BG00000000"))
CHEQUE_ROW = (("CHEQUENO", "CQ0001"), ("CHEQUEAMT", 85196.26))
FRESHNESS_ROW = (("BUDGETYEAR", 2569), ("INVOICES", 10), ("LAST_INVOICE", datetime(2026, 9, 14)))


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.description = [("X",)]
        self._rows = []

    def execute(self, sql, params=None):
        self.owner.executed.append((sql, params))
        if "SSBGL48." in sql:
            raise RuntimeError("42S02")
        if "GROUP BY BUDGETYEAR" in sql:
            rows = [FRESHNESS_ROW]
        elif "SSBBACKOFFICE.dbo.APINVPAY" in sql:
            rows = [PAYMENT_ROW]
        elif "SSBBACKOFFICE.dbo.APINV " in sql:
            rows = [INVOICE_ROW, OTHER_BILL_SAME_PO]
        elif "SSBBACKOFFICE.dbo.APCHQ" in sql:
            rows = [CHEQUE_ROW]
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


CASES = [
    dict(source="payment_case_IV-0000001.json", invoice="IV 0000001", receive_numbers=[],
         po_numbers=["P000000-001"], fields=[
             {"document": "บันทึกขออนุมัติจ่าย", "label": "รหัส BG", "type": "text", "value": "BG00000000"},
             {"document": "บันทึกขออนุมัติจ่าย", "label": "เลขที่บันทึก", "type": "text", "value": "MEMO-77777"},
         ]),
    dict(source="payment_batch_x.json", invoice="NOT-IN-AP", receive_numbers=["R0000-0404"],
         po_numbers=["P000000-404"], fields=[]),
    dict(source="payment_batch_x.json", invoice="", receive_numbers=[], po_numbers=["P000000-001"], fields=[]),
]


class TraceApPaymentTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.report = ap.collect_report(lambda: self.connection, CASES, pacing=0)
        self.cases = self.report["cases"]

    def test_every_statement_only_reads_and_document_numbers_travel_as_parameters(self):
        self.assertTrue(self.connection.executed)
        for sql, _params in self.connection.executed:
            with self.subTest(sql=sql.strip()[:60]):
                self.assertTrue(sql.strip().upper().startswith("SELECT"))
                self.assertIsNone(_WRITES.search(sql))
                for value in ("0000001", "P000000", "R0000", "INV-1", "CQ0001", "A0001"):
                    self.assertNotIn(value, sql)

    def test_paid_invoice_is_answered_with_its_payment_date(self):
        self.assertEqual(self.cases[0]["answer"], "พบรายการจ่ายเงิน")
        payment = self.cases[0]["by_database"]["SSBBACKOFFICE"]["payments"][0]
        self.assertTrue(payment["PAYMENTDATETIME"].startswith("2026-08-05"))

    def test_unknown_invoice_is_reported_as_not_found(self):
        self.assertEqual(self.cases[1]["answer"], "ไม่พบในบัญชีเจ้าหนี้")

    def test_purchase_order_only_match_is_not_claimed_as_this_bill(self):
        self.assertEqual(self.cases[2]["answer"], "พบเฉพาะจากเลขใบสั่งซื้อ (อาจเป็นบิลอื่นของใบสั่งซื้อเดียวกัน)")
        self.assertEqual(self.cases[2]["by_database"]["SSBBACKOFFICE"]["payments"], [])

    def test_payment_memo_values_say_which_column_holds_them(self):
        found = {field["label"]: field["found_in"] for field in self.report["located"]}
        self.assertEqual(found["รหัส BG"], ["SSBBACKOFFICE.APINVPAY.PAYMENTBATCHNO"])
        self.assertEqual(found["เลขที่บันทึก"], [])

    def test_free_text_that_may_name_a_patient_is_never_stored(self):
        text = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn("ผู้ป่วยสมมุติ", text)
        self.assertNotIn("999999", text)

    def test_one_database_failing_does_not_stop_the_others(self):
        statuses = {q["name"]: q["status"] for q in self.report["queries"]}
        self.assertEqual(statuses["SSBGL48.APINV:freshness"], "error")
        self.assertEqual(statuses["SSBBACKOFFICE.APINVPAY"], "complete")
        self.assertTrue(self.report["complete"])
        self.assertTrue(all("_keys" not in q for q in self.report["queries"]))

    def test_known_cases_come_from_ignored_diagnostics_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "payment_case_IV-0000001.json").write_text(json.dumps(
                {"po_numbers": ["P000000-001"], "fields": [{"label": "x", "value": "y"}]}), encoding="utf-8")
            (folder / "payment_batch_20260101.json").write_text(json.dumps(
                {"lines": [{"receive_no": "R0000-0001", "po_no": "P000000-001", "invoice_no": "123"}]}),
                encoding="utf-8")
            cases = ap.load_known_cases(folder)
        self.assertEqual([c["invoice"] for c in cases], ["IV-0000001", "123"])
        self.assertEqual(cases[1]["receive_numbers"], ["R0000-0001"])
        self.assertEqual(cases[0]["fields"][0]["value"], "y")


if __name__ == "__main__":
    unittest.main()
