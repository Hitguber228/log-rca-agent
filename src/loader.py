import os
from src.parser import ingest_log_line

def process_log_file(file_path: str):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    processed_count = 0
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                ingest_log_line(line)
                processed_count += 1

    return processed_count