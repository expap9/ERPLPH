import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import his_login


class TestHisLogin(unittest.TestCase):
    def setUp(self):
        with his_login._FAILURES_LOCK:
            his_login._FAILURES.clear()
        with his_login._SESSIONS_LOCK:
            his_login._SESSIONS.clear()

    def test_decode_com_password(self):
        # รหัสผ่านเริ่มที่ไบต์ 14 และ XOR ด้วย 0xFF จนเจอ 0x00
        prefix = b"\x00" * 14
        raw_pw = "TEST1234"
        encoded = bytes([ord(c) ^ 0xFF for c in raw_pw]) + b"\x00\x11\x22"
        com = prefix + encoded
        self.assertEqual(his_login._decode_com_password(com), "TEST1234")

    def test_offline_admin_success(self):
        # เมื่อฐานข้อมูลต่อไม่ได้ (throw OperationalError)
        with patch("database.connect", side_effect=Exception("DB down")):
            user = his_login.verify_login("ADMIN", "STOCK5")
            self.assertEqual(user["username"], "ADMIN")
            self.assertEqual(user["access_group"], "ADMIN")

    def test_offline_admin_wrong_password(self):
        with patch("database.connect", side_effect=Exception("DB down")):
            with self.assertRaises(his_login.LoginUnavailable):
                his_login.verify_login("ADMIN", "WRONG_PASSWORD")

    def test_rate_limiting(self):
        username = "TESTUSER"
        for _ in range(his_login.LOGIN_FAILURE_LIMIT):
            his_login.check_login_allowed(username)
            his_login.record_login_failure(username)

        with self.assertRaises(his_login.LoginThrottled):
            his_login.check_login_allowed(username)

        his_login.clear_login_failures(username)
        # Should not raise now
        his_login.check_login_allowed(username)

    def test_session_lifecycle(self):
        user_info = {"username": "TEST", "name": "นายทดสอบ", "access_group": "PHAR"}
        token = his_login.create_session(user_info)
        self.assertTrue(bool(token))

        sess = his_login.get_session(token)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["username"], "TEST")

        pub = his_login.public_user(sess)
        self.assertEqual(pub["name"], "นายทดสอบ")
        self.assertNotIn("login_at", pub)

        his_login.destroy_session(token)
        self.assertIsNone(his_login.get_session(token))

    def test_verify_login_with_mock_db(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        prefix = b"\x00" * 14
        encoded_pw = bytes([ord(c) ^ 0xFF for c in "MYPASSWORD"]) + b"\x00"
        # pad to > 84 bytes for access_group and facility
        com_bytes = prefix + encoded_pw + (b"\x20" * 100)

        mock_cursor.fetchone.return_value = (
            "PHAR01", "Pharmacist John", "ภก.จอห์น", com_bytes
        )

        with patch("database.connect", return_value=mock_conn):
            user = his_login.verify_login("PHAR01", "MYPASSWORD")
            self.assertEqual(user["username"], "PHAR01")
            self.assertEqual(user["name"], "Pharmacist John")

    def test_verify_login_when_hospital_database_is_mid_restore(self):
        """เจอจริง 19 ก.ย. 2569: ต่อเซิร์ฟเวอร์ได้ แต่ SSBHOSPITAL กำลัง restore รายวัน
        (pyodbc error 927) ต้องขึ้นข้อความสุภาพ ไม่ใช่ traceback ดิบ 500 ให้ผู้ใช้เห็น"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception(
            "[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
            "Database 'SSBHOSPITAL' cannot be opened. It is in the middle of a restore. (927)"
        )

        with patch("database.connect", return_value=mock_conn):
            with self.assertRaises(his_login.LoginUnavailable) as ctx:
                his_login.verify_login("41850", "somepassword")
            self.assertIn("ปรับปรุงข้อมูล", str(ctx.exception))
        mock_conn.close.assert_called_once()

    def test_verify_login_cancelled_account(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        prefix = b"\x00" * 14
        encoded_pw = bytes([ord(c) ^ 0xFF for c in "PASS"]) + b"\x00"
        com_bytes = prefix + encoded_pw

        mock_cursor.fetchone.return_value = (
            "USER99", "Old User", "นายเก่า (ยกเลิก)", com_bytes
        )

        with patch("database.connect", return_value=mock_conn):
            with self.assertRaises(his_login.LoginError) as ctx:
                his_login.verify_login("USER99", "PASS")
            self.assertIn("ยกเลิก", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
