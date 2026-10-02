from collections import defaultdict
from typing import List, Dict, Any, Tuple
from src.db import SessionLocal
from src.models import LogEvent, LogTemplate
from src.config import settings


def is_valid_incident(cluster: List[Tuple[LogEvent, str]]) -> bool:
    """Фильтрация шума: отсеивает одиночные или некритичные предупреждения."""
    if not cluster:
        return False

    levels = [ev.log_level for ev, _ in cluster]
    services = {ev.service_name for ev, _ in cluster}

    # 1. Если есть ERROR или CRITICAL — это гарантированный инцидент
    if any(lvl in ("ERROR", "CRITICAL") for lvl in levels):
        return True

    # 2. Если задело более одного микросервиса — это каскад
    if len(services) > 1:
        return True

    # 3. Если предупреждений накопилось >= 3 — признак системной деградации
    if len(cluster) >= 3:
        return True

    # Одиночные 1-2 WARN без последствий считаются шумом
    return False


def build_incident_dict(cluster: list, incident_id: int, total_count: int, trace_id: str = None) -> Dict[str, Any]:
    root_event, root_template = cluster[0]

    incident_summary = {
        "incident_id": incident_id,
        "trace_id": trace_id or root_event.trace_id,
        "status": "incident_detected",
        "incident_start": root_event.timestamp.isoformat(),
        "first_affected_service": root_event.service_name,
        "root_cause_candidate": {
            "service": root_event.service_name,
            "level": root_event.log_level,
            "template": root_template,
            "raw_message": root_event.raw_message
        },
        "chain_length": len(cluster),
        "total_incidents_detected": total_count,
        "cascade_flow": []
    }

    seen = set()
    for ev, tmpl in cluster:
        sig = (ev.timestamp, ev.service_name, ev.log_level, ev.raw_message)
        if sig in seen:
            continue
        seen.add(sig)

        incident_summary["cascade_flow"].append({
            "timestamp": ev.timestamp.strftime("%H:%M:%S"),
            "service": ev.service_name,
            "level": ev.log_level,
            "trace_id": ev.trace_id,
            "message": ev.raw_message
        })

    return incident_summary


def cluster_by_time_gap(events: list, max_gap_seconds: int = 300) -> List[List[Any]]:
    if not events:
        return []
    clusters = []
    current = [events[0]]
    for item in events[1:]:
        gap = (item[0].timestamp - current[-1][0].timestamp).total_seconds()
        if gap <= max_gap_seconds:
            current.append(item)
        else:
            clusters.append(current)
            current = [item]
    if current:
        clusters.append(current)
    return clusters


def get_all_incidents(limit_events: int = 300, incident_window_gap: int = 300) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        events = (
            session.query(LogEvent, LogTemplate.template_text)
            .join(LogTemplate, LogEvent.template_id == LogTemplate.template_id)
            .order_by(LogEvent.timestamp.asc(), LogEvent.event_id.asc())
            .limit(limit_events)
            .all()
        )

        errors = [(ev, tmpl) for ev, tmpl in events if ev.log_level in ("WARN", "ERROR", "CRITICAL")]
        if not errors:
            return []

        # 1. Группировка по Trace ID
        traces = defaultdict(list)
        untraced = []

        for ev, tmpl in errors:
            if ev.trace_id:
                traces[ev.trace_id].append((ev, tmpl))
            else:
                untraced.append((ev, tmpl))

        raw_clusters = []
        for tid, cluster in traces.items():
            raw_clusters.append((cluster, tid))

        # 2. Группировка оставшихся по временному окну
        if untraced:
            for time_cluster in cluster_by_time_gap(untraced, max_gap_seconds=incident_window_gap):
                raw_clusters.append((time_cluster, None))

        # 3. Фильтрация фонового шума
        valid_clusters = [
            (c, tid) for c, tid in raw_clusters
            if is_valid_incident(c)
        ]

        results = []
        for idx, (cluster, tid) in enumerate(valid_clusters, start=1):
            results.append(build_incident_dict(cluster, idx, len(valid_clusters), trace_id=tid))

        return results
    finally:
        session.close()


def find_incident_timeline(incident_index: int = -1, limit_events: int = 300) -> Dict[str, Any]:
    incidents = get_all_incidents(limit_events=limit_events)
    if not incidents:
        return {"status": "ok", "message": "Ошибок не обнаружено"}
    if 0 <= incident_index < len(incidents):
        return incidents[incident_index]
    return incidents[-1]