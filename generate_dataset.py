#!/usr/bin/env python3
"""
SRE Production Log Dataset Generator
====================================
Генератор реалистичных логов микросервисной архитектуры Audio-Scriber / Whisper SRE:
- 10 взаимосвязанных микросервисов (api-gateway, auth-service, audio-scriber, whisper-engine, llm-agent, etc.)
- Штатный фоновый трафик (INFO, DEBUG, WARN) с реалистичными IP, User ID, задержками и метриками
- 6 каскадных инцидентов со сквозными trace_id для проверки Root Cause Analysis (RCA) и алертинга

Режимы:
  --mode seed     : Быстрое заполнение PostgreSQL базы ~350 реалистичными записями за последние 24ч
  --mode live     : Реалтайм-стриминг логов в live_service.log (проверка watcher, WebSocket и Telegram)
  --mode file     : Экспорт эталонного датасета в файл datasets/production_sample.log
  --mode clear    : Очистка тестовых записей из базы данных
"""

import os
import sys
import time
import random
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple

# Установка UTF-8 для консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Импорт моделей и сессии
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.db import SessionLocal
from src.models import LogEvent, LogTemplate
from src.parser import ingest_log_batch, parse_raw_log

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
LIVE_LOG_FILE = Path(__file__).resolve().parent / "live_service.log"

# Репертуар пользователей и IP
USERS = [
    "usr_duty_sre",
    "usr_alex_dev",
    "usr_elena_pm",
    "usr_dmitry_qa",
    "usr_support_agent",
    "usr_premium_77",
    "usr_bot_runner",
    "usr_podcast_host",
]

CLIENT_IPS = [
    "94.25.180.6",
    "185.191.171.12",
    "178.62.194.22",
    "85.140.2.19",
    "95.173.136.71",
    "46.138.89.12",
    "212.45.12.8",
    "10.0.1.14",
    "10.0.2.8",
    "10.0.4.102",
]

SERVICES = [
    "api-gateway",
    "auth-service",
    "audio-scriber-service",
    "whisper-engine",
    "llm-agent-service",
    "postgres-cluster",
    "redis-cache",
    "telegram-notifier",
    "storage-s3-service",
    "billing-api",
]

# ---------------------------------------------------------------------------
# Генераторы штатного трафика
# ---------------------------------------------------------------------------

NORMAL_TEMPLATES: List[Tuple[str, str, str]] = [
    # (service, level, message_template)
    # API Gateway
    ("api-gateway", "INFO", "GET /api/v2/transcribe/status?job_id={job_id} HTTP/1.1 200 OK {dur}ms bytes_sent={bytes}"),
    ("api-gateway", "INFO", "POST /api/v2/transcribe/upload HTTP/1.1 202 Accepted {dur}ms upload_size={size_kb}KB"),
    ("api-gateway", "INFO", "POST /oauth/token HTTP/1.1 200 OK {dur}ms grant_type=refresh_token"),
    ("api-gateway", "INFO", "GET /health HTTP/1.1 200 OK 2ms status=healthy cluster=prod-k8s-eu"),
    ("api-gateway", "INFO", "POST /api/v2/summarize HTTP/1.1 200 OK {dur}ms model=llama-3.3-70b tokens={tokens}"),
    ("api-gateway", "DEBUG", "TLS handshake completed in {dur}ms cipher=TLS_AES_256_GCM_SHA384 client_ip={ip}"),
    ("api-gateway", "WARN", "Client {ip} reached 82% of sliding-window rate limit (100/120 rpm)"),

    # Auth Service
    ("auth-service", "INFO", "JWT verification succeeded for {user_id} (issuer=auth.sre.internal, alg=RS256, exp=+86400s)"),
    ("auth-service", "INFO", "Session token refreshed for {user_id}, sliding TTL extended by 86400s"),
    ("auth-service", "INFO", "Scope inspection granted: scopes=['transcribe:write', 'analytics:read'] for {user_id}"),
    ("auth-service", "DEBUG", "HMAC key rotation check: active key key_v2 (expires in 14d)"),
    ("auth-service", "WARN", "Cache MISS for user session {user_id}, falling back to postgresql fetch ({dur}ms)"),

    # Audio Scriber Service
    ("audio-scriber-service", "INFO", "Received multipart audio stream (codec={codec}, channels=1, sample_rate=48000Hz, size_bytes={size_bytes})"),
    ("audio-scriber-service", "INFO", "FFmpeg normalization completed: 16kHz mono PCM in {dur}ms (loudnorm=-14LUFS)"),
    ("audio-scriber-service", "INFO", "Dispatched task {task_id} to celery queue 'whisper_high_priority' (priority=1)"),
    ("audio-scriber-service", "INFO", "Transcription completed for job {job_id} in {dur}ms (rtf={rtf}, detected_language='ru', confidence={conf})"),
    ("audio-scriber-service", "DEBUG", "Audio chunk temp file /tmp/audio_{job_id}.pcm unlinked cleanly"),
    ("audio-scriber-service", "WARN", "Downstream whisper response took {dur}ms (> threshold 2500ms) for audio_dur={audio_dur}s"),

    # Whisper Engine
    ("whisper-engine", "INFO", "Worker whisper-gpu-node-01 ready (device=cuda:0, VRAM_free={vram_free}MB, compute_type=float16)"),
    ("whisper-engine", "INFO", "Loaded audio segment duration={audio_dur}s, running Silero VAD: speech_segments={seg_count}"),
    ("whisper-engine", "INFO", "Encoder forward pass completed in {dur}ms (batch_size=1, beam_size=1)"),
    ("whisper-engine", "INFO", "Decoder finished: {tokens} tokens generated, text='{text_sample}'"),
    ("whisper-engine", "DEBUG", "Faster-Whisper KV cache recycled: allocated={vram_alloc}MB peak={vram_peak}MB"),
    ("whisper-engine", "WARN", "VRAM memory pressure at 78%: 6240MB / 8000MB currently utilized on GPU 0"),

    # LLM Agent Service
    ("llm-agent-service", "INFO", "Prompt rendered with {tokens} tokens for executive summarization pipeline"),
    ("llm-agent-service", "INFO", "Calling Groq API (model=llama-3.3-70b-versatile, temperature=0.2, max_tokens=500)"),
    ("llm-agent-service", "INFO", "Groq response received in {dur}ms (prompt_tokens={p_tok}, completion_tokens={c_tok}, tps={tps})"),
    ("llm-agent-service", "DEBUG", "Parsed structured entities: action_items=4, key_decisions=1, speakers_identified=2"),
    ("llm-agent-service", "WARN", "Upstream Groq API latency elevated: {dur}ms (p99 threshold is 1200ms)"),

    # Postgres Cluster
    ("postgres-cluster", "INFO", "checkpoint starting: time"),
    ("postgres-cluster", "INFO", "checkpoint complete: wrote {bufs} buffers ({pct}%), {run_s}s run, {sync_s}s sync"),
    ("postgres-cluster", "INFO", "LOG: duration: {dur} ms statement: SELECT * FROM user_subscriptions WHERE user_id = '{user_id}' LIMIT 1;"),
    ("postgres-cluster", "INFO", "LOG: duration: {dur} ms statement: INSERT INTO audio_jobs (job_id, user_id, duration_sec, status) VALUES ('{job_id}', '{user_id}', {audio_dur}, 'COMPLETED');"),
    ("postgres-cluster", "DEBUG", "Autovacuum worker 4102 completed on table 'log_events': 1200 tuples removed, 18 pages vacuumed"),
    ("postgres-cluster", "WARN", "Slow query detected on table 'audio_jobs' duration={dur}ms statement: SELECT count(*) FROM audio_jobs;"),

    # Redis Cache
    ("redis-cache", "INFO", "Accepted connection from auth-service (client_id={cid}, addr=10.0.2.14:48122)"),
    ("redis-cache", "INFO", "DB 0: {keys} keys, {exp} expiring, maxmemory: 2147483648B, used: {used_mb}MB (39.2%)"),
    ("redis-cache", "INFO", "Key 'session:{user_id}' refreshed with TTL 86400s"),
    ("redis-cache", "DEBUG", "ZREMRANGEBYSCORE on queue:whisper_tasks removed 0 expired tasks"),
    ("redis-cache", "WARN", "Memory fragmentation ratio is 1.42 (used_memory_rss={rss_mb}MB, used_memory={used_mb}MB)"),

    # Telegram Notifier
    ("telegram-notifier", "INFO", "Dispatching async notification for {user_id} via Bot API"),
    ("telegram-notifier", "INFO", "HTTP POST https://api.telegram.org/bot/sendMessage: 200 OK (latency={dur}ms, msg_id={mid})"),
    ("telegram-notifier", "INFO", "Polling update received: message_id={mid}, chat_id={user_id}, type=voice (duration={audio_dur}s)"),
    ("telegram-notifier", "DEBUG", "CDN audio payload downloaded: size={size_mb}MB, download_time={dur}ms"),
    ("telegram-notifier", "WARN", "Telegram API responded with 502 Bad Gateway; retry 1/3 scheduled in 500ms"),

    # Storage S3
    ("storage-s3-service", "INFO", "PutObject s3://audio-vault/raw/2026/10/{job_id}.opus - 200 OK (latency={dur}ms)"),
    ("storage-s3-service", "INFO", "GetObject s3://audio-vault/raw/2026/10/{job_id}.opus - 200 OK (size={size_mb}MB, latency={dur}ms)"),
    ("storage-s3-service", "DEBUG", "S3 multipart upload completed: 4 parts assembled in {dur}ms etag=3f81e9..."),
    ("storage-s3-service", "WARN", "Storage pool disk volume /data/minio-01 at 84% capacity (16.2GB free remaining)"),

    # Billing API
    ("billing-api", "INFO", "Subscription status verified for {user_id}: plan=PRO status=ACTIVE quota_remaining=420min"),
    ("billing-api", "INFO", "Usage decrement: deducted {mins} audio minutes for {user_id}"),
    ("billing-api", "DEBUG", "Stripe webhook processed: invoice.paid event_id=evt_{job_id}"),
]

TEXT_SAMPLES = [
    "Здравствуйте, отправляю запись встречи по планированию спринта 42",
    "Проверили интеграцию с Whisper, точность распознавания отличная",
    "Коллеги, напоминаю про деплой новой версии сервиса в 18:00",
    "Краткое резюме звонка: договорились переключить модель на qwen2.5",
    "Пожалуйста, сформируйте выгрузку логов за вчерашний день",
]


def render_normal_message(template: str, user_id: str, client_ip: str, job_id: str) -> str:
    """Подставляет реалистичные параметры в шаблон сообщения."""
    return template.format(
        user_id=user_id,
        ip=client_ip,
        job_id=job_id,
        task_id=f"task_{random.randint(10000, 99999)}",
        dur=random.randint(8, 380),
        bytes=random.randint(1024, 65536),
        size_kb=random.randint(120, 4800),
        size_bytes=random.randint(100000, 5000000),
        size_mb=round(random.uniform(0.8, 6.5), 1),
        tokens=random.randint(45, 1200),
        p_tok=random.randint(400, 1800),
        c_tok=random.randint(80, 320),
        tps=random.randint(180, 240),
        audio_dur=round(random.uniform(5.0, 95.0), 1),
        rtf=round(random.uniform(0.08, 0.22), 2),
        conf=round(random.uniform(0.94, 0.99), 2),
        vram_free=random.randint(3800, 6900),
        vram_alloc=random.randint(1800, 3400),
        vram_peak=random.randint(3100, 5200),
        seg_count=random.randint(1, 8),
        text_sample=random.choice(TEXT_SAMPLES),
        bufs=random.randint(150, 800),
        pct=round(random.uniform(1.2, 5.8), 1),
        run_s=round(random.uniform(0.1, 0.4), 1),
        sync_s=round(random.uniform(0.2, 0.8), 1),
        cid=random.randint(10000, 99999),
        keys=random.randint(12000, 18000),
        exp=random.randint(120, 450),
        used_mb=random.randint(620, 940),
        rss_mb=random.randint(950, 1280),
        mid=random.randint(1000, 9999),
        mins=random.randint(1, 15),
        codec=random.choice(["opus", "aac", "mp3", "ogg"]),
    )


# ---------------------------------------------------------------------------
# 6 Каскадных инцидентов со сквозными Trace ID
# ---------------------------------------------------------------------------

def generate_incidents() -> List[Dict[str, Any]]:
    """Возвращает список из 6 реалистичных каскадных аварий."""
    return [
        # Инцидент 1: Whisper CUDA VRAM OOM
        {
            "id": "whisper-oom",
            "trace_id": "trace-whisper-oom-4819",
            "service": "whisper-engine",
            "user_id": "usr_duty_sre",
            "client_ip": "94.25.180.6",
            "title": "CUDA Out of Memory в Whisper GPU воркере",
            "events": [
                ("audio-scriber-service", "INFO", "Incoming audio job duration=740.0s (size=72.4MB), worker dispatched to whisper-gpu-node-02"),
                ("whisper-engine", "WARN", "VRAM allocation spike: 7.48 GiB / 8.00 GiB consumed, initiating beam_size=5 search on long audio context"),
                ("whisper-engine", "CRITICAL", "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.15 GiB (GPU 0; 8.00 GiB total capacity; 7.52 GiB already allocated by PyTorch; 0 bytes free)"),
                ("audio-scriber-service", "ERROR", "Worker whisper-gpu-node-02 terminated with SIGKILL (exit code 137). Transcription job failed ungracefully"),
                ("api-gateway", "ERROR", "Upstream audio-scriber-service returned 502 Bad Gateway for POST /api/v2/transcribe/process"),
                ("telegram-notifier", "ERROR", "Failed to deliver transcription callback: Job error status 502 returned to user_id=usr_duty_sre"),
            ]
        },
        # Инцидент 2: PostgreSQL Pool Exhaustion
        {
            "id": "pg-pool-exhaust",
            "trace_id": "trace-pg-pool-exhaust-7721",
            "service": "postgres-cluster",
            "user_id": "usr_alex_dev",
            "client_ip": "185.191.171.12",
            "title": "Исчерпание пула соединений PostgreSQL (max_connections)",
            "events": [
                ("postgres-cluster", "WARN", "WARNING: active connections (98/100) approaching max_connections limit. Slow query on 'audio_audit_logs' duration=42.1s"),
                ("postgres-cluster", "CRITICAL", "FATAL: remaining connection slots are reserved for non-replication superuser connections (max_connections=100 reached)"),
                ("auth-service", "ERROR", "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 10 reached, connection timed out, timeout 5.00s"),
                ("billing-api", "ERROR", "500 Internal Server Error: Failed to acquire database transaction for subscription validation"),
                ("api-gateway", "ERROR", "Upstream auth-service timed out: 504 Gateway Timeout on POST /oauth/token"),
            ]
        },
        # Инцидент 3: Groq LLM API Rate Limit 429
        {
            "id": "groq-rate-limit",
            "trace_id": "trace-groq-rate-limit-9014",
            "service": "llm-agent-service",
            "user_id": "usr_premium_77",
            "client_ip": "178.62.194.22",
            "title": "Превышение лимита TPM в Groq API (HTTP 429)",
            "events": [
                ("llm-agent-service", "INFO", "Submitting transcript (3800 tokens) to Groq API endpoint for executive summary generation"),
                ("llm-agent-service", "CRITICAL", "HTTP 429 Too Many Requests from api.groq.com: Rate limit reached for model 'llama-3.3-70b-versatile' on TPM: Limit 30000, Used 31850 (retry-after: 15s)"),
                ("llm-agent-service", "ERROR", "Upstream Groq connection timeout after 15000ms. Fallback to local Ollama failed: Ollama daemon not reachable at http://127.0.0.1:11434"),
                ("audio-scriber-service", "ERROR", "ERROR: analyze_with_llm: summary generation pipeline aborted ungracefully"),
                ("api-gateway", "WARN", "Serving degraded response (raw transcription only, AI post-processing omitted) with status 206 Partial Content"),
            ]
        },
        # Инцидент 4: Deadlock в базе данных при продлении подписки
        {
            "id": "db-deadlock",
            "trace_id": "trace-db-deadlock-3382",
            "service": "postgres-cluster",
            "user_id": "usr_support_agent",
            "client_ip": "85.140.2.19",
            "title": "Взаимная блокировка (Deadlock) в таблице подписок",
            "events": [
                ("postgres-cluster", "WARN", "WARNING: transaction 89128 locked table 'user_subscriptions' in ExclusiveLock; transaction 89134 waiting"),
                ("postgres-cluster", "CRITICAL", "ERROR: deadlock detected. Process 14023 waits for ShareLock on transaction 89128; Process 14028 waits for ExclusiveLock on tuple (42, 18) of relation 'user_subscriptions'"),
                ("billing-api", "ERROR", "psycopg2.errors.DeadlockDetected: deadlock detected during subscription quota update. Transaction aborted"),
                ("api-gateway", "ERROR", "HTTP 500 Internal Server Error: POST /api/v2/user/billing/renew failed for user_id=usr_support_agent"),
            ]
        },
        # Инцидент 5: Redis Maxmemory Eviction Failure
        {
            "id": "redis-oom",
            "trace_id": "trace-redis-oom-6641",
            "service": "redis-cache",
            "user_id": "usr_bot_runner",
            "client_ip": "95.173.136.71",
            "title": "Переполнение оперативной памяти Redis (maxmemory OOM)",
            "events": [
                ("redis-cache", "WARN", "Memory usage exceeded 95% threshold: 2041MB / 2048MB allocated"),
                ("redis-cache", "CRITICAL", "OOM command not allowed when used memory > 'maxmemory'. Eviction policy 'noeviction' prevented key removal"),
                ("auth-service", "ERROR", "redis.exceptions.ResponseError: OOM command not allowed. Failed to store session for user_id=usr_bot_runner"),
                ("api-gateway", "ERROR", "HTTP 500 Internal Server Error on POST /oauth/token: Redis session persistence failure"),
            ]
        },
        # Инцидент 6: MinIO / S3 Хранилище переполнено
        {
            "id": "s3-diskfull",
            "trace_id": "trace-s3-diskfull-8812",
            "service": "storage-s3-service",
            "user_id": "usr_podcast_host",
            "client_ip": "46.138.89.12",
            "title": "Переполнение дискового пространства S3 / MinIO",
            "events": [
                ("storage-s3-service", "WARN", "Disk volume /data/minio-storage at 99.4% capacity (only 124MB free remaining)"),
                ("storage-s3-service", "CRITICAL", "S3Error: XMinioStorageFull: Storage backend has reached capacity threshold. Write rejected for object 'audio_vault/2026/10/audio_podcast_ep42.mp3'"),
                ("audio-scriber-service", "ERROR", "Failed to persist uploaded audio file: S3 multipart upload aborted with error XMinioStorageFull"),
                ("api-gateway", "ERROR", "HTTP 507 Insufficient Storage: POST /api/v2/transcribe/upload rejected"),
                ("telegram-notifier", "WARN", "Voice message from user_id=usr_podcast_host rejected: unable to store audio payload"),
            ]
        },
    ]


# ---------------------------------------------------------------------------
# Сборка хронологического списка логов
# ---------------------------------------------------------------------------

def format_log_line(dt: datetime, level: str, service: str, trace_id: str, user_id: str, client_ip: str, message: str) -> str:
    """Форматирует строку лога в эталонный формат системы."""
    ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"{ts_str} [{level}] [{service}] [trace_id={trace_id}] [user_id={user_id}] [ip={client_ip}] {message}"


def build_full_dataset(total_normal_count: int = 300) -> List[Tuple[datetime, str, Dict[str, Any]]]:
    """
    Генерирует целостный хронологический список логов за последние 24 часа.
    Возвращает список кортежей: (timestamp, formatted_string, metadata_dict).
    """
    now = datetime.now()
    start_time = now - timedelta(hours=24)
    time_window_sec = int((now - start_time).total_seconds())

    items: List[Tuple[datetime, str, Dict[str, Any]]] = []

    # 1. Генерируем обычный трафик (300 записей)
    for i in range(total_normal_count):
        # Распределяем время с небольшой случайностью в пределах 24 часов
        offset_sec = int((i / total_normal_count) * (time_window_sec - 120)) + random.randint(0, 40)
        dt = start_time + timedelta(seconds=offset_sec)

        service, level, tmpl = random.choice(NORMAL_TEMPLATES)
        user_id = random.choice(USERS)
        client_ip = random.choice(CLIENT_IPS)
        job_id = f"job_{random.randint(1000, 9999)}"
        trace_id = f"req-{service[:4]}-{job_id}"

        msg = render_normal_message(tmpl, user_id, client_ip, job_id)
        line = format_log_line(dt, level, service, trace_id, user_id, client_ip, msg)

        items.append((dt, line, {
            "timestamp": dt,
            "level": level,
            "service": service,
            "trace_id": trace_id,
            "user_id": user_id,
            "client_ip": client_ip,
            "message": msg,
        }))

    # 2. Встраиваем 6 инцидентов в определенные временные точки
    incidents = generate_incidents()
    # Точки аварий: 21ч назад, 16ч назад, 11ч назад, 6ч назад, 2ч назад, 15 минут назад
    incident_offsets_hours = [21.5, 16.2, 11.0, 6.4, 2.1, 0.25]

    for idx, inc in enumerate(incidents):
        hours_ago = incident_offsets_hours[idx] if idx < len(incident_offsets_hours) else random.uniform(1, 23)
        base_inc_dt = now - timedelta(hours=hours_ago)

        trace_id = inc["trace_id"]
        user_id = inc["user_id"]
        client_ip = inc["client_ip"]

        step_delay = 2 # секунды между шагами аварии
        for step_idx, (svc, lvl, raw_msg) in enumerate(inc["events"]):
            ev_dt = base_inc_dt + timedelta(seconds=step_idx * step_delay)
            line = format_log_line(ev_dt, lvl, svc, trace_id, user_id, client_ip, raw_msg)
            items.append((ev_dt, line, {
                "timestamp": ev_dt,
                "level": lvl,
                "service": svc,
                "trace_id": trace_id,
                "user_id": user_id,
                "client_ip": client_ip,
                "message": raw_msg,
            }))

    # Сортируем строго хронологически
    items.sort(key=lambda x: x[0])
    return items


# ---------------------------------------------------------------------------
# Режимы работы
# ---------------------------------------------------------------------------

def seed_database(clean: bool = False) -> int:
    """Заполняет базу данных PostgreSQL сгенерированными логами."""
    print("[SEED] Инициализация генерации реалистичного датасета...")
    items = build_full_dataset(total_normal_count=320)
    raw_lines = [item[1] for item in items]

    session = SessionLocal()
    try:
        if clean:
            print("[SEED] Очистка существующих логов перед заполнением...")
            session.query(LogEvent).delete()
            session.commit()
            print("База данных очищена.")
    except Exception as e:
        session.rollback()
        print(f"[!] Ошибка очистки: {e}")
    finally:
        session.close()

    print(f"[SEED] Сохранение {len(raw_lines)} записей в PostgreSQL (таблица log_events)...")
    inserted = ingest_log_batch(raw_lines)
    print(f"[SEED] Успешно загружено {inserted} логов!")
    print(f"   • Включая 6 каскадных аварий со сквозными Trace ID.")
    print(f"   • Охват временного окна: последние 24 часа.")
    return inserted


def export_to_file(filepath: Path = None) -> Path:
    """Экспортирует датасет в текстовый файл .log"""
    if filepath is None:
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        filepath = DATASETS_DIR / "production_sample.log"

    items = build_full_dataset(total_normal_count=320)
    lines = [item[1] for item in items]

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[EXPORT] Датасет успешно записан в файл: {filepath} ({len(lines)} строк, {round(filepath.stat().st_size/1024, 1)} KB)")
    return filepath


def stream_live_logs(delay_sec: float = 0.8, max_cycles: int = 1):
    """
    Реалтайм-стриминг логов в live_service.log.
    Каждая запись добавляется с текущим временем, позволяя watcher и WebSocket
    обрабатывать логи и аварии в реальном времени.
    """
    print(f"[LIVE STREAM] Запуск эмуляции продакшн-потока в {LIVE_LOG_FILE} (интервал: {delay_sec}s)...")
    print("   (Нажмите Ctrl+C для остановки)")

    incidents = generate_incidents()
    cycle = 0

    try:
        while max_cycles <= 0 or cycle < max_cycles:
            cycle += 1
            print(f"\n--- Цикл #{cycle}: Генерация потока нормального трафика ---")

            # 1. 10-15 обычных запросов
            normal_burst = random.randint(10, 16)
            for _ in range(normal_burst):
                now = datetime.now()
                service, level, tmpl = random.choice(NORMAL_TEMPLATES)
                user_id = random.choice(USERS)
                client_ip = random.choice(CLIENT_IPS)
                job_id = f"job_{random.randint(1000, 9999)}"
                trace_id = f"live-{service[:4]}-{job_id}"
                msg = render_normal_message(tmpl, user_id, client_ip, job_id)

                line = format_log_line(now, level, service, trace_id, user_id, client_ip, msg)
                with open(LIVE_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

                print(f"[{level:<5}] [{service:<22}] {msg[:65]}...")
                time.sleep(delay_sec)

            # 2. Выбираем случайную аварию и запускаем каскад
            incident = random.choice(incidents)
            print(f"\n[ИНИЦИАЛИЗАЦИЯ АВАРИИ]: {incident['title']} (Trace: {incident['trace_id']})")

            for svc, lvl, raw_msg in incident["events"]:
                now = datetime.now()
                line = format_log_line(
                    now, lvl, svc, incident["trace_id"], incident["user_id"], incident["client_ip"], raw_msg
                )
                with open(LIVE_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

                print(f"  [{lvl:<8}] [{svc:<22}] {raw_msg[:70]}...")
                # Небольшая пауза между шагами каскада
                time.sleep(1.2)

            print(f"Каскад аварии завершён. Ожидание реакции Watcher...\n")
            time.sleep(3.0)

    except KeyboardInterrupt:
        print("\n[LIVE STREAM] Стриминг остановлен пользователем.")


def clear_database():
    """Очищает логи из базы данных."""
    session = SessionLocal()
    try:
        count = session.query(LogEvent).count()
        session.query(LogEvent).delete()
        session.commit()
        print(f"[CLEAR] Удалено {count} записей из таблицы log_events.")
    except Exception as e:
        session.rollback()
        print(f"[!] Ошибка очистки: {e}")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CLI точка входа
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Генератор реалистичных продакшн-логов SRE")
    parser.add_argument(
        "--mode",
        choices=["seed", "live", "file", "clear"],
        default="seed",
        help="Режим: seed (вставить в БД), live (стриминг в live_service.log), file (сохранить в файл), clear (очистить БД)"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Очистить старые записи перед заполнением (для --mode seed)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Интервал в секундах между записями в режиме live (по умолчанию 0.8s)"
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Количество циклов для режима live (0 для бесконечного стриминга)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Путь к файлу для режима file (по умолчанию datasets/production_sample.log)"
    )

    args = parser.parse_args()

    if args.mode == "seed":
        seed_database(clean=args.clean)
    elif args.mode == "file":
        out_path = Path(args.output) if args.output else None
        export_to_file(out_path)
    elif args.mode == "live":
        stream_live_logs(delay_sec=args.delay, max_cycles=args.cycles)
    elif args.mode == "clear":
        clear_database()


if __name__ == "__main__":
    main()
