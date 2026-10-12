import sys
import asyncio
from src.loader import process_log_file
from src.analyzer import find_incident_timeline
from src.agent import generate_rca_summary

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


async def run_pipeline():
    log_file = "app.log"
    print(f"1. Загрузка и парсинг событий из {log_file}...")
    try:
        count = process_log_file(log_file)
        print(f"   Обработано строк: {count}")
    except Exception as e:
        print(f"   [!] Ошибка парсинга {log_file}: {e}")

    print("\n2. Анализ цепочки инцидентов...")
    incident = find_incident_timeline()

    print("\n3. Генерация итогового RCA-отчёта:\n")
    report = await generate_rca_summary(incident)
    print(report)


def main():
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()