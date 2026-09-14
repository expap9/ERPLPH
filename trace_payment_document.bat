@echo off
setlocal
title ERPLPH - Trace One Supplier Invoice
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto :no_python

echo ======================================================================
echo    ERPLPH - Where does one payment document set live in SSB?
echo    Follows one supplier invoice number to its receipts and purchase
echo    order. Remarks and long text are not saved (they may name patients).
echo    Read-only diagnostic. No SQL updates. No ministry submission.
echo ======================================================================
echo.
set "INVOICE=%~1"
if "%INVOICE%"=="" set /p "INVOICE=Supplier invoice number (for example IV-2606063): "
if "%INVOICE%"=="" goto :no_invoice

python -X utf8 scripts\trace_payment_document.py "%INVOICE%"
echo.
echo ======================================================================
echo Trace complete.
echo The result is saved in diagnostics\payment_trace_*.json
echo ======================================================================
pause
exit /b

:no_invoice
echo No invoice number was entered.
pause
exit /b 1

:no_python
echo Python was not found in PATH.
echo Please run this on the hospital machine where Stock5 is installed.
pause
exit /b 1
