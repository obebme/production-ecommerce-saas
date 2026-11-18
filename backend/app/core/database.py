"""SQLAlchemy 2.0 database engine, declarative base, and session dependencies."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from fastapi import Request
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import Settings, get_app_settings, get_settings

POSTGRES_INDEXES_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models with consistent constraint naming."""

    metadata = MetaData(naming_convention=POSTGRES_INDEXES_NAMING_CONVENTION)


def create_engine_from_url(database_url: str) -> Engine:
    """Build a SQLAlchemy 2.0 Engine appropriate for the configured dialect."""
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=4)
def _cached_engine(database_url: str) -> Engine:
    return create_engine_from_url(database_url)


@lru_cache(maxsize=4)
def _cached_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = _cached_engine(database_url)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the cached Engine for the active Settings."""
    resolved = settings or get_settings()
    return _cached_engine(resolved.database_url)


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the cached sessionmaker for the active Settings."""
    resolved = settings or get_settings()
    return _cached_session_factory(resolved.database_url)


def clear_db_caches() -> None:
    """Dispose cached engines and reset caches (intended for tests)."""
    _cached_session_factory.cache_clear()
    _cached_engine.cache_clear()


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a per-request SQLAlchemy Session with automatic rollback on unhandled errors."""
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        settings = get_app_settings(request)
        factory = get_session_factory(settings)
    db: Session = factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_db_connection(db: Session) -> bool:
    """Execute a lightweight SELECT 1 probe and return True when the DB is reachable."""
    try:
        db.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
