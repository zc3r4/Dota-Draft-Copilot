@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Не найдено виртуальное окружение .venv
    echo Сначала запустите install.bat
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m app.main %*
if errorlevel 1 pause
endlocal
