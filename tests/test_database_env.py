import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import database


class TestDatabaseEnv(unittest.TestCase):
    def test_parse_dotenv(self):
        sample = """
        # Comment line
        DB_HOST=192.168.1.100
        DB_PORT=1433
        DB_NAME="SSBSTOCK"
        DB_USER='sa'
        DB_PASS=secret123
        EMPTY_VAL=
        """
        parsed = database.parse_dotenv(sample)
        self.assertEqual(parsed["DB_HOST"], "192.168.1.100")
        self.assertEqual(parsed["DB_PORT"], "1433")
        self.assertEqual(parsed["DB_NAME"], "SSBSTOCK")
        self.assertEqual(parsed["DB_USER"], "sa")
        self.assertEqual(parsed["DB_PASS"], "secret123")
        self.assertEqual(parsed["EMPTY_VAL"], "")

    def test_load_config_precedence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_file = tmp / ".env"
            json_file = tmp / "config.json"

            json_file.write_text('{"db_host": "from_json", "db_name": "json_db"}', encoding="utf-8")
            env_file.write_text("DB_HOST=from_env\nDB_PASS=env_secret\n", encoding="utf-8")

            with patch.object(database, "CONFIG_PATH", json_file), \
                 patch.object(database, "ENV_PATH", env_file), \
                 patch.object(database, "_stock5_config", return_value={"db_host": "from_stock5"}), \
                 patch.dict(os.environ, {}, clear=False):
                # .env overrides json and stock5
                cfg = database.load_config()
                self.assertEqual(cfg["db_host"], "from_env")
                self.assertEqual(cfg["db_name"], "json_db")
                self.assertEqual(cfg["db_pass"], "env_secret")

                # os.environ overrides .env
                with patch.dict(os.environ, {"DB_HOST": "from_os_environ"}):
                    cfg2 = database.load_config()
                    self.assertEqual(cfg2["db_host"], "from_os_environ")
                    self.assertEqual(cfg2["db_pass"], "env_secret")


if __name__ == "__main__":
    unittest.main()
