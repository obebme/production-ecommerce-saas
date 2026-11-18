"""Tests for CSRF token validation, Origin/Referer/Sec-Fetch-Site enforcement, and RBAC guards."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.conftest import DEFAULT_ORIGIN, DEFAULT_TEST_PASSWORD, AuthenticatedSession

from app.core.config import Settings
from app.core.errors import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.core.security import hash_token
from app.models.identity import AuthSession, User
from app.modules.identity.dependencies import require_roles


def test_patch_me_updates_full_name_and_forbids_role_or_is_active(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """PATCH /auth/me updates full_name with valid Origin + CSRF and rejects role/is_active."""
    session = login_as(client, role="customer", full_name="Alice Original")

    ok_resp = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "  Alice Updated  "},
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": session.csrf_token},
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["user"]["full_name"] == "Alice Updated"
    assert ok_resp.json()["user"]["role"] == "customer"

    db_session.expire_all()
    db_user = db_session.get(User, uuid.UUID(session.user_id))
    assert db_user is not None
    assert db_user.full_name == "Alice Updated"

    for forbidden_payload in (
        {"full_name": "Alice", "role": "admin"},
        {"full_name": "Alice", "is_active": False},
        {"full_name": "   "},
    ):
        bad_resp = client.patch(
            "/api/v1/auth/me",
            json=forbidden_payload,
            headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": session.csrf_token},
        )
        assert bad_resp.status_code == 422
        assert bad_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    db_session.refresh(db_user)
    assert db_user.role == "customer"
    assert db_user.is_active is True


def test_patch_me_rejects_missing_or_mismatched_csrf_token(
    client: TestClient,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """State-changing authenticated requests require an exact X-CSRF-Token header."""
    session = login_as(client, role="customer")

    missing_csrf = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Should Fail"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "CSRF_INVALID"

    mismatched_csrf = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Should Fail"},
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": "0" * 64},
    )
    assert mismatched_csrf.status_code == 403
    assert mismatched_csrf.json()["error"]["code"] == "CSRF_INVALID"

    # Valid token still works afterward
    valid_resp = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Still Valid"},
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": session.csrf_token},
    )
    assert valid_resp.status_code == 200


@pytest.mark.parametrize(
    "bad_headers",
    [
        {"Origin": "null"},
        {"Origin": ""},
        {"Origin": "https://evil.example.com"},
        {"Origin": "http://localhost:3000/extra-path"},
        {"Referer": "https://evil.example.com/checkout"},
        {"Referer": "not-a-valid-url"},
        {"Origin": DEFAULT_ORIGIN, "Sec-Fetch-Site": "cross-site"},
        {},  # Cookie-bearing request missing both Origin and Referer
    ],
)
def test_origin_referer_and_sec_fetch_site_rejection(
    client: TestClient,
    login_as: Callable[..., AuthenticatedSession],
    bad_headers: dict[str, str],
) -> None:
    """Reject untrusted/null Origin, untrusted Referer, cross-site Sec-Fetch-Site, or no headers."""
    session = login_as(client, role="customer")
    headers = {"X-CSRF-Token": session.csrf_token, **bad_headers}

    response = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Blocked"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_trusted_referer_allowed_when_origin_absent(
    client: TestClient,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """When Origin is omitted, a Referer matching ALLOWED_ORIGINS is accepted."""
    session = login_as(client, role="customer")
    response = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Referer Accepted"},
        headers={
            "Referer": f"{DEFAULT_ORIGIN}/account/profile?tab=info",
            "X-CSRF-Token": session.csrf_token,
        },
    )
    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "Referer Accepted"


def test_csrf_token_is_deterministic_across_login_and_me(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """CSRF token from POST /auth/login matches ecom_csrf cookie and repeated GET /auth/me calls."""
    user = create_user(email="csrf-det@example.com")

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_resp.status_code == 200
    login_csrf = login_resp.json()["csrf_token"]
    assert client.cookies.get(CSRF_COOKIE_NAME) == login_csrf

    me_1 = client.get("/api/v1/auth/me")
    me_2 = client.get("/api/v1/auth/me")
    assert me_1.status_code == 200 and me_2.status_code == 200
    assert me_1.json()["csrf_token"] == login_csrf
    assert me_2.json()["csrf_token"] == login_csrf

    # Bumping csrf_version rotates the derived token and invalidates the previous token
    raw_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert raw_token is not None
    session_row = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    )
    assert session_row is not None
    session_row.csrf_version = 2
    db_session.commit()

    me_3 = client.get("/api/v1/auth/me")
    assert me_3.status_code == 200
    rotated_csrf = me_3.json()["csrf_token"]
    assert rotated_csrf != login_csrf

    stale_patch = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Old Token"},
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": login_csrf},
    )
    assert stale_patch.status_code == 403
    assert stale_patch.json()["error"]["code"] == "CSRF_INVALID"

    fresh_patch = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Rotated Token"},
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": rotated_csrf},
    )
    assert fresh_patch.status_code == 200


def test_require_roles_positive_and_negative_across_customer_staff_admin(
    app_factory: Callable[..., FastAPI],
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """require_roles enforces 401 unauthenticated, 403 FORBIDDEN_ROLE mismatch, and 200 match."""
    rbac_app = app_factory()

    @rbac_app.get("/_test/rbac/customer-only")
    def customer_only(
        user: Annotated[User, Depends(require_roles("customer"))],
    ) -> dict[str, str]:
        return {"role": user.role}

    @rbac_app.get("/_test/rbac/staff-or-admin")
    def staff_or_admin(
        user: Annotated[User, Depends(require_roles("staff", "admin"))],
    ) -> dict[str, str]:
        return {"role": user.role}

    @rbac_app.get("/_test/rbac/admin-only")
    def admin_only(
        user: Annotated[User, Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        return {"role": user.role}

    with TestClient(rbac_app) as rbac_client:
        # Unauthenticated -> 401 AUTH_SESSION_EXPIRED
        for path in (
            "/_test/rbac/customer-only",
            "/_test/rbac/staff-or-admin",
            "/_test/rbac/admin-only",
        ):
            unauth_resp = rbac_client.get(path)
            assert unauth_resp.status_code == 401
            assert unauth_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

        # Customer role matrix
        login_as(rbac_client, role="customer")
        assert rbac_client.get("/_test/rbac/customer-only").status_code == 200
        staff_denied = rbac_client.get("/_test/rbac/staff-or-admin")
        assert staff_denied.status_code == 403
        assert staff_denied.json()["error"]["code"] == "FORBIDDEN_ROLE"
        admin_denied = rbac_client.get("/_test/rbac/admin-only")
        assert admin_denied.status_code == 403
        assert admin_denied.json()["error"]["code"] == "FORBIDDEN_ROLE"

        # Staff role matrix + live demotion check
        staff_session = login_as(rbac_client, role="staff")
        cust_denied = rbac_client.get("/_test/rbac/customer-only")
        assert cust_denied.status_code == 403
        assert cust_denied.json()["error"]["code"] == "FORBIDDEN_ROLE"
        assert rbac_client.get("/_test/rbac/staff-or-admin").status_code == 200
        assert rbac_client.get("/_test/rbac/staff-or-admin").json() == {"role": "staff"}
        staff_admin_denied = rbac_client.get("/_test/rbac/admin-only")
        assert staff_admin_denied.status_code == 403
        assert staff_admin_denied.json()["error"]["code"] == "FORBIDDEN_ROLE"

        # Demoting staff -> customer in DB takes effect immediately on existing session
        staff_user = db_session.scalar(select(User).where(User.email == staff_session.email))
        assert staff_user is not None
        staff_user.role = "customer"
        db_session.commit()
        demoted_resp = rbac_client.get("/_test/rbac/staff-or-admin")
        assert demoted_resp.status_code == 403
        assert demoted_resp.json()["error"]["code"] == "FORBIDDEN_ROLE"

        # Admin role matrix
        login_as(rbac_client, role="admin")
        admin_cust_denied = rbac_client.get("/_test/rbac/customer-only")
        assert admin_cust_denied.status_code == 403
        assert admin_cust_denied.json()["error"]["code"] == "FORBIDDEN_ROLE"
        assert rbac_client.get("/_test/rbac/staff-or-admin").status_code == 200
        assert rbac_client.get("/_test/rbac/admin-only").status_code == 200
        assert rbac_client.get("/_test/rbac/admin-only").json() == {"role": "admin"}


@pytest.mark.parametrize("bad_token", ["é", "\xff"])
def test_non_ascii_csrf_token_returns_403_not_500(
    client: TestClient,
    login_as: Callable[..., AuthenticatedSession],
    bad_token: str,
) -> None:
    """Non-ASCII X-CSRF-Token headers fail closed with 403 CSRF_INVALID instead of 500."""
    login_as(client, role="customer")
    headers = httpx.Headers(
        {"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": bad_token},
        encoding="latin-1",
    )
    response = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Blocked"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


@pytest.mark.parametrize(
    "duplicate_headers",
    [
        [("Origin", DEFAULT_ORIGIN), ("Origin", DEFAULT_ORIGIN)],
        [("Origin", DEFAULT_ORIGIN), ("Origin", "https://evil.example.com")],
        [("Referer", f"{DEFAULT_ORIGIN}/a"), ("Referer", f"{DEFAULT_ORIGIN}/b")],
        [
            ("Origin", DEFAULT_ORIGIN),
            ("Sec-Fetch-Site", "same-origin"),
            ("Sec-Fetch-Site", "same-origin"),
        ],
    ],
)
def test_duplicate_origin_referer_and_sec_fetch_site_headers_rejected(
    client: TestClient,
    login_as: Callable[..., AuthenticatedSession],
    duplicate_headers: list[tuple[str, str]],
) -> None:
    """Duplicate Origin, Referer, or Sec-Fetch-Site headers are rejected with 403 CSRF_INVALID."""
    session = login_as(client, role="customer")
    headers: list[tuple[str, str]] = [
        ("X-CSRF-Token", session.csrf_token),
        *duplicate_headers,
    ]
    response = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Blocked"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_auth_endpoints_set_cache_control_no_store(client: TestClient) -> None:
    """All /api/v1/auth/* responses include Cache-Control: no-store."""
    reg_resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "nostore@example.com",
            "password": DEFAULT_TEST_PASSWORD,
            "full_name": "No Store",
        },
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert reg_resp.status_code == 201
    assert reg_resp.headers.get("Cache-Control") == "no-store"
    csrf_token = reg_resp.json()["csrf_token"]

    me_get_resp = client.get("/api/v1/auth/me")
    assert me_get_resp.status_code == 200
    assert me_get_resp.headers.get("Cache-Control") == "no-store"

    me_patch_resp = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "No Store Updated"},
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_token},
    )
    assert me_patch_resp.status_code == 200
    assert me_patch_resp.headers.get("Cache-Control") == "no-store"

    logout_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_token},
    )
    assert logout_resp.status_code == 204
    assert logout_resp.headers.get("Cache-Control") == "no-store"

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "nostore@example.com", "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_resp.status_code == 200
    assert login_resp.headers.get("Cache-Control") == "no-store"


def test_cookie_secure_true_sets_secure_attribute_on_issue_and_deletion(
    app_factory: Callable[..., FastAPI],
    test_settings: Settings,
) -> None:
    """COOKIE_SECURE=true sets Secure on ecom_session and ecom_csrf on issue and deletion."""

    def _cookie_attrs(raw_headers: list[str]) -> dict[str, set[str]]:
        parsed: dict[str, set[str]] = {}
        for header in raw_headers:
            parts = [part.strip().lower() for part in header.split(";")]
            cookie_name = parts[0].split("=", 1)[0].strip()
            parsed[cookie_name] = set(parts[1:])
        return parsed

    secure_settings = test_settings.model_copy(update={"cookie_secure": True})
    secure_app = app_factory(secure_settings)

    with TestClient(secure_app, base_url="https://testserver") as secure_client:
        reg_resp = secure_client.post(
            "/api/v1/auth/register",
            json={
                "email": "secure-cookies@example.com",
                "password": DEFAULT_TEST_PASSWORD,
                "full_name": "Secure User",
            },
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert reg_resp.status_code == 201
        issued = _cookie_attrs(reg_resp.headers.get_list("set-cookie"))
        assert "secure" in issued[SESSION_COOKIE_NAME]
        assert "secure" in issued[CSRF_COOKIE_NAME]

        csrf_token = reg_resp.json()["csrf_token"]
        logout_resp = secure_client.post(
            "/api/v1/auth/logout",
            headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_token},
        )
        assert logout_resp.status_code == 204
        cleared_logout = _cookie_attrs(logout_resp.headers.get_list("set-cookie"))
        for cookie_name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
            assert "max-age=0" in cleared_logout[cookie_name]
            assert "secure" in cleared_logout[cookie_name]

        secure_client.cookies.clear()
        secure_client.cookies.set(SESSION_COOKIE_NAME, "invalid-token")
        err_resp = secure_client.get("/api/v1/auth/me")
        assert err_resp.status_code == 401
        cleared_err = _cookie_attrs(err_resp.headers.get_list("set-cookie"))
        for cookie_name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
            assert "max-age=0" in cleared_err[cookie_name]
            assert "secure" in cleared_err[cookie_name]
