"""Declarative base and shared mixins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable, diffable names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


def utcnow() -> datetime:
    """Timezone-aware UTC now. The system stores nothing naive."""
    return datetime.now(tz=UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    """``created_at`` / ``updated_at`` audit columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UuidPkMixin:
    """String UUID primary key, generated application-side for testability."""

    id: Mapped[str] = mapped_column(primary_key=True, default=new_uuid)


def as_dict(instance: Any) -> dict[str, Any]:
    """Shallow column dict for an ORM instance (diagnostics and tests)."""
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}
