from datetime import datetime, timedelta
from src.db import SessionLocal
from src.models import LogEvent

def seed_test_data():
    session = SessionLocal()
    now = datetime.utcnow()

    logs = [
        # 1. Свежее падение whisper (за последние 5 минут)
        LogEvent(
            service_name="whisper",
            log_level="ERROR",
            raw_message="CUDA out of memory: whisper pipeline crashed during speech decoding",
            trace_id="req-crash-777",
            user_id="user_101",
            client_ip="192.168.1.15",
            timestamp=now - timedelta(minutes=4)
        ),
        # 2. Предупреждение whisper
        LogEvent(
            service_name="whisper",
            log_level="WARNING",
            raw_message="High latency detected in faster-whisper inference: 3500ms",
            trace_id="req-warn-888",
            user_id="user_102",
            client_ip="192.168.1.20",
            timestamp=now - timedelta(minutes=8)
        ),
        # 3. Успешный лог другого сервиса
        LogEvent(
            service_name="auth-service",
            log_level="INFO",
            raw_message="User successfully authenticated via PIN",
            trace_id="req-auth-123",
            user_id="user_101",
            client_ip="192.168.1.15",
            timestamp=now - timedelta(minutes=15)
        ),
        # 4. Критическая ошибка базы данных
        LogEvent(
            service_name="postgres-cluster",
            log_level="CRITICAL",
            raw_message="Fatal: remaining connection slots are reserved (max_connections=100 reached)",
            trace_id="req-db-999",
            user_id="system",
            client_ip="127.0.0.1",
            timestamp=now - timedelta(minutes=2)
        )
    ]

    try:
        session.add_all(logs)
        session.commit()
        print(f"Успешно добавлено {len(logs)} тестовых событий в базу log_rca_db!")
    except Exception as e:
        session.rollback()
        print(f"Ошибка при вставке данных: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    seed_test_data()