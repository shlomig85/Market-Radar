"""Configuration parsing.

These exist because of a real startup failure: the API container crashed with
``SettingsError: error parsing value for field "cors_origins"`` when the variable was set
exactly as ``.env.example`` documents it. pydantic-settings JSON-decodes complex types
straight from the environment, *before* field validators run, so the comma-separated form
never reached the validator meant to split it.

Local runs never set these variables, so nothing caught it until the container smoke test
did. The cases below lock in every form a user might reasonably write.
"""

from __future__ import annotations

import pytest

from marketradar.config import Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Settings reads a .env file by default; these tests must see only what they set."""
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    for name in ("MARKETRADAR_CORS_ORIGINS", "MARKETRADAR_HTTP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_apply_when_unset():
    settings = Settings()
    assert settings.cors_origins == ["http://localhost:3000"]
    assert settings.http_allowed_hosts == ["data.sec.gov", "www.sec.gov"]


def test_single_value_csv_does_not_raise(monkeypatch):
    """The exact value docker-compose passes to the api service."""
    monkeypatch.setenv("MARKETRADAR_CORS_ORIGINS", "http://localhost:3000")
    assert Settings().cors_origins == ["http://localhost:3000"]


def test_multi_value_csv(monkeypatch):
    """The exact form documented in .env.example."""
    monkeypatch.setenv("MARKETRADAR_HTTP_ALLOWED_HOSTS", "data.sec.gov,www.sec.gov")
    assert Settings().http_allowed_hosts == ["data.sec.gov", "www.sec.gov"]


def test_csv_tolerates_whitespace(monkeypatch):
    monkeypatch.setenv("MARKETRADAR_CORS_ORIGINS", " http://a.test , http://b.test ")
    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_json_array_is_also_accepted(monkeypatch):
    """Neither form should surprise anyone, so both are supported."""
    monkeypatch.setenv("MARKETRADAR_CORS_ORIGINS", '["http://a.test","http://b.test"]')
    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_empty_value_yields_an_empty_list(monkeypatch):
    monkeypatch.setenv("MARKETRADAR_HTTP_ALLOWED_HOSTS", "")
    assert Settings().http_allowed_hosts == []


def test_trailing_separator_does_not_create_a_blank_entry(monkeypatch):
    """A blank host in the SSRF allowlist would be a silent correctness problem."""
    monkeypatch.setenv("MARKETRADAR_HTTP_ALLOWED_HOSTS", "data.sec.gov,")
    assert Settings().http_allowed_hosts == ["data.sec.gov"]
