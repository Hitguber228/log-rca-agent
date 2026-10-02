from src.parser import ingest_log_line

sample_logs = [
    "2026-10-01 10:15:00 [INFO] [auth-service] User 1042 successfully logged in from 192.168.1.15",
    "2026-10-01 10:15:02 [INFO] [auth-service] User 2085 successfully logged in from 192.168.1.20",
    "2026-10-01 10:15:05 [WARN] [payment-service] Gateway timeout on endpoint /v1/checkout/pay",
    "2026-10-01 10:15:08 [ERROR] [payment-service] Transaction failed for user 1042: code 504",
    "2026-10-01 10:15:10 [ERROR] [database] Connection pool exhausted: 100/100 active clients",
]

def main():
    print("Запись тестовых логов...")
    for line in sample_logs:
        ingest_log_line(line)
    print("Логи успешно распарсены и добавлены в PostgreSQL!")

if __name__ == "__main__":
    main()