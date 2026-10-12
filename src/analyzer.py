from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy import func
from src.db import SessionLocal
from src.models import LogEvent, LogTemplate
from src.config import settings

from datetime import datetime, timedelta, timezone



def is_valid_incident(cluster: list) -> bool:
    """Фильтрация шума: отсеивает одиночные или некритичные предупреждения."""
    if not cluster:
        return False

    def _unwrap(item):
        return item[0] if isinstance(item, (tuple, list)) else item

    levels = [
        getattr(_unwrap(it), 'log_level', None) or getattr(_unwrap(it), 'level', '') 
        for it in cluster
    ]
    services = {
        getattr(_unwrap(it), 'service_name', None) or getattr(_unwrap(it), 'service', 'unknown') 
        for it in cluster
    }

    # 1. Наличие ERROR, CRITICAL или FATAL — безусловный инцидент
    if any(lvl in ("ERROR", "CRITICAL", "FATAL") for lvl in levels):
        return True

    # 2. Задето более одного микросервиса — каскадный сбой
    if len(services) > 1:
        return True

    # 3. Накопление 3+ предупреждений — признак системной деградации
    if len(cluster) >= 3:
        return True

    return False


def build_incident_dict(cluster: list, incident_id: int, total_count: int, trace_id: str = None) -> Dict[str, Any]:
    root_tuple = cluster[0]
    root_event = root_tuple[0] if isinstance(root_tuple, (tuple, list)) else root_tuple
    root_template = root_tuple[1] if isinstance(root_tuple, (tuple, list)) and len(root_tuple) > 1 else ""

    service_name = getattr(root_event, "service_name", None) or getattr(root_event, "service", "unknown-service")
    log_lvl = getattr(root_event, "log_level", None) or getattr(root_event, "level", "CRITICAL")
    raw_msg = getattr(root_event, "raw_message", None) or getattr(root_event, "message", "Неизвестная ошибка")

    incident_summary = {
        "incident_id": incident_id,
        "trace_id": trace_id or getattr(root_event, "trace_id", "N/A"),
        "status": "incident_detected",
        "incident_start": root_event.timestamp.isoformat() if hasattr(root_event, "timestamp") and root_event.timestamp else "2026-10-02T10:00:00",
        "first_affected_service": service_name,
        "root_cause_candidate": {
            "service": service_name,
            "level": log_lvl,
            "template": root_template,
            "raw_message": raw_msg
        },
        "chain_length": len(cluster),
        "total_incidents_detected": total_count,
        "cascade_flow": []
    }

    seen = set()
    for item in cluster:
        ev = item[0] if isinstance(item, (tuple, list)) else item
        s_name = getattr(ev, "service_name", None) or getattr(ev, "service", "unknown")
        s_lvl = getattr(ev, "log_level", None) or getattr(ev, "level", "ERROR")
        s_msg = getattr(ev, "raw_message", None) or getattr(ev, "message", "")

        sig = (getattr(ev, "timestamp", None), s_name, s_lvl, s_msg)
        if sig in seen:
            continue
        seen.add(sig)

        incident_summary["cascade_flow"].append({
            "timestamp": ev.timestamp.strftime("%H:%M:%S") if hasattr(ev, "timestamp") and hasattr(ev.timestamp, "strftime") else str(getattr(ev, "timestamp", "")),
            "service": s_name,
            "level": s_lvl,
            "trace_id": getattr(ev, "trace_id", None),
            "message": s_msg
        })

    return incident_summary


def cluster_by_time_gap(events: list, max_gap_seconds: int = 300) -> List[List[Any]]:
    if not events:
        return []
    clusters = []
    current = [events[0]]
    for item in events[1:]:
        prev_ev = current[-1][0] if isinstance(current[-1], (tuple, list)) else current[-1]
        curr_ev = item[0] if isinstance(item, (tuple, list)) else item
        
        prev_ts = getattr(prev_ev, "timestamp", None)
        curr_ts = getattr(curr_ev, "timestamp", None)
        
        if prev_ts and curr_ts:
            gap = (curr_ts - prev_ts).total_seconds()
        else:
            gap = 0

        if gap <= max_gap_seconds:
            current.append(item)
        else:
            clusters.append(current)
            current = [item]
    if current:
        clusters.append(current)
    return clusters

def get_all_incidents(
    window_hours: int = 12,
    incident_window_gap: int = 300,
    safety_limit: int = 2000,
    limit_events: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if limit_events is not None:
        safety_limit = limit_events

    session = SessionLocal()
    try:
        query = session.query(LogEvent, LogTemplate.template_text).outerjoin(
            LogTemplate, LogEvent.template_id == LogTemplate.template_id
        ).filter(
            LogEvent.log_level.in_(["WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"])
        )

        latest_ts = session.query(func.max(LogEvent.timestamp)).scalar()
        if latest_ts:
            # Убираем tzinfo для совместимости с SQLite/Postgres naive datetime
            if hasattr(latest_ts, "tzinfo") and latest_ts.tzinfo is not None:
                latest_ts = latest_ts.replace(tzinfo=None)
            cutoff = latest_ts - timedelta(hours=window_hours)
            query = query.filter(LogEvent.timestamp >= cutoff)

        events = (
            query.order_by(LogEvent.timestamp.desc(), LogEvent.event_id.desc())
            .limit(safety_limit)
            .all()
        )
        if not events:
            return []

        events = events[::-1]
        traces = defaultdict(list)
        untraced = []

        for ev, tmpl in events:
            if getattr(ev, "trace_id", None):
                traces[ev.trace_id].append((ev, tmpl))
            else:
                untraced.append((ev, tmpl))

        raw_clusters = [(c, tid) for tid, c in traces.items()]
        if untraced:
            for tc in cluster_by_time_gap(untraced, max_gap_seconds=incident_window_gap):
                raw_clusters.append((tc, None))

        valid_clusters = [(c, tid) for c, tid in raw_clusters if is_valid_incident(c)]

        def get_cluster_timestamp(item):
            c = item[0]
            if not c:
                return datetime.min.replace(tzinfo=timezone.utc)
            ev = c[0][0] if isinstance(c[0], (tuple, list)) else c[0]
            ts = getattr(ev, "timestamp", None)
            if ts is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts

        valid_clusters.sort(key=get_cluster_timestamp)

        results = []
        for idx, (cluster, tid) in enumerate(valid_clusters, start=1):
            results.append(build_incident_dict(cluster, idx, len(valid_clusters), trace_id=tid))

        return results
    finally:
        session.close()


def find_incident_timeline(incident_index: int = -1, limit_events: int = 300) -> Dict[str, Any]:
    incidents = get_all_incidents(safety_limit=limit_events)
    if not incidents:
        return {"status": "ok", "message": "Ошибок не обнаружено"}
    if 0 <= incident_index < len(incidents):
        return incidents[incident_index]
    return incidents[-1]