"""การเชื่อมต่อฐานข้อมูลโรงพยาบาลของ ERPLPH

โปรเจกต์นี้ยืม "เครื่องคำนวณ" จาก Stock5 (ดู stock5_engine.py) แต่การตั้งค่า
ฐานข้อมูลเป็นเรื่องของการติดตั้ง ไม่ใช่สูตรคำนวณ จึงมีของตัวเอง เพื่อให้
- รายการโมดูลที่ยืมจาก Stock5 แคบอยู่ที่ 5 ตัวตามที่ประกาศไว้จริง ๆ
- ชี้ไปฐานข้อมูลคนละตัวได้ถ้าต้องการ โดยไม่กระทบ Stock5

ถ้ายังไม่มี config.json ของตัวเอง จะอ่านของ Stock5 เป็นค่าตั้งต้นให้ เพื่อไม่ต้อง
กรอกข้อมูลเดิมซ้ำตอนติดตั้ง — อ่านอย่างเดียว ไม่เขียนทับไฟล์ของ Stock5
"""
import json
import os
from pathlib import Path
import re

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "db_host": "",
    "db_port": "1433",
    "db_name": "SSBSTOCK",
    "db_user": "",
    "db_pass": "",
    "hosp_code": "",
}


ENV_PATH = BASE_DIR / ".env"

ENV_KEY_MAP = {
    "DB_HOST": "db_host",
    "DB_PORT": "db_port",
    "DB_NAME": "db_name",
    "DB_USER": "db_user",
    "DB_PASS": "db_pass",
    "HOSP_CODE": "hosp_code",
}


def parse_dotenv(content: str) -> dict[str, str]:
    """แยกคู่ตัวแปรจากข้อความรูปแบบ .env รองรับเครื่องหมายคำพูดและการตัดช่องว่าง"""
    result: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key:
            result[key] = val
    return result


def _load_env_file() -> dict[str, str]:
    """อ่านค่าจากไฟล์ .env ของ ERPLPH ถ้ามี"""
    if not ENV_PATH.is_file():
        return {}
    try:
        content = ENV_PATH.read_text(encoding="utf-8")
        raw_env = parse_dotenv(content)
        mapped = {}
        for env_key, val in raw_env.items():
            cfg_key = ENV_KEY_MAP.get(env_key.upper()) or env_key.lower()
            if cfg_key in DEFAULT_CONFIG and val != "":
                mapped[cfg_key] = val
        return mapped
    except OSError:
        return {}


def _stock5_config() -> dict:
    """ค่าตั้งต้นจาก Stock5 ถ้ามี — อ่านอย่างเดียว"""
    import stock5_engine

    path = stock5_engine.stock5_home() / "config.json"
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {}


def load_config() -> dict:
    """โหลดการตั้งค่าฐานข้อมูลโรงพยาบาล

    ลำดับความสำคัญ (จากต่ำไปสูง):
    1. ค่าเริ่มต้น (DEFAULT_CONFIG)
    2. ค่ายืมจาก Stock5 (ถ้ามี)
    3. config.json ของ ERPLPH
    4. ไฟล์ .env ของ ERPLPH
    5. Environment Variables ของระบบ (os.environ)
    """
    config = dict(DEFAULT_CONFIG)
    # 2. ยืมจาก Stock5
    config.update({
        key: value for key, value in _stock5_config().items()
        if key in DEFAULT_CONFIG and value
    })
    # 3. config.json ของ ERPLPH
    if CONFIG_PATH.is_file():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as stream:
                config.update(json.load(stream))
        except (OSError, ValueError):
            pass
    # 4. ไฟล์ .env ของ ERPLPH
    config.update(_load_env_file())
    # 5. Environment Variables ของระบบ
    for env_key, cfg_key in ENV_KEY_MAP.items():
        val = os.environ.get(env_key)
        if val is not None and val != "":
            config[cfg_key] = val
    return config


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)


def pick_odbc_driver() -> str:
    import pyodbc

    installed = [name for name in pyodbc.drivers() if "SQL Server" in name]
    for preferred in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server",
                      "ODBC Driver 13 for SQL Server", "SQL Server Native Client 11.0"):
        if preferred in installed:
            return preferred
    return installed[0] if installed else "SQL Server"


def build_connection_string(config: dict) -> tuple[str, str]:
    driver = pick_odbc_driver()
    parts = (
        f"DRIVER={{{driver}}};"
        f"SERVER={config['db_host']},{config.get('db_port', '1433')};"
        f"DATABASE={config['db_name']};"
        f"UID={config['db_user']};PWD={config['db_pass']}"
    )
    # ไดรเวอร์ 18 บังคับเข้ารหัสเป็นค่าเริ่มต้น ซึ่ง SQL Server รุ่นเก่ามักไม่มีใบรับรอง
    if driver.startswith("ODBC Driver 1"):
        parts += ";TrustServerCertificate=yes"
    return parts, driver


def connect(config: dict | None = None, timeout: int = 15):
    import pyodbc

    config = config or load_config()
    if not config.get("db_host"):
        raise ValueError("ยังไม่ได้ตั้งค่าฐานข้อมูล กรอก db_host ใน config.json ของ ERPLPH")
    connection_string, _ = build_connection_string(config)
    return pyodbc.connect(connection_string, timeout=timeout)


_SQLSTATE_MESSAGES = {
    "08001": "ติดต่อ SQL Server ไม่สำเร็จ ตรวจเครือข่ายโรงพยาบาลและการตั้งค่าฐานข้อมูล",
    "28000": "เข้าสู่ระบบฐานข้อมูลไม่สำเร็จ ตรวจบัญชีผู้ใช้โดยไม่ส่งรหัสผ่านมากับรายงาน",
    "42000": "อ่านโครงสร้างไม่สำเร็จ โปรดให้ผู้ดูแลตรวจสิทธิ์และชื่อฐานข้อมูล",
    "42S22": "ไม่พบคอลัมน์ที่คำสั่งอ้างถึง ตรวจชื่อคอลัมน์กับฐานข้อมูลรุ่นนี้",
    "42S02": "ไม่พบตารางที่คำสั่งอ้างถึง ตรวจชื่อตารางกับฐานข้อมูลรุ่นนี้",
    "HYT00": "หมดเวลารอการตอบกลับจากฐานข้อมูล",
    "HYT01": "หมดเวลารอการเชื่อมต่อฐานข้อมูล",
}


def error_summary(exc: Exception) -> dict:
    """สรุปข้อผิดพลาดโดยไม่คัดข้อความจากไดรเวอร์มาด้วย

    ข้อความจากไดรเวอร์อาจมีสตริงการเชื่อมต่อซึ่งมีรหัสผ่านอยู่ รายงานที่ส่งให้
    ผู้พัฒนาจึงเก็บแค่รหัสสถานะ แล้วแปลเป็นคำอธิบายที่เตรียมไว้
    """
    value = str(exc.args[0]) if exc.args else ""
    state = value if re.fullmatch(r"[A-Z0-9]{5}", value) else None
    return {
        "type": type(exc).__name__,
        "sqlstate": state,
        "message": _SQLSTATE_MESSAGES.get(
            state, "ตรวจสอบไม่สำเร็จ เก็บเฉพาะรหัสข้อผิดพลาดโดยไม่เก็บข้อความจากไดรเวอร์"),
    }


def describe_config() -> dict:
    """ค่าที่ตั้งไว้ โดยไม่เปิดเผยรหัสผ่าน"""
    config = load_config()
    env_file = ENV_PATH.is_file()
    return {
        "db_host": config.get("db_host", ""),
        "db_port": config.get("db_port", ""),
        "db_name": config.get("db_name", ""),
        "db_user": config.get("db_user", ""),
        "password_set": bool(config.get("db_pass")),
        "own_config": CONFIG_PATH.is_file(),
        "env_config": env_file,
        "inherited_from_stock5": not CONFIG_PATH.is_file() and not env_file and bool(_stock5_config()),
    }
