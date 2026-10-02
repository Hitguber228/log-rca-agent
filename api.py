import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.parser import LogParser
from src.agent import generate_rca_summary
from src.notifier import (
    get_active_chat_id,
    create_bind_request,
    get_bind_request_data,
    save_chat_id_to_env,
    send_telegram_alert,
    BIND_REQUEST_FILE,
)

app = FastAPI(title="SRE Incident Analysis Console")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Внутреннее состояние логов и найденных кластеров
CURRENT_EVENTS: List[Dict[str, Any]] = []
CURRENT_INCIDENTS: List[Dict[str, Any]] = []


# ----------------- Встроенный коррелятор инцидентов ----------------- #

class IncidentCorrelator:
    """Группирует критические события по скользящему временному окну."""
    def __init__(self, window_seconds: int = 120):
        self.window_seconds = window_seconds

    def correlate_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not events:
            return []

        # Выбираем аномальные события
        errors = [
            e for e in events 
            if str(e.get("level", "")).upper() in ("ERROR", "CRITICAL", "WARN", "WARNING")
        ]
        if not errors:
            errors = events

        incidents = []
        cluster_id = 1
        current_cluster = [errors[0]]

        for ev in errors[1:]:
            try:
                t_prev = datetime.fromisoformat(str(current_cluster[-1]["timestamp"]).replace(" ", "T"))
                t_curr = datetime.fromisoformat(str(ev["timestamp"]).replace(" ", "T"))
                delta = abs((t_curr - t_prev).total_seconds())
            except Exception:
                delta = 0

            if delta <= self.window_seconds:
                current_cluster.append(ev)
            else:
                incidents.append(self._build_incident(cluster_id, current_cluster))
                cluster_id += 1
                current_cluster = [ev]

        if current_cluster:
            incidents.append(self._build_incident(cluster_id, current_cluster))

        return incidents

    def _build_incident(self, incident_id: int, cluster_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        root = cluster_events[0]
        return {
            "incident_id": incident_id,
            "incident_start": root.get("timestamp", datetime.now().isoformat()),
            "first_affected_service": root.get("service", "unknown"),
            "root_cause_candidate": {
                "service": root.get("service", "unknown"),
                "level": root.get("level", "ERROR"),
                "raw_message": root.get("message") or root.get("raw_message", "")
            },
            "chain_length": len(cluster_events),
            "cascade_flow": [
                {
                    "timestamp": str(e.get("timestamp", "")).split("T")[-1] if "T" in str(e.get("timestamp", "")) else str(e.get("timestamp", "")),
                    "level": e.get("level", "ERROR"),
                    "service": e.get("service", "unknown"),
                    "message": e.get("message") or e.get("raw_message", "")
                }
                for e in cluster_events
            ]
        }


# ----------------- Базовые маршруты и UI ----------------- #

class BindRequestSchema(BaseModel):
    chat_id: str


@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    """Отображение главной консоли инцидентов."""
    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )


# ----------------- Эндпоинты авторизации и 2FA ----------------- #

@app.get("/api/auth/status")
def get_auth_status():
    """Возвращает текущий подтверждённый Chat ID и состояние ожидания PIN."""
    active_id = get_active_chat_id()
    req_data = get_bind_request_data()
    return {
        "active_chat_id": active_id,
        "pending_pin": req_data.get("pin") if req_data else None,
        "pending_target_id": req_data.get("chat_id") if req_data else None,
    }


@app.post("/api/auth/request-pin")
def request_pin(body: BindRequestSchema):
    """Генерирует одноразовый PIN для привязки Telegram-аккаунта."""
    clean_id = body.chat_id.strip()
    if not clean_id.isdigit():
        raise HTTPException(status_code=400, detail="Telegram ID должен содержать только цифры")

    pin = create_bind_request(clean_id)
    return {"status": "ok", "pin": pin, "target_chat_id": clean_id}


@app.post("/api/auth/unbind")
def unbind_telegram():
    """Сброс авторизации дежурного в .env, кэше рантайма и файле заявок."""
    save_chat_id_to_env("")
    try:
        BIND_REQUEST_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    return {"status": "ok", "message": "Привязка полностью аннулирована"}


@app.post("/settings/telegram/test")
async def test_telegram_alert():
    """Тестовый пинг оповещателя с отказом 403 при статусе NOT BOUND."""
    current_chat_id = get_active_chat_id()
    if not current_chat_id:
        raise HTTPException(
            status_code=403,
            detail="Отправка запрещена: Telegram ID не подтверждён (статус NOT BOUND)."
        )

    dummy_incident = {
        "first_affected_service": "test-service",
        "trace_id": "manual-test-probe",
    }
    dummy_summary = "🔔 *Тестовый пинг системы оповещений SRE.*\nКанал доставки проверен и подтверждён."

    success = await send_telegram_alert(dummy_incident, dummy_summary)
    if not success:
        raise HTTPException(status_code=502, detail="Не удалось доставить сообщение через Telegram API.")

    return {"status": "ok", "message": "Тестовое оповещение доставлено"}


# ----------------- Анализ логов и генерация отчетов ----------------- #

@app.post("/logs/upload")
async def upload_log_file(file: UploadFile = File(...)):
    """Загрузка файла журнала, парсинг и немедленная кластеризация сбоев."""
    global CURRENT_EVENTS, CURRENT_INCIDENTS
    temp_path = BASE_DIR / "temp_uploaded_logs.log"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        parser = LogParser()
        CURRENT_EVENTS = parser.parse_file(str(temp_path))

        correlator = IncidentCorrelator(window_seconds=120)
        CURRENT_INCIDENTS = correlator.correlate_events(CURRENT_EVENTS)

        return {
            "status": "ok",
            "events_count": len(CURRENT_EVENTS),
            "incidents_count": len(CURRENT_INCIDENTS),
        }
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@app.get("/rca/incidents")
def get_incidents():
    """Возвращает обнаруженные кластеры инцидентов."""
    global CURRENT_INCIDENTS, CURRENT_EVENTS
    if not CURRENT_INCIDENTS and CURRENT_EVENTS:
        correlator = IncidentCorrelator(window_seconds=120)
        CURRENT_INCIDENTS = correlator.correlate_events(CURRENT_EVENTS)
    return JSONResponse(content=CURRENT_INCIDENTS)


@app.get("/rca/report")
async def generate_incident_report(incident_id: int):
    """Генерирует AI-постмортем через generate_rca_summary и пушит алерт в Telegram."""
    global CURRENT_INCIDENTS
    target_incident = next((i for i in CURRENT_INCIDENTS if i.get("incident_id") == incident_id), None)
    if not target_incident:
        raise HTTPException(status_code=404, detail="Инцидент не найден")

    summary = await generate_rca_summary(target_incident)

    if get_active_chat_id():
        await send_telegram_alert(target_incident, summary)

    return {"incident_id": incident_id, "report": summary}