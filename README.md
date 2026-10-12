# SRE Log RCA Agent & Observability Platform

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0%2B-red.svg)](https://www.sqlalchemy.org/)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.x-2CA5E0.svg)](https://aiogram.dev/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Комплексная автономная **SRE-платформа мониторинга**, сбора распределённых логов, автоматического анализа первопричин аварий (**Root Cause Analysis, RCA**) с использованием языковых моделей (**LLM**) и доставки сжатых инженерных оповещений в **Telegram**.

Система фиксирует каскадные сбои микросервисов, выявляет эпицентры сбоев по сквозным `trace_id`, визуализирует радиус поражения (**Blast Radius DAG**) в веб-интерфейсе и предлагает регламентные действия по восстановлению (**Runbook Actions**).

---

## Архитектура системы

```mermaid
flowchart TD
    subgraph TargetSystem["Целевая система микросервисов"]
        TargetApp["Audio-Scriber Bot (main.py)"]
        Services["Microservices (Whisper, Postgres, Redis, API)"]
        TargetApp -->|Логирует ошибки| LiveLog["live_service.log"]
        Services -->|Логирует ошибки| LiveLog
    end

    subgraph MonitoringCore["Ядро мониторинга и анализа"]
        Watcher["Watcher (src/watcher.py)\nRealtime Tail & Debounce 1.5s"]
        LiveLog -->|Слушает файл| Watcher
        Watcher -->|HTTP Ingest| API["FastAPI Server (api.py)"]
        DB[("Storage (SQLite / PostgreSQL)")]
        API <--> DB
        
        Agent["RCA Agent (src/agent.py)\nGroq / Ollama / OpenAI / Fallback"]
        API --> Agent
    end

    subgraph ObservabilityUI["Интерфейсы оператора"]
        WebUI["Web Console (templates/index.html)\n- Blast Radius DAG\n- Streaming RCA Report\n- Smart NL Log Search"]
        API <-->|REST + WebSocket| WebUI
        
        TelegramBot["SRE Telegram Bot (bot.py)\n- PIN 2FA Pairing\n- Incident Alerts\n- Resolve / Runbook Actions"]
        Notifier["Notifier (src/notifier.py)"]
        Watcher --> Notifier
        Notifier --> TelegramBot
        TelegramBot <-->|Callback / Resolve| API
    end
```

---

## Ключевые возможности

- 🚀 **Потоковая генерация отчётов (Streaming Mode)**:
  - Генерация RCA-отчёта токен-за-токеном в реальном времени (`POST /rca/analyze/stream`).
  - Поддержка локальной **Ollama**, облачных **Groq** и **OpenAI**, а также встроенного детерминированного **Fallback-движка**.
  - Плавный рендеринг в UI с пульсирующим курсором и WebSocket-синхронизацией вкладок.

- 📊 **Интерактивный граф сбоев (Blast Radius DAG)**:
  - Визуализация каскадного падения сервисов от эпицентра (Root Cause) до сопутствующих звеньев.
  - Инспектор узлов с детальным просмотром стек-трейсов и регламентными командами (Bash / SQL / Docker).

- 🔍 **Поиск по логам на естественном языке (Smart NL Query Formalizer)**:
  - Встроенный модуль `LogQueryFormalizer` автоматически переводит фразы инженера (например, *«почему упал whisper вчера из-за памяти»*) в структурированные SQL-фильтры по сервисам, временным диапазонам и сигнатурам ошибок.

- 📱 **Мобильное рабочее место SRE (Telegram Bot)**:
  - Безопасная 2FA-привязка дежурного инженера по 4-значному PIN-коду через веб-панель.
  - Инженерные алерты без мусора с кнопками смены статуса (`Resolved / Reopen`) и сброса кэша.

---

## Структура проекта

```text
log-rca-agent/
├── api.py                    # Центральный FastAPI сервер и REST API
├── bot.py                    # Telegram-бот дежурного инженера (aiogram 3.x)
├── main.py                   # Целевой сервис мониторинга (Audio-Scriber)
├── run_analysis.py           # CLI-утилита быстрого запуска RCA-анализа
├── simulate_traffic.py       # Симулятор боевого трафика и фоновых ошибок
├── seed_logs.py              # Быстрое наполнение тестовой базы инцидентами
├── generate_dataset.py       # Экспорт и генерация каскадных сценариев
├── start_services.bat        # Скрипт единого запуска всех сервисов на Windows
├── requirements.txt          # Зависимости Python
├── .env.example              # Пример файла конфигурации
│
├── src/                      # Модули ядра платформы
│   ├── agent.py              # LLM RCA агент и потоковый генератор
│   ├── analyzer.py           # Детектор каскадных инцидентов и группировка
│   ├── config.py             # Настройки Pydantic Settings и окружения
│   ├── db.py                 # Подключение SQLAlchemy и сессии
│   ├── formalizer.py         # Разбор естественного языка в запросы к логам
│   ├── incident_store.py     # Хранилище статусов и истории инцидентов
│   ├── loader.py             # Батч-загрузчик лог-файлов
│   ├── log_search.py         # Движок полнотекстового поиска и пагинации
│   ├── models.py             # Модели SQLAlchemy (LogEvent, LogTemplate)
│   ├── notifier.py           # Доставка и форматирование Telegram-алертов
│   ├── parser.py             # Синтаксический разбор строк и JSON логов
│   ├── retention.py          # Политика ротации и очистки устаревших данных
│   └── watcher.py            # Realtime tail-f фоновый воркер с дебаунсом
│
├── templates/
│   └── index.html            # Web-дашборд (Tailwind, Lucide, Cytoscape/DAG)
│
├── tests/                    # Комплект автоматических тестов
│   ├── conftest.py
│   ├── test_api_endpoints.py
│   ├── test_log_search.py
│   ├── test_parser.py
│   ├── test_security.py
│   └── test_stream_endpoint.py
│
└── datasets/                 # Эталонные датасеты логов
    └── production_sample.log
```

---

## Быстрый старт

### 1. Клонирование и подготовка окружения

```bash
git clone https://github.com/your-username/log-rca-agent.git
cd log-rca-agent

python -m venv .venv

# Для Windows:
.venv\Scripts\activate

# Для Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Настройка конфигурации

Создайте файл `.env` на основе примера:
```bash
cp .env.example .env
```

Отредактируйте параметры (при необходимости укажите ключи LLM и Telegram бота):
```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key_here
DATABASE_URL=sqlite:///./logs.db
```

### 3. Запуск платформы

#### Вариант А: Windows (один клик)
Запустите скрипт:
```cmd
start_services.bat
```

#### Вариант Б: Ручной запуск компонентов
В отдельных терминалах выполните:

1. **API и Web-интерфейс**:
   ```bash
   uvicorn api:app --reload --port 8000
   ```
2. **Фоновый сборщик логов**:
   ```bash
   python -m src.watcher
   ```
3. **SRE Telegram Bot**:
   ```bash
   python bot.py
   ```

После запуска панель мониторинга доступна по адресу:  
👉 **http://127.0.0.1:8000**

---

## Тестирование и симуляция аварий

1. **Генерация тестового инцидента в целевом сервисе**:
   Отправьте команду `/test_crash` в Audio-Scriber боте или запустите симулятор:
   ```bash
   python simulate_traffic.py
   ```
2. **Тестирование потокового эндпоинта LLM**:
   ```bash
   python tests/test_stream_endpoint.py
   ```
3. **Запуск тестового набора**:
   ```bash
   pytest tests/ -v
   ```

---

## Лицензия

Проект распространяется под лицензией **MIT**. Подробности в файле [LICENSE](LICENSE).
