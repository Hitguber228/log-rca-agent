from datetime import datetime, timezone
from typing import Optional, Dict, Any, Set
from src.db import SessionLocal
from src.models import UserIncidentState


def get_incident_state(incident_key: str) -> Optional[UserIncidentState]:
    """Возвращает запись состояния инцидента по ID или trace_id."""
    key = str(incident_key).strip()
    session = SessionLocal()
    try:
        return session.query(UserIncidentState).filter(UserIncidentState.trace_id == key).first()
    finally:
        session.close()


def set_incident_resolved(incident_key: str, is_resolved: bool, user_id: str = "default_user") -> bool:
    """Устанавливает статус инцидента (resolved / active) в базе данных."""
    key = str(incident_key).strip()
    session = SessionLocal()
    try:
        record = session.query(UserIncidentState).filter(UserIncidentState.trace_id == key).first()
        now = datetime.now(timezone.utc)
        if not record:
            record = UserIncidentState(
                user_id=user_id,
                trace_id=key,
                status="resolved" if is_resolved else "active",
                resolved_at=now if is_resolved else None,
                updated_at=now,
            )
            session.add(record)
        else:
            record.status = "resolved" if is_resolved else "active"
            record.resolved_at = now if is_resolved else None
            record.updated_at = now
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"[!] Ошибка сохранения статуса инцидента: {e}")
        return False
    finally:
        session.close()


def is_incident_resolved(incident_key: str) -> bool:
    """Проверяет, помечен ли инцидент как решённый."""
    key = str(incident_key).strip()
    session = SessionLocal()
    try:
        record = session.query(UserIncidentState).filter(UserIncidentState.trace_id == key).first()
        return record.status == "resolved" if record else False
    finally:
        session.close()


def set_cached_report(incident_key: str, report: str, user_id: str = "default_user") -> None:
    """Сохраняет сгенерированный RCA-отчет в базу данных."""
    key = str(incident_key).strip()
    session = SessionLocal()
    try:
        record = session.query(UserIncidentState).filter(UserIncidentState.trace_id == key).first()
        now = datetime.now(timezone.utc)
        if not record:
            record = UserIncidentState(
                user_id=user_id,
                trace_id=key,
                status="active",
                cached_report=report,
                updated_at=now,
            )
            session.add(record)
        else:
            record.cached_report = report
            record.updated_at = now
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[!] Ошибка кэширования отчета RCA: {e}")
    finally:
        session.close()


def get_cached_report(incident_key: str) -> Optional[str]:
    """Получает кэшированный RCA-отчет из базы данных."""
    key = str(incident_key).strip()
    session = SessionLocal()
    try:
        record = session.query(UserIncidentState).filter(UserIncidentState.trace_id == key).first()
        return record.cached_report if record else None
    finally:
        session.close()


def clear_cached_report(incident_key: str) -> None:
    """Очищает кэш RCA-отчета для инцидента."""
    key = str(incident_key).strip()
    session = SessionLocal()
    try:
        records = session.query(UserIncidentState).filter(UserIncidentState.trace_id == key).all()
        for record in records:
            record.cached_report = None
            record.updated_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def get_all_cached_reports() -> Dict[str, str]:
    """Возвращает словарь {incident_key: cached_report} для быстрого поиска."""
    session = SessionLocal()
    try:
        records = session.query(UserIncidentState).filter(UserIncidentState.cached_report.isnot(None)).all()
        return {r.trace_id: r.cached_report for r in records if r.cached_report}
    finally:
        session.close()


def get_all_resolved_incidents() -> Set[str]:
    """Возвращает множество идентификаторов решённых инцидентов."""
    session = SessionLocal()
    try:
        records = session.query(UserIncidentState.trace_id).filter(UserIncidentState.status == "resolved").all()
        return {str(r[0]) for r in records if r[0]}
    finally:
        session.close()
