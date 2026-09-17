@echo off
setlocal
title ERPLPH - Build Web Screens
cd /d "%~dp0frontend"
where node >nul 2>nul
if errorlevel 1 goto :no_node

echo ======================================================================
echo    ERPLPH - build the Angular screens (copied from Stock5)
echo    Output: app\angular_dist   (served by run_web.bat)
echo    Only needed after changing files in frontend\src
echo ======================================================================
echo.
if not exist node_modules (
  echo node_modules not found. Linking to Stock5's installed packages...
  mklink /J node_modules "%~dp0..\Stock5\frontend-angular\node_modules"
)
node node_modules\@angular\cli\bin\ng.js build
echo.
pause
exit /b

:no_node
echo Node.js was not found in PATH. The built screens in app\angular_dist still work without it.
pause
exit /b 1
