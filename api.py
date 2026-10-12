"""
Центральный сервер API и оркестрации платформы (FastAPI).

Архитектурная роль:
1. REST API:
   - Прием и валидация входящих логов через эндпоинт /api/v1/ingest.
   - Агрегация инцидентов и вычисление первопричин в /rca/incidents (построение DAG графа).
   - Потоковая генерация отчетов RCA (Server-Sent Events) в /rca/stream.
   - Интерактивный диалог с SRE-ассистентом с автоинъекцией контекста аварии в /api/copilot/chat.
   - Интеллектуальный полнотекстовый поиск по логам с поддержкой фильтров в /api/logs/search.
2. Безопасность и авторизация:
   - Двухфакторная привязка дежурного инженера через 4-значный криптостойкий PIN.
   - Защита от Directory Traversal при смене источника логов.
   - Настройка строгих политик CORS и обработка SRE Guardrails.
3. WebSocket бродкастер (/ws/live):
   - Реалтайм-доставка новых событий и статусов инцидентов во фронтенд без поллинга.
"""

import os
import re
import time
import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Union

from fastapi import FastAPI, Request, HTTPException, Query, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import or_, desc

from src.config import settings, save_setting_to_env, reload_settings
from src.db import SessionLocal
from src.models import LogEvent, LogTemplate

try:
    from src.formalizer import LogQueryFormalizer
except ImportError:
    try:
        from src.parser import LogQueryFormalizer
    except ImportError:
        class LogQueryFormalizer:
            @staticmethod
            def formalize(q: str):
                return {"chips": [{"label": f"Поиск: {q}"}], "text_patterns": [q]}

try:
    from src.parser import parse_raw_log
except ImportError:
    parse_raw_log = None

try:
    from src.loader import process_log_file
except ImportError:
    process_log_file = None

from src.models import LogEvent, LogTemplate
from src.db import Base, engine
try:
    Base.metadata.create_all(bind=engine)
except Exception as _e:
    print(f"[DB] Таблицы уже существуют или инициализированы: {_e}")
from src.agent import generate_rca_summary, stream_rca_summary, ask_sre_assistant, get_llm_config, get_llm_status, check_ollama_available
from src.notifier import (
    get_active_chat_id,
    save_chat_id_to_env,
    clear_chat_id_from_env,
    get_active_bot_token,
    send_telegram_alert,
    get_watched_log_path,
    save_watched_log_path,
    ENV_PATH,
    create_pending_pin,
    get_active_pending_pin,
    clear_pending_pins,
    _load_pending_pins,
    _save_pending_pins,
)

app = FastAPI(title="SRE Log RCA Platform", version="2.1")

# Настройка безопасных заголовков CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# Защита от брутфорса и DoS запросов PIN-кодов (в памяти: chat_id -> timestamp последнего запроса)
_PIN_RATE_LIMIT: Dict[str, List[float]] = {}


# ---------------------------------------------------------------------------
# WebSocket Менеджер для реалтайм-стриминга
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

ws_manager = ConnectionManager()


async def broadcast_live_event(event_type: str, data: dict):
    """Шлёт оповещение во все открытые вкладки браузера через WebSocket."""
    try:
        await ws_manager.broadcast({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        print(f"[!] WebSocket broadcast error: {e}", flush=True)


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Читаем сообщения от клиента для поддержки соединения
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# Прокси-обёртка для синхронизации PENDING_PINS между процессами
class _PendingPinsProxy(dict):
    def __getitem__(self, key):
        pins = _load_pending_pins()
        return pins[key]
    def get(self, key, default=None):
        pins = _load_pending_pins()
        return pins.get(key, default)
    def __contains__(self, key):
        pins = _load_pending_pins()
        return key in pins
    def items(self):
        return _load_pending_pins().items()
    def clear(self):
        clear_pending_pins()
    def pop(self, key, default=None):
        pins = _load_pending_pins()
        val = pins.pop(key, default)
        _save_pending_pins(pins)
        return val

from src.incident_store import (
    set_incident_resolved,
    is_incident_resolved,
    get_cached_report,
    set_cached_report,
    clear_cached_report,
    get_all_cached_reports,
    get_all_resolved_incidents,
)

class _ResolvedIncidentsProxy(set):
    def __contains__(self, key):
        return str(key) in get_all_resolved_incidents()
    def add(self, key):
        set_incident_resolved(str(key), True)
    def discard(self, key):
        set_incident_resolved(str(key), False)

class _CacheReportsProxy(dict):
    def __getitem__(self, key):
        rep = get_cached_report(str(key))
        if rep is None:
            raise KeyError(key)
        return rep
    def get(self, key, default=None):
        rep = get_cached_report(str(key))
        return rep if rep is not None else default
    def __setitem__(self, key, val):
        set_cached_report(str(key), val)
    def __contains__(self, key):
        return get_cached_report(str(key)) is not None
    def __delitem__(self, key):
        clear_cached_report(str(key))

RESOLVED_INCIDENTS = _ResolvedIncidentsProxy()
CACHE_REPORTS = _CacheReportsProxy()


# ---------------------------------------------------------------------------
# Pydantic Схемы
# ---------------------------------------------------------------------------

class PinRequestSchema(BaseModel):
    chat_id: str


class BotTokenSchema(BaseModel):
    token: str


class LogSourceSchema(BaseModel):
    file_path: str


class ResolveIncidentSchema(BaseModel):
    incident_id: int
    trace_id: Optional[str] = None
    is_resolved: bool
    status: Optional[str] = "resolved"
    user_id: Optional[str] = "default_user"


class ClearCacheSchema(BaseModel):
    incident_id: int
    trace_id: Optional[str] = None
    user_id: Optional[str] = "default_user"


class RcaRequestSchema(BaseModel):
    incident_data: Dict[str, Any]
    force_refresh: bool = False


class IngestLogSchema(BaseModel):
    service: str
    message: str
    level: Optional[str] = "INFO"
    trace_id: Optional[str] = None
    user_id: Optional[str] = None
    client_ip: Optional[str] = None
    timestamp: Optional[str] = None


class SreChatRequest(BaseModel):
    message: str
    incident_id: Optional[str] = None
    trace_id: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = []


class BroadcastPayload(BaseModel):
    event_type: str
    data: Dict[str, Any]


class ShareTelegramSchema(BaseModel):
    incident_id: Union[int, str]
    trace_id: Optional[str] = None
    service_name: Optional[str] = None
    report_text: str



# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def save_token_to_env(token: str):
    clean_tok = token.strip()
    os.environ["TELEGRAM_BOT_TOKEN"] = clean_tok
    try:
        from src.config import settings
        settings.telegram_bot_token = clean_tok
    except Exception:
        pass

    targets = {ENV_PATH, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"}
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                if re.search(r"^TELEGRAM_BOT_TOKEN=.*$", content, flags=re.MULTILINE):
                    new_content = re.sub(
                        r"^TELEGRAM_BOT_TOKEN=.*$",
                        f"TELEGRAM_BOT_TOKEN={clean_tok}",
                        content,
                        flags=re.MULTILINE
                    )
                else:
                    new_content = content.rstrip() + f"\nTELEGRAM_BOT_TOKEN={clean_tok}\n"
                target.write_text(new_content, encoding="utf-8")
            except Exception:
                pass


async def get_bot_info(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Главная страница
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ---------------------------------------------------------------------------
# Telegram 2FA, Бот и Уведомления
# ---------------------------------------------------------------------------

@app.get("/api/auth/status")
async def get_auth_status():
    chat_id = get_active_chat_id()
    token = get_active_bot_token()
    bot_info = await get_bot_info(token) if token else None

    pending_pin = get_active_pending_pin()

    return {
        "is_bound": bool(chat_id),
        "chat_id": chat_id,
        "has_token": bool(token),
        "bot_username": bot_info.get("username") if bot_info else None,
        "pending_pin": pending_pin,
    }


@app.post("/api/auth/bot-token")
async def update_bot_token(body: BotTokenSchema):
    token = body.token.strip()
    if not token:
        save_token_to_env("")
        return {"status": "ok", "message": "Токен бота удалён"}

    info = await get_bot_info(token)
    if not info:
        raise HTTPException(status_code=400, detail="Неверный токен бота. Telegram API отклонил запрос.")

    save_token_to_env(token)
    return {"status": "ok", "username": info.get("username")}


@app.delete("/api/auth/bot-token")
async def delete_bot_token():
    save_token_to_env("")
    return {"status": "ok", "message": "Токен бота удалён"}


@app.post("/api/auth/request-pin")
async def request_pin(body: PinRequestSchema):
    cid = body.chat_id.strip()
    if not cid:
        raise HTTPException(status_code=400, detail="Укажите Telegram Chat ID")

    # Rate limiting: максимум 5 запросов PIN в течение 60 секунд
    now = time.time()
    reqs = [t for t in _PIN_RATE_LIMIT.get(cid, []) if now - t < 60]
    if len(reqs) >= 5:
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов PIN-кода. Пожалуйста, подождите минуту перед повторной попыткой."
        )
    reqs.append(now)
    _PIN_RATE_LIMIT[cid] = reqs

    pin = create_pending_pin(cid)
    return {"status": "ok", "pin": pin}


@app.post("/api/auth/unbind")
async def unbind_telegram():
    clear_chat_id_from_env()
    clear_pending_pins()
    return {"status": "ok", "message": "Привязка сброшена"}


@app.post("/api/telegram/test-alert")
@app.post("/settings/telegram/test")
async def test_telegram_alert():
    current_chat_id = get_active_chat_id().strip()
    if not current_chat_id:
        current_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not current_chat_id:
        raise HTTPException(status_code=403, detail="Для начала авторизуйтесь в Telegram")

    dummy_incident = {
        "first_affected_service": "test-service",
        "trace_id": "manual-test-probe",
        "incident_start": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "incident_id": 999999,
    }
    dummy_summary = "<b>Тестовый пинг системы оповещений SRE.</b>\nКанал доставки проверен и подтверждён."

    success = await send_telegram_alert(dummy_incident, dummy_summary)
    if not success:
        raise HTTPException(status_code=502, detail="Telegram API не смог доставить сообщение.")

    return {"status": "ok", "message": "Тестовое оповещение успешно отправлено в Telegram"}


# ---------------------------------------------------------------------------
# Управление источником логов
# ---------------------------------------------------------------------------

@app.get("/api/settings/log-source")
def get_log_source_status():
    p = get_watched_log_path()
    exists = p.exists()
    size_kb = round(p.stat().st_size / 1024, 2) if exists else 0
    return {
        "path": str(p),
        "exists": exists,
        "size_kb": size_kb,
        "name": p.name
    }


@app.post("/api/settings/log-source")
def update_log_source(body: LogSourceSchema):
    new_path_str = body.file_path.strip()
    if not new_path_str:
        raise HTTPException(status_code=400, detail="Укажите путь к файлу")

    # Защита от Path Traversal
    if ".." in new_path_str:
        raise HTTPException(status_code=400, detail="Недопустимый путь к файлу (содержит переход '..')")

    p = Path(new_path_str).resolve()
    lower_path = str(p).lower()

    # Защита от обращения к системным системным директориям Windows / Linux
    forbidden_prefixes = ["\\windows\\", "\\system32\\", "\\program files", "/etc/", "/sys/", "/proc/"]
    if any(fp in lower_path for fp in forbidden_prefixes):
        raise HTTPException(status_code=400, detail="Доступ к системным директориям ОС запрещён")

    if p.suffix.lower() not in [".log", ".txt"]:
        raise HTTPException(status_code=400, detail="Разрешены только файлы с расширением .log или .txt")

    if not p.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Не удалось получить доступ к файлу: {e}")

    save_watched_log_path(str(p))
    return {"status": "ok", "path": str(p), "name": p.name}


# ---------------------------------------------------------------------------
# Статус LLM
# ---------------------------------------------------------------------------

@app.get("/api/llm/status")
async def get_llm_status_endpoint():
    from src.agent import get_llm_status as get_status
    return get_status()



# ---------------------------------------------------------------------------
# Инциденты и RCA Аналитика
# ---------------------------------------------------------------------------

@app.get("/rca/incidents")
def get_rca_incidents():
    session = SessionLocal()
    try:
        critical_logs = (
            session.query(LogEvent)
            .filter(LogEvent.log_level.in_(["ERROR", "CRITICAL"]))
            .order_by(desc(LogEvent.timestamp))
            .limit(100)
            .all()
        )

        incidents_map = {}
        for ev in critical_logs:
            key = ev.trace_id if ev.trace_id else f"time-{ev.timestamp.strftime('%Y%m%d%H%M') if ev.timestamp else '0'}"
            if key not in incidents_map:
                incidents_map[key] = {
                    "incident_id": ev.event_id,
                    "trace_id": ev.trace_id,
                    "incident_start": ev.timestamp.strftime("%Y-%m-%d %H:%M:%S") if ev.timestamp else "--",
                    "first_affected_service": ev.service_name,
                    "root_cause_candidate": {
                        "service": ev.service_name,
                        "level": ev.log_level,
                        "raw_message": ev.raw_message,
                    },
                    "events": [],
                    "chain_length": 0,
                    "is_resolved": (ev.event_id in RESOLVED_INCIDENTS) or (bool(ev.trace_id and ev.trace_id in RESOLVED_INCIDENTS)),
                    "cached_report": CACHE_REPORTS.get(str(ev.event_id)) or (CACHE_REPORTS.get(ev.trace_id) if ev.trace_id else None),
                    "has_cached_report": (str(ev.event_id) in CACHE_REPORTS) or (bool(ev.trace_id and ev.trace_id in CACHE_REPORTS)),
                }
            incidents_map[key]["events"].append({
                "event_id": ev.event_id,
                "timestamp": ev.timestamp.strftime("%Y-%m-%d %H:%M:%S") if ev.timestamp else "--",
                "service": ev.service_name,
                "level": ev.log_level,
                "message": ev.raw_message,
            })
            incidents_map[key]["chain_length"] += 1

        result = list(incidents_map.values())
        for inc in result:
            # Сортируем события хронологически: от первого сбоя (эпицентра) к каскадным последствиям
            inc["events"].reverse()
            if inc["events"]:
                first_ev = inc["events"][0]
                inc["incident_start"] = first_ev["timestamp"]
                inc["first_affected_service"] = first_ev["service"]
                inc["root_cause_candidate"] = {
                    "service": first_ev["service"],
                    "level": first_ev["level"],
                    "raw_message": first_ev["message"],
                }
            inc["cascade_flow"] = inc["events"]
        return result
    finally:
        session.close()


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "connection_established",
            "message": "Connected to SRE Live Stream",
            "timestamp": datetime.now().isoformat()
        })
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


@app.post("/api/internal/broadcast")
async def internal_broadcast_endpoint(payload: BroadcastPayload):
    await broadcast_live_event(payload.event_type, payload.data)
    return {"status": "broadcasted"}


@app.post("/api/copilot/chat")
async def copilot_chat_endpoint(payload: SreChatRequest):
    """Интерактивный диалог со встроенным SRE-ассистентом."""
    incident_data = None
    session = SessionLocal()
    try:
        target_trace = payload.trace_id
        raw_inc_id = str(payload.incident_id).replace('#', '').strip() if payload.incident_id else ""
        target_ev = None

        if not target_trace and raw_inc_id.isdigit():
            target_ev = session.query(LogEvent).filter(LogEvent.event_id == int(raw_inc_id)).first()
            if target_ev and target_ev.trace_id:
                target_trace = target_ev.trace_id

        if target_trace:
            chain = (
                session.query(LogEvent)
                .filter(LogEvent.trace_id == target_trace)
                .order_by(LogEvent.timestamp.asc(), LogEvent.event_id.asc())
                .all()
            )
            if chain:
                first = chain[0]
                incident_data = {
                    "incident_id": first.event_id,
                    "trace_id": target_trace,
                    "first_affected_service": first.service_name,
                    "root_cause_candidate": {
                        "service": first.service_name,
                        "level": first.log_level,
                        "raw_message": first.raw_message,
                    },
                    "cascade_flow": [
                        {
                            "timestamp": c.timestamp.isoformat() if c.timestamp else "",
                            "level": c.log_level,
                            "service": c.service_name,
                            "message": c.raw_message,
                        }
                        for c in chain
                    ]
                }
        elif target_ev:
            incident_data = {
                "incident_id": target_ev.event_id,
                "trace_id": target_ev.trace_id or "N/A",
                "first_affected_service": target_ev.service_name,
                "root_cause_candidate": {
                    "service": target_ev.service_name,
                    "level": target_ev.log_level,
                    "raw_message": target_ev.raw_message,
                },
                "cascade_flow": [
                    {
                        "timestamp": target_ev.timestamp.isoformat() if target_ev.timestamp else "",
                        "level": target_ev.log_level,
                        "service": target_ev.service_name,
                        "message": target_ev.raw_message,
                    }
                ]
            }

        # Интеллектуальный подбор контекста: если пользователь упоминает сервис или проблему
        if not incident_data and payload.message:
            msg_lower = payload.message.lower()
            known_services = {
                "whisper": ["whisper-engine", "whisper", "faster-whisper"],
                "s3": ["storage-s3-service", "minio"],
                "minio": ["storage-s3-service", "minio"],
                "postgres": ["postgres-cluster", "postgres"],
                "pg": ["postgres-cluster", "postgres"],
                "redis": ["redis-cache", "redis"],
                "groq": ["llm-agent-service", "groq-llm"],
                "gateway": ["api-gateway"],
                "auth": ["auth-service"],
                "scriber": ["audio-scriber-service"],
            }
            target_candidates = None
            for kw, srv_list in known_services.items():
                if kw in msg_lower:
                    target_candidates = srv_list
                    break

            if target_candidates:
                ev = (
                    session.query(LogEvent)
                    .filter(LogEvent.service_name.in_(target_candidates))
                    .order_by(LogEvent.event_id.desc())
                    .first()
                )
                if ev:
                    incident_data = {
                        "incident_id": ev.event_id,
                        "trace_id": ev.trace_id or "N/A",
                        "first_affected_service": ev.service_name,
                        "root_cause_candidate": {
                            "service": ev.service_name,
                            "level": ev.log_level,
                            "raw_message": ev.raw_message,
                        },
                        "cascade_flow": [{
                            "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                            "level": ev.log_level,
                            "service": ev.service_name,
                            "message": ev.raw_message,
                        }]
                    }
            elif any(w in msg_lower for w in ["эту проблему", "эту аварию", "этот инцидент", "что случилось"]):
                latest_crit = (
                    session.query(LogEvent)
                    .filter(LogEvent.log_level.in_(["ERROR", "CRITICAL"]))
                    .order_by(LogEvent.event_id.desc())
                    .first()
                )
                if latest_crit:
                    incident_data = {
                        "incident_id": latest_crit.event_id,
                        "trace_id": latest_crit.trace_id or "N/A",
                        "first_affected_service": latest_crit.service_name,
                        "root_cause_candidate": {
                            "service": latest_crit.service_name,
                            "level": latest_crit.log_level,
                            "raw_message": latest_crit.raw_message,
                        },
                        "cascade_flow": [{
                            "timestamp": latest_crit.timestamp.isoformat() if latest_crit.timestamp else "",
                            "level": latest_crit.log_level,
                            "service": latest_crit.service_name,
                            "message": latest_crit.raw_message,
                        }]
                    }
    except Exception as e:
        print(f"[!] Ошибка извлечения инцидента для ассистента: {e}", flush=True)
    finally:
        session.close()

    answer = await ask_sre_assistant(
        user_message=payload.message,
        incident_data=incident_data,
        history=payload.history
    )
    return {
        "reply": answer,
        "incident_context": bool(incident_data),
        "timestamp": datetime.now().isoformat()
    }


# Замок для предотвращения параллельных тяжелых вызовов LLM и перегрузки системы
RCA_ANALYSIS_LOCK = asyncio.Lock()


@app.post("/rca/analyze")
async def run_rca_analysis(body: RcaRequestSchema):
    """Генерирует сводку RCA по инциденту через нейросеть или достает из кэша."""
    inc_data = body.incident_data
    inc_id = str(inc_data.get("incident_id"))

    if not body.force_refresh and inc_id in CACHE_REPORTS:
        return {"report": CACHE_REPORTS[inc_id], "cached": True}

    if RCA_ANALYSIS_LOCK.locked():
        raise HTTPException(
            status_code=429,
            detail="В данный момент уже выполняется анализ другого инцидента. Пожалуйста, дождитесь его завершения."
        )

    async with RCA_ANALYSIS_LOCK:
        report = await generate_rca_summary(inc_data)
        CACHE_REPORTS[inc_id] = report

        # Оповещаем открытые вкладки о готовом отчете
        asyncio.create_task(broadcast_live_event("rca_report_ready", {
            "incident_id": inc_id,
            "trace_id": inc_data.get("trace_id"),
            "report": report
        }))

        return {"report": report, "cached": False}


@app.post("/rca/analyze/stream")
async def stream_rca_analysis(body: RcaRequestSchema):
    """Стриминг RCA-отчёта в реальном времени (токен за токеном)."""
    inc_data = body.incident_data
    inc_id = str(inc_data.get("incident_id"))

    if not body.force_refresh and inc_id in CACHE_REPORTS:
        async def cached_stream():
            yield CACHE_REPORTS[inc_id]
        return StreamingResponse(cached_stream(), media_type="text/plain; charset=utf-8")

    if RCA_ANALYSIS_LOCK.locked():
        raise HTTPException(
            status_code=429,
            detail="В данный момент уже выполняется анализ другого инцидента. Пожалуйста, дождитесь его завершения."
        )

    await RCA_ANALYSIS_LOCK.acquire()

    async def token_generator():
        try:
            accumulated = []
            try:
                async for token in stream_rca_summary(inc_data):
                    accumulated.append(token)
                    yield token
            except Exception as e:
                print(f"[!] Ошибка во время стриминга RCA: {e}", flush=True)

            full_report = "".join(accumulated).strip()
            if full_report:
                CACHE_REPORTS[inc_id] = full_report
                asyncio.create_task(broadcast_live_event("rca_report_ready", {
                    "incident_id": inc_id,
                    "trace_id": inc_data.get("trace_id"),
                    "report": full_report
                }))
        finally:
            if RCA_ANALYSIS_LOCK.locked():
                RCA_ANALYSIS_LOCK.release()

    try:
        return StreamingResponse(token_generator(), media_type="text/plain; charset=utf-8")
    except Exception:
        if RCA_ANALYSIS_LOCK.locked():
            RCA_ANALYSIS_LOCK.release()
        raise


@app.post("/rca/incident/toggle-resolve")
async def toggle_incident_resolve(body: ResolveIncidentSchema):
    set_incident_resolved(str(body.incident_id), body.is_resolved, user_id=body.user_id or "default_user")
    if body.trace_id:
        set_incident_resolved(str(body.trace_id), body.is_resolved, user_id=body.user_id or "default_user")

    # Синхронизируем статус через WebSocket
    asyncio.create_task(broadcast_live_event("incident_resolved_toggle", {
        "incident_id": body.incident_id,
        "trace_id": body.trace_id,
        "is_resolved": body.is_resolved
    }))

    return {"status": "ok", "is_resolved": body.is_resolved}


@app.post("/rca/incident/clear-cache")
async def clear_incident_cache(body: ClearCacheSchema):
    inc_id = str(body.incident_id)
    clear_cached_report(inc_id)
    if body.trace_id:
        clear_cached_report(str(body.trace_id))
    return {"status": "ok"}


@app.post("/api/incidents/share-telegram")
async def share_incident_to_telegram(payload: ShareTelegramSchema):
    chat_id = get_active_chat_id()
    if not chat_id:
        raise HTTPException(status_code=403, detail="Telegram не привязан. Нажмите кнопку Telegram в шапке для привязки.")

    clean_report = payload.report_text.strip()
    incident_obj = {
        "incident_id": payload.incident_id,
        "first_affected_service": payload.service_name or "System",
        "trace_id": payload.trace_id or "N/A"
    }

    success = await send_telegram_alert(incident_obj, clean_report)
    if not success:
        raise HTTPException(status_code=502, detail="Не удалось доставить сообщение в Telegram. Проверьте токен бота.")

    return {"status": "ok", "message": "RCA-отчёт успешно отправлен в Telegram!"}


@app.post("/api/system/purge-logs")
async def purge_logs_endpoint(days: Optional[int] = None):
    """Принудительная очистка устаревших логов по политике хранения."""
    from src.retention import purge_old_log_events, rotate_live_log_file
    retention_days = days if days is not None else int(getattr(settings, "log_retention_days", 7))
    stats = purge_old_log_events(days=retention_days)
    rotated = rotate_live_log_file(max_size_mb=30)
    stats["file_rotated"] = rotated
    return stats


# ---------------------------------------------------------------------------
# Тестовый продакшн-датасет
# ---------------------------------------------------------------------------

@app.post("/api/dataset/seed")
async def seed_dataset_endpoint(clean: bool = True):
    """Генерирует и загружает эталонный продакшн-датасет (~350 логов) в базу данных."""
    from generate_dataset import seed_database
    loop = asyncio.get_event_loop()
    inserted_count = await loop.run_in_executor(None, seed_database, clean)
    asyncio.create_task(broadcast_live_event("system_data_seeded", {
        "count": inserted_count,
        "timestamp": datetime.now().isoformat()
    }))
    return {
        "status": "ok",
        "inserted": inserted_count,
        "message": f"Успешно загружено {inserted_count} реалистичных логов и 6 инцидентов!"
    }


@app.post("/api/dataset/stream-incident")
async def stream_incident_endpoint():
    """Стримит один реалистичный каскадный сбой в live_service.log для проверки watcher и WebSocket."""
    import random
    from generate_dataset import generate_incidents, format_log_line, LIVE_LOG_FILE

    incidents = generate_incidents()
    inc = random.choice(incidents)

    async def _stream_flow():
        for svc, lvl, raw_msg in inc["events"]:
            now = datetime.now()
            line = format_log_line(
                now, lvl, svc, inc["trace_id"], inc["user_id"], inc["client_ip"], raw_msg
            )
            with open(LIVE_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            await asyncio.sleep(0.3)

    asyncio.create_task(_stream_flow())
    return {
        "status": "ok",
        "incident_title": inc["title"],
        "trace_id": inc["trace_id"],
        "message": f"Запущен стриминг аварии '{inc['title']}' в live_service.log"
    }


@app.post("/api/dataset/clear")
async def clear_dataset_endpoint():
    """Очищает логи из базы данных."""
    from generate_dataset import clear_database
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, clear_database)
    asyncio.create_task(broadcast_live_event("system_data_cleared", {
        "timestamp": datetime.now().isoformat()
    }))
    return {"status": "ok", "message": "База данных логов очищена"}


@app.get("/api/system/health-summary")
def get_system_health_summary():
    session = SessionLocal()
    try:
        from sqlalchemy import func, case
        # Оптимизированный агрегирующий запрос: подсчёт общих логов и ошибок за один запрос
        stats = session.query(
            func.count(LogEvent.event_id).label("total_logs"),
            func.count(
                case(
                    (LogEvent.log_level.in_(["ERROR", "CRITICAL"]), 1)
                )
            ).label("total_errors")
        ).first()

        total_logs = stats.total_logs or 0 if stats else 0
        total_errors = stats.total_errors or 0 if stats else 0

        cnt_col = func.count(LogEvent.event_id).label("cnt")
        srv_rows = (
            session.query(LogEvent.service_name, cnt_col)
            .filter(LogEvent.log_level.in_(["ERROR", "CRITICAL"]))
            .group_by(LogEvent.service_name)
            .order_by(desc(cnt_col))
            .all()
        )

        service_errors = {}
        worst_service = "Все стабильны"
        worst_count = 0
        if srv_rows:
            worst_service = srv_rows[0][0] or "Неизвестный сервис"
            worst_count = int(srv_rows[0][1])
            for srv, cnt in srv_rows:
                if srv:
                    service_errors[str(srv)] = int(cnt)

        db_url_str = str(session.bind.url) if session.bind else ""
        db_type = "PostgreSQL" if not db_url_str.startswith("sqlite") else "SQLite"

        return {
            "total_logs": total_logs,
            "total_errors": total_errors,
            "worst_service": worst_service,
            "worst_errors_count": worst_count,
            "service_errors": service_errors,
            "db_type": db_type,
            "db_connected": True
        }
    except Exception as e:
        import logging
        logging.error(f"Database error in health-summary: {e}", exc_info=True)
        return {
            "total_logs": 0,
            "total_errors": 0,
            "worst_service": "N/A",
            "worst_errors_count": 0,
            "db_type": "PostgreSQL",
            "db_connected": False,
            "error": "Ошибка подключения к базе данных"
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Log Explorer, Поиск и Пагинация
# ---------------------------------------------------------------------------

@app.get("/api/logs/services")
def get_services_list():
    session = SessionLocal()
    try:
        rows = session.query(LogEvent.service_name).distinct().all()
        services = sorted([r[0] for r in rows if r[0]])
        return {"services": services}
    finally:
        session.close()


def _execute_log_search(
    query: Optional[str] = None,
    services: Optional[str] = None,
    levels: Optional[str] = None,
    trace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
):
    session = SessionLocal()
    try:
        db_query = session.query(LogEvent)
        chips = []
        raw_q = (query or "").strip()

        if raw_q:
            formalized = LogQueryFormalizer.formalize(raw_q)
            chips = formalized.get("chips", [])

            srv_list = list(formalized.get("services") or [])
            if services:
                srv_list.extend([s.strip() for s in services.split(",") if s.strip()])
            if srv_list:
                db_query = db_query.filter(LogEvent.service_name.in_(srv_list))

            lvl_list = list(formalized.get("levels") or [])
            if levels:
                lvl_list.extend([l.strip().upper() for l in levels.split(",") if l.strip()])
            if lvl_list:
                db_query = db_query.filter(LogEvent.log_level.in_(lvl_list))

            def _escape_like(text_val: str) -> str:
                return text_val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

            text_matches = []
            for pat in formalized.get("text_patterns", []):
                p = str(pat).strip()
                if p:
                    esc_p = _escape_like(p)
                    text_matches.append(LogEvent.raw_message.ilike(f"%{esc_p}%", escape="\\"))
                    text_matches.append(LogEvent.service_name.ilike(f"%{esc_p}%", escape="\\"))

            for phrase in formalized.get("exact_phrases", []):
                ph = str(phrase).strip()
                if ph:
                    esc_ph = _escape_like(ph)
                    text_matches.append(LogEvent.raw_message.ilike(f"%{esc_ph}%", escape="\\"))

            if text_matches:
                db_query = db_query.filter(or_(*text_matches))
        else:
            if services:
                s_parts = [s.strip() for s in services.split(",") if s.strip()]
                if s_parts:
                    db_query = db_query.filter(LogEvent.service_name.in_(s_parts))
            if levels:
                l_parts = [l.strip().upper() for l in levels.split(",") if l.strip()]
                if l_parts:
                    db_query = db_query.filter(LogEvent.log_level.in_(l_parts))

        if trace_id and trace_id.strip():
            db_query = db_query.filter(LogEvent.trace_id == trace_id.strip())

        if user_id and user_id.strip():
            db_query = db_query.filter(LogEvent.user_id == user_id.strip())

        if client_ip and client_ip.strip():
            db_query = db_query.filter(LogEvent.client_ip == client_ip.strip())

        if date_from and date_from.strip():
            try:
                dt_from = datetime.fromisoformat(date_from.strip().replace("Z", ""))
                db_query = db_query.filter(LogEvent.timestamp >= dt_from)
            except Exception:
                pass

        if date_to and date_to.strip():
            try:
                dt_to = datetime.fromisoformat(date_to.strip().replace("Z", ""))
                db_query = db_query.filter(LogEvent.timestamp <= dt_to)
            except Exception:
                pass

        total = db_query.count()
        events = (
            db_query.order_by(desc(LogEvent.timestamp), desc(LogEvent.event_id))
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        return {
            "events": [
                {
                    "event_id": ev.event_id,
                    "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                    "service_name": ev.service_name,
                    "log_level": ev.log_level,
                    "raw_message": ev.raw_message,
                    "trace_id": ev.trace_id,
                    "user_id": ev.user_id,
                    "client_ip": ev.client_ip,
                }
                for ev in events
            ],
            "total_events": total,
            "filters": {"chips": chips},
        }
    finally:
        session.close()


@app.get("/api/logs/search")
def search_logs_get(
    query: Optional[str] = Query(None),
    services: Optional[str] = Query(None),
    levels: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    client_ip: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=10, le=200),
):
    return _execute_log_search(
        query=query,
        services=services,
        levels=levels,
        trace_id=trace_id,
        user_id=user_id,
        client_ip=client_ip,
        date_from=date_from,
        date_to=date_to,
        page=page,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Загрузка файлов и Прямой API Ingest
# ---------------------------------------------------------------------------

@app.post("/logs/upload")
async def upload_log_file(file: UploadFile = File(...)):
    # 1. Валидация формата файла
    allowed_exts = {".log", ".txt", ".json", ".csv"}
    original_ext = Path(file.filename or "").suffix.lower()
    if original_ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый формат файла '{original_ext}'. Разрешены: {', '.join(sorted(allowed_exts))}"
        )

    # 2. Ограничение размера файла (максимум 50 МБ)
    MAX_FILE_SIZE = 50 * 1024 * 1024
    import uuid

    temp_dir = Path.cwd() / "scratch"
    temp_dir.mkdir(exist_ok=True)
    temp_path = temp_dir / f"upload_{uuid.uuid4().hex[:12]}{original_ext}"

    total_bytes = 0
    try:
        with open(temp_path, "wb") as f_out:
            while chunk := await file.read(1024 * 1024):  # Читаем чанками по 1 МБ
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="Размер файла превышает допустимый лимит (50 МБ)"
                    )
                f_out.write(chunk)

        count = process_log_file(temp_path) if process_log_file else 0
        return {"status": "ok", "filename": file.filename, "ingested_events": count}
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@app.post("/api/v1/ingest")
def ingest_single_log(payload: IngestLogSchema):
    session = SessionLocal()
    try:
        ts = datetime.now()
        if payload.timestamp:
            try:
                ts = datetime.fromisoformat(payload.timestamp.replace("Z", ""))
            except Exception:
                pass

        event = LogEvent(
            timestamp=ts,
            service_name=payload.service,
            log_level=payload.level.upper() if payload.level else "INFO",
            raw_message=payload.message,
            trace_id=payload.trace_id,
            user_id=payload.user_id,
            client_ip=payload.client_ip
        )
        session.add(event)
        session.commit()

        # Оповещаем WebSocket-клиентов о новом логе в реальном времени
        try:
            event_payload = {
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat() if event.timestamp else datetime.now().isoformat(),
                "service_name": event.service_name,
                "log_level": event.log_level,
                "raw_message": event.raw_message,
                "trace_id": event.trace_id,
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(broadcast_live_event("new_log", event_payload))
            except RuntimeError:
                pass
        except Exception:
            pass

        return {"status": "success", "event_id": event.event_id}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Настройки: 1. Нейросеть (LLM & RCA) и 2. Правила алертинга
# ---------------------------------------------------------------------------

class LLMSettingsSchema(BaseModel):
    provider: str = Field(..., description="groq, ollama, fallback")
    groq_api_key: Optional[str] = Field(None, description="Groq API key")
    model: Optional[str] = Field("llama-3.3-70b-versatile")
    temperature: Optional[float] = Field(0.2, ge=0.0, le=1.0)


class LLMTestSchema(BaseModel):
    provider: Optional[str] = None
    groq_api_key: Optional[str] = None
    model: Optional[str] = None


class AlertSettingsSchema(BaseModel):
    min_level: str = Field("ERROR", description="ERROR, CRITICAL, WARN")
    cascade_only: bool = Field(False)
    min_errors: int = Field(1, ge=1, le=50)
    debounce_seconds: float = Field(1.5, ge=0.5, le=10.0)


@app.get("/api/settings/llm")
def get_llm_settings():
    provider = os.getenv("LLM_PROVIDER") or getattr(settings, "llm_provider", "groq")
    model = os.getenv("LLM_MODEL") or getattr(settings, "llm_model", "llama-3.3-70b-versatile")
    raw_key = os.getenv("GROQ_API_KEY") or getattr(settings, "groq_api_key", "")
    masked_key = ""
    if raw_key:
        if len(raw_key) > 8:
            masked_key = raw_key[:4] + "••••••••••••" + raw_key[-4:]
        else:
            masked_key = "••••••••"

    try:
        temp = float(os.getenv("LLM_TEMPERATURE") or getattr(settings, "llm_temperature", 0.2))
    except Exception:
        temp = 0.2

    return {
        "provider": provider,
        "model": model,
        "temperature": temp,
        "has_key": bool(raw_key),
        "masked_key": masked_key,
        "ollama_available": check_ollama_available(),
        "status": get_llm_status()
    }


@app.post("/api/settings/llm")
def update_llm_settings(payload: LLMSettingsSchema):
    save_setting_to_env("LLM_PROVIDER", payload.provider)
    if payload.model:
        save_setting_to_env("LLM_MODEL", payload.model.strip())
    if payload.temperature is not None:
        save_setting_to_env("LLM_TEMPERATURE", round(payload.temperature, 2))
    if payload.groq_api_key and payload.groq_api_key.strip() and not payload.groq_api_key.startswith("••"):
        save_setting_to_env("GROQ_API_KEY", payload.groq_api_key.strip())

    return {
        "status": "ok",
        "message": "Параметры нейросети успешно сохранены",
        "active_status": get_llm_status()
    }


@app.post("/api/settings/llm/test")
async def test_llm_connection(payload: LLMTestSchema):
    import httpx
    provider = payload.provider or os.getenv("LLM_PROVIDER") or getattr(settings, "llm_provider", "groq")
    model = payload.model or os.getenv("LLM_MODEL") or getattr(settings, "llm_model", "llama-3.3-70b-versatile")
    api_key = payload.groq_api_key or os.getenv("GROQ_API_KEY") or getattr(settings, "groq_api_key", "")

    t0 = time.time()

    if provider == "groq":
        if not api_key or not api_key.strip():
            return {"ok": False, "error": "API-ключ Groq не указан"}

        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json"
        }
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
                latency_ms = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    return {
                        "ok": True,
                        "latency_ms": latency_ms,
                        "provider": "Groq Cloud API",
                        "model": model,
                        "message": f"Связь с Groq Cloud установлена (пинг {latency_ms} мс)"
                    }
                elif resp.status_code == 401:
                    return {"ok": False, "error": "Недействительный API-ключ Groq (401 Unauthorized)"}
                else:
                    return {"ok": False, "error": f"Ошибка Groq API ({resp.status_code}): {resp.text[:100]}"}
        except Exception as ex:
            return {"ok": False, "error": f"Сбой подключения к Groq API: {ex}"}

    elif provider == "ollama":
        ollama_url = getattr(settings, "ollama_url", "http://127.0.0.1:11434/api/generate")
        tags_url = ollama_url.replace("/api/generate", "/api/tags")
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(tags_url)
                latency_ms = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    return {
                        "ok": True,
                        "latency_ms": latency_ms,
                        "provider": "Local Ollama",
                        "model": model,
                        "message": f"Локальный сервер Ollama доступен (пинг {latency_ms} мс)"
                    }
                else:
                    return {"ok": False, "error": f"Ollama вернула статус {resp.status_code}"}
        except Exception as ex:
            return {"ok": False, "error": f"Сервер Ollama недоступен на localhost:11434 ({ex})"}

    elif provider == "fallback":
        return {
            "ok": True,
            "latency_ms": 1,
            "provider": "Rule-Based Fallback",
            "model": "Rule-Based Engine",
            "message": "Встроенный эвристический движок активен (работает мгновенно и оффлайн)"
        }

    return {"ok": False, "error": f"Неизвестный провайдер: {provider}"}


@app.get("/api/settings/alerts")
def get_alert_settings():
    from src.watcher import get_alert_rules
    rules = get_alert_rules()
    return {
        "min_level": rules["min_level"],
        "cascade_only": rules["cascade_only"],
        "min_errors": rules["min_errors"],
        "debounce_seconds": rules["debounce_sec"]
    }


@app.post("/api/settings/alerts")
def update_alert_settings(payload: AlertSettingsSchema):
    save_setting_to_env("ALERT_MIN_LEVEL", payload.min_level.upper())
    save_setting_to_env("ALERT_CASCADE_ONLY", str(payload.cascade_only))
    save_setting_to_env("ALERT_MIN_ERRORS", str(payload.min_errors))
    save_setting_to_env("ALERT_DEBOUNCE_SECONDS", str(payload.debounce_seconds))

    from src.watcher import get_alert_rules
    current_rules = get_alert_rules()

    return {
        "status": "ok",
        "message": "Правила алертинга успешно сохранены",
        "settings": {
            "min_level": current_rules["min_level"],
            "cascade_only": current_rules["cascade_only"],
            "min_errors": current_rules["min_errors"],
            "debounce_seconds": current_rules["debounce_sec"]
        }
    }