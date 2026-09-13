from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "AI Agent Harness Platform"
    app_env: str = "local"
    database_url: str = "sqlite:///./tasks.db"
    dashscope_api_key: SecretStr | None = None
    llm_model: str | None = None
    workspace_root: Path = Path(".")
    cli_timeout_seconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)
    cli_max_output_chars: int = Field(default=20000, gt=0)
    mcp_max_read_chars: int = Field(default=20000, gt=0)
    mcp_max_list_files: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
