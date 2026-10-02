from src.loader import process_log_file
from src.analyzer import find_incident_timeline
from src.agent import generate_rca_summary

def main():
    log_file = "app.log"
    print(f"1. Загрузка и парсинг событий из {log_file}...")
    count = process_log_file(log_file)
    print(f"   Обработано строк: {count}")

    print("\n2. Анализ цепочки инцидентов...")
    incident = find_incident_timeline()

    print("\n3. Генерация итогового RCA-отчета:\n")
    report = generate_rca_summary(incident)
    print(report)

if __name__ == "__main__":
    main()