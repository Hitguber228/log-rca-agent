sample_data = """2026-10-01 12:00:01 [INFO] [api-gateway] Incoming GET /products from 10.0.0.12
2026-10-01 12:00:03 [INFO] [catalog-service] Query cache hit for category electronics
2026-10-01 12:00:10 [WARN] [order-service] Retry 1/3 connecting to inventory-db: timeout 2000ms
2026-10-01 12:00:12 [ERROR] [order-service] Failed to reserve stock: order_id 99120
2026-10-01 12:00:15 [ERROR] [inventory-db] Deadlock detected on table stock_items during transaction 44102
2026-10-01 12:00:18 [CRITICAL] [order-service] Order pipeline aborted due to persistence layer failure
"""

with open("app.log", "w", encoding="utf-8") as f:
    f.write(sample_data.strip())

print("Тестовый файл app.log успешно создан!")