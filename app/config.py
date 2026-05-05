from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "纸模资源采集索引"
    base_url: str = "https://mir-modeley.com"
    database_path: str = "/app/data/index.db"
    request_delay_seconds: float = 2.5
    request_timeout_seconds: float = 45
    user_agent: str = "52zhimo public index bot; contact: https://52zhimo.cn"
    daily_check_hour: int = 3
    daily_check_minute: int = 15
    admin_username: str = "admin"
    admin_password: str = "admin123456"
    secret_key: str = "change-this-secret-key"


@lru_cache
def get_settings() -> Settings:
    return Settings()
