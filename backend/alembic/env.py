"""Alembic migration environment supporting online PostgreSQL and offline SQL compilation."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401 — ensure all 17 ORM tables are registered on Base.metadata
from app.core.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_database_url(*, offline: bool) -> str:
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url
    try:
        from app.core.config import get_settings

        return get_settings().database_url
    except Exception:
        if offline:
            fallback = config.get_main_option("sqlalchemy.url")
            if fallback:
                return fallback
        raise


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (--sql)."""
    url = _resolve_database_url(offline=True)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""
    url = _resolve_database_url(offline=False)
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
