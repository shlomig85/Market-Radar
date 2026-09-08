"""Alembic environment.

The database URL comes from application settings, never from alembic.ini, so migrations
run against exactly the database the application uses.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from marketradar.config import get_settings
from marketradar.db.base import Base
import marketradar.domain.models  # noqa: F401  (registers every model on the metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render ``StrEnumText`` columns as plain ``sa.Text()`` in generated migrations.

    ``StrEnumText`` is a ``TypeDecorator`` whose DDL is TEXT; emitting the decorator would
    make migration files depend on application code, which must stay replayable forever.
    """
    from marketradar.db.types import StrEnumText

    if type_ == "type" and isinstance(obj, StrEnumText):
        return "sa.Text()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        render_item=render_item,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
