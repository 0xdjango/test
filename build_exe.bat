@echo off
REM Builds ZBookFury16Tester.exe (standalone, no Python needed to RUN it).
REM Run this ONCE on any Windows PC that has Python installed (python.org, with "Add to PATH").
REM Keep this .bat in the same folder as zbook_fury16_tester.py.
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo Python not found. Install it from python.org ^(tick "Add python.exe to PATH"^) and run again.
  pause & exit /b 1
)

python -m pip install --upgrade pyinstaller || (echo pip failed & pause & exit /b 1)

REM --uac-admin: exe asks for Administrator automatically on launch
python -m PyInstaller --onefile --uac-admin --name ZBookFury16Tester zbook_fury16_tester.py || (echo build failed & pause & exit /b 1)

copy /y dist\ZBookFury16Tester.exe . >nul
echo.
echo Done. Copy ZBookFury16Tester.exe to a USB stick and run it on the laptop.
pause
