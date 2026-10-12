import os
import pytest
from starlette.testclient import TestClient

from api import app
from src.db import Base, engine, SessionLocal
from src.models import LogEvent

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Инициализирует таблицы базы данных перед тестами."""
    Base.metadata.create_all(bind=engine)
    yield

@pytest.fixture
def client():
    """Тестовый HTTP-клиент FastAPI."""
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture
def db_session():
    """Изолированная сессия для проверки данных в тестах."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
