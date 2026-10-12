import re
from datetime import datetime, timedelta
from typing import Dict, Any, List

KNOWN_SERVICES = {
    "api-gateway", "auth-service", "order-service", "inventory-db",
    "payment-service", "audio-scriber", "whisper", "telegram-bot",
    "postgres", "redis", "nginx"
}

LEVEL_SYNONYMS = {
    "CRITICAL": ["critical", "критич", "фатал", "fatal", "авария", "упал"],
    "ERROR": ["error", "ошибк", "ошибка", "сбой", "err", "fail"],
    "WARN": ["warn", "warning", "варнинг", "внимание", "предупреждение"],
    "INFO": ["info", "инфо", "успешно"],
    "DEBUG": ["debug", "дебаг", "отладка"]
}

def parse_natural_search_query(query_str: str) -> Dict[str, Any]:
    if not query_str or not query_str.strip():
        return {}

    text = query_str.strip()
    result: Dict[str, Any] = {
        "levels": [],
        "service": None,
        "trace_id": None,
        "user_id": None,
        "client_ip": None,
        "time_from": None,
        "text_query": ""
    }

    # 1. Поиск IP-адресов
    ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    if ip_match:
        result["client_ip"] = ip_match.group(0)
        text = text.replace(ip_match.group(0), " ")

    # 2. Поиск User ID (user:123, id=45, пользователь 99)
    user_match = re.search(r"(?:user[:=_\s]|пользовател[ья]\s+|id[:=_\s])([a-zA-Z0-9_\-]+)", text, re.IGNORECASE)
    if user_match:
        result["user_id"] = user_match.group(1)
        text = text.replace(user_match.group(0), " ")

    # 3. Поиск Trace ID (trace:abc, trace_id=xxx)
    trace_match = re.search(r"(?:trace[_\-]?id[:=\s]?|trace[:=\s])([a-zA-Z0-9_\-]+)", text, re.IGNORECASE)
    if trace_match:
        result["trace_id"] = trace_match.group(1)
        text = text.replace(trace_match.group(0), " ")

    # 4. Поиск уровней логирования
    for level, syns in LEVEL_SYNONYMS.items():
        for syn in syns:
            if re.search(rf"\b{syn}\w*\b", text, re.IGNORECASE):
                if level not in result["levels"]:
                    result["levels"].append(level)
                text = re.sub(rf"\b{syn}\w*\b", " ", text, flags=re.IGNORECASE)
                break

    # 5. Определение сервиса
    for srv in KNOWN_SERVICES:
        pattern = rf"\b{re.escape(srv)}\b"
        if re.search(pattern, text, re.IGNORECASE):
            result["service"] = srv
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
            break

    # 6. Определение временных рамок
    now = datetime.now()
    if re.search(r"(15\s*мин|15\s*m)", text, re.IGNORECASE):
        result["time_from"] = now - timedelta(minutes=15)
        text = re.sub(r"(за\s*)?15\s*мин\w*", " ", text, flags=re.IGNORECASE)
    elif re.search(r"(1\s*час|час|1\s*h)", text, re.IGNORECASE):
        result["time_from"] = now - timedelta(hours=1)
        text = re.sub(r"(за\s*)?(1\s*)?час\w*", " ", text, flags=re.IGNORECASE)
    elif re.search(r"(24\s*час|сутки|день|сегодня)", text, re.IGNORECASE):
        result["time_from"] = now - timedelta(days=1)
        text = re.sub(r"(за\s*)?(24\s*час\w*|сутки|день|сегодня)", " ", text, flags=re.IGNORECASE)
    elif re.search(r"(недел|7\s*дн)", text, re.IGNORECASE):
        result["time_from"] = now - timedelta(days=7)
        text = re.sub(r"(за\s*)?(недел\w*|7\s*дн\w*)", " ", text, flags=re.IGNORECASE)

    # Очищаем остаточный поисковый текст
    clean_text = " ".join(text.split())
    # Убираем служебные слова-связки
    stop_words = {"в", "на", "с", "по", "где", "покажи", "найди", "логи", "лог", "сообщения", "были"}
    tokens = [w for w in clean_text.split() if w.lower() not in stop_words]
    result["text_query"] = " ".join(tokens)

    return result
