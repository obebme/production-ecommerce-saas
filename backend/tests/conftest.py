"""Shared pytest fixtures for fast, isolated FastAPI + SQLite in-memory backend tests."""

from __future__ import annotations

import contextlib
import faulthandler
import math
import os
import socket
import sys
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as _app_models  # noqa: F401 — register ORM models before create_all
from app.core.config import Settings
from app.core.database import Base
from app.core.errors import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.core.security import RateLimiterProtocol, hash_password
from app.main import create_app
from app.models.identity import User

DEFAULT_ORIGIN = "http://localhost:3000"
DEFAULT_TEST_PASSWORD = "StrongPassword!2026"
DEFAULT_TEST_TIMEOUT_SECONDS = 30.0
LOOPBACK_PROBE_TIMEOUT_SECONDS = 2.0
TEST_CSRF_SECRET = "test-csrf-hmac-secret-at-least-32-chars!"
TEST_GUEST_ORDER_SECRET = "test-guest-order-hmac-secret-32-chars!"
TEST_WEBHOOK_SECRET = "test-payment-webhook-secret-32-chars!!"


def _parse_timeout_value(raw: object, source: str) -> float:
    """Parse a positive/non-positive finite timeout value in seconds."""
    if isinstance(raw, bool):
        raise pytest.UsageError(f"{source} must be a numeric timeout in seconds, got {raw!r}")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise pytest.UsageError(
                f"{source} must be a valid float timeout in seconds, got {raw!r}"
            ) from exc
    else:
        raise pytest.UsageError(f"{source} must be a numeric timeout in seconds, got {raw!r}")

    if not math.isfinite(value):
        raise pytest.UsageError(f"{source} must be finite, got {raw!r}")
    return value


def _resolve_test_timeout(item: pytest.Item) -> float:
    """Return the per-test timeout in seconds from @pytest.mark.timeout or environment."""
    marker = item.get_closest_marker("timeout")
    if marker is not None:
        if marker.args:
            raw_marker: object = marker.args[0]
        elif "seconds" in marker.kwargs:
            raw_marker = marker.kwargs["seconds"]
        elif "timeout" in marker.kwargs:
            raw_marker = marker.kwargs["timeout"]
        else:
            raise pytest.UsageError(
                f"@pytest.mark.timeout on {item.nodeid} requires a numeric seconds argument"
            )
        return _parse_timeout_value(raw_marker, f"@pytest.mark.timeout on {item.nodeid}")

    raw_env = os.environ.get("PYTEST_TEST_TIMEOUT_SECONDS")
    if raw_env is None or not raw_env.strip():
        return DEFAULT_TEST_TIMEOUT_SECONDS
    return _parse_timeout_value(raw_env, "PYTEST_TEST_TIMEOUT_SECONDS")


def _arm_test_watchdog(timeout: float) -> None:
    """Arm the faulthandler watchdog to dump tracebacks and exit if a test stalls."""
    try:
        faulthandler.dump_traceback_later(timeout, exit=True)
    except (AttributeError, OSError, ValueError):
        # Fallback when sys.stderr is captured without an OS file descriptor (e.g. --capture=sys).
        if sys.__stderr__ is not None:
            faulthandler.dump_traceback_later(timeout, exit=True, file=sys.__stderr__)


def _probe_tcp_loopback(timeout_seconds: float = LOOPBACK_PROBE_TIMEOUT_SECONDS) -> None:
    """Verify local 127.0.0.1 TCP loopback connectivity within the configured timeout."""
    lsock: socket.socket | None = None
    csock: socket.socket | None = None
    conn: socket.socket | None = None
    try:
        lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lsock.settimeout(timeout_seconds)
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(1)
        addr = lsock.getsockname()
        port = int(addr[1])

        csock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        csock.settimeout(timeout_seconds)
        csock.connect(("127.0.0.1", port))
        conn, _ = lsock.accept()
    finally:
        for sock in (conn, csock, lsock):
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers used by the backend test suite."""
    config.addinivalue_line(
        "markers",
        "timeout(seconds): override the per-test faulthandler watchdog timeout (<= 0 disables).",
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail fast if 127.0.0.1 TCP loopback is blocked in the current sandbox."""
    if bool(getattr(session.config.option, "collectonly", False)):
        return
    try:
        _probe_tcp_loopback()
    except OSError as exc:
        # Note: TimeoutError is an OSError subclass, so both timeouts and blocked sockets fail fast.
        pytest.exit(
            (
                "127.0.0.1 TCP loopback pre-flight check failed "
                f"({type(exc).__name__}: {exc}). "
                "127.0.0.1 TCP loopback required by CPython socket.socketpair() / "
                "asyncio.ProactorEventLoop / TestClient is blocked in the current sandbox."
            ),
            returncode=1,
        )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Start the per-test faulthandler watchdog before fixture setup and execution."""
    timeout = _resolve_test_timeout(item)
    if timeout > 0:
        _arm_test_watchdog(timeout)
    else:
        faulthandler.cancel_dump_traceback_later()


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Cancel the per-test faulthandler watchdog after fixture teardown completes."""
    faulthandler.cancel_dump_traceback_later()


def pytest_runtest_logfinish(
    nodeid: str,
    location: tuple[str, int | None, str],
) -> None:
    """Ensure the watchdog is cancelled even if fixture teardown raised an error."""
    faulthandler.cancel_dump_traceback_later()


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """Authenticated user details, session/CSRF cookies, and CSRF token."""

    user_id: str
    email: str
    role: str
    session_cookie: str
    csrf_cookie: str
    csrf_token: str
    user_payload: dict[str, Any]


@pytest.fixture(scope="session")
def default_password_hash() -> str:
    """Precompute one Argon2id hash per test session to keep user seeding fast."""
    return hash_password(DEFAULT_TEST_PASSWORD)


@pytest.fixture
def test_settings() -> Settings:
    """Return isolated non-production test Settings backed by in-memory SQLite."""
    return Settings(
        database_url="sqlite://",
        environment="test",
        cookie_secure=False,
        allowed_origins=[DEFAULT_ORIGIN],
        csrf_hmac_secret=TEST_CSRF_SECRET,
        guest_order_hmac_secret=TEST_GUEST_ORDER_SECRET,
        payment_webhook_secret=TEST_WEBHOOK_SECRET,
        payment_sandbox_mode=True,
        session_ttl_hours=168,
    )


@pytest.fixture
def db_engine() -> Generator[Engine, None, None]:
    """Create a fresh in-memory SQLite StaticPool engine per test and dispose cleanly."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    """Return a SQLAlchemy sessionmaker bound to the per-test in-memory SQLite engine."""
    return sessionmaker(
        bind=db_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@pytest.fixture
def db_session(db_session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a direct SQLAlchemy Session for test setup and DB assertions."""
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app_factory(
    test_settings: Settings,
    db_session_factory: sessionmaker[Session],
) -> Callable[..., FastAPI]:
    """Build a fresh FastAPI app wired to the per-test SQLite session factory."""

    def _create_test_app(
        settings: Settings | None = None,
        *,
        rate_limiter: RateLimiterProtocol | None = None,
    ) -> FastAPI:
        fastapi_app = create_app(settings or test_settings, rate_limiter=rate_limiter)
        fastapi_app.state.db_session_factory = db_session_factory
        return fastapi_app

    return _create_test_app


@pytest.fixture
def app(app_factory: Callable[..., FastAPI]) -> FastAPI:
    """Default FastAPI test application instance."""
    return app_factory()


@pytest.fixture
def client(app: FastAPI) -> Generator[TestClient, None, None]:
    """Yield a single non-nested TestClient bound to the default test app."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def create_user(
    db_session: Session,
    default_password_hash: str,
) -> Callable[..., User]:
    """Insert and commit a User directly with a specified role and active status."""

    def _create_user(
        *,
        email: str | None = None,
        password: str | None = None,
        full_name: str = "Test User",
        role: str = "customer",
        is_active: bool = True,
    ) -> User:
        resolved_email = (email or f"user-{uuid.uuid4().hex[:10]}@example.com").strip().lower()
        pw_hash = default_password_hash if password is None else hash_password(password)
        user = User(
            email=resolved_email,
            password_hash=pw_hash,
            full_name=full_name,
            role=role,
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _create_user


@pytest.fixture
def login_as(
    create_user: Callable[..., User],
    db_session: Session,
) -> Callable[..., AuthenticatedSession]:
    """Create (or promote) a user of a given role and authenticate via POST /api/v1/auth/login."""

    def _login_as(
        test_client: TestClient,
        *,
        role: str = "customer",
        email: str | None = None,
        full_name: str = "Test User",
        origin: str = DEFAULT_ORIGIN,
    ) -> AuthenticatedSession:
        resolved_email = (email or f"{role}-{uuid.uuid4().hex[:8]}@example.com").strip().lower()
        # Register via API or seed directly when non-customer role is required, then promote/login.
        user = create_user(email=resolved_email, full_name=full_name, role="customer")
        if role != "customer":
            user.role = role
            db_session.commit()
            db_session.refresh(user)

        test_client.cookies.clear()
        response = test_client.post(
            "/api/v1/auth/login",
            json={"email": resolved_email, "password": DEFAULT_TEST_PASSWORD},
            headers={"Origin": origin},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        session_cookie = test_client.cookies.get(SESSION_COOKIE_NAME)
        csrf_cookie = test_client.cookies.get(CSRF_COOKIE_NAME)
        assert session_cookie is not None
        assert csrf_cookie is not None
        return AuthenticatedSession(
            user_id=body["user"]["id"],
            email=body["user"]["email"],
            role=body["user"]["role"],
            session_cookie=session_cookie,
            csrf_cookie=csrf_cookie,
            csrf_token=body["csrf_token"],
            user_payload=body["user"],
        )

    return _login_as
