from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_TITLE: str = "МойСклад — Wholesale Distribution API"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/moysklad"
    WAYBILL_UPLOAD_DIR: str = "uploads/waybills"
    TELEGRAM_BOT_TOKEN: str = ""
    API_BASE_URL: str = "http://localhost:8000/api"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

