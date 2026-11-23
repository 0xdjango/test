@echo off
REM Launcher for the standalone exe: keeps the window open and passes options through.
REM Usage: double-click, or  run_tests.bat --quick
cd /d "%~dp0"
if not exist ZBookFury16Tester.exe (
  echo ZBookFury16Tester.exe not found next to this file.
  pause & exit /b 1
)
ZBookFury16Tester.exe %*
echo.
pause
