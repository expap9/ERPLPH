import sqlite3
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import substores
import web


@pytest.fixture
def test_db():
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
            expire_date TEXT,
            last_in_date TEXT
        );

        CREATE TABLE items (
            stock_code TEXT PRIMARY KEY,
            name TEXT,
            trade_name TEXT,
            main_category TEXT,
            item_group TEXT,
            base_unit TEXT,
            retired INTEGER DEFAULT 0
        );

        -- Items
        INSERT INTO items (stock_code, name, trade_name, main_category, item_group, base_unit, retired)
        VALUES 
        ('DRUG01', 'Paracetamol 500mg', 'Sara', '10', 'drug', 'TAB', 0),
        ('DRUG02', 'Amoxicillin 500mg', 'Amoxil', '10', 'drug', 'CAP', 0),
        ('RET01', 'Old Drug Discontinued', '', '10', 'drug', 'TAB', 1),
        ('SUPP01', 'Surgical Gloves M', 'Glove', '20', 'supply', 'BOX', 0);

        -- Issues in current FY (202510 - 202609)
        INSERT INTO issues (period, store, irno, stock_code, qty, value, unit, document_type, direction, division, dept, section, issued_at)
        VALUES 
        ('202609', 'I2', 'GTF01', 'DRUG01', 100, 1000.0, 'TAB', '35', 'in', '', '', '', '2026-09-10'),
        ('202609', 'I2', 'DSP01', 'DRUG01', 80, 800.0, 'TAB', '32', 'out', '104', '05', '', '2026-09-12'),
        ('202608', 'I2', 'DSP02', 'DRUG01', 60, 600.0, 'TAB', '32', 'out', '104', '05', '', '2026-08-15'),
        ('202607', 'I2', 'DSP03', 'DRUG01', 100, 1000.0, 'TAB', '32', 'out', '104', '05', '', '2026-07-20'),
        ('202609', '1',  'SUP01', 'SUPP01', 10, 5000.0, 'BOX', '32', 'out', '102', '08', '', '2026-09-05'),
        -- Previous FY (202508)
        ('202508', 'I2', 'OLD01', 'DRUG01', 50, 500.0, 'TAB', '32', 'out', '104', '05', '', '2025-08-10');

        -- Balances
        INSERT INTO balances (period, store, stock_code, lot_no, qty, value, unit, expire_date, last_in_date)
        VALUES 
        ('202609', 'I2', 'DRUG01', 'LOT-A', 200, 2000.0, 'TAB', '20270115', '2026-09-01'),
        ('202609', 'I2', 'DRUG02', 'LOT-B', 50, 500.0, 'CAP', '20260630', '2026-01-10'), -- expired 6m ago
        ('202609', 'I2', 'RET01',  'LOT-C', 10, 100.0, 'TAB', '20261130', '2025-01-01'), -- retired item
        ('202609', '1',  'SUPP01', 'LOT-D', 30, 15000.0, 'BOX', '20281231', '2026-08-01');
    """)
    yield conn
    conn.close()


def test_fiscal_year_range():
    start_p, end_p, label = substores.fiscal_year_range("202609")
    assert start_p == "202510"
    assert end_p == "202609"
    assert "2569" in label

    start_p, end_p, label = substores.fiscal_year_range("202610")
    assert start_p == "202610"
    assert end_p == "202610"
    assert "2570" in label


def test_substore_kpis_monthly_and_fy(test_db):
    kpis = substores.get_substore_kpis(test_db, "I2", months=18, latest="202609")
    # FY disp val should include 202609 (800), 202608 (600), 202607 (1000) = 2400.0 (excludes 202508)
    assert kpis["fy_disp_val"] == 2400.0
    assert kpis["monthly_disp_avg"] > 0
    assert "2569" in kpis["fy_label"]


def test_get_expiring_medicines_tabs(test_db):
    exp = substores.get_expiring_medicines(test_db, "I2")
    assert "expiring_soon" in exp
    assert "already_expired" in exp
    assert exp["count_soon"] >= 1
    assert exp["count_expired"] >= 1
    # Check that retired items are excluded
    codes_soon = [x["stock_code"] for x in exp["expiring_soon"]]
    assert "RET01" not in codes_soon


def test_hospital_all_stores_analytics(test_db):
    data = substores.get_hospital_all_stores_analytics(test_db, months=18, latest="202609")
    assert "kpis" in data
    assert "groups_data" in data
    assert "top_items" in data
    assert len(data["groups_data"]) >= 3
    # Check groups contain drug and supply
    names = [g["name"] for g in data["groups_data"]]
    assert any("ยา" in n for n in names)
    assert any("พัสดุ" in n for n in names)


def test_item_substore_detail(test_db):
    detail = substores.get_item_substore_detail(test_db, "I2", "DRUG01", months=18)
    assert detail["found"] is True
    assert detail["item"]["stock_code"] == "DRUG01"
    assert detail["on_hand"]["qty"] == 200.0
    assert len(detail["on_hand"]["lots"]) == 1
    assert len(detail["dispensations"]) >= 1
    assert len(detail["transfers_in"]) >= 1


def test_web_routes_and_api(tmp_path, monkeypatch):
    db_file = tmp_path / "test_wh.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE issues (
            period TEXT, store TEXT, irno TEXT, suffix TEXT, movement_key TEXT,
            stock_code TEXT, lot_no TEXT, qty REAL, value REAL, unit TEXT,
            department TEXT, issued_at TEXT, document_type TEXT, movement_kind TEXT,
            check_status TEXT, check_reason TEXT, direction TEXT,
            division TEXT, dept TEXT, section TEXT
        );
        CREATE TABLE balances (
            period TEXT, store TEXT, stock_code TEXT, lot_no TEXT,
            qty REAL, value REAL, unit TEXT, expire_date TEXT, last_in_date TEXT
        );
        CREATE TABLE items (
            stock_code TEXT PRIMARY KEY, name TEXT, trade_name TEXT,
            main_category TEXT, item_group TEXT, base_unit TEXT, retired INTEGER DEFAULT 0
        );
        INSERT INTO items VALUES ('DRUG01', 'Paracetamol 500mg', 'Sara', '10', 'drug', 'TAB', 0);
        INSERT INTO balances VALUES ('202609', 'I2', 'DRUG01', 'LOT-A', 200, 2000.0, 'TAB', '20270115', '2026-09-01');
        INSERT INTO issues VALUES ('202609', 'I2', 'DSP01', '', '', 'DRUG01', 'LOT-A', 80, 800.0, 'TAB', '', '2026-09-12', '32', '', '', '', 'out', '104', '05', '');
    """)
    conn.close()

    def get_conn():
        c = sqlite3.connect(str(db_file))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(web, "open_warehouse", get_conn)
    client = web.app.test_client()

    # 1. Page store=ALL
    resp = client.get("/substores?store=ALL&months=18")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "รวมทุกคลังในโรงพยาบาล" in html
    assert "สรุปการใช้และตัดจ่ายแยกตามกลุ่มคลัง" in html

    # 2. JSON API item detail
    resp_api = client.get("/api/substores/item-detail?store=I2&code=DRUG01")
    assert resp_api.status_code == 200
    res_json = resp_api.get_json()
    assert res_json["status"] == "success"
    assert res_json["data"]["found"] is True

    # 3. JSON API item search by name substring
    resp_search = client.get("/api/substores/item-detail?store=I2&code=Paracetamol")
    assert resp_search.status_code == 200
    res_s_json = resp_search.get_json()
    assert res_s_json["status"] == "success"
    assert res_s_json["data"]["item"]["stock_code"] == "DRUG01"

    # 4. Page tab=leaders
    resp_leaders = client.get("/substores?tab=leaders&months=18")
    assert resp_leaders.status_code == 200
    html_lead = resp_leaders.get_data(as_text=True)
    assert "Dashboard ใครเบิกอะไรเยอะบ้าง" in html_lead
    assert "ยา TOP 5" in html_lead
    assert "พัสดุสิ้นเปลือง TOP 5" in html_lead

    # 5. JSON API /api/requisition-leaders
    resp_leaders_api = client.get("/api/requisition-leaders?months=18")
    assert resp_leaders_api.status_code == 200
    data_lead = resp_leaders_api.get_json()
    assert data_lead["status"] == "success"
    assert "drugs" in data_lead["data"]
    assert "supplies" in data_lead["data"]
    assert "metrics" in data_lead["data"]["supplies"]["paper"]


def test_top_requisition_leaders_dashboard_and_retired_filtering(test_db):
    # Insert paper and toner
    test_db.executescript("""
        INSERT INTO items (stock_code, name, trade_name, main_category, item_group, base_unit, retired)
        VALUES
        ('PAPER01', 'กระดาษ A4 80g', 'Double A', '6', 'material', 'REAM', 0),
        ('TONER01', 'หมึก HP Laser 107A', 'HP', '6', 'material', 'BOX', 0),
        ('RET_PAPER', '((ยกเลิก) กระดาษต่อเนื่อง', '', '6', 'material', 'BOX', 0);

        INSERT INTO issues (period, store, irno, stock_code, qty, value, unit, document_type, direction, division, dept, section, issued_at)
        VALUES
        ('202609', '1', 'REQ_P1', 'PAPER01', 50, 5000.0, 'REAM', '32', 'out', '208', '02', '02', '2026-09-15'),
        ('202609', '1', 'REQ_T1', 'TONER01', 5, 12000.0, 'BOX', '32', 'out', '306', '02', '02', '2026-09-15'),
        ('202609', '1', 'REQ_RET', 'RET_PAPER', 10, 1000.0, 'BOX', '32', 'out', '104', '05', '', '2026-09-15');
    """)
    leaders = substores.get_top_requisition_leaders_dashboard(test_db, months=18, use_cache=False)
    assert "drugs" in leaders
    assert "supplies" in leaders
    assert "ipd" in leaders["drugs"]
    assert "opd" in leaders["drugs"]
    assert "chemo" in leaders["drugs"]
    assert "paper" in leaders["supplies"]
    assert "toner" in leaders["supplies"]
    assert "consumables" in leaders["supplies"]

    # Check paper top items has PAPER01 and excludes RET_PAPER
    paper_codes = [x["stock_code"] for x in leaders["supplies"]["paper"]["top_items"]]
    assert "PAPER01" in paper_codes
    assert "RET_PAPER" not in paper_codes

    # Check AMC list filters out retired items
    amc_list = substores.get_amc_and_mos_list(test_db, "I2")
    amc_codes = [x["stock_code"] for x in amc_list]
    assert "RET01" not in amc_codes
    assert "RET_PAPER" not in amc_codes
