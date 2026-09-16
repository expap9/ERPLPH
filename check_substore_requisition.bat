@echo off
setlocal
title ERPLPH - Sub-store Requisition Check
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Where does a sub-store requisition live in SSB?
echo    Looks up the requisition slips collected in diagnostics\, checks
echo    whether sub-stores hold their own stock, and lists which stores
echo    moved goods in the last 12 months.
echo    Read-only diagnostic. No SQL updates.
echo ======================================================================
echo.
python -X utf8 scripts\probe_substore_requisition.py %*
echo.
echo ======================================================================
echo Check complete.
echo The result is saved in diagnostics\substore_requisition_*.json
echo ======================================================================
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
