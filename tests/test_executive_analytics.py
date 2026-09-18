import sqlite3
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import executive_analytics
import web


@pytest.fixture
def test_db():
    """สร้าง in-memory SQLite DB พร้อมตารางจำลองสำหรับทดสอบ"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE periods (
            id INTEGER PRIMARY KEY,
            period TEXT,
            store TEXT,
            kind TEXT,
            status TEXT,
            pulled_at TEXT
        );

        CREATE TABLE items (
            stock_code TEXT PRIMARY KEY,
            name TEXT,
            main_category TEXT,
            item_group TEXT,
            base_unit TEXT,
            retired INTEGER
        );

        CREATE TABLE balances (
            period TEXT,
            store TEXT,
            stock_code TEXT,
            lot_no TEXT,
            qty REAL,
            value REAL,
            unit TEXT,
            expire_date TEXT,
            last_in_date TEXT
        );

        CREATE TABLE receipts (
            period TEXT,
            store TEXT,
            rcv_no TEXT,
            suffix TEXT,
            stock_code TEXT,
            lot_no TEXT,
            qty REAL,
            value REAL,
            unit TEXT,
            unit_price REAL,
            po_no TEXT,
            supplier TEXT,
            rcv_date TEXT
        );

        CREATE TABLE issues (
            period TEXT,
            store TEXT,
            irno TEXT,
            suffix TEXT,
            movement_key TEXT,
            stock_code TEXT,
            lot_no TEXT,
            qty REAL,
            value REAL,
            unit TEXT,
            department TEXT,
            issued_at TEXT,
            document_type TEXT,
            movement_kind TEXT,
            check_status TEXT,
            check_reason TEXT,
            direction TEXT,
            division TEXT,
            dept TEXT,
            section TEXT
        );

        -- ใส่ข้อมูลทดสอบ
        INSERT INTO periods (period, store, kind, status, pulled_at)
        VALUES ('202609', '2', 'balance', 'complete', '2026-09-17T05:00:00+00:00');

        INSERT INTO items (stock_code, name, main_category, item_group, base_unit)
        VALUES
        ('1001', 'Drug A 500mg', '10', 'drug', 'TAB'),
        ('1002', 'Adrenaline Inj', '10', 'drug', 'AMP'),
        ('2001', 'Gauze 4x4', '2', 'medical_supply', 'ROL'),
        ('B001', 'Knee Joint Implant (ข้อเข่าเทียม)', '2', 'medical_supply', 'SET');

        INSERT INTO balances (period, store, stock_code, lot_no, qty, value, unit, expire_date)
        VALUES
        ('202609', '2', '1001', 'L01', 500, 25000, 'TAB', '20261115'),
        ('202609', '2', '1002', 'L02', 100, 10000, 'AMP', '20270501'),
        ('202609', 'B', 'B001', 'L03', 10, 350000, 'SET', '20280101');

        INSERT INTO receipts (period, store, rcv_no, stock_code, qty, value, po_no, supplier, rcv_date)
        VALUES
        ('202609', '2', 'RC01', '1001', 1000, 450000, 'PO01', 'Pharma A Co', '20260901'),
        ('202609', '2', 'RC02', '1001', 800, 400000, 'PO02', 'Pharma A Co', '20260910');

        INSERT INTO issues (period, store, irno, stock_code, qty, value, document_type, direction, check_status, division, dept)
        VALUES
        ('202609', '2', 'IR01', '1001', 300, 15000, '32', 'out', 'VERIFIED', '203', '01'),
        ('202609', '2', 'IR02', '1002', 50, 5000, '32', 'out', 'VERIFIED', '203', '02');
    """)
    yield conn
    conn.close()


def test_concurrency_pragmas(test_db):
    executive_analytics.apply_sqlite_concurrency_pragmas(test_db)
    # ไม่พังแม้เป็น in-memory DB


def test_data_freshness_status(test_db):
    freshness = executive_analytics.get_data_freshness_status(test_db)
    assert freshness["status"] in ("fresh", "warning", "stale")
    assert freshness["badge_class"] in ("ok", "slow", "late")
    assert "17/09" in freshness["last_sync_thai"]


def test_split_po_risks(test_db):
    split_cases = executive_analytics.detect_split_po_risks(test_db, max_split_amount=500000.0)
    assert len(split_cases) >= 1
    case = split_cases[0]
    assert case["supplier"] == "Pharma A Co"
    assert case["combined_value"] == 850000.0
    assert case["po_count"] == 2
    assert "สตง." in case["audit_risk_note"]


def test_near_expiry_returns(test_db):
    returns = executive_analytics.detect_near_expiry_returns(test_db, months_threshold=6)
    assert returns["total_return_value"] >= 25000.0
    assert len(returns["return_items"]) >= 1
    assert returns["return_items"][0]["stock_code"] == "1001"


def test_consignment_separation(test_db):
    cons = executive_analytics.get_stock_with_consignment_separation(test_db)
    assert cons["total_gross_value"] >= 385000.0
    assert cons["consignment_value"] >= 350000.0
    assert cons["hospital_owned_value"] >= 35000.0
    assert cons["consignment_pct"] > 0


def test_action_required_inbox(test_db):
    actions = executive_analytics.get_action_required_inbox(test_db)
    assert len(actions) >= 1
    assert any("PO" in a["title"] or "Split" in a["title"] or "ยา" in a["title"] for a in actions)


def test_executive_summary_sheet(test_db):
    sheet = executive_analytics.get_executive_summary_sheet(test_db)
    assert "freshness" in sheet
    assert "urgent_actions" in sheet
    assert "main_stores" in sheet
    assert "top_fast_moving" in sheet


def test_biomedical_and_facilities():
    high_repairs = executive_analytics.high_repair_cost_assets()
    assert len(high_repairs) >= 3
    assert high_repairs[0]["ratio_pct"] >= 50.0

    warranties = executive_analytics.detect_warranty_overlap()
    assert len(warranties) >= 2
    assert warranties[0]["alert_status"] == "LOCKED"


def test_top10_functions(test_db):
    overdue = executive_analytics.top10_overdue_pos(test_db)
    assert len(overdue) <= 10

    fast = executive_analytics.top10_fast_moving(test_db)
    assert len(fast) <= 10
    assert fast[0]["stock_code"] == "1001"

    freq = executive_analytics.top10_frequent_purchases(test_db)
    assert len(freq) <= 10

    high_val = executive_analytics.top10_highest_value(test_db)
    assert len(high_val) <= 10

    depts = executive_analytics.top10_top_requisitioning_depts(test_db)
    assert len(depts) <= 10


def test_procure_to_pay_and_substores(test_db):
    p2p = executive_analytics.procure_to_pay_pipeline(test_db)
    assert p2p["po_amount_available"] is False, "ยังไม่มีตาราง PO จริง (SKPO) ต้องบอกตรง ๆ ว่าไม่มี ไม่ใช่เลขคำนวณเอง"
    assert "pipeline_items" in p2p
    for item in p2p["pipeline_items"]:
        assert "po_ordered_amount" not in item, "ห้ามมีเลขสั่งซื้อปลอม (เดิม = ยอดรับ×1.15)"
        assert "ap_status" not in item, "ห้ามมีสถานะจ่ายเงินปลอม (เดิมสลับกันตาม index)"

    sub = executive_analytics.substore_status_summary(test_db)
    assert "in_transit" in sub
    assert "overstock" in sub
    assert "deadstock" in sub


def test_flask_routes():
    web.app.config["TESTING"] = True  # ข้ามการบังคับล็อกอิน (enforce_login_for_pages) ตอนเทสต์
    client = web.app.test_client()

    routes = [
        "/top10",
        "/procure-to-pay",
        "/substores",
        "/projects",
        "/savings",
        "/governance",
        "/executive-print",
    ]

    for route in routes:
        resp = client.get(route)
        assert resp.status_code == 200, f"Route {route} failed with status {resp.status_code}"
