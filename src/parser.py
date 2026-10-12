"""
Модуль синтаксического анализа, нормализации и шаблонизации логов.

Архитектурная роль:
1. Универсальный разбор форматов:
   - Автоматически распознает структурированный JSON (Logstash, Bunyan, Serilog).
   - Поддерживает традиционный скобочный формат вида [TIMESTAMP] [LEVEL] [SERVICE] [TRACE_ID].
   - Устойчив к смене порядка полей и нестандартным строкам.
2. Алгоритм шаблонизации (Log Templating):
   - Маскирует динамические переменные (IP-адреса, UUIDv4, шестнадцатеричные адреса памяти, ID)
     заменяя их на маркер `<*>`.
   - Позволяет сжимать миллионы однотипных записей в небольшое число уникальных сигнатур
     (по принципу алгоритма Drain3), снижая нагрузку на реляционную БД SQLite.
"""

import re
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from src.db import SessionLocal
from src.models import LogTemplate, LogEvent

# Регулярные выражения для выделения динамических параметров (IP, UUID, HEX, Числа)
PARAM_REGEXES = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b[0-9a-fA-F-]{36}\b"),
    re.compile(r"0x[0-9a-fA-F]+"),
    re.compile(r"\b\d+\b"),
]


def extract_template(message: str) -> str:
    """
    Извлекает инвариантный шаблон сообщения, заменяя динамические значения на токен <*>.
    Пример: 'User 142 logged in from 192.168.1.1' -> 'User <*> logged in from <*>'
    """
    cleaned = message
    for rx in PARAM_REGEXES:
        cleaned = rx.sub("<*>", cleaned)
    return cleaned


def parse_raw_log(raw_line: str) -> Optional[Dict[str, Any]]:
    """
    Универсальный парсер строк логов:
    - Поддерживает JSON формат.
    - Поддерживает произвольный порядок блоков в скобках: [LEVEL], [SERVICE], [TRACE_ID], [trace_id=...], [USER_ID].
    - Корректно определяет уровень лога даже при нестандартных сообщениях.
    """
    line = raw_line.strip()
    if not line:
        return None

    # 1. Попытка распарсить как JSON
    if line.startswith("{") and line.endswith("}"):
        try:
            data = json.loads(line)
            ts_str = data.get("timestamp") or data.get("time") or data.get("@timestamp")
            dt = datetime.now()
            if ts_str:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                    try:
                        dt = datetime.strptime(ts_str.replace("Z", ""), fmt)
                        break
                    except Exception:
                        pass

            level = str(data.get("level") or data.get("log_level") or "INFO").upper()
            service = str(data.get("service") or data.get("service_name") or "unknown")
            trace = data.get("trace_id") or data.get("trace")
            msg = str(data.get("message") or data.get("raw_message") or line)
            user_id = data.get("user_id")
            client_ip = data.get("client_ip")

            return {
                "timestamp": dt,
                "log_level": level,
                "service_name": service,
                "trace_id": trace,
                "user_id": user_id,
                "client_ip": client_ip,
                "raw_message": msg,
                "template_text": extract_template(msg)
            }
        except Exception:
            pass

    # 2. Парсинг стандартных текстовых логов с таймстемпом и блоками [ ... ]
    ts_match = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?)\s+(.*)$", line)
    if ts_match:
        ts_str, rest = ts_match.group(1), ts_match.group(2)
        try:
            clean_ts = ts_str.replace("T", " ").split(".")[0].split(",")[0]
            dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            dt = datetime.now()

        # Извлекаем все начальные блоки в скобках [ ... ]
        tokens = []
        msg_start = 0
        while True:
            m = re.match(r"\s*\[([^\]]+)\]", rest[msg_start:])
            if m:
                tokens.append(m.group(1).strip())
                msg_start += m.end()
            else:
                break

        msg = rest[msg_start:].strip()

        level = None
        service = None
        trace_id = None
        user_id = None
        client_ip = None

        KNOWN_LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"}

        for token in tokens:
            token_upper = token.upper()
            # Уровень логирования
            if token_upper in KNOWN_LEVELS and level is None:
                level = token_upper
            # Trace ID: [trace_id:xxx], [trace_id=xxx], [trace:xxx]
            elif re.match(r"^(?:trace_id|trace)[:=]\s*(.+)$", token, re.IGNORECASE):
                m_tr = re.match(r"^(?:trace_id|trace)[:=]\s*(.+)$", token, re.IGNORECASE)
                trace_id = m_tr.group(1).strip()
            # Service: [service:xxx], [service=xxx]
            elif re.match(r"^service[:=]\s*(.+)$", token, re.IGNORECASE):
                m_srv = re.match(r"^service[:=]\s*(.+)$", token, re.IGNORECASE)
                service = m_srv.group(1).strip()
            # User ID: [user:xxx], [user_id:xxx], [user_id=xxx]
            elif re.match(r"^(?:user|user_id)[:=]\s*(.+)$", token, re.IGNORECASE):
                m_usr = re.match(r"^(?:user|user_id)[:=]\s*(.+)$", token, re.IGNORECASE)
                user_id = m_usr.group(1).strip()
            # Client IP: [ip:xxx], [ip=xxx]
            elif re.match(r"^(?:ip|client_ip)[:=]\s*(.+)$", token, re.IGNORECASE):
                m_ip = re.match(r"^(?:ip|client_ip)[:=]\s*(.+)$", token, re.IGNORECASE)
                client_ip = m_ip.group(1).strip()
            # Имя сервиса напрямую: [postgres-cluster], [whisper], [api-gateway]
            elif service is None and not token.lower().startswith("trace"):
                service = token
            elif trace_id is None and token.lower().startswith("trace"):
                trace_id = token

        # Дефолтный поиск уровня, если не был в скобках
        if not level:
            for lvl in ("CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG"):
                if lvl in rest.upper():
                    level = "WARNING" if lvl in ("WARN", "WARNING") else ("CRITICAL" if lvl in ("CRITICAL", "FATAL") else lvl)
                    break
            if not level:
                level = "INFO"

        if not service:
            service = "system"

        if not msg:
            msg = line

        return {
            "timestamp": dt,
            "log_level": level,
            "service_name": service,
            "trace_id": trace_id,
            "user_id": user_id,
            "client_ip": client_ip,
            "raw_message": msg,
            "template_text": extract_template(msg)
        }

    # 3. Резервный разбор для произвольных неструктурированных строк
    line_upper = line.upper()
    fallback_level = "INFO"
    for lvl in ("CRITICAL", "FATAL", "ERROR", "WARNING", "WARN"):
        if lvl in line_upper:
            fallback_level = "CRITICAL" if lvl in ("CRITICAL", "FATAL") else ("WARNING" if lvl in ("WARN", "WARNING") else "ERROR")
            break

    return {
        "timestamp": datetime.now(),
        "log_level": fallback_level,
        "service_name": "system",
        "trace_id": None,
        "user_id": None,
        "client_ip": None,
        "raw_message": line,
        "template_text": extract_template(line)
    }


def ingest_log_batch(raw_lines: List[str], batch_size: int = 500) -> int:
    """Пакетное сохранение логов с кешированием шаблонов в одной транзакции."""
    parsed_items = [parse_raw_log(l) for l in raw_lines]
    parsed_items = [p for p in parsed_items if p is not None]

    if not parsed_items:
        return 0

    session = SessionLocal()
    try:
        # Кешируем существующие шаблоны, чтобы не делать лишних запросов
        unique_texts = {p["template_text"] for p in parsed_items}
        existing_templates = (
            session.query(LogTemplate)
            .filter(LogTemplate.template_text.in_(unique_texts))
            .all()
        )
        template_map = {t.template_text: t.template_id for t in existing_templates}

        # Создаем новые шаблоны пачкой
        new_templates = []
        for text in unique_texts:
            if text not in template_map:
                tmpl = LogTemplate(template_text=text)
                new_templates.append(tmpl)

        if new_templates:
            session.add_all(new_templates)
            session.commit()
            for tmpl in new_templates:
                template_map[tmpl.template_text] = tmpl.template_id

        # Создаем и вставляем события пачками
        events_to_insert = [
            LogEvent(
                template_id=template_map[p["template_text"]],
                timestamp=p["timestamp"],
                log_level=p["log_level"],
                service_name=p["service_name"],
                trace_id=p["trace_id"],
                user_id=p.get("user_id"),
                client_ip=p.get("client_ip"),
                raw_message=p["raw_message"]
            )
            for p in parsed_items
        ]

        for i in range(0, len(events_to_insert), batch_size):
            session.bulk_save_objects(events_to_insert[i:i + batch_size])
            session.commit()

        return len(events_to_insert)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ingest_log_line(raw_line: str) -> Optional[LogEvent]:
    """Для обратной совместимости при отправке одиночной строки."""
    res = ingest_log_batch([raw_line])
    return res > 0

class LogParser:
    """Адаптер для интеграции с API консоли инцидентов."""
    def parse_file(self, filepath: str) -> List[Dict[str, Any]]:
        events = []
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parsed = parse_raw_log(line)
                if parsed:
                    events.append(parsed)
        return events