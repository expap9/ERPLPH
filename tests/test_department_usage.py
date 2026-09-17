"""ยอดเบิกรายหน่วยงาน — ตัวเลขที่ผู้บริหารจะเห็น ผิดแล้วไม่มีสัญญาณเตือน

เรื่องที่ต้องไม่พลาด
    1. การโอนระหว่างคลังต้องไม่ถูกนับ ไม่งั้นของชิ้นเดียวถูกนับสองรอบ
    2. ใบคืนต้องถูกหัก เพราะระบบแยกใบขายกับใบคืนเป็นคนละใบ
    3. หน่วยงานที่ไม่มีชื่อในตารางรหัสต้องยังปรากฏ ไม่ใช่หายไปเงียบ ๆ
"""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import categories  # noqa: E402
import department_usage as usage  # noqa: E402
import departments  # noqa: E402

NAMES = {
    "source": "SSBSTOCK.dbo.SYSCONFIG",
    "entries": [
        {"level": "division", "path": ["208", "", ""], "name": "กลุ่มงานเภสัชกรรม"},
        {"level": "division", "path": ["305", "", ""], "name": "ฝ่ายพัสดุ"},
        {"level": "dept", "path": ["208", "02", ""], "name": "งานคลังยา"},
    ],
}

#: (store, irno, division, dept, section, stock_code, qty, value, doctype, direction)
ROWS = [
    ("I2", "A1", "208", "02", "", "1000", 10, 1000.0, "32", "out"),
    ("I2", "A2", "208", "02", "", "1000", 2, 200.0, "32", "in"),       # ใบคืน ต้องถูกหัก
    ("2", "T1", "208", "02", "", "1000", 99, 99000.0, "35", "out"),    # โอน ต้องไม่ถูกนับ
    ("1", "B1", "305", "", "", "6000", 5, 500.0, "32", "out"),
    ("1", "B2", "777", "01", "", "6000", 3, 300.0, "32", "out"),       # ไม่มีชื่อในตารางรหัส
]


def make_warehouse(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE issues (period TEXT, store TEXT, irno TEXT, suffix TEXT, "
                 "movement_key TEXT, stock_code TEXT, qty REAL, value REAL, unit TEXT, "
                 "division TEXT, dept TEXT, section TEXT, document_type TEXT, direction TEXT, "
                 "check_status TEXT)")
    conn.execute("CREATE TABLE items (stock_code TEXT, name TEXT, main_category TEXT, "
                 "item_group TEXT)")
    for store, irno, div, dept, section, code, qty, value, doctype, direction in ROWS:
        # ขาเข้าไม่เคยผ่านการสอบทานของ Stock5 จึงเป็น PENDING เหมือนข้อมูลจริง
        status = "VERIFIED" if direction == "out" else "PENDING"
        conn.execute("INSERT INTO issues VALUES ('202608',?,?,'1','',?,?,?,'TAB',?,?,?,?,?,?)",
                     (store, irno, code, qty, value, div, dept, section, doctype, direction, status))
    # งวดล่าสุดที่ยังไม่จบเดือน — ทำให้ "งวดล่าสุด" เป็น 202609 แต่ต้องไม่ถูกนับรวม
    conn.execute("INSERT INTO issues VALUES ('202609','I2','OPEN','1','','1000',1,5000.0,'TAB',"
                 "'208','02','','32','out','VERIFIED')")
    conn.execute("INSERT INTO items VALUES ('1000', 'PARACETAMOL', '11', ?)",
                 (categories.DRUG,))
    conn.execute("INSERT INTO items VALUES ('6000', 'กระดาษ A4', '6', ?)",
                 (categories.MATERIAL,))
    conn.commit()
    return conn


class DepartmentUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        names = Path(self.temp.name) / "departments.json"
        names.write_text(json.dumps(NAMES, ensure_ascii=False), encoding="utf-8")
        patcher = patch.object(departments, "REGISTRY_PATH", str(names))
        patcher.start()
        self.addCleanup(patcher.stop)
        departments.reload()
        self.addCleanup(departments.reload)
        self.conn = make_warehouse(Path(self.temp.name) / "erplph.db")
        self.addCleanup(self.conn.close)

    def rows(self, **kwargs):
        return {row.path[0]: row for row in usage.breakdown(self.conn, **kwargs)["rows"]}

    def test_a_transfer_between_stores_is_not_counted_as_a_department_taking_goods(self):
        """ของยังอยู่ในโรงพยาบาลและจะถูกเบิกอีกทอดที่ปลายทาง นับที่นี่ด้วยคือนับซ้ำ"""
        self.assertEqual(self.rows()["208"].net, 800.0, "1,000 ออก − 200 คืน ไม่รวมโอน 99,000")

    def test_a_return_is_subtracted_because_the_system_does_not_subtract_it(self):
        row = self.rows()["208"]
        self.assertEqual((row.issued, row.returned, row.net), (1000.0, 200.0, 800.0))

    def test_a_department_with_no_name_still_appears_with_its_code(self):
        row = self.rows()["777"]
        self.assertEqual(row.name, "777")
        self.assertEqual(row.net, 300.0)

    def test_the_named_departments_read_as_their_thai_names(self):
        self.assertEqual(self.rows()["305"].name, "ฝ่ายพัสดุ")

    def test_drilling_in_shows_only_that_department_one_level_down(self):
        report = usage.breakdown(self.conn, level=departments.DEPT, parent=("208",))
        self.assertEqual([row.path for row in report["rows"]], [("208", "02", "")])
        self.assertEqual(report["rows"][0].name, "งานคลังยา")

    def test_filtering_by_store_narrows_the_numbers(self):
        self.assertNotIn("305", self.rows(store="I2"))
        self.assertEqual(self.rows(store="I2")["208"].net, 800.0)

    def test_filtering_by_item_group_keeps_only_that_group(self):
        drugs = self.rows(group=categories.MATERIAL)
        self.assertNotIn("208", drugs, "ยาต้องไม่โผล่ในมุมมองพัสดุ")
        self.assertEqual(drugs["305"].net, 500.0)

    def test_items_are_listed_net_of_returns_for_the_chosen_department(self):
        items = usage.items_of(self.conn, ("208",))
        self.assertEqual([item.stock_code for item in items], ["1000"])
        self.assertEqual(items[0].net, 800.0)
        self.assertEqual(items[0].qty, 8.0)
        self.assertEqual(items[0].name, "PARACETAMOL")

    def test_group_totals_split_the_hospital_into_the_four_groups(self):
        totals = {part["key"]: part["net"] for part in usage.group_totals(self.conn)}
        self.assertEqual(totals[categories.DRUG], 800.0)
        self.assertEqual(totals[categories.MATERIAL], 800.0)

    def test_the_group_comes_from_the_category_not_from_what_was_saved_at_pull_time(self):
        """การแบ่งกลุ่มต้องเปลี่ยนได้โดยไม่ต้องดึงข้อมูล 2 ล้านแถวใหม่

        หมวด 03 ถูกแยกจาก "อื่น ๆ" มาเป็น "อาหารและโภชนาการ" เมื่อ 17 ก.ย. 2569
        ถ้าอ่านจากช่องกลุ่มที่บันทึกไว้ตอนดึง หน้าจอจะยังแสดงกลุ่มเดิมจนกว่าจะดึงใหม่
        """
        self.conn.execute("UPDATE items SET item_group = 'ค่าเก่าที่ไม่ควรถูกใช้'")
        totals = {part["key"]: part["net"] for part in usage.group_totals(self.conn)}
        self.assertEqual(totals[categories.DRUG], 800.0)
        self.assertEqual(usage.items_of(self.conn, ("208",))[0].group, "ยา")
        self.assertIn("305", self.rows(group=categories.MATERIAL))

    def test_an_empty_warehouse_explains_itself_instead_of_showing_zero(self):
        empty = sqlite3.connect(":memory:")
        empty.execute("CREATE TABLE issues (period TEXT, store TEXT, irno TEXT, stock_code TEXT, "
                      "qty REAL, value REAL, unit TEXT, division TEXT, dept TEXT, section TEXT, "
                      "document_type TEXT, direction TEXT)")
        self.addCleanup(empty.close)
        report = usage.breakdown(empty)
        self.assertEqual(report["rows"], [])
        self.assertTrue(report["reason"])

    def test_an_unknown_level_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            usage.breakdown(self.conn, level="ไม่มีชั้นนี้")

    def test_the_window_counts_back_from_the_newest_period_not_from_today(self):
        """ต้นทางเป็นสำเนาที่คัดลอกวันละครั้ง ถ้านับจากวันนี้ เดือนสุดท้ายจะดูตกลงเสมอ"""
        clause, values = usage._period_clause(12, "202609")
        self.assertEqual(values, ["202509", "202609"])
        self.assertIn("period < ?", clause, "งวดล่าสุดยังไม่จบเดือน ต้องไม่นับ")
        self.assertEqual(usage._period_clause(3, "202601")[1], ["202510", "202601"])

    def test_the_unfinished_latest_month_is_not_counted(self):
        """ต้องตรงกับหน้าภาพรวม ซึ่งตัดเดือนที่ยังไม่จบออกตามกติกาของ metrics.py"""
        self.assertEqual(self.rows()["208"].net, 800.0, "ใบ OPEN ของ 202609 ต้องไม่ถูกนับ")

    def test_an_issue_that_failed_reconciliation_is_not_counted_as_used(self):
        """กติกาผู้ใช้ 11 ก.ย. 2569: ขาออกนับเฉพาะที่สอบทานผ่าน ขาเข้าหักทั้งหมด"""
        self.conn.execute("INSERT INTO issues VALUES ('202608','I2','X1','1','','1000',5,777.0,"
                          "'TAB','208','02','','32','out','PENDING')")
        self.assertEqual(self.rows()["208"].net, 800.0)


if __name__ == "__main__":
    unittest.main()
