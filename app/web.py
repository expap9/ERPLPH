"""หน้าเว็บของ ERPLPH — หน้าแรกคือ "สถานะการตัดขายของคลังย่อย"

กติกาหน้าจอที่ตกลงกับผู้ใช้ 16 กันยายน 2569
    1. หนึ่งหน้า = หนึ่งคำถามที่คนถามจริงทุกวัน
    2. ทุกตัวเลขกดลงไปหาเอกสารต้นทางได้
    3. ทุกตัวเลขบอกความน่าเชื่อถือของตัวเอง — โดยเฉพาะหน้านี้ที่ต้นทางเป็นสำเนาซึ่ง
       คัดลอกวันละครั้ง ถ้าไม่บอก คนจะเข้าใจว่าเป็นข้อมูลสด
    4. ค้นหาเดียวครอบจักรวาล (ยังไม่ทำในรอบนี้)

อ่านจากฐานข้อมูลของ ERPLPH เท่านั้น ไม่ต่อฐานข้อมูลโรงพยาบาลตอนเปิดหน้า เพื่อให้หน้าเปิดเร็ว
และไม่เพิ่มภาระให้เครื่องต้นทาง (ดู docs/system_design.md ข้อ 2)
"""
from pathlib import Path
import sqlite3
import sys

from flask import Flask, render_template, request

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "app"))

import cut_status  # noqa: E402
import warehouse_db  # noqa: E402

WINDOW_CHOICES = (30, 90, 180)
DEFAULT_WINDOW = 90

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


def open_warehouse():
    """เปิดฐานข้อมูลของ ERPLPH แบบอ่านอย่างเดียว — หน้าเว็บไม่มีเหตุผลต้องเขียนอะไรลงคลังข้อมูล"""
    path = Path(warehouse_db.DB_PATH)
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


@app.route("/")
@app.route("/cut-status")
def cut_status_page():
    try:
        window = int(request.args.get("days", DEFAULT_WINDOW))
    except ValueError:
        window = DEFAULT_WINDOW
    if window not in WINDOW_CHOICES:
        window = DEFAULT_WINDOW

    connection = open_warehouse()
    if connection is None:
        return render_template("cut_status.html", report=None, window=window,
                               windows=WINDOW_CHOICES,
                               problem="ยังไม่มีคลังข้อมูลของ ERPLPH บนเครื่องนี้ "
                                       "(warehouse_data/erplph.db) ให้ดึงข้อมูลก่อน")
    try:
        report = cut_status.collect(connection, window_days=window)
    finally:
        connection.close()
    return render_template("cut_status.html", report=report, window=window,
                           windows=WINDOW_CHOICES, problem=None,
                           warning_days=cut_status.MIN_BEHIND_DAYS)


def main():
    app.run(host="127.0.0.1", port=8090, debug=False)


if __name__ == "__main__":
    main()
