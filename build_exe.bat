@echo off
setlocal
cd /d "%~dp0"
title StudyPDF - Windows Build

echo [1/4] Checking Python...
where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found. Install Python 3.10-3.13 from python.org and enable Add Python to PATH.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  if errorlevel 1 goto :failed
)

echo [2/4] Installing dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed

echo [3/4] Building StudyPDF desktop app...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q StudyPDF.spec StudyPDFServer.spec 2>nul
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name StudyPDF --collect-all PySide6 run.py
if errorlevel 1 goto :failed

echo [4/4] Building StudyPDF server...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name StudyPDFServer run_server.py
if errorlevel 1 goto :failed

echo.
echo SUCCESS!
echo Desktop app: dist\StudyPDF\StudyPDF.exe (keep the whole dist\StudyPDF folder together)
echo Server:      dist\StudyPDFServer.exe
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. Read the error above, then try again with a supported Python version and internet access.
pause
exit /b 1
