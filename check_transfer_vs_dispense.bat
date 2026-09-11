@echo off
setlocal
title ERPLPH - Separate Patient Dispensing from Internal Transfers
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - How the database separates dispensing from transfers
echo    Needed before totalling every warehouse: a transfer counted on
echo    both sides would double the hospital-wide figures.
echo    Read-only diagnostic. No SQL updates. No ministry submission.
echo ======================================================================
echo.
python -X utf8 scripts\probe_transfer_vs_dispense.py
echo.
echo ======================================================================
echo Survey complete.
echo Please send diagnostics\transfer_vs_dispense_*.json to the developer.
echo ======================================================================
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
