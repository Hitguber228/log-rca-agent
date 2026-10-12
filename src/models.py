import html
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index, TypeDecorator
from src.db import Base


class SafeText(TypeDecorator):
    """
    Автоматически экранирует символы (эмодзи и т.д.), которые не поддерживаются
    локальными кодировками баз данных (например, PostgreSQL WIN1251),
    и восстанавливает их при чтении.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or not isinstance(value, str):
            return value
        try:
            value.encode("cp1251")
            return value
        except (UnicodeEncodeError, UnicodeError):
            return value.encode("cp1251", errors="xmlcharrefreplace").decode("cp1251")

    def process_result_value(self, value, dialect):
        if value is None or not isinstance(value, str):
            return value
        return html.unescape(value)


class LogTemplate(Base):
    __tablename__ = "log_templates"

    template_id = Column(Integer, primary_key=True, index=True)
    template_text = Column(SafeText, unique=True, nullable=False)


class LogEvent(Base):
    __tablename__ = "log_events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    service_name = Column(String(64), index=True, nullable=False)
    log_level = Column(String(16), index=True, nullable=False)
    trace_id = Column(String(128), index=True, nullable=True)
    raw_message = Column(SafeText, nullable=False)
    template_id = Column(Integer, ForeignKey("log_templates.template_id"), nullable=True)
    user_id = Column(String(100), index=True, nullable=True)
    client_ip = Column(String(50), index=True, nullable=True)

    # Составные B-Tree индексы
    __table_args__ = (
        Index("ix_log_level_timestamp", "log_level", "timestamp"),
        Index("ix_trace_id_timestamp", "trace_id", "timestamp"),
        Index("ix_service_timestamp", "service_name", "timestamp"),
        Index("ix_service_level_ts", "service_name", "log_level", "timestamp"),
        Index("ix_level_ts_event_id", "log_level", "timestamp", "event_id"),
    )


class UserIncidentState(Base):
    __tablename__ = "user_incident_states"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    trace_id = Column(String(255), nullable=False, index=True)
    status = Column(String(16), default="active", nullable=False)  # "active" | "resolved"
    cached_report = Column(SafeText, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
