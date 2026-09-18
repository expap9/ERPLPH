"""ทำความสะอาดชื่อยาและชื่อคู่ค้า (Vendor/Supplier) ในฐานข้อมูล SQLite warehouse_data/erplph.db

ตรวจสอบและปรับปรุง:
1. ตาราง items: คอลัมน์ name และ trade_name ให้ผ่าน clean_drug_name
2. ตาราง receipts: คอลัมน์ supplier ให้ผ่าน clean_vendor_name
"""
import argparse
import os
from pathlib import Path
import sqlite3
import sys

# เพิ่ม root directory และ app directory เข้า sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "app"))

import name_cleaner  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Clean duplicated characters in items and receipts")
    parser.add_argument("--apply", action="store_true", help="Apply changes to the database")
    parser.add_argument("--db", type=str, default=str(BASE_DIR / "warehouse_data" / "erplph.db"),
                        help="Path to erplph.db")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        return

    print(f"Connecting to {db_path}...")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 1. ตรวจสอบตาราง items
    print("\n--- Checking items table ---")
    cursor.execute("SELECT stock_code, name, trade_name FROM items")
    item_rows = cursor.fetchall()
    items_to_update = []
    for code, name, trade_name in item_rows:
        cleaned_name = name_cleaner.clean_drug_name(name) if name else ""
        cleaned_trade = name_cleaner.clean_drug_name(trade_name) if trade_name else ""
        if cleaned_name != (name or "") or cleaned_trade != (trade_name or ""):
            items_to_update.append((cleaned_name, cleaned_trade, code, name, trade_name))

    print(f"Total items: {len(item_rows):,}")
    print(f"Items to update: {len(items_to_update):,}")
    for cleaned_name, cleaned_trade, code, old_name, old_trade in items_to_update[:20]:
        print(f"  [{code}] Name: {old_name!r} -> {cleaned_name!r} | Trade: {old_trade!r} -> {cleaned_trade!r}")
    if len(items_to_update) > 20:
        print(f"  ... and {len(items_to_update) - 20} more items")

    # 2. ตรวจสอบตาราง receipts
    print("\n--- Checking receipts table ---")
    cursor.execute("SELECT DISTINCT supplier FROM receipts WHERE supplier IS NOT NULL AND supplier != ''")
    suppliers = [row[0] for row in cursor.fetchall()]
    suppliers_to_update = {}
    for sup in suppliers:
        cleaned_sup = name_cleaner.clean_vendor_name(sup)
        if cleaned_sup != sup:
            suppliers_to_update[sup] = cleaned_sup

    print(f"Total distinct suppliers: {len(suppliers):,}")
    print(f"Distinct suppliers to update: {len(suppliers_to_update):,}")
    for old_sup, new_sup in list(suppliers_to_update.items())[:20]:
        print(f"  Supplier: {old_sup!r} -> {new_sup!r}")
    if len(suppliers_to_update) > 20:
        print(f"  ... and {len(suppliers_to_update) - 20} more suppliers")

    if args.apply:
        print("\nApplying updates to database...")
        conn.execute("BEGIN TRANSACTION")
        try:
            # อัปเดต items
            item_update_count = 0
            for cleaned_name, cleaned_trade, code, _, _ in items_to_update:
                cursor.execute(
                    "UPDATE items SET name = ?, trade_name = ? WHERE stock_code = ?",
                    (cleaned_name, cleaned_trade, code)
                )
                item_update_count += cursor.rowcount

            # อัปเดต receipts
            receipt_update_count = 0
            for old_sup, new_sup in suppliers_to_update.items():
                cursor.execute(
                    "UPDATE receipts SET supplier = ? WHERE supplier = ?",
                    (new_sup, old_sup)
                )
                receipt_update_count += cursor.rowcount

            conn.commit()
            print(f"Successfully updated {item_update_count:,} item records and {receipt_update_count:,} receipt records.")
        except Exception as e:
            conn.rollback()
            print(f"Error during update: {e}")
            raise
    else:
        print("\nDry-run complete. Run with --apply to commit changes to database.")

    conn.close()


if __name__ == "__main__":
    main()
