@echo off
setlocal
title ERPLPH - Pull Warehouse Data Into The Database
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Pull every warehouse into the local database
echo    First run loads the whole fiscal window; later runs load only
echo    the periods that are missing plus the current month.
echo    Read-only against the hospital server. No ministry submission.
echo ======================================================================
echo.
python -X utf8 scripts\pull_warehouse_data.py %*
echo.
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
