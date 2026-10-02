from src.db import engine, Base
import src.models  # Импортируем, чтобы SQLAlchemy узнал о таблицах

def init_tables():
    print("Создание таблиц в базе данных log_rca_db...")
    Base.metadata.create_all(bind=engine)
    print("Таблицы log_templates и log_events успешно созданы и готовы к работе!")

if __name__ == "__main__":
    init_tables()