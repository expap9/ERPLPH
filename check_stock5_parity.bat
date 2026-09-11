@echo off
setlocal
title ERPLPH - Check Main Store Against Stock5
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Compare main store 2 with the data Stock5 is showing
echo    Both use the same query and the same checking engine, so every
echo    closed month must match line by line. Reads files only.
echo ======================================================================
echo.
python -X utf8 scripts\check_stock5_parity.py %*
echo.
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
