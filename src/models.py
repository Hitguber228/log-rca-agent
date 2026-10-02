from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from src.db import Base

class LogTemplate(Base):
    __tablename__ = "log_templates"

    template_id = Column(Integer, primary_key=True, index=True)
    template_text = Column(Text, unique=True, nullable=False)


class LogEvent(Base):
    __tablename__ = "log_events"

    event_id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("log_templates.template_id"), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    log_level = Column(String(16), nullable=False, index=True)
    service_name = Column(String(64), nullable=False, index=True)
    trace_id = Column(String(64), nullable=True, index=True)  # <-- Новое поле
    raw_message = Column(Text, nullable=False)