import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from src.db import SessionLocal
from src.models import LogTemplate, LogEvent

LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})\s+"
    r"\[(?P<level>[A-Z]+)\]\s+"
    r"(\[(?:trace_id:\s*)?(?:trace:\s*)?(?P<trace_id>[a-zA-Z0-9_\-]+)\]\s+)?"
    r"\[(?P<service>[a-zA-Z0-9_\-]+)\]\s+"
    r"(?P<message>.*)$"
)

PARAM_REGEXES = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b[0-9a-fA-F-]{36}\b"),
    re.compile(r"0x[0-9a-fA-F]+"),
    re.compile(r"\b\d+\b"),
]


def extract_template(message: str) -> str:
    cleaned = message
    for rx in PARAM_REGEXES:
        cleaned = rx.sub("<*>", cleaned)
    return cleaned


def parse_raw_log(raw_line: str) -> Optional[Dict[str, Any]]:
    line = raw_line.strip()
    if not line:
        return None

    match = LOG_PATTERN.match(line)
    if not match:
        return {
            "timestamp": datetime.now(),
            "log_level": "ERROR" if "error" in line.lower() else "INFO",
            "service_name": "system",
            "trace_id": None,
            "raw_message": line,
            "template_text": extract_template(line)
        }

    data = match.groupdict()
    return {
        "timestamp": datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S"),
        "log_level": data["level"],
        "service_name": data["service"],
        "trace_id": data.get("trace_id"),
        "raw_message": data["message"],
        "template_text": extract_template(data["message"])
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