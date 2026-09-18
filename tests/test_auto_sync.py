"""ทดสอบระบบ Auto-Sync และ Background Scheduler"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import auto_sync
import warehouse_db
from test_overview import build_warehouse


class AutoSyncUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "erplph.db")):
            patcher = patch.object(warehouse_db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        build_warehouse()
        auto_sync.set_enabled(True)
        auto_sync.set_interval(auto_sync.DEFAULT_INTERVAL_SECONDS)

    def test_get_status_returns_required_angular_fields(self):
        status = auto_sync.get_status()
        for key in ("enabled", "is_syncing", "interval_seconds", "last_status", "last_message", "next_run"):
            self.assertIn(key, status)
        self.assertTrue(status["enabled"])
        self.assertFalse(status["is_syncing"])
        self.assertEqual(status["last_status"], "ok")
        self.assertIn("17/09/2026", status["last_message"])

    def test_set_enabled_and_interval(self):
        auto_sync.set_enabled(False)
        self.assertFalse(auto_sync.get_status()["enabled"])
        self.assertIsNone(auto_sync.get_status()["next_run"])

        auto_sync.set_enabled(True)
        self.assertTrue(auto_sync.get_status()["enabled"])
        self.assertIsNotNone(auto_sync.get_status()["next_run"])

        auto_sync.set_interval(3600)
        self.assertEqual(auto_sync.get_status()["interval_seconds"], 3600)

        # ห้ามต่ำกว่า 300 วินาที
        auto_sync.set_interval(10)
        self.assertEqual(auto_sync.get_status()["interval_seconds"], 300)

    def test_run_sync_with_mocked_extractor(self):
        mock_run = MagicMock(return_value={
            "started_at": "2026-09-17T12:00:00Z",
            "planned": 2,
            "success": 2,
            "failed": 0,
            "rows": 50,
            "details": [],
        })
        with patch("extractor.run", mock_run):
            res = auto_sync.run_sync()
            self.assertEqual(res["status"], "success")
            self.assertIn("อัปเดตสำเร็จ 2 งวด (50 แถว)", res["message"])
            status = auto_sync.get_status()
            self.assertEqual(status["last_status"], "ok")
            self.assertFalse(status["is_syncing"])
            mock_run.assert_called_once()

    def test_run_sync_lock_prevents_concurrent_runs(self):
        with auto_sync._sync_lock:
            # ขณะ lock กำลังถูกถืออยู่ run_sync ต้องคืน busy ทันที
            res = auto_sync.run_sync()
            self.assertEqual(res["status"], "busy")
            self.assertTrue(res["is_syncing"])

    def test_trigger_sync_background(self):
        with patch.object(auto_sync, "run_sync") as mock_sync:
            res = auto_sync.trigger_sync(background=True)
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["is_syncing"])

    def test_scheduler_lifecycle(self):
        auto_sync.start_scheduler(interval_seconds=3600)
        self.assertTrue(auto_sync._scheduler_running)
        auto_sync.stop_scheduler()
        self.assertFalse(auto_sync._scheduler_running)


if __name__ == "__main__":
    unittest.main()
