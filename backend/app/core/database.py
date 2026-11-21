"""SQLAlchemy 2.0 database engine, declarative base, and session dependencies."""

from __future__ import annotations

import threading
from collections.abc import Generator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import ColumnElement, MetaData, String, case, cast, create_engine, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Session, sessionmaker

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


_EngineCacheKey = tuple[str, int, int, int, int, int, int, int]
_engine_lock = threading.Lock()
_tracked_engines: dict[_EngineCacheKey, Engine] = {}


def _engine_params_from_settings(
    database_url: str,
    settings: Settings | None = None,
) -> _EngineCacheKey:
    if settings is not None:
        return (
            database_url,
            settings.db_pool_size,
            settings.db_max_overflow,
            settings.db_pool_timeout_seconds,
            settings.db_pool_recycle_seconds,
            settings.db_statement_timeout_ms,
            settings.db_lock_timeout_ms,
            settings.db_idle_in_transaction_timeout_ms,
        )
    return (database_url, 5, 10, 15, 1800, 30000, 10000, 60000)


def create_engine_from_url(
    database_url: str,
    settings: Settings | None = None,
) -> Engine:
    """Build a SQLAlchemy 2.0 Engine appropriate for the configured dialect."""
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    (
        _url,
        pool_size,
        max_overflow,
        pool_timeout,
        pool_recycle,
        stmt_ms,
        lock_ms,
        idle_ms,
    ) = _engine_params_from_settings(database_url, settings)
    pg_options: list[str] = []
    if stmt_ms > 0:
        pg_options.append(f"-c statement_timeout={stmt_ms}")
    if lock_ms > 0:
        pg_options.append(f"-c lock_timeout={lock_ms}")
    if idle_ms > 0:
        pg_options.append(f"-c idle_in_transaction_session_timeout={idle_ms}")
    connect_args: dict[str, Any] = {"options": " ".join(pg_options)} if pg_options else {}
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        connect_args=connect_args,
        future=True,
    )


@lru_cache(maxsize=8)
def _cached_engine(
    database_url: str,
    pool_size: int,
    max_overflow: int,
    pool_timeout: int,
    pool_recycle: int,
    stmt_ms: int,
    lock_ms: int,
    idle_ms: int,
) -> Engine:
    key: _EngineCacheKey = (
        database_url,
        pool_size,
        max_overflow,
        pool_timeout,
        pool_recycle,
        stmt_ms,
        lock_ms,
        idle_ms,
    )
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    else:
        pg_options: list[str] = []
        if stmt_ms > 0:
            pg_options.append(f"-c statement_timeout={stmt_ms}")
        if lock_ms > 0:
            pg_options.append(f"-c lock_timeout={lock_ms}")
        if idle_ms > 0:
            pg_options.append(f"-c idle_in_transaction_session_timeout={idle_ms}")
        connect_args: dict[str, Any] = {"options": " ".join(pg_options)} if pg_options else {}
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            connect_args=connect_args,
            future=True,
        )
    with _engine_lock:
        _tracked_engines[key] = engine
    return engine


@lru_cache(maxsize=8)
def _cached_session_factory(
    database_url: str,
    pool_size: int,
    max_overflow: int,
    pool_timeout: int,
    pool_recycle: int,
    stmt_ms: int,
    lock_ms: int,
    idle_ms: int,
) -> sessionmaker[Session]:
    engine = _cached_engine(
        database_url,
        pool_size,
        max_overflow,
        pool_timeout,
        pool_recycle,
        stmt_ms,
        lock_ms,
        idle_ms,
    )
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the cached Engine for the active Settings."""
    resolved = settings or get_settings()
    return _cached_engine(*_engine_params_from_settings(resolved.database_url, resolved))


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the cached sessionmaker for the active Settings."""
    resolved = settings or get_settings()
    return _cached_session_factory(
        *_engine_params_from_settings(resolved.database_url, resolved)
    )


def dispose_engine(settings: Settings | None = None) -> None:
    """Dispose the cached Engine matching settings and reset engine/session caches."""
    resolved = settings or get_settings()
    key = _engine_params_from_settings(resolved.database_url, resolved)
    with _engine_lock:
        engine = _tracked_engines.pop(key, None)
        _cached_session_factory.cache_clear()
        _cached_engine.cache_clear()
    if engine is not None:
        engine.dispose()


def clear_db_caches() -> None:
    """Dispose all cached engines and reset caches (intended for tests and shutdown)."""
    with _engine_lock:
        engines = list(_tracked_engines.values())
        _tracked_engines.clear()
        _cached_session_factory.cache_clear()
        _cached_engine.cache_clear()
    for engine in engines:
        engine.dispose()


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


def _to_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


TimestampColumn = (
    InstrumentedAttribute[datetime]
    | InstrumentedAttribute[datetime | None]
    | ColumnElement[datetime]
    | ColumnElement[datetime | None]
)


def _ts_compare(
    db: Session,
    column: TimestampColumn,
    op: Literal["gte", "lte", "lt"],
    bound: datetime,
) -> ColumnElement[bool]:
    """Compare a TIMESTAMPTZ column against a UTC datetime safely on PostgreSQL and SQLite."""
    utc_bound = _to_utc_datetime(bound)
    if db.get_bind().dialect.name == "sqlite":
        # SQLite CURRENT_TIMESTAMP/func.now() omits fractional seconds ('YYYY-MM-DD HH:MM:SS'),
        # while SQLAlchemy DateTime binds '.ffffff'. Normalize 19-char rows to '.000000'.
        col_str = cast(column, String)
        sqlite_expr = case(
            (func.length(col_str) == 19, col_str + ".000000"),
            else_=col_str,
        )
        bound_str = utc_bound.strftime("%Y-%m-%d %H:%M:%S.%f")
        if op == "gte":
            return sqlite_expr >= bound_str
        if op == "lte":
            return sqlite_expr <= bound_str
        return sqlite_expr < bound_str

    if op == "gte":
        return column >= utc_bound
    if op == "lte":
        return column <= utc_bound
    return column < utc_bound


def ts_gte(
    db: Session,
    column: TimestampColumn,
    bound: datetime,
) -> ColumnElement[bool]:
    """Dialect-safe column >= bound UTC timestamp predicate."""
    return _ts_compare(db, column, "gte", bound)


def ts_lte(
    db: Session,
    column: TimestampColumn,
    bound: datetime,
) -> ColumnElement[bool]:
    """Dialect-safe column <= bound UTC timestamp predicate."""
    return _ts_compare(db, column, "lte", bound)


def ts_lt(
    db: Session,
    column: TimestampColumn,
    bound: datetime,
) -> ColumnElement[bool]:
    """Dialect-safe column < bound UTC timestamp predicate."""
    return _ts_compare(db, column, "lt", bound)
