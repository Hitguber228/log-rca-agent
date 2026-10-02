import time
from datetime import datetime

LOG_FILE = "live_service.log"


def log(level: str, service: str, trace_id: str, msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"{now} [{level}] [{service}] [trace_id={trace_id}] {msg}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"➡️ Записано: {entry.strip()}")


def run_simulation():
    print("🚀 Старт эмуляции микросервисной архитектуры...")
    
    # 1. Штатный фоновый трафик
    for i in range(5):
        log("INFO", "api-gateway", f"req-norm-{i}", f"GET /items/{100+i} HTTP/1.1 200 OK")
        time.sleep(1)

    print("\n💥 ИНИЦИАЛИЗАЦИЯ КАСКАДНОЙ АВАРИИ 💥\n")
    trace = "req-crash-777"

    # 2. Первопричина (Root Cause): падение пула БД
    log("CRITICAL", "postgres-cluster", trace, "Fatal: remaining connection slots are reserved for non-replication superuser connections (max_connections=100 reached)")
    time.sleep(1.5)

    # 3. Каскад: падает auth-service
    log("ERROR", "auth-service", trace, "Timeout acquiring connection from pool after 5000ms. Aborting session verification.")
    time.sleep(1.5)

    # 4. Каскад: падает billing-api
    log("ERROR", "billing-api", trace, "500 Internal Server Error: Failed to validate bearer token from auth-service")
    time.sleep(1.5)

    # 5. Каскад: клиент получает 504
    log("ERROR", "api-gateway", trace, "Upstream billing-api failed response with status 504 Gateway Timeout")
    
    print("\n🏁 Авария сгенерирована. Проверь консоль watcher и Telegram!")


if __name__ == "__main__":
    run_simulation()