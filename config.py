"""Configuration settings loaded from environment variables or .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    collect_interval_seconds: int = 3600
    db_path: str = "onchain_metrics.db"

    # Google Sheets API configuration (service account JSON credentials path)
    google_sheets_credentials_path: str = ""
    google_sheets_spreadsheet_id: str = ""
    google_sheets_sheet_name: str = "OnChainMetrics"
    google_sheets_enable_backfill_on_startup: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
