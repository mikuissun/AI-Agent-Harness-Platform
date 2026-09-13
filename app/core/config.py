from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "AI Agent Harness Platform"
    app_env: str = "local"
    database_url: str = "sqlite:///./tasks.db"
    dashscope_api_key: SecretStr | None = None
    llm_model: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
