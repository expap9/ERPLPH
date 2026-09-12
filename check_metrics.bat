@echo off
setlocal
title ERPLPH - Executive Metrics
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Stock value, monthly use, months of stock, expiry,
echo    dormant stock and items about to run out.
echo    Reads the local ERPLPH database only. The hospital server is
echo    never touched by this report.
echo ======================================================================
echo.
python -X utf8 scripts\show_metrics.py %*
echo.
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
