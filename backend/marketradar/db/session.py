"""Engine and session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from marketradar.config import Settings, get_settings

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_engine(
            settings.sync_database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(
            bind=get_engine(settings), expire_on_commit=False, future=True
        )
    return _sessionmaker


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = get_sessionmaker(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/sessionmaker. Used by tests when the URL changes."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None
