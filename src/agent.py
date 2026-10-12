import os
import json
import re
import asyncio
import httpx
from typing import Dict, Any, Optional, List, AsyncGenerator
from datetime import datetime
from src.config import settings

def _contains_chinese(text: str) -> bool:
    """Проверяет наличие иероглифов китайского языка (CJK Unified Ideographs)."""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))

def _build_fallback_report(incident: Dict[str, Any]) -> str:
    """Лаконичный инженерный постмортем без канцелярщины, эмодзи и дублирования строк."""
    service = incident.get("first_affected_service", "Сервис не определен")
    trace_id = incident.get("trace_id", "untraced")
    root = incident.get("root_cause_candidate") or {}
    raw_error = root.get("raw_message") or incident.get("raw_message") or "Сбой без описания"
    cascade = incident.get("cascade_flow") or []

    # Определяем характер проблемы по ключевым словам
    diagnosis = "Зафиксирован критический сбой узла."
    fix_steps = [
        "Проверить статус процесса и системные ресурсы хоста.",
        "Изучить трассировку вызовов в точке отказа.",
    ]

    err_lower = raw_error.lower()
    if "cuda out of memory" in err_lower or "allocation failed" in err_lower:
        diagnosis = (
            f"Сервис **{service}** исчерпал видеопамять (VRAM) при выполнении задачи. "
            f"Параметры инференса (например, высокий `beam_size`) превысили доступный лимит GPU."
        )
        fix_steps = [
            "Снизить `beam_size` (например, с 5 до 1–2) или переключить compute_type на `int8_float16`.",
            "Настроить ограничение на максимальную длительность входного аудиофайла.",
            "Реализовать выгрузку батчей в CPU RAM при риске OOM."
        ]
    elif "connection slots" in err_lower or "connection pool" in err_lower:
        diagnosis = (
            f"Сервис **{service}** исчерпал пул соединений с базой данных. "
            f"Новые транзакции блокируются, вызывая цепную остановку зависимых обработчиков."
        )
        fix_steps = [
            "Увеличить `max_connections` или подключить балансировщик пула (PgBouncer).",
            "Проверить сервис на утечку открытых сессий (забытые session.close()).",
            "Снизить таймаут удержания неактивных соединений (idle timeout)."
        ]
    elif "timeout" in err_lower or "504" in err_lower:
        diagnosis = (
            f"Таймаут сетевого вызова в **{service}**: вышестоящий сервис или внешний API "
            f"не вернул ответ за отведенный лимит времени."
        )
        fix_steps = [
            "Проверить доступность и задержки внешнего эндпоинта.",
            "Настроить экспоненциальный retry с джиттером и локальный fallback-ответ."
        ]

    # Формируем компактную цепочку последствий
    consequences = []
    for step in cascade[1:]:
        svc = step.get("service") or step.get("affected_service")
        msg = step.get("message") or step.get("raw_message") or ""
        if svc and svc not in consequences:
            consequences.append(f"`{svc}`")

    impact_text = f"Сбой распространился на узлы: {', '.join(consequences)}." if consequences else "Сбой локализован внутри сервиса, каскад не вышел за границы узла."

    fix_list = "\n".join([f"{idx+1}. {step}" for idx, step in enumerate(fix_steps)])

    return f"""### Суть инцидента
{diagnosis}
Trace ID: `{trace_id}`.

### Влияние на систему
{impact_text}

### Что сделать для устранения:
{fix_list}"""


def check_ollama_available() -> bool:
    """Проверяет доступность локального сервера Ollama."""
    try:
        import urllib.request
        tags_url = getattr(settings, "ollama_url", "http://127.0.0.1:11434/api/generate").replace("/api/generate", "/api/tags")
        with urllib.request.urlopen(tags_url, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_llm_config():
    """Определяет активный LLM провайдер и модель на основе настроек и окружения."""
    provider = os.getenv("LLM_PROVIDER") or getattr(settings, "llm_provider", "groq")
    model = os.getenv("LLM_MODEL") or getattr(settings, "llm_model", "llama-3.3-70b-versatile")
    api_key = os.getenv("GROQ_API_KEY") or getattr(settings, "groq_api_key", None)

    if provider == "groq":
        if api_key:
            return api_key, model, "groq", "https://api.groq.com/openai/v1/chat/completions"
        return None, "Rule-Based Engine", "fallback", None

    if provider == "ollama":
        if check_ollama_available():
            url = getattr(settings, "ollama_url", "http://127.0.0.1:11434/api/generate")
            return None, model, "ollama", url
        return None, "Rule-Based Engine", "fallback", None

    if provider == "fallback":
        return None, "Rule-Based Engine", "fallback", None

    openai_key = os.getenv("OPENAI_API_KEY") or getattr(settings, "openai_api_key", None)
    if openai_key and provider == "openai":
        return openai_key, model or "gpt-4o-mini", "openai", "https://api.openai.com/v1/chat/completions"

    return None, "Rule-Based Engine", "fallback", None


def get_llm_temperature() -> float:
    try:
        val = os.getenv("LLM_TEMPERATURE") or getattr(settings, "llm_temperature", 0.2)
        return float(val)
    except Exception:
        return 0.2


def get_llm_status() -> Dict[str, str]:
    """Возвращает статус готовности LLM для веб-дашборда."""
    api_key, model, provider, _ = get_llm_config()
    if provider == "groq" and api_key:
        return {"status": "ready", "model": model, "provider": "Groq Cloud"}
    if provider == "ollama":
        return {"status": "ready", "model": model, "provider": "Local Ollama"}
    if provider == "openai" and api_key:
        return {"status": "ready", "model": model, "provider": "OpenAI"}
    return {"status": "fallback", "model": "Rule-Based Engine", "provider": "Fallback"}



# Экспортируемые переменные для совместимости с api.py
_api_key, MODEL_NAME, _provider, _ = get_llm_config()
client = object() if (_api_key or _provider == "ollama") else None


async def generate_rca_summary(incident: Dict[str, Any]) -> str:
    """Генерация осмысленного, человечного анализа с помощью LLM (Ollama/Groq/OpenAI) с fallback."""
    api_key, model_name, provider, base_url = get_llm_config()
    if provider == "fallback":
        return _build_fallback_report(incident)

    srv = incident.get("first_affected_service") or incident.get("service") or "Unknown"
    trace = incident.get("trace_id") or "N/A"
    root = incident.get("root_cause_candidate") or {}
    raw_err = root.get("raw_message") or incident.get("raw_message") or ""
    cascade = incident.get("cascade_flow") or []
    
    flow_services = []
    for step in cascade:
        s = step.get("service") or step.get("affected_service")
        if s and s not in flow_services:
            flow_services.append(s)
    cascade_chain = " -> ".join(flow_services) if flow_services else srv

    system_instruction = (
        "Ты — Senior SRE инженер. Твоя задача — формировать краткие, четкие RCA-отчеты по авариям. "
        "ОБЯЗАТЕЛЬНОЕ ПРАВИЛО: Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке. "
        "Категорически запрещено использовать китайский язык, китайские иероглифы (Chinese characters) и любые эмодзи. "
        "Все формулировки строго по-русски, в лаконичном инженерном стиле."
    )

    prompt = f"""Ты — Senior SRE инженер. Напиши краткий, четкий RCA-отчет по аварии.
ЯЗЫК: ИСКЛЮЧИТЕЛЬНО РУССКИЙ. Запрещены любые китайские иероглифы, иностранный язык (кроме названий сервисов и кодов ошибок) и любые эмодзи.

СЕРВИС: {srv}
TRACE: {trace}
ОШИБКА: {raw_err[:250]}
КАСКАД: {cascade_chain}

Ответь строго в 3 коротких пунктах без вводных слов на русском языке:
- **Что произошло**: суть первопричины.
- **Каскадный эффект**: влияние на систему.
- **Шаги решения**: 2-3 практических действия."""

    if provider == "ollama":
        payload = {
            "model": model_name,
            "system": system_instruction,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": get_llm_temperature(),
                "num_predict": 260
            }
        }
        try:
            timeout_sec = float(getattr(settings, "llm_timeout_seconds", 60.0))
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                resp = await client.post(base_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    text = (data.get("response") or "").strip()
                    if text:
                        if _contains_chinese(text):
                            print(f"[!] Внимание: Модель {model_name} вернула китайский текст. Использую fallback-отчет.", flush=True)
                            return _build_fallback_report(incident)
                        return text
                print(f"[!] Ошибка Ollama API: {resp.status_code} {resp.text}", flush=True)
                return _build_fallback_report(incident)
        except Exception as e:
            print(f"[!] Сбой вызова Ollama ({type(e).__name__}): {e or 'Timeout'}", flush=True)
            return _build_fallback_report(incident)

    # Cloud OpenAI / Groq
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        "temperature": get_llm_temperature(),
        "max_tokens": 600
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(base_url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices")
                if choices and len(choices) > 0:
                    content = choices[0].get("message", {}).get("content", "")
                    if content:
                        if _contains_chinese(content):
                            print(f"[!] Внимание: Cloud LLM вернула китайский текст. Использую fallback-отчет.", flush=True)
                            return _build_fallback_report(incident)
                        return content.strip()
            print(f"[!] Ошибка LLM API: {resp.status_code} {resp.text}", flush=True)
            return _build_fallback_report(incident)
    except Exception as e:
        print(f"[!] Сбой вызова LLM: {e}", flush=True)
        return _build_fallback_report(incident)


async def stream_rca_summary(incident: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """Генерация осмысленного RCA-отчёта в Streaming mode (по токенам в реальном времени)."""
    api_key, model_name, provider, base_url = get_llm_config()

    if provider == "fallback":
        report = _build_fallback_report(incident)
        for chunk in report.split(" "):
            yield chunk + " "
            await asyncio.sleep(0.015)
        return

    srv = incident.get("first_affected_service") or incident.get("service") or "Unknown"
    trace = incident.get("trace_id") or "N/A"
    root = incident.get("root_cause_candidate") or {}
    raw_err = root.get("raw_message") or incident.get("raw_message") or ""
    cascade = incident.get("cascade_flow") or []

    flow_services = []
    for step in cascade:
        s = step.get("service") or step.get("affected_service")
        if s and s not in flow_services:
            flow_services.append(s)
    cascade_chain = " -> ".join(flow_services) if flow_services else srv

    system_instruction = (
        "Ты — Senior SRE инженер. Твоя задача — формировать краткие, четкие RCA-отчеты по авариям. "
        "ОБЯЗАТЕЛЬНОЕ ПРАВИЛО: Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке. "
        "Категорически запрещено использовать китайский язык, китайские иероглифы (Chinese characters) и любые эмодзи. "
        "Все формулировки строго по-русски, в лаконичном инженерном стиле."
    )

    prompt = f"""Ты — Senior SRE инженер. Напиши краткий, четкий RCA-отчет по аварии.
ЯЗЫК: ИСКЛЮЧИТЕЛЬНО РУССКИЙ. Запрещены любые китайские иероглифы, иностранный язык (кроме названий сервисов и кодов ошибок) и любые эмодзи.

СЕРВИС: {srv}
TRACE: {trace}
ОШИБКА: {raw_err[:250]}
КАСКАД: {cascade_chain}

Ответь строго в 3 коротких пунктах без вводных слов на русском языке:
- **Что произошло**: суть первопричины.
- **Каскадный эффект**: влияние на систему.
- **Шаги решения**: 2-3 практических действия."""

    if provider == "ollama":
        payload = {
            "model": model_name,
            "system": system_instruction,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": get_llm_temperature(),
                "num_predict": 260
            }
        }
        accumulated = []
        try:
            timeout_sec = float(getattr(settings, "llm_timeout_seconds", 60.0))
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                async with client.stream("POST", base_url, json=payload) as resp:
                    if resp.status_code == 200:
                        async for line in resp.aiter_lines():
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                token = data.get("response", "")
                                if token:
                                    accumulated.append(token)
                                    yield token
                                if data.get("done"):
                                    break
                            except Exception:
                                continue
                        full_res = "".join(accumulated)
                        if _contains_chinese(full_res):
                            print(f"[!] Ollama вернула китайский текст при стриминге. Досылаем fallback.", flush=True)
                            fallback = _build_fallback_report(incident)
                            yield "\n\n" + fallback
                        return
                    else:
                        print(f"[!] Ollama stream вернула код {resp.status_code}, переключаюсь на fallback", flush=True)
        except Exception as e:
            print(f"[!] Ollama stream прерван ({e}), использую fallback", flush=True)

        report = _build_fallback_report(incident)
        for chunk in report.split(" "):
            yield chunk + " "
            await asyncio.sleep(0.015)
        return

    # Cloud OpenAI / Groq
    if provider in ("groq", "openai") and api_key:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "temperature": get_llm_temperature(),
            "max_tokens": 600,
            "stream": True
        }
        accumulated = []
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                async with client.stream("POST", base_url, headers=headers, json=payload) as resp:
                    if resp.status_code == 200:
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                piece = line[6:].strip()
                                if piece == "[DONE]":
                                    break
                                try:
                                    data = json.loads(piece)
                                    choices = data.get("choices") or []
                                    if choices:
                                        token = choices[0].get("delta", {}).get("content", "")
                                        if token:
                                            accumulated.append(token)
                                            yield token
                                except Exception:
                                    continue
                        full_res = "".join(accumulated)
                        if _contains_chinese(full_res):
                            print(f"[!] Cloud LLM вернула китайский текст при стриминге. Досылаем fallback.", flush=True)
                            yield "\n\n" + _build_fallback_report(incident)
                        return
                    else:
                        print(f"[!] Cloud LLM stream вернула {resp.status_code}, переключаюсь на fallback", flush=True)
        except Exception as e:
            print(f"[!] Cloud LLM stream прерван ({e}), использую fallback", flush=True)

    report = _build_fallback_report(incident)
    for chunk in report.split(" "):
        yield chunk + " "
        await asyncio.sleep(0.015)


def _build_sre_assistant_fallback(user_message: str, incident_data: Optional[Dict[str, Any]] = None) -> str:
    """Умный автономный генератор ответов SRE-ассистента без внешних API и эмодзи."""
    msg_low = user_message.lower()
    srv = (incident_data or {}).get("first_affected_service", "service-worker")
    trace = (incident_data or {}).get("trace_id", "N/A")
    root = (incident_data or {}).get("root_cause_candidate", {})
    raw_err = root.get("raw_message") or (incident_data or {}).get("raw_message") or ""

    if any(k in msg_low for k in ["bash", "скрипт", "команд", "перезапуск", "reboot", "restart"]):
        return f"""### Скрипт восстановления для сервиса `{srv}`:
```bash
# 1. Завершить подвисший процесс воркера
kill -9 $(pgrep -f "{srv}") 2>/dev/null || docker restart {srv} 2>/dev/null || sudo systemctl restart {srv}

# 2. Очистить выделенную память GPU/VRAM и временные файлы
python -c "import torch; torch.cuda.empty_cache()" 2>/dev/null

# 3. Проверить статус восстановления в живых логах
tail -n 25 live_service.log | grep "{srv}"
```
*Совет: проверьте лимиты VRAM/Memory в конфигурации сервиса для предотвращения повторного сбоя.*"""

    if any(k in msg_low for k in ["sql", "баз", "запрос", "postgres", "pg"]):
        return """### Диагностические SQL-запросы для PostgreSQL:
```sql
-- 1. Поиск активных блокировок и зависших транзакций
SELECT pid, age(clock_timestamp(), query_start), usename, state, query 
FROM pg_stat_activity 
WHERE state != 'idle' AND query NOT ILIKE '%pg_stat_activity%'
ORDER BY query_start ASC LIMIT 10;

-- 2. Состояние пула соединений
SELECT count(*), state FROM pg_stat_activity GROUP BY state;

-- 3. Принудительное освобождение слотов (при исчерпании пула)
-- SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction';
```"""

    if any(k in msg_low for k in ["post-mortem", "постмортем", "отчет", "report"]):
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        return f"""# Post-Mortem Отчёт
**Сервис:** `{srv}` | **Trace ID:** `{trace}` | **Дата:** {now_str}

### 1. Суть инцидента
Зафиксирован сбой узла с нарушением непрерывности обработки запросов.
{f'**Первопричина:** `{raw_err[:150]}`' if raw_err else ''}

### 2. Влияние на систему
Вызовы зависимых сервисов были заблокированы или завершились по таймауту.

### 3. Регламентные превентивные меры:
1. Внедрить автоматический circuit breaker на стороне клиентов.
2. Снизить нагрузку на вычислительные воркеры через ограничение размера входного батча.
3. Настроить авто-алерт в Telegram при превышении 80% утилизации ресурсов."""

    if any(k in msg_low for k in ["устран", "исправ", "почему", "причина", "как"]):
        cause_expl = "исчерпание доступных аппаратных ресурсов (VRAM / пул коннектов) либо таймаут зависимости."
        if "cuda" in raw_err.lower() or "oom" in raw_err.lower():
            cause_expl = "переполнение видеопамяти GPU (CUDA Out of Memory) из-за слишком большого размера модели или параметров инференса (beam_size)."
        elif "connection" in raw_err.lower() or "slots" in raw_err.lower():
            cause_expl = "исчерпание лимита подключений к базе данных PostgreSQL (max_connections)."

        return f"""### План устранения аварии `{srv}`:
1. **Причина:** {cause_expl}
2. **Шаг 1:** Примените перезапуск узла (используйте кнопку «Сгенерировать bash-скрипт»).
3. **Шаг 2:** Проверьте актуальную нагрузку на хосте.
4. **Шаг 3:** После стабилизации логов нажмите кнопку «Решено» в карточке инцидента."""

    return f"""### SRE-Ассистент
Анализирую ваш запрос: *«{user_message}»*
{'Контекст активной аварии: `' + trace + '`' if trace != 'N/A' else 'Режим мониторинга всей системы'}.

**Рекомендации инженера:**
- Используйте быстрые кнопки сверху для мгновенной генерации **bash-скриптов** или **SQL-диагностики**.
- Для расследования конкретной аварии выберите её в селекторе контекста выше.
- Все действия логируются и доступны для команды в реальном времени."""


async def ask_sre_assistant(
    user_message: str,
    incident_data: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, str]]] = None
) -> str:
    """Интерактивный диалог с SRE-ассистентом по инциденту или общему состоянию системы."""
    api_key, model_name, provider, base_url = get_llm_config()

    # 1. Fallback режим (без LLM)
    if provider == "fallback":
        return _build_sre_assistant_fallback(user_message, incident_data)

    incident_context_block = ""
    if incident_data:
        srv = incident_data.get("first_affected_service") or incident_data.get("service") or "Unknown"
        trace = incident_data.get("trace_id") or "N/A"
        root = incident_data.get("root_cause_candidate") or {}
        raw_err = root.get("raw_message") or incident_data.get("raw_message") or ""
        cascade = incident_data.get("cascade_flow") or []
        incident_context_block = f"""
КОНТЕКСТ ВЫБРАННОГО ИНЦИДЕНТА:
- Сервис: {srv}
- Trace ID: {trace}
- Корневая ошибка: {raw_err}
- Шаги каскада ({len(cascade)}): {json.dumps(cascade, ensure_ascii=False)}
"""

    system_prompt = f"""Ты — автономный SRE-ассистент платформы мониторинга и самоисцеления сервисов.
Твоя цель: помогать инженеру оперативно расследовать аварии, писать скрипты устранения (bash, python, docker, kubectl, SQL), давать четкие рекомендации по надежности.
ОБЯЗАТЕЛЬНОЕ ПРАВИЛО: Отвечай структурированно, профессионально, ИСКЛЮЧИТЕЛЬНО на русском языке. Категорически запрещено использовать китайский язык, китайские иероглифы (Chinese characters) и любые эмодзи. При необходимости приводи готовые к запуску команды в блоках кода с пояснениями.
{incident_context_block}"""

    # 2. Локальная Ollama
    if provider == "ollama":
        prompt_full = f"Пользователь: {user_message}\nSRE-Ассистент:"
        payload = {
            "model": model_name,
            "system": system_prompt,
            "prompt": prompt_full,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 350}
        }
        try:
            timeout_sec = 25.0
            async with httpx.AsyncClient(timeout=timeout_sec) as cl:
                resp = await cl.post(base_url, json=payload)
                if resp.status_code == 200:
                    ans = (resp.json().get("response") or "").strip()
                    if ans:
                        if _contains_chinese(ans):
                            print(f"[!] Внимание: Ollama в чате вернула китайский текст. Использую fallback.", flush=True)
                            return _build_sre_assistant_fallback(user_message, incident_data)
                        return ans
        except Exception as ex:
            print(f"[!] Ollama не ответила вовремя: {ex}. Отдаю структурированный ответ.", flush=True)

        return _build_sre_assistant_fallback(user_message, incident_data)

    # 3. Cloud LLM (Groq / OpenAI)
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for h in history[-4:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model_name, "messages": messages, "temperature": 0.3, "max_tokens": 800}

    try:
        async with httpx.AsyncClient(timeout=20.0) as cl:
            resp = await cl.post(base_url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    ans = data["choices"][0]["message"]["content"].strip()
                    if _contains_chinese(ans):
                        print(f"[!] Внимание: Cloud LLM вернула китайский текст в чате. Использую fallback.", flush=True)
                        return _build_sre_assistant_fallback(user_message, incident_data)
                    return ans
    except Exception as ex:
        print(f"[!] Ошибка вызова Cloud LLM: {ex}", flush=True)

    return _build_sre_assistant_fallback(user_message, incident_data)


