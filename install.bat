@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python не найден в PATH. Установите Python 3.12 с python.org
    echo и включите опцию "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение .venv ...
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Не удалось создать .venv. Проверьте установку Python 3.12.
        pause
        exit /b 1
    )
)

echo Устанавливаю зависимости ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Установка зависимостей не удалась.
    pause
    exit /b 1
)

echo Готово. Запускайте run.bat
pause
