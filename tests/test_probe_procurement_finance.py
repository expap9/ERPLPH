"""ตัวสำรวจที่อยู่ข้อมูลการเงิน: อ่านอย่างเดียว และไม่ยอมให้ชื่อฐานข้อมูลกลายเป็นคำสั่ง"""
import importlib.util
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

_spec = importlib.util.spec_from_file_location(
    "probe_procurement_finance", ROOT / "scripts" / "probe_procurement_finance.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

_WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|EXEC|TRUNCATE|GRANT)\b", re.I)


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.description = [("X",)]
        self._rows = []

    def execute(self, sql):
        self.owner.executed.append(sql)
        if "sys.databases" in sql:
            self.description = [("DATABASE_NAME",), ("STATE",), ("CAN_READ",)]
            self._rows = [("SSBSTOCK", "ONLINE", 1), ("SSBBACKOFFICE", "ONLINE", 1),
                          ("HRPAYROLL", "ONLINE", 0), ("bad];DROP TABLE x--", "ONLINE", 1)]
        elif "SKPO WITH" in sql and "POSTATUS" in sql:
            raise RuntimeError("42S22")
        else:
            self.description = [("X",)]
            self._rows = []

    def fetchmany(self, _limit):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


class ProbeTests(unittest.TestCase):
    def test_every_query_only_reads(self):
        statements = [sql for _name, sql, _limit in probe.base_queries()]
        statements += [sql for _name, sql, _limit in probe.database_queries("SSBBACKOFFICE")]
        for sql in statements:
            with self.subTest(sql=sql.strip()[:40]):
                self.assertTrue(sql.strip().upper().startswith("SELECT"))
                self.assertIsNone(_WRITES.search(sql))

    def test_a_database_name_cannot_carry_a_command(self):
        for name in ("bad];DROP TABLE x--", "SSB STOCK", "1SSB", "", None):
            with self.subTest(name=name):
                self.assertIsNone(probe.safe_database_name(name))
        with self.assertRaises(ValueError):
            probe.database_queries("x]; DELETE FROM y")

    def test_only_readable_databases_with_safe_names_are_surveyed(self):
        connection = FakeConnection()
        report = probe.collect_report(lambda: connection, pacing=0)
        surveyed = {q["name"].split(":", 1)[1] for q in report["queries"] if ":" in q["name"]}
        self.assertEqual(surveyed, {"SSBSTOCK", "SSBBACKOFFICE"})
        self.assertFalse(any("DROP" in sql for sql in connection.executed))

    def test_one_failing_query_does_not_stop_the_survey(self):
        report = probe.collect_report(lambda: FakeConnection(), pacing=0)
        statuses = {q["name"]: q["status"] for q in report["queries"]}
        self.assertEqual(statuses["po_status"], "error")
        self.assertTrue(report["complete"])
        self.assertIn("tables:SSBBACKOFFICE", statuses)


if __name__ == "__main__":
    unittest.main()
