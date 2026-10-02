from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Явно указываем psycopg2 в качестве драйвера
DATABASE_URL = "postgresql+psycopg2://postgres@localhost:5432/log_rca_db"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()