"""API รูปแบบ Stock5 — หน้าจอ Angular ที่ยกมาอ่านช่องเหล่านี้ตรง ๆ ถ้าชื่อช่องหาย หน้าจอว่างเงียบ ๆ

ผู้ใช้สั่ง 17 ก.ย. 2569 ให้ดูข้อมูลเหมือน Stock5 แต่ครบทุกประเภทของ เทสต์ชุดนี้ตรึงสามเรื่อง
    1. ช่องที่หน้าจอ Stock5 อ่าน ต้องมีครบ (ดึงรายชื่อจากเทมเพลตของ frontend จริง)
    2. ตัวเลขต้องตรงกับหน้ารายแผนกและหน้าภาพรวมเดิม
    3. MOS รายเดือนต้องไม่คำนวณตอนใบรับยังไม่ครบ ไม่งั้นคงคลังย้อนหลังสูงเกินจริง
"""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import categories  # noqa: E402
import department_usage  # noqa: E402
import stock5_api  # noqa: E402
import warehouse_db  # noqa: E402
from test_overview import build_warehouse  # noqa: E402


class Stock5ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "erplph.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        build_warehouse()
        stock5_api.clear_cache()
        self.addCleanup(stock5_api.clear_cache)
        self.conn = sqlite3.connect(warehouse_db.DB_PATH)
        self.addCleanup(self.conn.close)

    # --- รูปของข้อมูลที่หน้าจอ Stock5 อ่าน
    def test_dashboard_has_every_section_the_stock5_screen_reads(self):
        summary = stock5_api.build_summary(self.conn)
        for key in ("meta", "kpis", "stock_buckets", "monthly_trend", "department_usage",
                    "recommendations", "tables", "data_quality"):
            self.assertIn(key, summary)
        for key in ("stock_value", "system_mos", "system_stock_days", "verified_scope_mos",
                    "non_moving_value", "non_moving_drugs", "expiry_selected_value",
                    "expiry_selected_lots", "expired_lots", "inventory_drugs", "inventory_lots",
                    "unit_pending_count", "cost_reference_rows", "target_days"):
            self.assertIn(key, summary["kpis"])
        for key in ("backorder", "excess", "non_moving", "expiry", "shortage", "top_value",
                    "top_receipt_value", "top_distribution_value", "unit_pending", "already_expired"):
            self.assertIn(key, summary["tables"])

    def test_department_rows_carry_what_the_ranking_list_and_modal_show(self):
        department = stock5_api.build_summary(self.conn)["department_usage"][0]
        for key in ("rank", "department", "value", "percent_of_total", "drug_count", "issue_count",
                    "last_movement", "top_drugs", "monthly_trend"):
            self.assertIn(key, department)
        for key in ("code", "name", "qty", "unit", "value", "percent", "last_date"):
            self.assertIn(key, department["top_drugs"][0])

    def test_item_detail_has_every_section_the_stock5_detail_screen_reads(self):
        detail = stock5_api.build_item_detail(self.conn, "1000")
        for key in ("code", "name", "trade_names", "tpuids", "vendors", "procurement_info", "summary",
                    "facts", "monthly_movement", "monthly_trend", "department_distribution",
                    "audit_signals", "timeline", "po_tracking", "source_notes", "read_only",
                    "investigation", "question_items", "record_counts"):
            self.assertIn(key, detail)
        for key in ("stock_value", "stock_quantities", "stock_lots", "receipt_value", "receipt_quantities",
                    "receipt_rows_raw", "issue_value", "issue_quantities", "issue_rows_raw", "days_on_hand",
                    "months_on_hand", "avg_monthly_issue_value_3m", "avg_monthly_issue_quantities_3m",
                    "unit_pending"):
            self.assertIn(key, detail["summary"])
        for key in ("pending_pos", "received_pos", "alerts", "pending_count", "received_count"):
            self.assertIn(key, detail["po_tracking"])
        self.assertTrue(detail["read_only"], "หน้าจอต้องซ่อนปุ่มบันทึก เพราะ ERPLPH ยังไม่มีล็อกอิน")

    def test_search_cards_have_what_the_result_grid_shows(self):
        result = stock5_api.search_items(self.conn, "กระดาษ")
        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        for key in ("WORKING_CODE", "name", "trade_name", "stock_value", "stock_quantities", "receipt_rows",
                    "issue_rows", "has_stock_no_issue", "group"):
            self.assertIn(key, item)
        self.assertNotIn("_search", item)

    def test_record_view_lists_columns_and_values_like_stock5(self):
        records = stock5_api.build_records(self.conn, "1000", "distribution")
        self.assertGreater(records["total"], 0)
        names = {column["name"] for column in records["columns"]}
        self.assertTrue({"IRNO", "QTY_DIS", "VALUE", "DEPT_NAME"} <= names)
        self.assertIsNone(stock5_api.build_records(self.conn, "1000", "ไม่มีชนิดนี้"))

    # --- ตัวเลข
    def test_department_totals_match_the_department_page(self):
        summary = stock5_api.build_summary(self.conn, usage_months=12)
        api_total = sum(row["value"] for row in summary["department_usage"])
        page_total = department_usage.breakdown(self.conn, months=12)["total_net"]
        self.assertAlmostEqual(api_total, page_total, places=2)

    def test_hospital_use_excludes_transfers_and_unverified_lines(self):
        detail = stock5_api.build_item_detail(self.conn, "1000")
        self.assertEqual(detail["summary"]["issue_value"], 800.0 + 4321.0,
                         "1,000 − คืน 200 + ใบเดือนล่าสุด · ไม่นับ PENDING 777 · ไม่นับโอน 5,000")

    def test_equipment_and_hired_work_get_no_months_of_stock(self):
        self.assertIsNone(stock5_api.build_item_detail(self.conn, "7000")["summary"]["months_on_hand"])
        hire = stock5_api.build_summary(self.conn, group=categories.HIRE)
        self.assertIsNone(hire["kpis"]["system_mos"])

    def test_scope_filters_narrow_the_dashboard(self):
        material = stock5_api.build_summary(self.conn, group=categories.MATERIAL)
        self.assertEqual(material["kpis"]["stock_value"], 1000.0)
        self.assertEqual(material["meta"]["scope"]["group"], categories.MATERIAL)

    # --- MOS รายเดือน
    def test_monthly_mos_is_withheld_while_receipts_cover_only_one_store(self):
        """เห็นจริง 17 ก.ย. 2569: ใบรับมีแค่คลังยา MOS ต.ค. 68 ขึ้นเป็น 5.41 ทั้งที่ปัจจุบันคือ 2.3"""
        self.conn.execute("DELETE FROM receipts WHERE store <> '2'")
        summary = stock5_api.build_summary(self.conn)
        self.assertFalse(summary["meta"]["trend_mos_available"])
        self.assertTrue(all(month["mos"] is None for month in summary["monthly_trend"]))
        self.assertTrue(summary["meta"]["trend_mos_note"])

    def test_the_unfinished_month_never_gets_a_monthly_mos(self):
        summary = stock5_api.build_summary(self.conn)
        self.assertTrue(summary["meta"]["trend_mos_available"])
        latest = summary["monthly_trend"][-1]
        self.assertTrue(latest["partial"])
        self.assertIsNone(latest["mos"])

    def test_an_old_warehouse_without_receipt_departments_still_opens_item_pages(self):
        """หน้าเว็บเปิดคลังข้อมูลแบบอ่านอย่างเดียว อัปเกรดตารางเองไม่ได้"""
        self.conn.executescript(
            "CREATE TABLE receipts_old AS SELECT period, store, rcv_no, suffix, stock_code, lot_no, qty, "
            "value, unit, unit_price, po_no, supplier, rcv_date FROM receipts; "
            "DROP TABLE receipts; ALTER TABLE receipts_old RENAME TO receipts;")
        self.assertIsNotNone(stock5_api.build_item_detail(self.conn, "1000"))


class Stock5RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "erplph.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        stock5_api.clear_cache()
        self.addCleanup(stock5_api.clear_cache)
        import web
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def test_every_route_the_angular_screens_call_answers(self):
        build_warehouse()
        for path in ("/api/auth/me", "/api/status", "/api/scopes", "/api/monitor/summary",
                     "/api/monitor/summary?group=material&store=2", "/api/monitor/auto-sync/status",
                     "/api/drugs/search?q=para", "/api/drugs/1000", "/api/drugs/1000/records/receipt",
                     "/api/drugs/1000/pos", "/api/pos/alerts", "/api/documents/search?q=M1"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_writing_is_refused_because_there_is_no_login_yet(self):
        build_warehouse()
        for path in ("/api/drugs/1000/pos", "/api/drugs/1000/investigation", "/api/drugs/1000/primary-vendor",
                     "/api/pos/PO1/dispatch", "/api/pos/PO1/receive", "/api/monitor/pull"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={}).status_code, 403)

    def test_scope_values_from_the_address_bar_are_validated(self):
        build_warehouse()
        response = self.client.get("/api/monitor/summary?group=' OR 1=1&store=ไม่มี")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["meta"]["scope"]["group"])

    def test_unknown_item_and_missing_warehouse_answer_clearly(self):
        self.assertEqual(self.client.get("/api/monitor/summary").status_code, 404)
        build_warehouse()
        stock5_api.clear_cache()
        self.assertEqual(self.client.get("/api/drugs/ไม่มีรหัสนี้").status_code, 404)

    def test_unknown_api_paths_are_not_swallowed_by_the_angular_fallback(self):
        self.assertEqual(self.client.get("/api/ไม่มีเส้นทางนี้").status_code, 404)

    def test_client_side_routes_return_the_angular_page(self):
        response = self.client.get("/dashboard")
        self.assertIn(response.status_code, (200, 503), "503 = ยังไม่ได้ build หน้าจอบนเครื่องนี้")
