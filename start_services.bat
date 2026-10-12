@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ==================================================
echo   SRE Log RCA Platform - Запуск всех сервисов
echo ==================================================

cd /d "%~dp0"

echo [1/4] Поиск и запуск базы данных...
set "PG_FOUND=0"

:: 1. Проверяем, слушает ли уже порт 5432 (PostgreSQL уже работает)
netstat -ano | findstr /R /C:":5432 .*LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] PostgreSQL уже запущен и слушает порт 5432.
    set "PG_FOUND=1"
    goto :db_ready
)

:: 2. Поиск pg_ctl в системной переменной PATH
where pg_ctl.exe >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where pg_ctl.exe') do (
        set "PG_BIN=%%i"
        set "PG_DIR=!PG_BIN!\..\..\data"
        if exist "!PG_DIR!" (
            echo   [OK] Найден PostgreSQL в PATH. Запуск...
            "!PG_BIN!" -D "!PG_DIR!" start
            set "PG_FOUND=1"
            goto :db_ready
        )
    )
)

:: 3. Поиск в стандартных системных директориях Program Files
for /d %%D in ("%ProgramFiles%\PostgreSQL\*") do (
    if exist "%%D\bin\pg_ctl.exe" if exist "%%D\data" (
        echo   [OK] Найден установленный PostgreSQL: "%%D". Запуск...
        "%%D\bin\pg_ctl.exe" -D "%%D\data" start
        set "PG_FOUND=1"
        goto :db_ready
    )
)

:: 4. Поиск в локальном профиле пользователя (портативные сборки в Downloads / Programs)
for /d %%D in ("%USERPROFILE%\Downloads\postgresql*\pgsql") do (
    if exist "%%D\bin\pg_ctl.exe" if exist "%%D\data" (
        echo   [OK] Найден локальный PostgreSQL: "%%D". Запуск...
        "%%D\bin\pg_ctl.exe" -D "%%D\data" start
        set "PG_FOUND=1"
        goto :db_ready
    )
)

:: 5. Если PostgreSQL не найден - система автоматически работает через SQLite
if !PG_FOUND! equ 0 (
    echo   [INFO] Локальный PostgreSQL не обнаружен.
    echo   [INFO] Платформа будет использовать встроенную базу SQLite (sqlite:///./logs.db).
)

:db_ready
echo.

:: Определение виртуального окружения Python
set "VENV_CMD="
if exist ".venv\Scripts\activate.bat" (
    set "VENV_CMD=call .venv\Scripts\activate.bat && "
) else if exist "venv\Scripts\activate.bat" (
    set "VENV_CMD=call venv\Scripts\activate.bat && "
)

echo [2/4] Запуск FastAPI Web Dashboard (порт 8000)...
start "SRE API & Web UI" cmd /k "chcp 65001 > nul && %VENV_CMD% uvicorn api:app --reload --port 8000"

echo [3/4] Запуск Log Watcher (мониторинг live_service.log)...
start "SRE Log Watcher" cmd /k "chcp 65001 > nul && %VENV_CMD% python -m src.watcher"

echo [4/4] Запуск SRE Telegram Bot...
start "SRE Telegram Bot" cmd /k "chcp 65001 > nul && %VENV_CMD% python bot.py"

echo.
echo ==================================================
echo Все сервисы платформы запущены в отдельных окнах!
echo Панель управления доступна по адресу: http://localhost:8000
echo ==================================================
pause
