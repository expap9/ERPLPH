@echo off
setlocal
title ERPLPH - Have These Invoices Been Paid?
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Have the known invoices been booked and paid?
echo    Looks up the invoices already collected in diagnostics\ inside the
echo    SSB accounts-payable tables (SSBBACKOFFICE, SSBGL48, SSBWEL).
echo    Names and long text are not saved.
echo    Read-only diagnostic. No SQL updates.
echo ======================================================================
echo.
python -X utf8 scripts\trace_ap_payment.py %*
echo.
echo ======================================================================
echo Check complete.
echo The result is saved in diagnostics\ap_payment_trace_*.json
echo ======================================================================
pause
exit /b

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
