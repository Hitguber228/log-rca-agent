@echo off
chcp 65001 > nul
echo ==================================================
echo   SRE Log RCA Platform - Запуск всех сервисов
echo ==================================================

cd /d "%~dp0"

echo [1/4] Запуск PostgreSQL...
"C:\Users\mar4e\Downloads\postgresql-18.4-2-windows-x64-binaries\pgsql\bin\pg_ctl.exe" -D "C:\Users\mar4e\Downloads\postgresql-18.4-2-windows-x64-binaries\pgsql\data" start

echo [2/4] Запуск FastAPI Web Dashboard (порт 8000)...
start "SRE API & Web UI" cmd /k "chcp 65001 > nul && .venv\Scripts\activate && uvicorn api:app --reload --port 8000"

echo [3/4] Запуск Log Watcher (мониторинг live_service.log)...
start "SRE Log Watcher" cmd /k "chcp 65001 > nul && .venv\Scripts\activate && python -m src.watcher"

echo [4/4] Запуск SRE Telegram Bot...
start "SRE Telegram Bot" cmd /k "chcp 65001 > nul && .venv\Scripts\activate && python bot.py"

echo.
echo Все сервисы запущены в отдельных окнах!
echo Веб-интерфейс доступен по адресу: http://localhost:8000
pause
