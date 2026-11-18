"""Tests for production fail-closed Settings validation, /health probe, and error envelopes."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.database import clear_db_caches, get_engine
from app.main import create_app

_STRONG_SECRET_A = "prod-csrf-hmac-secret-9f8e7d6c5b4a3210!"
_STRONG_SECRET_B = "prod-guest-hmac-secret-1a2b3c4d5e6f7890!"
_STRONG_SECRET_C = "prod-webhook-hmac-secret-0123456789abcdef!"


def _valid_prod_kwargs(**overrides: Any) -> dict[str, Any]:
    """Build baseline valid production Settings keyword arguments."""
    base: dict[str, Any] = {
        "database_url": "postgresql+psycopg://app:secret@db:5432/ecommerce",
        "environment": "production",
        "cookie_secure": True,
        "allowed_origins": ["https://store.example.com"],
        "csrf_hmac_secret": _STRONG_SECRET_A,
        "guest_order_hmac_secret": _STRONG_SECRET_B,
        "payment_webhook_secret": _STRONG_SECRET_C,
        "payment_sandbox_mode": False,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"payment_sandbox_mode": True}, "PAYMENT_SANDBOX_MODE"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"allowed_origins": []}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": "   ,  "}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["*"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://*.example.com"]}, "ALLOWED_ORIGINS"),
    ],
)
def test_production_settings_fail_closed_on_insecure_flags_or_origins(
    overrides: dict[str, Any],
    expected_fragment: str,
) -> None:
    """Production mode must reject sandbox payments, non-secure cookies, empty or wildcard CORS."""
    with pytest.raises(ValidationError, match=expected_fragment):
        Settings(**_valid_prod_kwargs(**overrides))


@pytest.mark.parametrize(
    "secret_field",
    ["csrf_hmac_secret", "guest_order_hmac_secret", "payment_webhook_secret"],
)
@pytest.mark.parametrize(
    "bad_secret",
    [
        "short-secret-under-32-chars",
        "CHANGE_ME_" + "x" * 30,
        "ci_test_only_" + "y" * 30,
    ],
)
def test_production_settings_reject_short_or_placeholder_secrets(
    secret_field: str,
    bad_secret: str,
) -> None:
    """Production mode must reject secrets < 32 chars or carrying CHANGE_ME_/ci_test_only_."""
    with pytest.raises(ValidationError, match=secret_field.upper()):
        Settings(**_valid_prod_kwargs(**{secret_field: bad_secret}))


def test_production_settings_valid_config_succeeds_and_normalizes_origins() -> None:
    """Valid production Settings succeeds and strips trailing slashes from origin entries."""
    settings = Settings(
        **_valid_prod_kwargs(
            allowed_origins="https://shop.example.com/, https://admin.example.com/ "
        )
    )
    assert settings.environment == "production"
    assert settings.payment_sandbox_mode is False
    assert settings.cookie_secure is True
    assert settings.allowed_origins == [
        "https://shop.example.com",
        "https://admin.example.com",
    ]


def test_settings_postgres_fallback_builds_escaped_psycopg_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When DATABASE_URL is empty, Settings builds postgresql+psycopg:// from POSTGRES_* vars."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "ENVIRONMENT",
        "CSRF_HMAC_SECRET",
        "GUEST_ORDER_HMAC_SECRET",
        "PAYMENT_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("POSTGRES_USER", "ecom user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/w:rd+1")
    monkeypatch.setenv("POSTGRES_DB", "ecom_db")
    monkeypatch.setenv("CSRF_HMAC_SECRET", _STRONG_SECRET_A)
    monkeypatch.setenv("GUEST_ORDER_HMAC_SECRET", _STRONG_SECRET_B)
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", _STRONG_SECRET_C)

    settings = Settings()
    assert settings.database_url == "postgresql+psycopg://ecom+user:p%40ss%2Fw%3Ard%2B1@db:5432/ecom_db"


def test_settings_require_database_url_or_all_postgres_components(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Settings must fail when both DATABASE_URL and complete POSTGRES_* variables are missing."""
    monkeypatch.chdir(tmp_path)
    for var in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(
            database_url="",
            postgres_user="ecom",
            postgres_password=None,
            postgres_db="ecom_db",
            csrf_hmac_secret=_STRONG_SECRET_A,
            guest_order_hmac_secret=_STRONG_SECRET_B,
            payment_webhook_secret=_STRONG_SECRET_C,
        )


def test_health_returns_200_and_generates_or_propagates_request_id(client: TestClient) -> None:
    """GET /api/v1/health returns 200 OK JSON and handles X-Request-ID generation/propagation."""
    generated_resp = client.get("/api/v1/health")
    assert generated_resp.status_code == 200
    assert generated_resp.json() == {
        "status": "ok",
        "database": "ok",
        "sandbox_payments": True,
    }
    generated_id = generated_resp.headers.get("X-Request-ID")
    assert generated_id is not None
    assert re.match(r"^req_[0-9a-f]{32}$", generated_id)

    custom_id = "req_trace-123.health"
    propagated_resp = client.get("/api/v1/health", headers={"X-Request-ID": custom_id})
    assert propagated_resp.status_code == 200
    assert propagated_resp.headers.get("X-Request-ID") == custom_id

    unsafe_resp = client.get("/api/v1/health", headers={"X-Request-ID": "bad header value!"})
    assert unsafe_resp.status_code == 200
    sanitized_id = unsafe_resp.headers.get("X-Request-ID")
    assert sanitized_id is not None
    assert sanitized_id != "bad header value!"
    assert re.match(r"^req_[0-9a-f]{32}$", sanitized_id)


def test_health_returns_503_error_envelope_when_database_query_fails(
    app_factory: Callable[..., FastAPI],
) -> None:
    """GET /api/v1/health returns 503 DATABASE_UNAVAILABLE with request_id when SELECT 1 fails."""

    class _FailingSession:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    failing_app = app_factory()
    failing_app.state.db_session_factory = _FailingSession

    with TestClient(failing_app) as failing_client:
        response = failing_client.get(
            "/api/v1/health",
            headers={"X-Request-ID": "req_db_down_check"},
        )

    assert response.status_code == 503
    assert response.headers.get("X-Request-ID") == "req_db_down_check"
    assert response.json() == {
        "error": {
            "code": "DATABASE_UNAVAILABLE",
            "message": "Database connectivity check failed.",
            "details": {},
            "request_id": "req_db_down_check",
        }
    }


def test_validation_error_strips_raw_input_and_matches_envelope(client: TestClient) -> None:
    """422 validation errors conform to the OpenAPI envelope and never echo submitted passwords."""
    sentinel_password = "BadPw1!"
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": sentinel_password,
            "full_name": "Alice",
        },
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 422
    assert sentinel_password not in response.text
    assert "not-an-email" not in response.text

    payload = response.json()
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    error = payload["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "Request validation failed."
    assert error["request_id"] == request_id
    errors = error["details"]["errors"]
    assert isinstance(errors, list) and len(errors) >= 2
    for entry in errors:
        assert set(entry.keys()) == {"loc", "msg", "type"}
        assert "input" not in entry


@pytest.mark.parametrize(
    "bad_origin",
    [
        "http://store.example.com",
        "null",
        "NULL",
        "store.example.com",
        "https://store.example.com/shop",
        "https://store.example.com?foo=bar",
        "https://store.example.com#frag",
        "https://user:pass@store.example.com",
        "*",
        "https://*.example.com",
    ],
)
def test_production_allowed_origins_rejects_unsafe_forms(bad_origin: str) -> None:
    """Production ALLOWED_ORIGINS rejects http, null, paths, queries, userinfo, and wildcards."""
    with pytest.raises(ValidationError, match="ALLOWED_ORIGINS"):
        Settings(**_valid_prod_kwargs(allowed_origins=[bad_origin]))


def test_production_allowed_origins_normalizes_trailing_slash_and_default_port() -> None:
    """Production ALLOWED_ORIGINS normalizes case, trailing slashes, and default :443 port."""
    settings = Settings(
        **_valid_prod_kwargs(
            allowed_origins=(
                "HTTPS://shop.example.com:443/, https://shop.example.com/, "
                "https://admin.example.com:8443/ "
            )
        )
    )
    assert settings.allowed_origins == [
        "https://shop.example.com",
        "https://admin.example.com:8443",
    ]


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"database_url": "sqlite://"}, "DATABASE_URL"),
        ({"database_url": "sqlite:///./prod.db"}, "DATABASE_URL"),
        (
            {"database_url": "postgresql+psycopg://app:Change_Me_123@db:5432/ecommerce"},
            "DATABASE_URL",
        ),
        (
            {"database_url": "postgresql+psycopg://app:CI_TEST_ONLY_pw@db:5432/ecommerce"},
            "DATABASE_URL",
        ),
        ({"postgres_password": "dev_Change_Me_password"}, "POSTGRES_PASSWORD"),
        ({"postgres_password": "ci_Test_Only_password"}, "POSTGRES_PASSWORD"),
        (
            {
                "database_url": "",
                "postgres_user": "app",
                "postgres_password": "Change_Me_pw",
                "postgres_db": "ecommerce",
            },
            "DATABASE_URL",
        ),
    ],
)
def test_production_settings_reject_sqlite_and_placeholder_database_credentials(
    overrides: dict[str, Any],
    expected_fragment: str,
) -> None:
    """Production Settings rejects sqlite:// URLs and case-insensitive placeholder DB creds."""
    with pytest.raises(ValidationError, match=expected_fragment):
        Settings(**_valid_prod_kwargs(**overrides))


@pytest.mark.parametrize(
    "secret_field",
    ["csrf_hmac_secret", "guest_order_hmac_secret", "payment_webhook_secret"],
)
@pytest.mark.parametrize(
    "bad_secret",
    [
        "prod_Change_Me_" + "a" * 24,
        "prod_Ci_TeSt_OnLy_" + "b" * 24,
        " " + "c" * 32,
        "d" * 32 + "\t",
    ],
)
def test_production_settings_reject_case_insensitive_placeholders_and_whitespace_secrets(
    secret_field: str,
    bad_secret: str,
) -> None:
    """Production rejects case-insensitive placeholder substrings and untrimmed HMAC secrets."""
    with pytest.raises(ValidationError, match=secret_field.upper()):
        Settings(**_valid_prod_kwargs(**{secret_field: bad_secret}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"guest_order_hmac_secret": _STRONG_SECRET_A},
        {"payment_webhook_secret": _STRONG_SECRET_A},
        {"payment_webhook_secret": _STRONG_SECRET_B},
    ],
)
def test_production_settings_reject_reused_hmac_secrets(overrides: dict[str, Any]) -> None:
    """Production Settings requires distinct CSRF, guest-order, and webhook HMAC secrets."""
    with pytest.raises(ValidationError, match="distinct"):
        Settings(**_valid_prod_kwargs(**overrides))


def test_production_create_app_disables_docs_and_openapi_endpoints() -> None:
    """Production create_app disables /api/v1/docs, /api/v1/redoc, and /api/v1/openapi.json."""
    prod_settings = Settings(**_valid_prod_kwargs())
    prod_app = create_app(prod_settings)
    assert prod_app.docs_url is None
    assert prod_app.redoc_url is None
    assert prod_app.openapi_url is None

    try:
        with TestClient(prod_app) as prod_client:
            for path in ("/api/v1/docs", "/api/v1/redoc", "/api/v1/openapi.json"):
                resp = prod_client.get(path)
                assert resp.status_code == 404
                assert resp.json()["error"]["code"] == "NOT_FOUND"
    finally:
        get_engine(prod_settings).dispose()
        clear_db_caches()
