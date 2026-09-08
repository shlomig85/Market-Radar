"""Application configuration.

Every value is sourced from the environment (or a local ``.env``). Secrets are never
committed; ``.env.example`` documents the full set of variables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the backend."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="MARKETRADAR_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- environment -----------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- database --------------------------------------------------------
    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+psycopg://marketradar:marketradar@localhost:5432/marketradar"
        ),
    )
    db_echo: bool = False
    db_pool_size: int = 5

    # --- api -------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- providers -------------------------------------------------------
    # A provider with no credentials configured resolves to UNAVAILABLE. It is never
    # silently replaced with fabricated data.
    news_provider: str = "fixture"
    filings_provider: str = "fixture"
    market_data_provider: str = "unavailable"
    company_provider: str = "fixture"

    sec_user_agent: str = Field(
        default="",
        description="Required by SEC EDGAR fair-access policy: 'Name contact@example.com'.",
    )
    sec_base_url: str = "https://data.sec.gov"
    sec_request_timeout_seconds: float = 20.0

    news_api_key: str = ""
    market_data_api_key: str = ""

    http_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["data.sec.gov", "www.sec.gov"],
        description="SSRF allowlist. Outbound provider fetches must match one of these hosts.",
    )

    # --- llm -------------------------------------------------------------
    anthropic_api_key: str = ""
    planner_model: str = "claude-sonnet-5"
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 60.0

    # --- pipeline tuning -------------------------------------------------
    # Defaults for the deterministic engines. Overridable per run; never hardcoded
    # at the call sites.
    observation_window_days: int = 30
    baseline_window_days: int = 90
    duplicate_similarity_threshold: float = 0.60

    @field_validator("cors_origins", "http_allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        return str(self.database_url)

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
