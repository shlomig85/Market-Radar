"""Application configuration.

Every value is sourced from the environment (or a local ``.env``). Secrets are never
committed; ``.env.example`` documents the full set of variables.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

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
    #: CIKs whose filings are ingested. This is a *watchlist*, not discovery: the system
    #: monitors the issuers named here. Broadening it to the full registrant universe is a
    #: scaling problem for a later cycle, and the limitation is surfaced in the UI.
    sec_ciks: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: Cap on filing bodies fetched per run. Each is one rate-limited HTTP request.
    sec_max_documents: int = 40

    #: Subscribed feeds, as ``key|name|publisher|url|SOURCE_TYPE|SOURCE_CLASS|quality``
    #: entries. Empty means "use the built-in list of reliable publishers"; setting it
    #: replaces that list entirely, because which sources are trustworthy is the operator's
    #: judgement, not a constant in this repository.
    feeds: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: Items taken per feed per run. Each item may cost one article fetch.
    feed_max_items: int = 40
    #: Contact string sent as User-Agent when fetching feeds and articles. Publishers block
    #: anonymous scrapers, and identifying the client is the honest thing to do regardless.
    feed_user_agent: str = Field(
        default="",
        description=(
            "Descriptive User-Agent with contact details, "
            "e.g. 'Market Radar you@example.com'."
        ),
    )

    news_api_key: str = ""
    market_data_api_key: str = ""

    http_allowed_hosts: Annotated[list[str], NoDecode] = Field(
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

    @field_validator("cors_origins", "http_allowed_hosts", "sec_ciks", "feeds", mode="before")
    @classmethod
    def _parse_list(cls, value: object) -> object:
        """Accept a comma-separated string or a JSON array.

        These fields are annotated ``NoDecode`` because pydantic-settings otherwise
        JSON-decodes complex types straight from the environment, before validators run —
        which made the documented ``HOST=a.example,b.example`` form raise SettingsError at
        startup. Both forms are accepted here so neither the documented CSV style nor a
        JSON array surprises anyone.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]

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
