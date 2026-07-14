from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://robot:robot@localhost:5432/robot_leads"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    search_provider: str = "tavily"
    tavily_api_key: str = ""
    bing_api_key: str = ""
    bing_endpoint: str = "https://api.bing.microsoft.com/v7.0/search"
    search_results_per_query: int = Field(default=5, ge=1, le=20)
    fetch_timeout_seconds: int = 20
    enable_playwright: bool = False
    schedule_enabled: bool = False
    schedule_hour: int = Field(default=8, ge=0, le=23)
    schedule_minute: int = Field(default=0, ge=0, le=59)
    default_lookback_days: int = Field(default=7, ge=1, le=90)


@lru_cache
def get_settings() -> Settings:
    return Settings()

