from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/rca_db"
    ollama_url: str = "http://127.0.0.1:11434/api/generate"
    ollama_model: str = "qwen2.5:1.5b"
    llm_timeout_seconds: float = 12.0
    sliding_window_seconds: int = 300
    batch_size: int = 500

    # Telegram Alerting
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_alerts_enabled: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()