@echo off
setlocal
title ERPLPH - Pull 5 Years Warehouse Data
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Pull 5 Years Warehouse Data Into Database
echo    ดึงข้อมูลย้อนหลัง 5 ปีงบประมาณ (ตั้งแต่ปีงบ 2564 ถึงปัจจุบัน)
echo    อ่านอย่างเดียว (Read-Only) จาก SQL Server ไม่กระทบข้อมูลต้นทาง
echo ======================================================================
echo.
python -X utf8 scripts\pull_warehouse_data.py --years-back 5 %*
echo.
pause
exit /b

:no_python
echo Python was not found in PATH.
pause
exit /b 1
