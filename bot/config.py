from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: SecretStr
    admin_bot_token: SecretStr | None = None
    admin_id: int
    database_url: str = "sqlite+aiosqlite:///bot.db"
    redis_url: str = "redis://localhost:6379/0"
    referral_percent: float = 5.0
    ticket_expire_minutes: int = 15
    admin_web_user: str = "admin"
    admin_web_password: str = "admin123"
    web_secret_key: str = "tg-shop-bot-super-secret-key-admin"
    admin_tron_address: str = ""
    admin_tron_private_key: str = ""
    trongrid_api_key: str = ""
    tron_network: str = "mainnet"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

config = Settings()
