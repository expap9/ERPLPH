@echo off
setlocal
title ERPLPH - Web
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - open the web pages
echo    Reads only ERPLPH's own database (warehouse_data\erplph.db).
echo    It does not connect to the hospital database while serving pages.
echo.
echo    Open in a browser:  http://127.0.0.1:8090/   (screens like Stock5)
echo    Press Ctrl+C in this window to stop.
echo ======================================================================
echo.
python -X utf8 app\web.py
pause
exit /b

:no_python
echo Python was not found in PATH.
pause
exit /b 1
