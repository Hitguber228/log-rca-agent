from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/rca_db"
    ollama_url: str = "http://127.0.0.1:11434/api/generate"
    ollama_model: str = "qwen2.5:1.5b"
    llm_timeout_seconds: float = 12.0
    sliding_window_seconds: int = 300
    batch_size: int = 500
    log_retention_days: int = 7

    # Telegram Alerting
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_alerts_enabled: bool = False

    # LLM Settings
    groq_api_key: str = ""
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.2

    # Alerting Rules
    alert_min_level: str = "ERROR"
    alert_cascade_only: bool = False
    alert_min_errors: int = 1
    alert_debounce_seconds: float = 1.5

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings


def save_setting_to_env(key: str, value):
    """Сохраняет настройку в os.environ, объекте settings и файле .env."""
    import os
    import re
    from pathlib import Path

    global settings
    key_upper = key.upper()
    val_str = str(value)
    os.environ[key_upper] = val_str

    attr_name = key.lower()
    if hasattr(settings, attr_name):
        curr_val = getattr(settings, attr_name)
        if isinstance(curr_val, bool):
            setattr(settings, attr_name, val_str.lower() in ("true", "1", "yes"))
        elif isinstance(curr_val, int):
            try:
                setattr(settings, attr_name, int(val_str))
            except ValueError:
                setattr(settings, attr_name, val_str)
        elif isinstance(curr_val, float):
            try:
                setattr(settings, attr_name, float(val_str))
            except ValueError:
                setattr(settings, attr_name, val_str)
        else:
            setattr(settings, attr_name, val_str)

    current = Path(__file__).resolve().parent
    targets = {
        Path(".env"),
        Path.cwd() / ".env",
        current.parent / ".env",
        current / ".env"
    }
    for target in targets:
        if target.exists():
            try:
                content = target.read_text(encoding="utf-8")
                pattern = rf"^{re.escape(key_upper)}=.*$"
                if re.search(pattern, content, flags=re.MULTILINE):
                    new_content = re.sub(pattern, f"{key_upper}={val_str}", content, flags=re.MULTILINE)
                else:
                    new_content = content.rstrip() + f"\n{key_upper}={val_str}\n"
                target.write_text(new_content, encoding="utf-8")
            except Exception:
                pass