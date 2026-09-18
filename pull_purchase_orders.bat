@echo off
setlocal
title ERPLPH - Pull Purchase Orders (SKPO)
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Pull real purchase orders (SKPO/SKPODTL) into warehouse
echo    Read-only from the hospital database. No SQL updates.
echo    Vendor payment status (AP) is NOT included yet - only PO amount,
echo    approval date and delivery dates.
echo ======================================================================
echo.
python -X utf8 scripts\pull_purchase_orders.py %*
echo.
echo ======================================================================
echo Done. Refresh the "khlang yai and chatsu" page to see real PO data.
echo ======================================================================
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the machine where ERPLPH/Stock5 is installed.
pause
exit /b 1
