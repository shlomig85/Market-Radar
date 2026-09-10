"""Test fixtures.

Tests run against a **real PostgreSQL** database, never SQLite (ADR-005): the schema uses
JSONB, check constraints and Postgres defaults, and a SQLite suite would pass while
production diverged.

The schema is created by running the Alembic migrations, so every test run also verifies
that the migrations actually produce the schema the models expect.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "MARKETRADAR_TEST_DATABASE_URL",
    "postgresql+psycopg://marketradar:marketradar@localhost:5432/marketradar_test",
)
os.environ["MARKETRADAR_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["MARKETRADAR_ENVIRONMENT"] = "test"
os.environ.setdefault("MARKETRADAR_LOG_LEVEL", "WARNING")

# Pin every provider to the synthetic corpus for the whole suite, BEFORE any Settings is
# constructed. The application defaults are the real providers, which is right for an
# operator and wrong here: a suite whose result depends on whether Reuters answered today
# is not a test. This is set in the environment rather than only on the `settings` fixture
# because code reached through the API builds its own registry from the process-wide
# settings, which would otherwise pick up the developer's .env and go to the network.
os.environ["MARKETRADAR_NEWS_PROVIDER"] = "fixture"
os.environ["MARKETRADAR_FILINGS_PROVIDER"] = "fixture"
os.environ["MARKETRADAR_COMPANY_PROVIDER"] = "fixture"
os.environ["MARKETRADAR_MARKET_DATA_PROVIDER"] = "unavailable"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine for the test database, with the schema built from the migrations."""
    try:
        eng = create_engine(TEST_DATABASE_URL, future=True)
        with eng.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment setup problem
        pytest.fail(
            "PostgreSQL is required for the test suite and is not reachable at "
            f"{TEST_DATABASE_URL}.\n"
            "Start it with `make db-up` (docker compose) or create the database with:\n"
            "  createdb marketradar_test\n"
            f"Underlying error: {exc}"
        )

    from alembic.config import Config

    from alembic import command
    from marketradar.db.session import reset_engine

    reset_engine()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session inside a transaction that is always rolled back.

    Tests therefore share one migrated schema but never share data, and no test can leave
    residue for the next one.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False, future=True)
    try:
        yield session
    finally:
        session.close()
        # A test that asserts on an IntegrityError leaves the transaction already aborted;
        # rolling back an inactive transaction is a no-op we should not warn about.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def settings():
    from marketradar.config import Settings

    return Settings(
        database_url=TEST_DATABASE_URL,
        environment="test",
        observation_window_days=30,
        baseline_window_days=90,
    )
