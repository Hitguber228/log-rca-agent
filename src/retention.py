import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any

from sqlalchemy import func
from src.db import SessionLocal
from src.models import LogEvent, LogTemplate
from src.config import settings


def purge_old_log_events(days: int = 7) -> Dict[str, Any]:
    """
    Удаляет записи логов из таблицы log_events старше указанного количества дней.
    Возвращает статистику очистки.
    """
    session = SessionLocal()
    try:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        # Подсчитываем сколько записей подлежит удалению
        count_to_delete = (
            session.query(func.count(LogEvent.event_id))
            .filter(LogEvent.timestamp < cutoff_date)
            .scalar()
            or 0
        )

        deleted = 0
        if count_to_delete > 0:
            deleted = (
                session.query(LogEvent)
                .filter(LogEvent.timestamp < cutoff_date)
                .delete(synchronize_session=False)
            )
            session.commit()
            print(f"[RETENTION] Удалено {deleted} устаревших записей логов (старше {days} дней, до {cutoff_date.isoformat()}).", flush=True)

        total_remaining = session.query(func.count(LogEvent.event_id)).scalar() or 0

        return {
            "status": "success",
            "days_retention": days,
            "cutoff_date": cutoff_date.isoformat(),
            "deleted_count": deleted,
            "total_remaining": total_remaining,
        }
    except Exception as e:
        session.rollback()
        print(f"[!] [RETENTION] Ошибка при очистке логов: {e}", flush=True)
        return {
            "status": "error",
            "error": str(e),
            "days_retention": days,
            "deleted_count": 0,
        }
    finally:
        session.close()


def rotate_live_log_file(max_size_mb: int = 30, keep_lines: int = 25000) -> bool:
    """
    Проверяет размер активного файла логов live_service.log.
    Если размер превышает max_size_mb, сохраняет последние keep_lines строк,
    предотвращая переполнение диска.
    """
    from src.notifier import get_watched_log_path
    log_path = get_watched_log_path()

    if not log_path or not log_path.exists():
        return False

    try:
        size_bytes = log_path.stat().st_size
        max_bytes = max_size_mb * 1024 * 1024

        if size_bytes > max_bytes:
            print(f"[ROTATION] Файл логов {log_path.name} превысил {max_size_mb}MB ({size_bytes / (1024*1024):.1f}MB). Выполняем ротацию...", flush=True)
            
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            if len(lines) > keep_lines:
                lines_to_keep = lines[-keep_lines:]
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(lines_to_keep)
                print(f"[ROTATION] Ротация завершена. Оставлено {len(lines_to_keep)} последних строк.", flush=True)
                return True
    except Exception as e:
        print(f"[!] [ROTATION] Ошибка при ротации файла логов: {e}", flush=True)

    return False
