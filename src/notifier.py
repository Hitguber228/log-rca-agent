import os
import re
import json
import random
import time
import httpx
from typing import Dict, Any, Tuple
from pathlib import Path
from src.config import settings

# Корневая директория проекта
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
BIND_REQUEST_FILE = BASE_DIR / ".active_bind.json"
BLOCK_FILE = BASE_DIR / ".blocked_users.json"

ACTIVE_CHAT_ID: str = getattr(settings, "telegram_chat_id", "") or ""


# ----------------- Работа с персистентным файлом блокировок ----------------- #

def _load_block_data() -> Dict[str, Any]:
    """Считывает актуальные данные блокировок и попыток из файла."""
    if not BLOCK_FILE.exists():
        return {}
    try:
        return json.loads(BLOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_block_data(data: Dict[str, Any]):
    """Записывает данные блокировок на диск."""
    try:
        BLOCK_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[!] Ошибка сохранения файла блокировок: {e}", flush=True)


# ----------------- Управление Telegram Chat ID ----------------- #

def get_active_chat_id() -> str:
    """Всегда читает актуальное значение напрямую из .env файла и синхронизирует память."""
    global ACTIVE_CHAT_ID
    if ENV_PATH.exists():
        try:
            content = ENV_PATH.read_text(encoding="utf-8")
            match = re.search(r"^TELEGRAM_CHAT_ID=(.*)$", content, flags=re.MULTILINE)
            ACTIVE_CHAT_ID = match.group(1).strip() if match else ""
        except Exception:
            ACTIVE_CHAT_ID = ""
    else:
        ACTIVE_CHAT_ID = ""
    return str(ACTIVE_CHAT_ID).strip()


def set_active_chat_id(chat_id: str):
    """Устанавливает и сохраняет Chat ID."""
    save_chat_id_to_env(str(chat_id).strip())


def save_chat_id_to_env(new_chat_id: str):
    """Персистентно записывает ID в .env и сразу обновляет рантайм-переменную."""
    global ACTIVE_CHAT_ID
    ACTIVE_CHAT_ID = str(new_chat_id).strip()

    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"TELEGRAM_CHAT_ID={ACTIVE_CHAT_ID}\n", encoding="utf-8")
        print(f"💾 [CONFIG] Chat ID записан в новый .env: '{ACTIVE_CHAT_ID}'", flush=True)
        return

    content = ENV_PATH.read_text(encoding="utf-8")
    if re.search(r"^TELEGRAM_CHAT_ID=.*", content, flags=re.MULTILINE):
        new_content = re.sub(
            r"^TELEGRAM_CHAT_ID=.*",
            f"TELEGRAM_CHAT_ID={ACTIVE_CHAT_ID}",
            content,
            flags=re.MULTILINE,
        )
    else:
        new_content = content.rstrip() + f"\nTELEGRAM_CHAT_ID={ACTIVE_CHAT_ID}\n"

    ENV_PATH.write_text(new_content, encoding="utf-8")
    print(f"💾 [CONFIG] Chat ID обновлен в рантайме и .env: '{ACTIVE_CHAT_ID}'", flush=True)


# ----------------- Заявки на привязку (Web -> Telegram) ----------------- #

def create_bind_request(chat_id: str) -> str:
    """Создает одноразовую заявку на привязку для указанного ID."""
    clean_id = str(chat_id).strip()
    pin = f"{random.randint(1000, 9999)}"
    data = {
        "chat_id": clean_id,
        "pin": pin,
        "created_at": time.time(),
        "expires_at": time.time() + 600,  # PIN действует 10 минут
    }
    try:
        BIND_REQUEST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"🔐 [AUTH] Создана заявка: ID={clean_id} -> PIN={pin}", flush=True)
    except Exception as e:
        print(f"[!] Ошибка сохранения заявки на привязку: {e}", flush=True)
    return pin


def get_bind_request_data() -> Dict[str, Any]:
    """Возвращает текущую активную заявку на привязку."""
    if not BIND_REQUEST_FILE.exists():
        return {}
    try:
        data = json.loads(BIND_REQUEST_FILE.read_text(encoding="utf-8"))
        if data.get("expires_at", 0) > time.time():
            return data
    except Exception:
        pass
    return {}


# ----------------- Валидация и проверка PIN (/bind) ----------------- #

def verify_telegram_binding(incoming_chat_id: str, entered_pin: str) -> Tuple[bool, str]:
    """
    Строгая верификация:
    1. Проверка персистентного бана на 5 минут (хранится на диске).
    2. Проверка наличия активной заявки из веб-консоли.
    3. Проверка совпадения ID отправителя и ID из заявки.
    4. Проверка PIN-кода.
    """
    incoming_chat_id = str(incoming_chat_id).strip()
    entered_pin = str(entered_pin).strip()
    now = time.time()

    # 1. Проверяем блокировку на диске
    blocks = _load_block_data()
    user_state = blocks.get(incoming_chat_id, {"attempts": 0, "blocked_until": 0.0})

    if user_state.get("blocked_until", 0.0) > now:
        left = int(user_state["blocked_until"] - now)
        return False, f"⛔ Доступ временно заблокирован из-за перебора паролей! Подождите {left} сек."

    # 2. Проверяем наличие активной заявки из веба
    req = get_bind_request_data()
    if not req:
        return False, "⚠️ Нет активной заявки из веб-интерфейса. Сначала укажите свой ID в панели."

    target_id = str(req.get("chat_id", "")).strip()
    expected_pin = str(req.get("pin", "")).strip()

    # 3. Проверка соответствия Telegram ID
    if incoming_chat_id != target_id:
        return (
            False,
            f"🚫 Ошибка безопасности! Заявка в вебе создана для ID <code>{target_id}</code>, "
            f"а вы отправляете команду с аккаунта <code>{incoming_chat_id}</code>.",
        )

    # 4. Проверка PIN-кода
    if entered_pin == expected_pin:
        # Успешная авторизация — сбрасываем счетчик ошибок и сжигаем PIN
        blocks.pop(incoming_chat_id, None)
        _save_block_data(blocks)

        save_chat_id_to_env(incoming_chat_id)
        try:
            BIND_REQUEST_FILE.unlink(missing_ok=True)
        except Exception:
            pass

        return True, "✅ Верификация успешно завершена! Ваш Telegram ID подтверждён и назначен дежурным."

    # 5. Обработка неверного ввода
    attempts = user_state.get("attempts", 0) + 1
    if attempts >= 3:
        user_state["blocked_until"] = now + 300  # Бан ровно на 5 минут (300 секунд)
        user_state["attempts"] = 0
        blocks[incoming_chat_id] = user_state
        _save_block_data(blocks)
        return False, "🚫 3 неверных ввода. Доступ заблокирован ровно на 5 минут!"

    user_state["attempts"] = attempts
    blocks[incoming_chat_id] = user_state
    _save_block_data(blocks)
    remains = 3 - attempts
    return False, f"❌ Неверный PIN-код. Осталось попыток: {remains}."


# ----------------- Отправка оповещений с жесткой защитой ----------------- #

async def send_telegram_alert(incident: Dict[str, Any], ai_summary: str) -> bool:
    """Строгая отправка: блокируется на корню, если статус NOT BOUND."""
    target_id = get_active_chat_id()
    token = getattr(settings, "telegram_bot_token", None)

    if not target_id:
        print("⛔ [BLOCKED] Попытка отправки алерта отклонена: Telegram дежурного не привязан (NOT BOUND).", flush=True)
        return False

    if not token:
        print("⛔ [BLOCKED] Попытка отправки алерта отклонена: отсутствует TELEGRAM_BOT_TOKEN.", flush=True)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": f"🚨 *ИНЦИДЕНТ: {incident.get('first_affected_service', 'service')}*\n"
                f"Trace ID: `{incident.get('trace_id', 'unknown')}`\n\n"
                f"{ai_summary}",
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            print(f"[!] Telegram API вернул код {resp.status_code}: {resp.text}", flush=True)
            return False
    except Exception as e:
        print(f"[!] Ошибка сетевого запроса к Telegram API: {e}", flush=True)
        return False