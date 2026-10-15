#!/usr/bin/env python3
"""
SRE Production Log Dataset Generator
====================================
Генератор высокореалистичных продакшн-логов микросервисной экосистемы Audio-Scriber / Whisper SRE:
- 12 взаимосвязанных микросервисов:
    * api-gateway
    * auth-service
    * audio-scriber-service
    * whisper-engine
    * llm-agent-service
    * postgres-cluster
    * redis-cache
    * telegram-notifier
    * storage-s3-service
    * billing-api
    * celery-worker
    * nginx-ingress
- Естественное распределение уровней логирования: ~72% INFO, ~14% DEBUG, ~9% WARN, ~3.8% ERROR, ~1.2% CRITICAL
- Суточный суточный цикл активности (diurnal curve) за последние 48 часов
- Сквозные транзакционные цепочки (correlated transaction flows) с единым trace_id
- 10 детализированных каскадных аварийных сценариев со сквозными trace_id для Root Cause Analysis (RCA)
- Поддержка точного лимита записей (по умолчанию 2000 логов)
"""

import os
import sys
import time
import math
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

USERS = [
    "usr_alex_dev",
    "usr_elena_pm",
    "usr_dmitry_qa",
    "usr_duty_sre",
    "usr_support_agent",
    "usr_premium_77",
    "usr_bot_runner",
    "usr_podcast_host",
    "usr_maria_designer",
    "usr_sergey_cto",
    "usr_artem_devops",
    "usr_anna_analyst",
    "usr_corp_lead",
    "usr_intern_mark",
]

CLIENT_IPS = [
    # Публичные клиентские IP (RU/EU/Global провайдеры)
    "94.25.180.6",
    "185.191.171.12",
    "178.62.194.22",
    "85.140.2.19",
    "95.173.136.71",
    "46.138.89.12",
    "212.45.12.8",
    "195.208.5.1",
    "77.88.55.77",
    "213.180.204.62",
    "89.223.104.14",
    "194.87.144.20",
    # Внутренние узлы кластера VPC
    "10.0.1.14",
    "10.0.2.8",
    "10.0.4.102",
    "10.0.5.21",
    "172.20.0.15",
    "172.20.0.42",
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
    "celery-worker",
    "nginx-ingress",
]

# ---------------------------------------------------------------------------
# Шаблоны регулярного эксплуатационного трафика
# ---------------------------------------------------------------------------

NORMAL_TEMPLATES: List[Tuple[str, str, str]] = [
    # API Gateway
    ("api-gateway", "INFO", "GET /api/v2/transcribe/status?job_id={job_id} HTTP/1.1 200 OK {dur}ms bytes_sent={bytes}"),
    ("api-gateway", "INFO", "POST /api/v2/transcribe/upload HTTP/1.1 202 Accepted {dur}ms upload_size={size_kb}KB"),
    ("api-gateway", "INFO", "POST /oauth/token HTTP/1.1 200 OK {dur}ms grant_type=refresh_token"),
    ("api-gateway", "INFO", "GET /health HTTP/1.1 200 OK 2ms status=healthy cluster=prod-k8s-eu"),
    ("api-gateway", "INFO", "POST /api/v2/summarize HTTP/1.1 200 OK {dur}ms model=llama-3.3-70b tokens={tokens}"),
    ("api-gateway", "INFO", "OPTIONS /api/v2/transcribe/upload HTTP/1.1 204 No Content cors_origin=app.audio-scriber.internal"),
    ("api-gateway", "DEBUG", "TLS handshake completed in {dur}ms cipher=TLS_AES_256_GCM_SHA384 client_ip={ip}"),
    ("api-gateway", "DEBUG", "Route matched: ^/api/v2/transcribe/(.*)$ -> upstream audio-scriber-service:8080"),
    ("api-gateway", "WARN", "Client {ip} reached 82% of sliding-window rate limit (98/120 rpm)"),
    ("api-gateway", "WARN", "Upstream response time {dur}ms exceeded p95 baseline (threshold=350ms) for /api/v2/summarize"),

    # Nginx Ingress
    ("nginx-ingress", "INFO", "10.0.1.1 - - [{time_str}] \"POST /api/v2/transcribe/upload HTTP/2.0\" 202 {bytes} \"-\" \"TelegramBot/1.2 (+https://t.me/AudioScriberBot)\""),
    ("nginx-ingress", "INFO", "10.0.1.1 - - [{time_str}] \"GET /api/v2/transcribe/status HTTP/2.0\" 200 412 \"-\" \"Dart/3.2 (dart:io)\""),
    ("nginx-ingress", "DEBUG", "keepalive connection recycled: fd=42 remote=10.0.2.14:52318"),
    ("nginx-ingress", "DEBUG", "SSL session cache hit for client_ip={ip} session_id={job_id}"),
    ("nginx-ingress", "WARN", "upstream server 10.0.4.102:8000 slow to respond, request time: {dur}ms"),

    # Auth Service
    ("auth-service", "INFO", "JWT verification succeeded for {user_id} (issuer=auth.sre.internal, alg=RS256, exp=+86400s)"),
    ("auth-service", "INFO", "Session token refreshed for {user_id}, sliding TTL extended by 86400s"),
    ("auth-service", "INFO", "Scope inspection granted: scopes=['transcribe:write', 'analytics:read'] for {user_id}"),
    ("auth-service", "INFO", "API key validated for service account sre-agent-service-account (role=system_observability)"),
    ("auth-service", "DEBUG", "HMAC key rotation check: active key key_v2 (expires in 14d)"),
    ("auth-service", "DEBUG", "Cache HIT for user permissions {user_id} in redis db=1 (lookup_dur=1.2ms)"),
    ("auth-service", "WARN", "Cache MISS for user session {user_id}, falling back to postgresql fetch ({dur}ms)"),
    ("auth-service", "WARN", "Suspicious GeoIP change detected for {user_id}: previous=RU current=DE (confidence=0.81)"),

    # Audio Scriber Service
    ("audio-scriber-service", "INFO", "Received multipart audio stream (codec={codec}, channels=1, sample_rate=48000Hz, size_bytes={size_bytes})"),
    ("audio-scriber-service", "INFO", "FFmpeg normalization completed: 16kHz mono PCM in {dur}ms (loudnorm=-14LUFS, peak=-1.0dBTP)"),
    ("audio-scriber-service", "INFO", "Dispatched task {task_id} to celery queue 'whisper_high_priority' (priority=1, routing_key=gpu.worker)"),
    ("audio-scriber-service", "INFO", "Transcription completed for job {job_id} in {dur}ms (rtf={rtf}, detected_language='ru', confidence={conf})"),
    ("audio-scriber-service", "INFO", "Audio manifest generated for job {job_id}: chunks={seg_count}, duration_sec={audio_dur}s"),
    ("audio-scriber-service", "DEBUG", "Audio chunk temp file /tmp/audio_{job_id}.pcm unlinked cleanly"),
    ("audio-scriber-service", "DEBUG", "FFmpeg demuxer read 142 packets, avg_bitrate=64kbps, container_format=ogg"),
    ("audio-scriber-service", "WARN", "Downstream whisper response took {dur}ms (> threshold 2500ms) for audio_dur={audio_dur}s"),
    ("audio-scriber-service", "WARN", "Audio payload volume level was low (-32.4 LUFS), amplified by +18.4dB via loudnorm filter"),

    # Whisper Engine
    ("whisper-engine", "INFO", "Worker whisper-gpu-node-01 ready (device=cuda:0, VRAM_free={vram_free}MB, compute_type=float16)"),
    ("whisper-engine", "INFO", "Loaded audio segment duration={audio_dur}s, running Silero VAD: speech_segments={seg_count}"),
    ("whisper-engine", "INFO", "Encoder forward pass completed in {dur}ms (batch_size=1, beam_size=2)"),
    ("whisper-engine", "INFO", "Decoder finished: {tokens} tokens generated, text='{text_sample}'"),
    ("whisper-engine", "INFO", "Language detected: 'ru' probability=0.982 (alternate: 'en' 0.012)"),
    ("whisper-engine", "DEBUG", "Faster-Whisper KV cache recycled: allocated={vram_alloc}MB peak={vram_peak}MB"),
    ("whisper-engine", "DEBUG", "Silero VAD threshold filter: 14 non-speech silence pauses trimmed ({dur}ms saved)"),
    ("whisper-engine", "WARN", "VRAM memory pressure at 78%: 6240MB / 8000MB currently utilized on GPU 0"),
    ("whisper-engine", "WARN", "Temperature fallback triggered for segment 3: t=0.0 had repetition_penalty, retried with t=0.2"),

    # Celery Worker
    ("celery-worker", "INFO", "Received task: tasks.transcribe_audio[{task_id}]"),
    ("celery-worker", "INFO", "Task tasks.transcribe_audio[{task_id}] succeeded in {dur}ms: result_size={size_kb}KB"),
    ("celery-worker", "INFO", "Heartbeat sent to broker redis://10.0.2.14:6379/0 (worker_pid=14092, active_tasks=2)"),
    ("celery-worker", "DEBUG", "Prefetch count optimal: prefetch_multiplier=1, concurrency=4"),
    ("celery-worker", "WARN", "Task tasks.transcribe_audio[{task_id}] execution time ({dur}ms) was greater than soft time limit notification threshold"),

    # LLM Agent Service
    ("llm-agent-service", "INFO", "Prompt rendered with {tokens} tokens for executive summarization pipeline"),
    ("llm-agent-service", "INFO", "Calling Groq API (model=llama-3.3-70b-versatile, temperature=0.2, max_tokens=500)"),
    ("llm-agent-service", "INFO", "Groq response received in {dur}ms (prompt_tokens={p_tok}, completion_tokens={c_tok}, tps={tps})"),
    ("llm-agent-service", "INFO", "Summary schema validation passed: 3 action items, 1 decision point extracted"),
    ("llm-agent-service", "DEBUG", "Parsed structured entities: action_items=4, key_decisions=1, speakers_identified=2"),
    ("llm-agent-service", "DEBUG", "Groq system prompt token cache hit: 340 prefix tokens reused"),
    ("llm-agent-service", "WARN", "Upstream Groq API latency elevated: {dur}ms (p99 threshold is 1200ms)"),
    ("llm-agent-service", "WARN", "Estimated completion token count {tokens} was close to max_tokens limit 500"),

    # Postgres Cluster
    ("postgres-cluster", "INFO", "checkpoint starting: time"),
    ("postgres-cluster", "INFO", "checkpoint complete: wrote {bufs} buffers ({pct}%), {run_s}s run, {sync_s}s sync"),
    ("postgres-cluster", "INFO", "LOG: duration: {dur} ms statement: SELECT * FROM user_subscriptions WHERE user_id = '{user_id}' LIMIT 1;"),
    ("postgres-cluster", "INFO", "LOG: duration: {dur} ms statement: INSERT INTO audio_jobs (job_id, user_id, duration_sec, status) VALUES ('{job_id}', '{user_id}', {audio_dur}, 'COMPLETED');"),
    ("postgres-cluster", "INFO", "LOG: duration: {dur} ms statement: UPDATE audio_jobs SET transcript_text = '...', status = 'COMPLETED' WHERE job_id = '{job_id}';"),
    ("postgres-cluster", "DEBUG", "Autovacuum worker 4102 completed on table 'log_events': 1200 tuples removed, 18 pages vacuumed"),
    ("postgres-cluster", "DEBUG", "Index scan using ix_log_level_timestamp on log_events: cost=0.42..8.44 rows=1 width=128"),
    ("postgres-cluster", "WARN", "Slow query detected on table 'audio_jobs' duration={dur}ms statement: SELECT count(*) FROM audio_jobs WHERE created_at > now() - interval '24 hours';"),
    ("postgres-cluster", "WARN", "Temporary file created on disk: path='base/pgsql_tmp/pgsql_tmp4102.0' size=14208KB for hash join"),

    # Redis Cache
    ("redis-cache", "INFO", "Accepted connection from auth-service (client_id={cid}, addr=10.0.2.14:48122)"),
    ("redis-cache", "INFO", "DB 0: {keys} keys, {exp} expiring, maxmemory: 2147483648B, used: {used_mb}MB (39.2%)"),
    ("redis-cache", "INFO", "Key 'session:{user_id}' refreshed with TTL 86400s"),
    ("redis-cache", "INFO", "SETEX job_status:{job_id} 3600 'COMPLETED' executed in 0.4ms"),
    ("redis-cache", "DEBUG", "ZREMRANGEBYSCORE on queue:whisper_tasks removed 0 expired tasks"),
    ("redis-cache", "DEBUG", "PubSub channel 'channel:job_updates' broadcasted event to 3 subscribers"),
    ("redis-cache", "WARN", "Memory fragmentation ratio is 1.42 (used_memory_rss={rss_mb}MB, used_memory={used_mb}MB)"),
    ("redis-cache", "WARN", "Slowlog entry: command 'KEYS session:*' took 14.8ms by client_id={cid}"),

    # Telegram Notifier
    ("telegram-notifier", "INFO", "Dispatching async notification for {user_id} via Bot API"),
    ("telegram-notifier", "INFO", "HTTP POST https://api.telegram.org/bot/sendMessage: 200 OK (latency={dur}ms, msg_id={mid})"),
    ("telegram-notifier", "INFO", "HTTP POST https://api.telegram.org/bot/sendVoice: 200 OK (latency={dur}ms, file_id=AgAD{job_id})"),
    ("telegram-notifier", "INFO", "Polling update received: message_id={mid}, chat_id={user_id}, type=voice (duration={audio_dur}s)"),
    ("telegram-notifier", "DEBUG", "CDN audio payload downloaded: size={size_mb}MB, download_time={dur}ms"),
    ("telegram-notifier", "DEBUG", "Inline keyboard markup rendered for incident card callback_data='res:{mid}'"),
    ("telegram-notifier", "WARN", "Telegram API responded with 502 Bad Gateway; retry 1/3 scheduled in 500ms"),
    ("telegram-notifier", "WARN", "Webhook delivery latency spiked to {dur}ms (Telegram datacenter DC4 Netherlands)"),

    # Storage S3 Service
    ("storage-s3-service", "INFO", "PutObject s3://audio-vault/raw/2026/10/{job_id}.opus - 200 OK (latency={dur}ms)"),
    ("storage-s3-service", "INFO", "GetObject s3://audio-vault/raw/2026/10/{job_id}.opus - 200 OK (size={size_mb}MB, latency={dur}ms)"),
    ("storage-s3-service", "INFO", "Presigned GET URL generated for s3://audio-vault/transcripts/{job_id}.txt (expires_in=3600s)"),
    ("storage-s3-service", "DEBUG", "S3 multipart upload completed: 4 parts assembled in {dur}ms etag=3f81e9..."),
    ("storage-s3-service", "DEBUG", "MinIO cluster health probe ok: 4/4 nodes online, read_quorum=2, write_quorum=3"),
    ("storage-s3-service", "WARN", "Storage pool disk volume /data/minio-01 at 84% capacity (16.2GB free remaining)"),
    ("storage-s3-service", "WARN", "High I/O wait on disk /dev/nvme0n1: await=18.4ms, util=78.2%"),

    # Billing API
    ("billing-api", "INFO", "Subscription status verified for {user_id}: plan=PRO status=ACTIVE quota_remaining=420min"),
    ("billing-api", "INFO", "Usage decrement: deducted {mins} audio minutes for {user_id}"),
    ("billing-api", "INFO", "Payment intent pi_{job_id} confirmed via Stripe webhook for {user_id}"),
    ("billing-api", "DEBUG", "Stripe webhook signature verified using whsec_live_99... in 3.1ms"),
    ("billing-api", "WARN", "User {user_id} consumed 92% of monthly allocated transcription quota (460/500 min)"),
]

TEXT_SAMPLES = [
    "Здравствуйте, отправляю запись встречи по планированию спринта 42",
    "Проверили интеграцию с Whisper, точность распознавания отличная",
    "Коллеги, напоминаю про деплой новой версии сервиса в 18:00",
    "Краткое резюме звонка: договорились переключить модель на qwen2.5",
    "Пожалуйста, сформируйте выгрузку логов за вчерашний день",
    "Обсудили архитектуру микросервисов, необходимо настроить кэширование сессий в Redis",
    "Клиент согласовал ТЗ на интеграцию Telegram бота",
    "Завершили нагрузочное тестирование: 500 RPS без деградации latency",
    "Зафиксировали баг в модуле демультиплексирования Opus, фикс в релизе 2.4",
    "Совещание по бюджетам: переходим на локальные инференс-воркеры",
    "Please review the pull request for whisper batch inference pipeline",
    "Audio recording quality is good, speech recognition confidence is 0.98",
    "Summary of the incident: PostgreSQL connection pool leak resolved with PgBouncer",
    "Дежурный инженер подтвердил привязку Telegram бота через 4-значный PIN",
    "Регламентное действие: перезапустить воркер и сбросить кэш VRAM",
]


def render_normal_message(template: str, user_id: str, client_ip: str, job_id: str, dt: datetime = None) -> str:
    """Подставляет реалистичные параметры в шаблон сообщения."""
    time_str = dt.strftime("%d/%b/%Y:%H:%M:%S +0300") if dt else "13/Oct/2026:12:00:00 +0300"
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
        codec=random.choice(["opus", "aac", "mp3", "ogg", "flac"]),
        time_str=time_str,
    )


# ---------------------------------------------------------------------------
# 10 Каскадных инцидентов со сквозными Trace ID
# ---------------------------------------------------------------------------

def generate_incidents() -> List[Dict[str, Any]]:
    """Возвращает список из 10 реалистичных каскадных аварий."""
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
                ("celery-worker", "ERROR", "Worker whisper-gpu-node-02 terminated with SIGKILL (exit code 137). Process killed by OS OOM killer"),
                ("audio-scriber-service", "ERROR", "Worker whisper-gpu-node-02 terminated ungracefully. Celery task tasks.transcribe_audio aborted"),
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
        # Инцидент 7: Ошибка демультиплексирования битого аудио (FFmpeg Demuxer)
        {
            "id": "ffmpeg-corrupt",
            "trace_id": "trace-ffmpeg-corrupt-1942",
            "service": "audio-scriber-service",
            "user_id": "usr_maria_designer",
            "client_ip": "212.45.12.8",
            "title": "Сбой декодирования поврежденного контейнера Opus (FFmpeg 183)",
            "events": [
                ("api-gateway", "INFO", "POST /api/v2/transcribe/upload HTTP/1.1 200 OK upload_size=1840KB"),
                ("audio-scriber-service", "WARN", "[opus @ 0x55f9a12c] Invalid Opus packet header, bitstream corrupted at offset 0x0014ef"),
                ("audio-scriber-service", "CRITICAL", "FFmpeg process exited with code 183: Invalid data found when processing input /tmp/raw_audio_1942.opus"),
                ("audio-scriber-service", "ERROR", "Audio normalization aborted. Unable to extract valid PCM frames"),
                ("api-gateway", "ERROR", "HTTP 422 Unprocessable Entity: Failed to process audio stream for job_id=job_1942"),
                ("telegram-notifier", "INFO", "Sent polite retry prompt to user_id=usr_maria_designer: 'Не удалось прочитать аудиофайл, запишите повторно'"),
            ]
        },
        # Инцидент 8: Сетевой сбой и таймаут Telegram Bot API
        {
            "id": "tg-network-flap",
            "trace_id": "trace-tg-network-flap-5512",
            "service": "telegram-notifier",
            "user_id": "usr_anna_analyst",
            "client_ip": "77.88.55.77",
            "title": "Сетевой сбой и 502 Bad Gateway в Telegram Bot API",
            "events": [
                ("telegram-notifier", "INFO", "Dispatching final transcript to user_id=usr_anna_analyst via sendMessage"),
                ("telegram-notifier", "WARN", "Telegram API responded with 502 Bad Gateway; retry 1/3 scheduled in 500ms"),
                ("telegram-notifier", "ERROR", "Telegram API POST https://api.telegram.org/bot/sendMessage failed: ClientConnectorError: Cannot connect to host api.telegram.org:443 ssl:default [Connect call failed ('149.154.167.220', 443)]"),
                ("telegram-notifier", "WARN", "Retrying via backup CDN route in 2000ms (retry 2/3)..."),
                ("telegram-notifier", "INFO", "Retry 2/3 succeeded: message_id=8912 delivered to user_id=usr_anna_analyst (total_delay=2610ms)"),
            ]
        },
        # Инцидент 9: Потеря воркера Celery из-за нехватки памяти хоста
        {
            "id": "celery-oom-lost",
            "trace_id": "trace-celery-oom-lost-7104",
            "service": "celery-worker",
            "user_id": "usr_sergey_cto",
            "client_ip": "10.0.4.102",
            "title": "Аварийное завершение воркера Celery (Heartbeat Missed)",
            "events": [
                ("celery-worker", "WARN", "Worker memory RSS reached 3.82 GB / 4.00 GB host cgroup threshold"),
                ("celery-worker", "CRITICAL", "Process 'ForkProcess-3' terminated by signal 9 (SIGKILL) due to system OutOfMemory cgroup constraint"),
                ("audio-scriber-service", "ERROR", "Celery heartbeat missed from celery@worker-gpu-01. Task tasks.transcribe_audio[task_7104] marked as lost"),
                ("celery-worker", "WARN", "Task task_7104 redelivered to celery@worker-gpu-02 (delivery_count=2)"),
                ("audio-scriber-service", "INFO", "Task recovered and re-queued successfully on failover worker node"),
            ]
        },
        # Инцидент 10: Nginx обрыв upstream keepalive соединения
        {
            "id": "nginx-keepalive-reset",
            "trace_id": "trace-nginx-keepalive-reset-6211",
            "service": "nginx-ingress",
            "user_id": "usr_corp_lead",
            "client_ip": "213.180.204.62",
            "title": "Сброс TCP Keepalive соединения в Nginx Ingress (502)",
            "events": [
                ("nginx-ingress", "WARN", "upstream prematurely closed connection while reading response header from upstream: \"http://10.0.4.102:8080/api/v2/transcribe/upload\""),
                ("nginx-ingress", "ERROR", "502 Bad Gateway: client 213.180.204.62 request \"POST /api/v2/transcribe/upload HTTP/2.0\" upstream: \"http://10.0.4.102:8080\""),
                ("api-gateway", "ERROR", "Client connection reset by peer before complete body transfer"),
                ("nginx-ingress", "INFO", "Retried upstream connection on 10.0.4.103:8080 succeeded in 42ms"),
            ]
        },
    ]


# ---------------------------------------------------------------------------
# Сквозные штатные транзакционные цепочки
# ---------------------------------------------------------------------------

def generate_correlated_pipeline_transaction(base_dt: datetime) -> List[Tuple[datetime, str, Dict[str, Any]]]:
    """
    Генерирует полную реалистичную цепочку обработки одного аудио-запроса от старта до финиша,
    связанную единым сквозным trace_id через все ключевые микросервисы.
    """
    user_id = random.choice(USERS)
    client_ip = random.choice(CLIENT_IPS)
    job_num = random.randint(10000, 99999)
    job_id = f"job_{job_num}"
    trace_id = f"req-pipe-{job_num}"
    audio_dur = round(random.uniform(8.0, 75.0), 1)
    tokens = random.randint(80, 450)
    dur_transcribe = int(audio_dur * 120 + random.randint(40, 150))
    conf = round(random.uniform(0.96, 0.99), 2)
    sample_text = random.choice(TEXT_SAMPLES)
    mid = random.randint(1000, 9999)

    steps = [
        # 1. API Gateway принимает запрос
        (0.0, "nginx-ingress", "INFO", f"\"{client_ip}\" \"POST /api/v2/transcribe/upload HTTP/2.0\" 202 Accepted {random.randint(120, 2400)}KB"),
        (0.05, "api-gateway", "INFO", f"POST /api/v2/transcribe/upload HTTP/1.1 202 Accepted {random.randint(15, 45)}ms client_ip={client_ip}"),
        # 2. Auth Service валидирует токен
        (0.08, "auth-service", "INFO", f"JWT verification succeeded for {user_id} (issuer=auth.sre.internal, alg=RS256)"),
        # 3. S3 сохраняет сырое аудио
        (0.20, "storage-s3-service", "INFO", f"PutObject s3://audio-vault/raw/2026/10/{job_id}.opus - 200 OK ({random.randint(25, 65)}ms)"),
        # 4. Audio-Scriber нормализует и ставит задачу в Celery
        (0.35, "audio-scriber-service", "INFO", f"Received multipart audio stream (codec=opus, duration={audio_dur}s), dispatched to celery"),
        (0.40, "celery-worker", "INFO", f"Received task tasks.transcribe_audio[task_{job_num}] for job_id={job_id}"),
        # 5. Whisper Engine распознает речь
        (0.45, "whisper-engine", "INFO", f"Loaded audio segment duration={audio_dur}s, running Silero VAD: speech_segments=3"),
        (0.50 + dur_transcribe / 1000.0, "whisper-engine", "INFO", f"Transcription completed: text='{sample_text}', confidence={conf}, rtf=0.14"),
        # 6. LLM Agent генерирует выжимку
        (0.55 + dur_transcribe / 1000.0, "llm-agent-service", "INFO", f"Calling Groq API (model=llama-3.3-70b-versatile, tokens={tokens})"),
        (0.85 + dur_transcribe / 1000.0, "llm-agent-service", "INFO", f"Groq response received in {random.randint(180, 290)}ms (action_items=3, decisions=1)"),
        # 7. База данных сохраняет финальный статус
        (0.90 + dur_transcribe / 1000.0, "postgres-cluster", "INFO", f"LOG: duration: 3.2 ms statement: UPDATE audio_jobs SET status = 'COMPLETED' WHERE job_id = '{job_id}';"),
        # 8. Billing API списывает минуты
        (0.95 + dur_transcribe / 1000.0, "billing-api", "INFO", f"Usage decrement: deducted {max(1, int(audio_dur/60)+1)} minutes for user={user_id}"),
        # 9. Telegram Notifier доставляет результат
        (1.05 + dur_transcribe / 1000.0, "telegram-notifier", "INFO", f"HTTP POST https://api.telegram.org/bot/sendMessage: 200 OK (latency=142ms, msg_id={mid})"),
    ]

    items: List[Tuple[datetime, str, Dict[str, Any]]] = []
    for delay_s, svc, lvl, msg in steps:
        dt = base_dt + timedelta(seconds=delay_s)
        line = format_log_line(dt, lvl, svc, trace_id, user_id, client_ip, msg)
        items.append((dt, line, {
            "timestamp": dt,
            "level": lvl,
            "service": svc,
            "trace_id": trace_id,
            "user_id": user_id,
            "client_ip": client_ip,
            "message": msg,
        }))

    return items


# ---------------------------------------------------------------------------
# Форматирование и Сборка Датасета
# ---------------------------------------------------------------------------

def format_log_line(dt: datetime, level: str, service: str, trace_id: str, user_id: str, client_ip: str, message: str) -> str:
    """Форматирует строку лога в эталонный формат системы."""
    ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"{ts_str} [{level}] [{service}] [trace_id={trace_id}] [user_id={user_id}] [ip={client_ip}] {message}"


def get_diurnal_time_offset_seconds(random_val: float, total_span_seconds: int) -> int:
    """
    Моделирует естественную суточную кривую активности пользователей (Diurnal Traffic Pattern):
    - Пик активности: 10:00 - 18:00 (MSK)
    - Спад: глубокая ночь 01:00 - 06:00
    """
    while True:
        candidate_sec = random.uniform(0, total_span_seconds)
        hour_of_day = (candidate_sec / 3600.0) % 24.0
        norm_factor = 0.5 + 0.5 * math.sin((hour_of_day - 8.0) * math.pi / 12.0)
        prob = 0.15 + 0.85 * max(0.0, norm_factor)
        if random.random() <= prob:
            return int(candidate_sec)


def build_full_dataset(target_total_count: int = 2000) -> List[Tuple[datetime, str, Dict[str, Any]]]:
    """
    Генерирует целостный хронологический список логов за последние 48 часов.
    Возвращает список кортежей: (timestamp, formatted_string, metadata_dict).
    Гарантирует ровно target_total_count записей.
    """
    now = datetime.now()
    start_time = now - timedelta(hours=48)
    total_span_seconds = int((now - start_time).total_seconds())

    all_items: List[Tuple[datetime, str, Dict[str, Any]]] = []

    # 1. Формируем 10 каскадных инцидентов со сквозными Trace ID
    incidents = generate_incidents()
    incident_offsets_hours = [45.2, 38.5, 31.0, 24.2, 18.0, 12.5, 7.8, 4.2, 1.5, 0.25]

    for idx, inc in enumerate(incidents):
        hours_ago = incident_offsets_hours[idx] if idx < len(incident_offsets_hours) else random.uniform(0.5, 47)
        base_inc_dt = now - timedelta(hours=hours_ago)
        trace_id = inc["trace_id"]
        user_id = inc["user_id"]
        client_ip = inc["client_ip"]

        step_delay = 1.2
        for step_idx, (svc, lvl, raw_msg) in enumerate(inc["events"]):
            ev_dt = base_inc_dt + timedelta(seconds=step_idx * step_delay)
            line = format_log_line(ev_dt, lvl, svc, trace_id, user_id, client_ip, raw_msg)
            all_items.append((ev_dt, line, {
                "timestamp": ev_dt,
                "level": lvl,
                "service": svc,
                "trace_id": trace_id,
                "user_id": user_id,
                "client_ip": client_ip,
                "message": raw_msg,
            }))

    # 2. Генерируем сквозные транзакционные цепочки (каждая по ~13 шагов)
    pipeline_chains_count = 35
    for _ in range(pipeline_chains_count):
        offset_sec = get_diurnal_time_offset_seconds(random.random(), total_span_seconds)
        base_chain_dt = start_time + timedelta(seconds=offset_sec)
        chain_items = generate_correlated_pipeline_transaction(base_chain_dt)
        all_items.extend(chain_items)

    # 3. Добираем остаток обычными операционными и фоновыми логами ровно до target_total_count
    remaining_count = target_total_count - len(all_items)
    if remaining_count < 0:
        all_items = all_items[:target_total_count]
        remaining_count = 0

    for _ in range(remaining_count):
        offset_sec = get_diurnal_time_offset_seconds(random.random(), total_span_seconds)
        dt = start_time + timedelta(seconds=offset_sec)

        service, level, tmpl = random.choice(NORMAL_TEMPLATES)
        user_id = random.choice(USERS)
        client_ip = random.choice(CLIENT_IPS)
        job_id = f"job_{random.randint(10000, 99999)}"
        trace_id = f"req-{service[:4]}-{job_id}"

        msg = render_normal_message(tmpl, user_id, client_ip, job_id, dt)
        line = format_log_line(dt, level, service, trace_id, user_id, client_ip, msg)

        all_items.append((dt, line, {
            "timestamp": dt,
            "level": level,
            "service": service,
            "trace_id": trace_id,
            "user_id": user_id,
            "client_ip": client_ip,
            "message": msg,
        }))

    # Сортируем строго хронологически
    all_items.sort(key=lambda x: x[0])
    return all_items


# ---------------------------------------------------------------------------
# Режимы работы
# ---------------------------------------------------------------------------

def seed_database(clean: bool = True, count: int = 2000) -> int:
    """Заполняет базу данных реалистичными логами в точном объеме count."""
    print(f"[SEED] Инициализация генерации {count} реалистичных продакшн-логов...")
    items = build_full_dataset(target_total_count=count)
    raw_lines = [item[1] for item in items]

    session = SessionLocal()
    try:
        if clean:
            print("[SEED] Очистка существующих записей перед заполнением...")
            session.query(LogEvent).delete()
            session.commit()
            print("Таблица log_events очищена.")
    except Exception as e:
        session.rollback()
        print(f"[!] Ошибка очистки: {e}")
    finally:
        session.close()

    print(f"[SEED] Сохранение {len(raw_lines)} записей в базу данных (таблица log_events)...")
    inserted = ingest_log_batch(raw_lines, batch_size=500)
    print(f"[SEED] Успешно загружено {inserted} логов!")
    print(f"   • Включая 10 каскадных аварий со сквозными Trace ID.")
    print(f"   • Включая 35 сквозных транзакционных цепочек между 12 микросервисами.")
    print(f"   • Охват временного окна: последние 48 часов с суточным профилем нагрузки.")

    # Также сохраняем образец в файл datasets/production_sample.log
    try:
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        export_file = DATASETS_DIR / "production_sample.log"
        with open(export_file, "w", encoding="utf-8") as f:
            f.write("\n".join(raw_lines) + "\n")
        print(f"[SEED] Эталонный файл также обновлен: {export_file} ({round(export_file.stat().st_size/1024, 1)} KB)")
    except Exception as ex:
        print(f"[!] Предупреждение при записи файла датасета: {ex}")

    return inserted


def export_to_file(filepath: Path = None, count: int = 2000) -> Path:
    """Экспортирует датасет в текстовый файл .log"""
    if filepath is None:
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        filepath = DATASETS_DIR / "production_sample.log"

    items = build_full_dataset(target_total_count=count)
    lines = [item[1] for item in items]

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[EXPORT] Датасет успешно записан в файл: {filepath} ({len(lines)} строк, {round(filepath.stat().st_size/1024, 1)} KB)")
    return filepath


def stream_live_logs(delay_sec: float = 0.8, max_cycles: int = 1):
    """
    Реалтайм-стриминг логов в live_service.log.
    Каждая запись добавляется с текущим временем, позволяя watcher и веб-панели
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

            normal_burst = random.randint(10, 16)
            for _ in range(normal_burst):
                now = datetime.now()
                service, level, tmpl = random.choice(NORMAL_TEMPLATES)
                user_id = random.choice(USERS)
                client_ip = random.choice(CLIENT_IPS)
                job_id = f"job_{random.randint(10000, 99999)}"
                trace_id = f"live-{service[:4]}-{job_id}"
                msg = render_normal_message(tmpl, user_id, client_ip, job_id, now)

                line = format_log_line(now, level, service, trace_id, user_id, client_ip, msg)
                with open(LIVE_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

                print(f"[{level:<5}] [{service:<22}] {msg[:65]}...")
                time.sleep(delay_sec)

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
                time.sleep(0.35)

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
        "--count",
        type=int,
        default=2000,
        help="Количество логов для генерации (по умолчанию 2000)"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=True,
        help="Очистить старые записи перед заполнением (по умолчанию True)"
    )
    parser.add_argument(
        "--no-clean",
        action="store_false",
        dest="clean",
        help="Не очищать старые записи перед заполнением"
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
        seed_database(clean=args.clean, count=args.count)
    elif args.mode == "file":
        out_path = Path(args.output) if args.output else None
        export_to_file(out_path, count=args.count)
    elif args.mode == "live":
        stream_live_logs(delay_sec=args.delay, max_cycles=args.cycles)
    elif args.mode == "clear":
        clear_database()


if __name__ == "__main__":
    main()
