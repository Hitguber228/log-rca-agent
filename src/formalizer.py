import re
from datetime import datetime, timedelta
from typing import Dict, Any, List

from datetime import datetime, timezone


class LogQueryFormalizer:
    KNOWN_SERVICES = {
        "whisper", "faster-whisper", "whisper-engine", "audio-scriber-service",
        "nginx", "auth", "auth-service", "api", "api-gateway", 
        "postgres", "postgresql", "postgres-cluster", "postgres-pool",
        "redis", "redis-cache", "worker", "speech-worker", "payment-gateway", 
        "order-service", "billing-service", "billing-api", "database-cluster",
        "llm-agent-service", "llm", "groq", "storage-s3-service", "s3", "minio",
        "telegram-notifier", "celery"
    }

    SYNONYM_MAP = {
        "crash": [
            "crash", "crashed", "failed", "aborted", "panic", "fatal", 
            "exception", "упал", "лежит", "сбой", "слетел", "слетела", 
            "слетело", "отвалился", "отвалилась", "повис", "завис", "умер",
            "дроп", "дропнулся", "полег"
        ],
        "oom": [
            "out of memory", "cuda out of memory", "oom", "allocation failed", 
            "vram", "память", "памяти", "переполнение"
        ],
        "storage": [
            "xminio", "minio", "s3", "storagefull", "диск", "диска", "диске", 
            "хранилищ", "enospc", "места", "507", "capacity"
        ],
        "rate_limit": [
            "429", "too many requests", "rate limit", "лимит", "квота", "квоты", 
            "tpm", "rpm", "groq", "retry-after"
        ],
        "gpu": [
            "cuda", "vram", "видеопам", "видеокарт", "gpu", "beam_size"
        ],
        "timeout": [
            "timeout", "timed out", "504", "502", "deadline exceeded", "таймаут", 
            "задержка", "тормозит", "лаги", "виснет"
        ],
        "database": [
            "connection slots", "connection pool", "deadlock", "max_connections", 
            "queuepool", "пул", "блокировка", "дедлок", "бд", "база", "базы", 
            "постгрес", "postgres", "postgresql"
        ],
        "network": [
            "connection refused", "broken pipe", "network error", "сеть", "коннект", 
            "502", "503", "504", "шлюз"
        ],
        "auth": [
            "token invalid", "unauthorized", "bearer token", "401", "403", "токен", 
            "авторизац", "права"
        ]
    }

    LEVEL_MAP = {
        "CRITICAL": ["critical", "критич", "фатал", "fatal"],
        "ERROR": ["error", "err", "ошибк", "сбой"],
        "WARN": ["warn", "warning", "варнинг", "внимание", "предупреждение"],
        "INFO": ["info", "инфо"],
        "DEBUG": ["debug", "дебаг"]
    }

    STOP_WORDS = {
        "в", "на", "с", "по", "где", "за", "от", "до", "для", "из", "к", "у", "о", "об",
        "и", "а", "но", "да", "же", "ли", "бы", "то", "что", "как",
        "покажи", "найди", "выведи", "глянь", "посмотри", "че", "чо", "там",
        "логи", "лог", "сообщения", "сообщение", "записи", "были", "было", "случилось",
        "пожалуйста", "пж", "почему", "когда", "кто"
    }

    @classmethod
    def formalize(cls, query_str: str) -> Dict[str, Any]:
        if not query_str or not query_str.strip():
            return cls._empty_payload()

        raw = query_str.strip()
        chips: List[Dict[str, str]] = []

        result: Dict[str, Any] = {
            "services": [],
            "levels": [],
            "trace_id": None,
            "user_id": None,
            "client_ip": None,
            "http_statuses": [],
            "time_from": None,
            "time_to": None,
            "exact_phrases": [],
            "text_patterns": [],
            "exclude_terms": [],
            "chips": chips
        }

        now = datetime.now(timezone.utc)

        # 1. Относительное время
        time_rx = re.compile(r'(?:за\s+)?([0-9]+)\s*([a-zA-Zа-яА-ЯёЁ]+)', re.IGNORECASE)
        for match in list(time_rx.finditer(raw)):
            val = int(match.group(1))
            unit = match.group(2).lower()
            captured = False

            if unit.startswith(('м', 'min', 'm')) and not unit.startswith('max'):
                result["time_from"] = now - timedelta(minutes=val)
                chips.append({"type": "time", "label": f"За {val} мин"})
                captured = True
            elif unit.startswith(('ч', 'h', 'hour')):
                result["time_from"] = now - timedelta(hours=val)
                chips.append({"type": "time", "label": f"За {val} ч"})
                captured = True
            elif unit.startswith(('д', 'd', 'day')):
                result["time_from"] = now - timedelta(days=val)
                chips.append({"type": "time", "label": f"За {val} дн"})
                captured = True

            if captured:
                raw = raw[:match.start()] + ' ' + raw[match.end():]
                break

        # 2. Фразы в кавычках
        for p in re.findall(r'["\']([^"\']+)["\']', raw):
            clean_p = p.strip()
            if clean_p:
                result["exact_phrases"].append(clean_p)
                chips.append({"type": "exact", "label": f'"{clean_p}"'})
                raw = raw.replace(f'"{p}"', ' ').replace(f"'{p}'", ' ')

        # 3. Исключения (-nginx, !debug)
        for exc in re.findall(r'(?:^|\s)[-!]([a-zA-Z0-9_\-\.]+)', raw):
            clean_exc = exc.strip()
            result["exclude_terms"].append(clean_exc)
            chips.append({"type": "exclude", "label": f"-{clean_exc}"})
            raw = re.sub(rf'(?:^|\s)[-!]{re.escape(clean_exc)}', ' ', raw)

        # 4. Префиксы явных фильтров
        for key, val in re.findall(r'(?:^|\s)(service|s|level|lvl|trace|trace_id|user|u|user_id|ip|client_ip):([^\s]+)', raw, re.I):
            val_clean = val.strip('",\'')
            k_low = key.lower()
            if k_low in ('s', 'service'):
                result["services"].append(val_clean.lower())
                chips.append({"type": "service", "label": f"Сервис: {val_clean}"})
            elif k_low in ('lvl', 'level'):
                lvl_up = val_clean.upper()
                result["levels"].append(lvl_up)
                chips.append({"type": "level", "label": f"Уровень: {lvl_up}"})
            elif k_low in ('user', 'u', 'user_id'):
                result["user_id"] = val_clean
                chips.append({"type": "user", "label": f"User: {val_clean}"})
            raw = re.sub(rf'(?:^|\s){key}:{re.escape(val)}', ' ', raw, flags=re.I)

        # 5. Токенизация
        tokens = [re.sub(r'^[^\w]+|[^\w]+$', '', t.lower()) for t in raw.split()]
        tokens = [t for t in tokens if t]
        consumed_tokens = set()

        # 5.1 Сервисы без префикса
        for t in tokens:
            if t in cls.KNOWN_SERVICES and t not in result["services"]:
                result["services"].append(t)
                chips.append({"type": "service", "label": f"Сервис: {t}"})
                consumed_tokens.add(t)

        # 5.2 Поиск аномалий (упал, слетел, oom, crash, дедлок и т.д.)
        crash_stems = ("упал", "слет", "отвал", "паден", "сбо", "повис", "завис", "умер", "дроп", "полег")
        for group, patterns in cls.SYNONYM_MAP.items():
            matched_group = False
            for pat in patterns:
                pat_clean = pat.lower().strip()
                for t in tokens:
                    # Проверка совпадения, начала слова или характерных корней сбоя
                    is_match = (
                        t == pat_clean or 
                        (len(t) >= 4 and pat_clean.startswith(t[:4])) or
                        (group == "crash" and any(t.startswith(stem) for stem in crash_stems))
                    )
                    if is_match:
                        result["text_patterns"].extend(["crash", "fail", "abort", "error", "fatal", "out of memory"])
                        chips.append({"type": "incident", "label": f"Аномалия: {group.upper()}"})
                        if not result["levels"]:
                            result["levels"].extend(["ERROR", "CRITICAL", "FATAL"])
                        consumed_tokens.add(t)
                        matched_group = True
                        break
                if matched_group:
                    break
            if matched_group:
                break

        # 5.3 Поиск явных уровней логирования
        for lvl_name, syns in cls.LEVEL_MAP.items():
            for syn in syns:
                matched_token = next((t for t in tokens if t.startswith(syn) and t not in consumed_tokens), None)
                if matched_token:
                    if lvl_name not in result["levels"]:
                        result["levels"].append(lvl_name)
                        chips.append({"type": "level", "label": f"Level: {lvl_name}"})
                    consumed_tokens.add(matched_token)
                    break

        # 5.4 Остаточный свободный текст
        free_tokens = [
            t for t in tokens
            if t not in consumed_tokens and t not in cls.STOP_WORDS
        ]
        if free_tokens:
            for ft in free_tokens:
                result["text_patterns"].append(ft)
                chips.append({"type": "text", "label": f'"{ft}"'})

        return result

    @classmethod
    def _empty_payload(cls) -> Dict[str, Any]:
        return {
            "services": [], "levels": [], "trace_id": None, "user_id": None,
            "client_ip": None, "http_statuses": [], "time_from": None, "time_to": None,
            "exact_phrases": [], "text_patterns": [], "exclude_terms": [], "chips": []
        }