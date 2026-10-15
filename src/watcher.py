"""
Модуль фонового мониторинга и агрегации логов в реальном времени (Watcher Daemon).

Архитектурная роль:
1. Непрерывно читает отслеживаемый файл (эмуляция tail -f без блокировки CPU).
2. Нормализует входящие строки через универсальный парсер и передает их в веб-интерфейс.
3. Реализует алгоритм скользящего окна (Debounce Window): каскадные сбои микросервисов
   происходят с разницей в доли секунды (например, упал Postgres -> завис пул -> Gateway отдал 504).
   Дебаунс собирает всю цепочку в единый структурированный инцидент, предотвращая лавину алертов.
4. Выполняет автоматический запуск анализа первопричин (RCA) и доставку уведомления в Telegram.
"""

import os
import sys
import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

# Принудительно включаем UTF-8 для стандартных потоков на Windows,
# чтобы избежать искажения символов в PowerShell и командной строке
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.parser import parse_raw_log
from src.agent import generate_rca_summary
from src.notifier import send_telegram_alert, get_active_chat_id, get_watched_log_path, are_telegram_alerts_enabled
from src.db import SessionLocal
from src.models import LogEvent
from src.config import settings
from src.retention import purge_old_log_events, rotate_live_log_file


def notify_api_broadcast(event_type: str, data: Dict[str, Any]):
    """
    Отправляет внутреннее HTTP-уведомление в FastAPI для мгновенного бродкаста
    события подключенным браузерам через WebSocket (без перезагрузки страницы).
    """
    try:
        import urllib.request
        import json
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/internal/broadcast",
            data=json.dumps({"event_type": event_type, "data": data}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=1.0)
    except Exception:
        # Если API временно недоступен или перезапускается, не прерываем работу сборщика
        pass



def get_alert_rules():
    """
    Считывает актуальные правила фильтрации из .env и переменных окружения.
    Динамическое чтение позволяет изменять пороги алертов в настройках без рестарта воркера.
    """
    env_vars = {}
    base_dir = Path(__file__).resolve().parent.parent
    targets = [Path(".env"), Path.cwd() / ".env", base_dir / ".env"]
    for target in targets:
        if target.exists():
            try:
                for line in target.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip().upper()] = v.strip().strip("'\"")
                break
            except Exception:
                pass

    min_level = (env_vars.get("ALERT_MIN_LEVEL") or os.getenv("ALERT_MIN_LEVEL") or getattr(settings, "alert_min_level", "ERROR")).upper()
    cascade_only_str = env_vars.get("ALERT_CASCADE_ONLY") or os.getenv("ALERT_CASCADE_ONLY") or str(getattr(settings, "alert_cascade_only", False))
    cascade_only = cascade_only_str.lower() in ("true", "1", "yes")

    try:
        min_errors = int(env_vars.get("ALERT_MIN_ERRORS") or os.getenv("ALERT_MIN_ERRORS") or getattr(settings, "alert_min_errors", 1))
    except Exception:
        min_errors = 1

    try:
        debounce_sec = float(env_vars.get("ALERT_DEBOUNCE_SECONDS") or os.getenv("ALERT_DEBOUNCE_SECONDS") or getattr(settings, "alert_debounce_seconds", 1.5))
    except Exception:
        debounce_sec = 1.5

    return {
        "min_level": min_level,
        "cascade_only": cascade_only,
        "min_errors": min_errors,
        "debounce_sec": debounce_sec
    }


def parse_event_timestamp(ts_val) -> datetime:
    """Безопасный парсинг даты лога."""
    if isinstance(ts_val, datetime):
        return ts_val
    if isinstance(ts_val, str) and ts_val.strip():
        # Пробуем форматы: ISO, стандартный лог YYYY-MM-DD HH:MM:SS, и т.д.
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S,%f",
            "%Y-%m-%d %H:%M:%S.%f",
        ):
            try:
                clean_ts = ts_val.replace("Z", "").split("+")[0].strip()
                return datetime.strptime(clean_ts, fmt)
            except Exception:
                pass
        try:
            return datetime.fromisoformat(ts_val.replace("Z", ""))
        except Exception:
            pass
    return datetime.now()


def save_cascade_to_db(events: List[Dict[str, Any]]) -> int:
    """Сохраняет события цепочки сбоя в SQLite и возвращает event_id корневой ошибки."""
    session = SessionLocal()
    first_event_id = None
    try:
        for ev in events:
            lvl = str(ev.get("log_level") or ev.get("level") or "ERROR").upper()
            srv = ev.get("service_name") or ev.get("service") or "unknown"
            msg = ev.get("raw_message") or ev.get("message") or ""
            tr_id = ev.get("trace_id") or "N/A"
            dt = parse_event_timestamp(ev.get("timestamp"))

            record = LogEvent(
                timestamp=dt,
                service_name=srv,
                log_level=lvl,
                trace_id=tr_id,
                raw_message=msg,
                user_id=ev.get("user_id"),
                client_ip=ev.get("client_ip"),
            )
            session.add(record)
            session.flush() # Получаем присвоенный автоинкрементный ID
            
            if first_event_id is None:
                first_event_id = record.event_id

        session.commit()
        return first_event_id or int(time.time())
    except Exception as e:
        session.rollback()
        print(f"[!] [WATCHER] Ошибка сохранения инцидента в базу SQLite: {e}", flush=True)
        return int(time.time())
    finally:
        session.close()


def run_watcher():
    print("[WATCHER] Сервис мониторинга логов запущен...", flush=True)

    # При запуске выполняем очистку по политике хранения
    retention_days = int(getattr(settings, "log_retention_days", 7))
    purge_old_log_events(days=retention_days)
    rotate_live_log_file(max_size_mb=30)
    last_retention_check: float = time.time()

    # Хранилище активных инцидентов, сгруппированных по trace_id (или по сервису, если trace_id отсутствует)
    pending_incidents: Dict[str, Dict[str, Any]] = {}
    current_path: Path = get_watched_log_path()
    file_handle = None

    while True:
        # 1. Проверяем смену отслеживаемого файла
        new_path = get_watched_log_path()
        if new_path != current_path or file_handle is None:
            if file_handle:
                try:
                    file_handle.close()
                except Exception:
                    pass
            current_path = new_path
            print(f"[WATCHER] Мониторинг переключен на файл: {current_path}", flush=True)

            if not current_path.exists():
                try:
                    current_path.parent.mkdir(parents=True, exist_ok=True)
                    current_path.touch()
                except Exception:
                    pass

            try:
                file_handle = open(current_path, "r", encoding="utf-8", errors="ignore")
                file_handle.seek(0, os.SEEK_END)
            except Exception as e:
                print(f"[!] Ошибка открытия {current_path}: {e}", flush=True)
                file_handle = None
                time.sleep(2)
                continue

        # 2. Построчное чтение логов
        line = file_handle.readline()
        now = time.time()

        if line:
            raw_line = line.strip()
            if raw_line:
                event = parse_raw_log(raw_line)
                if event:
                    level = str(event.get("log_level") or event.get("level", "")).upper()
                    service = event.get("service_name") or event.get("service", "unknown")
                    raw_msg = event.get("raw_message") or event.get("message", "")

                    # Транслируем каждый новый лог в веб-интерфейс
                    notify_api_broadcast("new_log", {
                        "timestamp": str(event.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        "service_name": service,
                        "log_level": level,
                        "raw_message": raw_msg,
                        "trace_id": event.get("trace_id"),
                    })

                    rules = get_alert_rules()
                    if rules["min_level"] == "CRITICAL":
                        alertable_levels = ("CRITICAL",)
                    elif rules["min_level"] in ("WARN", "WARNING"):
                        alertable_levels = ("WARN", "WARNING", "ERROR", "CRITICAL")
                    else:
                        alertable_levels = ("ERROR", "CRITICAL")

                    if level in alertable_levels:
                        tr_id = str(event.get("trace_id") or "").strip()
                        # Группировка в первую очередь по trace_id, если он есть
                        if tr_id and tr_id.upper() != "N/A" and tr_id.lower() != "none":
                            group_key = f"trace:{tr_id}"
                        else:
                            group_key = f"service:{service}"

                        if group_key not in pending_incidents:
                            pending_incidents[group_key] = {
                                "group_key": group_key,
                                "incident_start": str(event.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
                                "first_affected_service": service,
                                "trace_id": tr_id if (tr_id and tr_id.upper() != "N/A") else "N/A",
                                "root_cause_candidate": {
                                    "service": service,
                                    "level": level,
                                    "raw_message": raw_msg,
                                },
                                "buffer_errors": [event],
                                "last_error_time": now,
                            }
                            print(
                                f"[WATCHER] Новый инцидент [{group_key}] в [{service}]: {raw_msg[:70]}...",
                                flush=True,
                            )
                        else:
                            pending_incidents[group_key]["buffer_errors"].append(event)
                            pending_incidents[group_key]["last_error_time"] = now
                            print(
                                f"[WATCHER] Развитие каскада [{group_key}] -> [{service}]: {raw_msg[:70]}...",
                                flush=True,
                            )

        # 3. Дебаунс (Debounce Window):
        # В микросервисной архитектуре сбой распространяется волной (Postgres -> Redis -> Gateway).
        # Проверяем таймер затишья для каждого инцидента независимо: если за последние debounce_sec
        # секунд новых ошибок по этой цепочке не поступало, значит каскад сформировался полностью.
        # Фиксируем единый инцидент в базе и готовим один качественный алерт вместо спама десятком сообщений.
        rules = get_alert_rules()
        ready_keys = [
            k for k, inc in pending_incidents.items()
            if (now - inc["last_error_time"] >= rules["debounce_sec"])
        ]

        for k in ready_keys:
            inc = pending_incidents.pop(k)
            buffer_errors = inc["buffer_errors"]
            incident_data = {
                "incident_start": inc["incident_start"],
                "first_affected_service": inc["first_affected_service"],
                "trace_id": inc["trace_id"],
                "root_cause_candidate": inc["root_cause_candidate"],
            }

            # Сохраняем цепочку событий в базу данных
            saved_event_id = save_cascade_to_db(buffer_errors)
            incident_data["incident_id"] = saved_event_id
            incident_data["chain_length"] = len(buffer_errors)
            incident_data["cascade_flow"] = [
                {
                    "timestamp": str(e.get("timestamp", "")),
                    "level": str(e.get("log_level") or e.get("level", "ERROR")),
                    "service": e.get("service_name") or e.get("service", "unknown"),
                    "message": e.get("raw_message") or e.get("message", ""),
                }
                for e in buffer_errors
            ]

            print(f"[WATCHER] Инцидент #{saved_event_id} ({k}) зафиксирован в базе данных.", flush=True)

            # Мгновенно оповещаем дашборд через WebSocket
            notify_api_broadcast("incident_created", {
                "incident_id": saved_event_id,
                "trace_id": incident_data.get("trace_id"),
                "service": incident_data.get("first_affected_service"),
                "chain_length": len(buffer_errors)
            })

            # Фильтрация оповещений по правилам алертинга
            distinct_services = {e.get("service_name") or e.get("service") for e in buffer_errors if (e.get("service_name") or e.get("service"))}
            should_alert_tg = True
            filter_reason = ""

            if len(buffer_errors) < rules["min_errors"]:
                should_alert_tg = False
                filter_reason = f"ошибок {len(buffer_errors)} < минимального порога {rules['min_errors']}"
            elif rules["cascade_only"] and len(distinct_services) < 2:
                should_alert_tg = False
                filter_reason = f"затронут только 1 сервис ({', '.join(distinct_services) or 'unknown'}), активно правило 'Только каскадные'"

            # Проверка авторизации дежурного инженера и правил алертинга
            active_chat_id = get_active_chat_id()
            if not active_chat_id:
                print("[WATCHER BLOCKED] Telegram не привязан (дежурный инженер не авторизован). Алерт отменен.", flush=True)
            elif not are_telegram_alerts_enabled():
                print("[WATCHER BLOCKED] Оповещения в Telegram отключены в конфигурации (TELEGRAM_ALERTS_ENABLED=false).", flush=True)
            elif not should_alert_tg:
                print(f"[WATCHER] Алерт в Telegram отфильтрован правилами: {filter_reason}.", flush=True)
            else:
                print(f"[WATCHER] Отправка алерта #{saved_event_id} авторизованному дежурному (Chat ID: {active_chat_id})", flush=True)
                try:
                    report = asyncio.run(generate_rca_summary(incident_data))

                    # Кэшируем отчёт в БД, чтобы веб-панель сразу видела готовый RCA-отчёт
                    try:
                        from src.incident_store import set_cached_report
                        set_cached_report(str(saved_event_id), report)
                        if incident_data.get("trace_id"):
                            set_cached_report(str(incident_data["trace_id"]), report)
                    except Exception as ex:
                        print(f"[!] [WATCHER] Ошибка сохранения RCA в кэш: {ex}", flush=True)

                    # Оповещаем WebSocket о готовом RCA
                    notify_api_broadcast("rca_report_ready", {
                        "incident_id": str(saved_event_id),
                        "trace_id": incident_data.get("trace_id"),
                        "report": report
                    })

                    success = asyncio.run(send_telegram_alert(incident_data, report))
                    if success:
                        print("[WATCHER] Алерт успешно доставлен в Telegram.", flush=True)
                except Exception as ex:
                    print(f"[!] [WATCHER] Ошибка генерации/доставки алерта: {ex}", flush=True)


        # Периодическая проверка политики хранения каждые 4 часа
        if now - last_retention_check >= 4 * 3600:
            last_retention_check = now
            purge_old_log_events(days=retention_days)
            rotate_live_log_file(max_size_mb=30)

        if not line:
            time.sleep(0.3)


if __name__ == "__main__":
    try:
        run_watcher()
    except KeyboardInterrupt:
        print("\n[WATCHER] Мониторинг остановлен.", flush=True)