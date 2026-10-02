import os
import time
import asyncio
from pathlib import Path
from typing import Dict, Any, List

from src.parser import parse_raw_log
from src.agent import generate_rca_summary
from src.notifier import send_telegram_alert, get_active_chat_id

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE_PATH = BASE_DIR / "app.log"


def follow_log_file(file_path: Path):
    """Генератор, читающий строки из файла в реальном времени (tail -f) без блокировки записи в Windows."""
    if not file_path.exists():
        file_path.touch()

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        # Переходим в конец файла при старте
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line.strip()


def run_watcher():
    """Основной рабочий цикл мониторинга логов и генерации RCA."""
    print(f"👀 [WATCHER] Мониторинг запущен для файла: {LOG_FILE_PATH}", flush=True)

    buffer_errors: List[Dict[str, Any]] = []

    for raw_line in follow_log_file(LOG_FILE_PATH):
        if not raw_line:
            continue

        event = parse_raw_log(raw_line)
        if not event:
            continue

        # Сопоставляем точные ключи, которые отдает parse_raw_log
        level = str(event.get("log_level") or event.get("level", "")).upper()
        service = event.get("service_name") or event.get("service", "unknown")
        raw_msg = event.get("raw_message") or event.get("message", "")

        # Фильтруем события уровня сбоя
        if level in ("ERROR", "CRITICAL"):
            current_time = time.time()
            buffer_errors.append(event)

            print(
                f"⚠️ [WATCHER] Зафиксирована ошибка в сервисе [{service}]: {raw_msg[:60]}...",
                flush=True,
            )

            # Формируем структуру инцидента для генератора отчета
            incident_data = {
                "incident_id": int(current_time),
                "incident_start": str(event.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))),
                "first_affected_service": service,
                "root_cause_candidate": {
                    "service": service,
                    "level": level,
                    "raw_message": raw_msg,
                },
                "chain_length": len(buffer_errors),
                "cascade_flow": [
                    {
                        "timestamp": str(e.get("timestamp", "")),
                        "level": str(e.get("log_level") or e.get("level", "ERROR")),
                        "service": e.get("service_name") or e.get("service", "unknown"),
                        "message": e.get("raw_message") or e.get("message", ""),
                    }
                    for e in buffer_errors
                ],
            }

            # ----------------- СТРОГАЯ ПРОВЕРКА NOT BOUND ----------------- #
            active_chat_id = get_active_chat_id()
            if not active_chat_id:
                print(
                    "⛔ [WATCHER BLOCKED] Инцидент обнаружен, но отправка алерта отменена: "
                    "Telegram дежурного не привязан (статус NOT BOUND).",
                    flush=True,
                )
                continue

            print(f"🤖 [WATCHER] Генерация RCA отчета -> отправка на Chat ID: {active_chat_id}", flush=True)

            try:
                report = asyncio.run(generate_rca_summary(incident_data))
                success = asyncio.run(send_telegram_alert(incident_data, report))
                if not success:
                    print("[!] [WATCHER] Не удалось доставить оповещение в Telegram.", flush=True)
                else:
                    print("✅ [WATCHER] Алерт успешно доставлен дежурному.", flush=True)
            except Exception as ex:
                print(f"[!] [WATCHER] Ошибка при обработке инцидента: {ex}", flush=True)


if __name__ == "__main__":
    try:
        run_watcher()
    except KeyboardInterrupt:
        print("\n🛑 [WATCHER] Мониторинг остановлен.", flush=True)