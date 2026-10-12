import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from typing import Dict, Any, List, Optional
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, func, or_, and_
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Строка подключения к базе данных (SQLite по умолчанию, либо PostgreSQL через DATABASE_URL)
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "sqlite:///./logs.db"
)

def _init_engine():
    global DATABASE_URL
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    if not url.startswith("sqlite"):
        try:
            kw = {
                "echo": False,
                "pool_size": 20,
                "max_overflow": 10,
                "pool_pre_ping": True,
                "pool_recycle": 1800,
                "pool_timeout": 3,
                "connect_args": {"connect_timeout": 2}
            }
            eng = create_engine(url, **kw)
            with eng.connect():
                pass
            return eng
        except Exception as e:
            print(f"[DB] PostgreSQL недоступен ({e}). Автоматическое переключение на SQLite: sqlite:///./logs.db", flush=True)
            DATABASE_URL = "sqlite:///./logs.db"

    return create_engine("sqlite:///./logs.db", echo=False, connect_args={"check_same_thread": False})


engine = _init_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    service = Column(String(100), nullable=False, index=True)
    level = Column(String(20), nullable=False, index=True)
    message = Column(Text, nullable=False)
    status_code = Column(Integer, nullable=True, index=True)
    user_id = Column(String(100), nullable=True, index=True)
    trace_id = Column(String(100), nullable=True, index=True)
    client_ip = Column(String(50), nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "service": self.service,
            "level": self.level,
            "message": self.message,
            "status_code": self.status_code,
            "user_id": self.user_id,
            "trace_id": self.trace_id,
            "client_ip": self.client_ip
        }


def init_db():
    """Создаёт таблицы и индексы в PostgreSQL при запуске."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Генератор сессии для FastAPI Depends(get_db)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def filter_logs(db: Session, filters: Dict[str, Any], limit: int = 100) -> List[Log]:
    """
    Принимает результат LogQueryFormalizer.formalize() и выполняет фильтрацию через SQLAlchemy.
    """
    query = db.query(Log)

    # 1. Фильтр по сервисам (с приведением к нижнему регистру)
    if filters.get("services"):
        services_lower = [s.lower() for s in filters["services"]]
        query = query.filter(func.lower(Log.service).in_(services_lower))

    # 2. Фильтр по уровням (ERROR, CRITICAL, WARN и т.д.)
    if filters.get("levels"):
        levels_upper = [l.upper() for l in filters["levels"]]
        query = query.filter(func.upper(Log.level).in_(levels_upper))

    # 3. Временное окно
    if filters.get("time_from"):
        t_from = filters["time_from"]
        query = query.filter(Log.timestamp >= t_from)

    if filters.get("time_to"):
        t_to = filters["time_to"]
        query = query.filter(Log.timestamp <= t_to)

    # 4. User ID
    if filters.get("user_id"):
        query = query.filter(Log.user_id == str(filters["user_id"]))

    # 5. Client IP
    if filters.get("client_ip"):
        query = query.filter(Log.client_ip == str(filters["client_ip"]))

    # 6. Trace ID
    if filters.get("trace_id"):
        query = query.filter(Log.trace_id == str(filters["trace_id"]))

    # 7. HTTP-статусы
    if filters.get("http_statuses"):
        int_statuses = [int(s) for s in filters["http_statuses"] if str(s).isdigit()]
        if int_statuses:
            query = query.filter(Log.status_code.in_(int_statuses))

    # 8. Исключения (-nginx, !debug)
    for exc in filters.get("exclude_terms", []):
        exc_pattern = f"%{exc}%"
        query = query.filter(
            and_(
                ~Log.message.ilike(exc_pattern),
                ~Log.service.ilike(exc_pattern)
            )
        )

    # 9. Шаблоны аномалий (crash, oom, timeout) и точные фразы
    text_conditions = []
    for pat in filters.get("text_patterns", []):
        text_conditions.append(Log.message.ilike(f"%{pat}%"))

    for phrase in filters.get("exact_phrases", []):
        text_conditions.append(Log.message.ilike(f"%{phrase}%"))

    if text_conditions:
        query = query.filter(or_(*text_conditions))

    # Сортировка по времени от свежих к старым с лимитом
    return query.order_by(Log.timestamp.desc()).limit(limit).all()