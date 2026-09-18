import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import his_login
import web


class TestAuthApi(unittest.TestCase):
    def setUp(self):
        self.app = web.app
        self.app.testing = True
        self.client = self.app.test_client()
        with his_login._FAILURES_LOCK:
            his_login._FAILURES.clear()
        with his_login._SESSIONS_LOCK:
            his_login._SESSIONS.clear()

    def test_auth_me_unauthenticated(self):
        res = self.client.get("/api/auth/me")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIsNone(data["user"])

    def test_auth_login_invalid_credentials(self):
        with patch("database.connect", side_effect=Exception("DB unreachable")):
            res = self.client.post("/api/auth/login", json={"username": "NONEXISTENT", "password": "WRONG"})
            self.assertIn(res.status_code, (401, 503))
            data = res.get_json()
            self.assertEqual(data["status"], "error")

    def test_auth_login_offline_admin_success_and_logout(self):
        with patch("database.connect", side_effect=Exception("DB unreachable")):
            res = self.client.post("/api/auth/login", json={"username": "ADMIN", "password": "STOCK5"})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["user"]["username"], "ADMIN")

            # Check cookie was set
            cookie_header = res.headers.get("Set-Cookie", "")
            self.assertIn(his_login.SESSION_COOKIE_NAME, cookie_header)

            # Now /api/auth/me should return the logged-in user
            me_res = self.client.get("/api/auth/me")
            self.assertEqual(me_res.status_code, 200)
            me_data = me_res.get_json()
            self.assertEqual(me_data["user"]["username"], "ADMIN")

            # Logout
            logout_res = self.client.post("/api/auth/logout")
            self.assertEqual(logout_res.status_code, 200)

            # /api/auth/me should now return null
            me_after = self.client.get("/api/auth/me")
            self.assertIsNone(me_after.get_json()["user"])

    def test_enforce_login_for_pages_redirects_when_not_testing(self):
        self.app.testing = False
        try:
            res = self.client.get("/substores?store=I2")
            self.assertEqual(res.status_code, 302)
            self.assertIn("/login?returnUrl=", res.headers["Location"])
        finally:
            self.app.testing = True


if __name__ == "__main__":
    unittest.main()
