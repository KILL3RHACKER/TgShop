from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: SecretStr
    admin_id: int
    database_url: str = "sqlite+aiosqlite:///bot.db"
    redis_url: str = "redis://localhost:6379/0"
    referral_percent: float = 5.0
    ticket_expire_minutes: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

config = Settings()
