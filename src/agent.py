import json
import httpx
from typing import Dict, Any
from src.config import settings


def build_heuristic_fallback(incident: Dict[str, Any]) -> str:
    root = incident.get("root_cause_candidate", {})
    service = root.get("service", "Неизвестный сервис")
    msg = root.get("raw_message", "Неизвестная ошибка")

    return (
        f"**Сбой начался в `{service}`.** Причина: {msg}.\n\n"
        f"**Что произошло дальше:** Сбой затронул зависимые узлы "
        f"и вызвал каскад из {incident.get('chain_length', 1)} событий.\n\n"
        f"**Что сделать:**\n"
        f"1. Проверить логи и состояние сервиса `{service}`.\n"
        f"2. Проверить сетевые таймауты и активировать Circuit Breaker.\n"
        f"3. Проверить пулы соединений и блокировки в БД."
    )


async def generate_llm_postmortem(incident: Dict[str, Any]) -> str:
    """Асинхронная неблокирующая генерация инженерного постмортема."""
    if incident.get("status") in ("empty", "ok"):
        return "В логах всё спокойно: сбоев и критических ошибок не обнаружено."

    system_prompt = (
        "Ты — дежурный старший SRE инженер. На основе JSON с инцидентом напиши краткое, "
        "живое и технически точное заключение на русском языке.\n\n"
        "Формат (3 пункта Markdown без вводных фраз):\n"
        "• **Что сломалось:** 1-2 предложения о корневом узле и причине ошибки.\n"
        "• **Как развивался каскад:** 1-2 предложения о том, какие сервисы задело следом.\n"
        "• **Быстрое решение:** 2-3 конкретных действия (таймауты, индексы, рестарт, настройки)."
    )

    compact_context = {
        "start": incident.get("incident_start"),
        "trace_id": incident.get("trace_id"),
        "root_service": incident.get("first_affected_service"),
        "root_error": incident.get("root_cause_candidate", {}).get("raw_message"),
        "events_count": incident.get("chain_length"),
        "chain": [
            f"{e.get('service')} [{e.get('level')}]: {e.get('message')}"
            for e in incident.get("cascade_flow", [])
        ]
    }

    payload = {
        "model": settings.ollama_model,
        "system": system_prompt,
        "prompt": f"Контекст инцидента:\n{json.dumps(compact_context, ensure_ascii=False)}",
        "stream": False,
        "options": {
            "temperature": 0.15,
            "num_predict": 250,
            "num_ctx": 1536
        }
    }

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            res = await client.post(settings.ollama_url, json=payload)
            if res.status_code == 200:
                answer = res.json().get("response", "").strip()
                if answer:
                    return answer
    except Exception:
        pass

    return build_heuristic_fallback(incident)


async def generate_rca_summary(incident: Dict[str, Any]) -> str:
    return await generate_llm_postmortem(incident)

class SREAgent:
    """Адаптер для вызова генератора отчетов через интерфейс класса."""
    async def analyze_incident(self, incident: Dict[str, Any]) -> str:
        return await generate_rca_summary(incident)