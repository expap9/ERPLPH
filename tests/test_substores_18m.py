import sqlite3
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import substores
import web


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
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

        CREATE TABLE balances (
            period TEXT,
            store TEXT,
            stock_code TEXT,
            lot_no TEXT,
            qty REAL,
            value REAL,
            unit TEXT,
            expire_date TEXT
        );

        CREATE TABLE items (
            stock_code TEXT PRIMARY KEY,
            name TEXT,
            main_category TEXT,
            item_group TEXT,
            base_unit TEXT
        );

        -- ใส่ข้อมูล 18 เดือน (202504 - 202609)
        INSERT INTO issues (period, store, irno, stock_code, qty, value, unit, document_type, direction, division, dept, section)
        VALUES 
        ('202609', 'I2', 'IR09', '1001', 10, 5000.0, 'TAB', '35', 'in', '', '', ''),
        ('202609', 'I2', 'IR09B', '1001', 5, 2500.0, 'TAB', '32', 'out', '104', '05', ''),
        ('202608', 'I2', 'IR08', '1001', 20, 10000.0, 'TAB', '35', 'in', '', '', ''),
        ('202608', 'I2', 'IR08B', '1001', 15, 7500.0, 'TAB', '32', 'out', '104', '05', ''),
        ('202504', 'I2', 'IR04', '1001', 30, 15000.0, 'TAB', '35', 'in', '', '', ''),
        ('202504', 'I2', 'IR04B', '1001', 25, 12500.0, 'TAB', '32', 'out', '104', '05', '');

        INSERT INTO items (stock_code, name, main_category, item_group, base_unit)
        VALUES ('1001', 'Paracetamol 500mg', '10', 'drug', 'TAB');

        INSERT INTO balances (period, store, stock_code, lot_no, qty, value, unit, expire_date)
        VALUES ('202609', 'I2', '1001', 'L01', 100, 50000.0, 'TAB', '20270101');
    """)
    yield conn
    conn.close()


def test_month_choices_includes_18():
    assert 18 in web.MONTH_CHOICES


def test_substore_monthly_trend_calculation(memory_db):
    trend = substores.get_substore_monthly_trend(memory_db, "I2", months=18, latest="202609")
    assert trend["months_count"] >= 3
    assert trend["total_trans_in"] == 30000.0
    assert trend["total_disp"] == 22500.0
    assert trend["net_overall"] == 7500.0
    assert trend["avg_trans_in"] > 0
    assert trend["avg_disp"] > 0
    assert len(trend["months_data"]) >= 3


def test_ward_monthly_trend_calculation(memory_db):
    trend = substores.get_ward_monthly_trend(memory_db, "104-05", months=18, latest="202609")
    assert trend["months_count"] >= 3
    assert trend["total_val"] == 22500.0
    assert trend["total_slips"] == 3
    assert trend["avg_val"] > 0
    assert len(trend["months_data"]) >= 3


def test_substores_page_render_with_18_months():
    client = web.app.test_client()
    resp_substore = client.get("/substores?store=I2&months=18")
    assert resp_substore.status_code == 200
    html_sub = resp_substore.get_data(as_text=True)
    assert "ข้อมูลรายเดือนย้อนหลัง 18 เดือน" in html_sub
    assert "18 เดือน" in html_sub

    resp_ward = client.get("/substores?store=W:104-05&months=18")
    assert resp_ward.status_code == 200
    html_ward = resp_ward.get_data(as_text=True)
    assert "ข้อมูลการเบิกใช้รายเดือนย้อนหลัง 18 เดือน" in html_ward
    assert "18 เดือน" in html_ward


def test_requisition_leaders_and_breakdown(memory_db):
    data = substores.get_top_requisition_leaders_dashboard(memory_db, months=18)
    assert "drugs" in data
    assert "ipd" in data["drugs"]
    ipd_depts = data["drugs"]["ipd"]["top_depts"]
    assert len(ipd_depts) >= 1
    # Check that top_items is attached to the department
    dept = ipd_depts[0]
    assert "top_items" in dept
    assert len(dept["top_items"]) >= 1
    assert dept["top_items"][0]["stock_code"] == "1001"
    assert dept["top_items"][0]["name"] == "Paracetamol 500mg"

    # Test breakdown for this department
    breakdown = substores.get_dept_requisition_breakdown(
        memory_db, months=18, div="104", dept="05", sec="", scope="ipd"
    )
    assert breakdown["dept_code"] == "104-05"
    assert breakdown["total_items"] == 1
    assert breakdown["total_val"] == 22500.0
    assert breakdown["items"][0]["stock_code"] == "1001"
    assert breakdown["items"][0]["qty"] == 45.0
    assert breakdown["items"][0]["percent"] == 100.0


def test_api_requisition_leaders_dept_breakdown():
    client = web.app.test_client()
    resp = client.get("/api/requisition-leaders/dept-breakdown?months=18&div=104&dept=05&scope=ipd")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        json_data = resp.get_json()
        assert json_data["status"] == "success"
        assert "items" in json_data["data"]

