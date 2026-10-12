import os
import re
import json
import time
import secrets
import aiohttp
from pathlib import Path
from typing import Dict, Any, Optional

ACTIVE_CHAT_ID: Optional[str] = None
ENV_PATH = Path(".env")


def _find_base_dir() -> Path:
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / ".env").exists() or (parent / "app.log").exists():
            return parent
    return Path.cwd()


BASE_DIR = _find_base_dir()
ENV_PATH = BASE_DIR / ".env"


def get_active_chat_id() -> str:
    """Возвращает актуальный Chat ID дежурного, синхронизируясь с файлом .env."""
    global ACTIVE_CHAT_ID
    targets = [ENV_PATH, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                match = re.search(r"^TELEGRAM_CHAT_ID=(.*)$", content, flags=re.MULTILINE)
                if match:
                    val = match.group(1).strip()
                    ACTIVE_CHAT_ID = val
                    return val
            except Exception:
                pass

    if ACTIVE_CHAT_ID:
        return ACTIVE_CHAT_ID

    env_val = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if env_val:
        ACTIVE_CHAT_ID = env_val
        return env_val

    return ""


def save_chat_id_to_env(chat_id: str):
    global ACTIVE_CHAT_ID
    clean_id = str(chat_id).strip()
    ACTIVE_CHAT_ID = clean_id
    os.environ["TELEGRAM_CHAT_ID"] = clean_id

    targets = {ENV_PATH, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"}
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                if re.search(r"^TELEGRAM_CHAT_ID=.*$", content, flags=re.MULTILINE):
                    new_content = re.sub(
                        r"^TELEGRAM_CHAT_ID=.*$",
                        f"TELEGRAM_CHAT_ID={clean_id}",
                        content,
                        flags=re.MULTILINE,
                    )
                else:
                    new_content = content.rstrip() + f"\nTELEGRAM_CHAT_ID={clean_id}\n"
                target.write_text(new_content, encoding="utf-8")
            except Exception:
                pass


def clear_chat_id_from_env():
    global ACTIVE_CHAT_ID
    ACTIVE_CHAT_ID = ""
    os.environ["TELEGRAM_CHAT_ID"] = ""

    targets = {ENV_PATH, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"}
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                new_content = re.sub(
                    r"^TELEGRAM_CHAT_ID=.*$",
                    "TELEGRAM_CHAT_ID=",
                    content,
                    flags=re.MULTILINE,
                )
                target.write_text(new_content, encoding="utf-8")
            except Exception:
                pass


def get_active_bot_token() -> str:
    """Возвращает актуальный токен бота из .env или переменных окружения."""
    targets = [ENV_PATH, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                match = re.search(r"^TELEGRAM_BOT_TOKEN=(.*)$", content, flags=re.MULTILINE)
                if match:
                    val = match.group(1).strip()
                    if val:
                        return val
            except Exception:
                pass
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def get_watched_log_path() -> Path:
    val = os.getenv("WATCH_LOG_PATH", "").strip()
    targets = [ENV_PATH, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                match = re.search(r"^WATCH_LOG_PATH=(.*)$", content, flags=re.MULTILINE)
                if match and match.group(1).strip():
                    val = match.group(1).strip()
                    break
            except Exception:
                pass
    if not val:
        val = str(BASE_DIR / "app.log")
    return Path(val)


def save_watched_log_path(new_path: str):
    clean_val = str(new_path).strip()
    os.environ["WATCH_LOG_PATH"] = clean_val
    targets = {ENV_PATH, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"}
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                if re.search(r"^WATCH_LOG_PATH=.*$", content, flags=re.MULTILINE):
                    new_content = re.sub(
                        r"^WATCH_LOG_PATH=.*$",
                        f"WATCH_LOG_PATH={clean_val}",
                        content,
                        flags=re.MULTILINE,
                    )
                else:
                    new_content = content.rstrip() + f"\nWATCH_LOG_PATH={clean_val}\n"
                target.write_text(new_content, encoding="utf-8")
            except Exception:
                pass


import html


def format_incident_alert(incident: Dict[str, Any], rca_summary: str) -> str:
    service = incident.get("first_affected_service", "Unknown")
    trace_id = incident.get("trace_id", "N/A")
    raw_time = incident.get("incident_start", "Now")
    inc_id = incident.get("incident_id", "New")

    # Форматируем время в HH:MM:SS
    time_display = raw_time.split(" ")[-1] if " " in str(raw_time) else str(raw_time)

    # Достаем первопричину из сырой ошибки инцидента
    root_info = incident.get("root_cause_candidate", {})
    raw_error = root_info.get("raw_message", "")
    clean_error = raw_error.replace("CRITICAL:", "").replace("ERROR:", "").strip()
    if not clean_error and rca_summary:
        clean_error = rca_summary.strip()
    if len(clean_error) > 160:
        clean_error = clean_error[:157] + "..."

    # Экранируем спецсимволы HTML для безопасного парсинга в Telegram
    service_esc = html.escape(str(service))
    trace_id_esc = html.escape(str(trace_id))
    inc_id_esc = html.escape(str(inc_id))
    clean_error_esc = html.escape(str(clean_error))
    time_display_esc = html.escape(str(time_display))

    # Собираем уникальный каскад сервисов без повторов и без "system"
    cascade_flow = incident.get("cascade_flow", [])
    seen = set()
    chain = []
    for step in cascade_flow:
        s_name = str(step.get("service", "")).strip()
        if s_name and s_name not in seen and s_name.lower() != "system":
            seen.add(s_name)
            chain.append(f"<code>{html.escape(s_name)}</code>")
    cascade_str = " -&gt; ".join(chain) if chain else f"<code>{service_esc}</code>"

    # Определяем точечное действие по ошибке
    err_lower = clean_error.lower()
    if "cuda" in err_lower or "memory" in err_lower:
        action = "Освободить VRAM / проверить лимиты воркера."
    elif "timeout" in err_lower:
        action = "Проверить доступность внешнего API / шлюза."
    elif "pool" in err_lower or "connection" in err_lower:
        action = "Перезапустить пул соединений / увеличить max_connections."
    else:
        action = "Проверить логи узла и перезапустить сервис."
    action_esc = html.escape(action)

    return (
        f"🚨 <b>[CRITICAL] SRE INCIDENT #{inc_id_esc}</b>\n"
        f"⏱ <b>Время:</b> {time_display_esc} | 📦 <b>Сервис:</b> <code>{service_esc}</code>\n"
        f"🔗 <b>Trace:</b> <code>{trace_id_esc}</code>\n\n"
        f"💥 <b>Root Cause:</b>\n"
        f"<code>{clean_error_esc}</code>\n\n"
        f"🔀 <b>Каскад:</b> {cascade_str}\n\n"
        f"🛠 <b>Рекомендация:</b> {action_esc}\n\n"
        f"📊 <b>Панель управления:</b> http://127.0.0.1:8000"
    )



async def send_telegram_alert(incident: Dict[str, Any], rca_summary: str) -> bool:
    chat_id = get_active_chat_id()
    if not chat_id:
        print("[!] Оповещение отменено: Telegram chat_id не привязан.")
        return False

    bot_token = get_active_bot_token()
    if not bot_token:
        try:
            from src.config import settings
            bot_token = getattr(settings, "telegram_bot_token", "").strip()
        except Exception:
            pass

    if not bot_token:
        print("[!] Оповещение отменено: TELEGRAM_BOT_TOKEN не задан.")
        return False

    message_text = format_incident_alert(incident, rca_summary)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                else:
                    body = await resp.text()
                    print(f"[!] Сбой отправки Telegram алерта (HTTP {resp.status}): {body}")
                    return False
    except Exception as e:
        print(f"[!] Сетевая ошибка при отправке Telegram алерта: {e}")
        return False

# ----------------- Управление 2FA PIN-кодами и привязкой Telegram ----------------- #

PIN_STORAGE_PATH = BASE_DIR / ".pending_pins.json"
PIN_EXPIRY_SECONDS = 300  # Срок действия PIN-кода — 5 минут
MAX_PIN_ATTEMPTS = 3      # Защита от перебора — максимум 3 попытки


def _load_pending_pins() -> Dict[str, Dict[str, Any]]:
    """Загружает активные PIN-коды из файлового хранилища, удаляя устаревшие."""
    now = time.time()
    valid_pins: Dict[str, Dict[str, Any]] = {}
    if PIN_STORAGE_PATH.exists():
        try:
            content = PIN_STORAGE_PATH.read_text(encoding="utf-8")
            raw_pins = json.loads(content)
            if isinstance(raw_pins, dict):
                for p, meta in raw_pins.items():
                    if isinstance(meta, dict):
                        created = float(meta.get("created_at", 0))
                        if now - created < PIN_EXPIRY_SECONDS:
                            valid_pins[str(p)] = meta
        except Exception:
            valid_pins = {}
    return valid_pins


def _save_pending_pins(pins: Dict[str, Dict[str, Any]]) -> None:
    """Атомарно сохраняет активные PIN-коды для синхронизации между процессами."""
    try:
        temp_file = PIN_STORAGE_PATH.with_suffix(".tmp")
        temp_file.write_text(json.dumps(pins, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(PIN_STORAGE_PATH)
    except Exception:
        try:
            PIN_STORAGE_PATH.write_text(json.dumps(pins, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def create_pending_pin(chat_id: str) -> str:
    """
    Генерирует криптографически стойкий 4-значный PIN-код и сохраняет его
    в общем хранилище с привязкой к указанному Chat ID.
    """
    clean_chat_id = str(chat_id).strip()
    pins = _load_pending_pins()

    # Сбрасываем старые активные коды для этого же chat_id
    pins = {p: meta for p, meta in pins.items() if str(meta.get("chat_id", "")).strip() != clean_chat_id}

    # Генерируем случайный 4-значный PIN через модуль secrets
    pin = f"{secrets.randbelow(9000) + 1000}"
    pins[pin] = {
        "chat_id": clean_chat_id,
        "created_at": time.time(),
        "attempts": 0,
    }
    _save_pending_pins(pins)
    return pin


def get_active_pending_pin() -> Optional[str]:
    """Возвращает последний активный PIN для отображения в веб-интерфейсе."""
    pins = _load_pending_pins()
    if not pins:
        return None
    sorted_items = sorted(pins.items(), key=lambda x: x[1].get("created_at", 0), reverse=True)
    return sorted_items[0][0]


def clear_pending_pins() -> None:
    """Очищает все ожидающие подтверждения PIN-коды."""
    _save_pending_pins({})


def verify_telegram_binding(
    chat_id: Optional[str] = None,
    pin: Optional[str] = None,
    incoming_chat_id: Optional[str] = None,
    entered_pin: Optional[str] = None,
) -> bool:
    """
    Безопасная проверка PIN-кода привязки:
    1. Ищет активный PIN в общем файловом хранилище между процессами.
    2. Проверяет срок действия (TTL 300 сек).
    3. Проверяет лимит неверных попыток ввода (максимум 3).
    4. Строго проверяет совпадение Chat ID.
    5. При успешной проверке сохраняет Chat ID в .env и мгновенно уничтожает PIN (одноразовый токен).
    6. При несовпадении или ошибке возвращает False.
    """
    clean_chat_id = (str(chat_id) if chat_id is not None else str(incoming_chat_id or "")).strip()
    clean_pin = (str(pin) if pin is not None else str(entered_pin or "")).strip()

    if not clean_pin or not clean_chat_id:
        return False

    pins = _load_pending_pins()
    record = pins.get(clean_pin)

    if not record:
        return False

    now = time.time()
    created_at = float(record.get("created_at", 0))
    if now - created_at >= PIN_EXPIRY_SECONDS:
        pins.pop(clean_pin, None)
        _save_pending_pins(pins)
        return False

    # Защита от перебора: увеличиваем счётчик попыток
    attempts = int(record.get("attempts", 0)) + 1
    record["attempts"] = attempts

    expected_cid = str(record.get("chat_id", "")).strip()
    if expected_cid and expected_cid != clean_chat_id:
        if attempts >= MAX_PIN_ATTEMPTS:
            pins.pop(clean_pin, None)  # Сжигаем скомпрометированный PIN
        else:
            pins[clean_pin] = record
        _save_pending_pins(pins)
        return False

    # Успешная авторизация: связываем chat_id и сжигаем одноразовый PIN
    save_chat_id_to_env(clean_chat_id)
    pins.pop(clean_pin, None)
    _save_pending_pins(pins)
    return True