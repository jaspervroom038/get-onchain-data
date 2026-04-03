"""Configuration settings loaded from environment variables or .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    collect_interval_seconds: int = 3600
    db_path: str = "onchain_metrics.db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
