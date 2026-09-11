@echo off
setlocal
title ERPLPH - Where Are Payments, Assets And Contracts
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Find payment status, durable assets and service contracts
echo    Lists databases, tables, column names and row counts, plus
echo    12-month totals per store and category. No row-level data.
echo    Read-only diagnostic. No SQL updates. No ministry submission.
echo ======================================================================
echo.
python -X utf8 scripts\probe_procurement_finance.py
echo.
echo ======================================================================
echo Survey complete.
echo The result is saved in diagnostics\procurement_finance_*.json
echo ======================================================================
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
