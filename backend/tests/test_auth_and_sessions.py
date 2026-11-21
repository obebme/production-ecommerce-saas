"""Tests for identity registration, login, session rotation/expiry, rate limits, and dual-auth."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.conftest import DEFAULT_ORIGIN, DEFAULT_TEST_PASSWORD, AuthenticatedSession

from app.core.errors import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.core.security import SlidingWindowRateLimiter, hash_token
from app.models.identity import AuthSession, User
from app.modules.identity.dependencies import (
    get_optional_user_fail_closed,
    revoke_all_user_sessions,
)
from app.modules.identity.schemas import LoginRequest, RegisterRequest
from app.modules.identity.service import purge_expired_auth_sessions


def _set_cookie_headers_by_name(headers: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in headers:
        cookie_name = raw.split("=", 1)[0].strip()
        result[cookie_name] = raw
    return result


def test_register_creates_customer_canonicalizes_email_and_sets_cookies(
    client: TestClient,
    db_session: Session,
) -> None:
    """POST /auth/register normalizes email, sets customer role, and issues session/CSRF cookies."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "  Alice.Mercer@Example.COM ",
            "password": DEFAULT_TEST_PASSWORD,
            "full_name": "  Alice Mercer  ",
        },
        headers={"Origin": DEFAULT_ORIGIN},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "alice.mercer@example.com"
    assert body["user"]["full_name"] == "Alice Mercer"
    assert body["user"]["role"] == "customer"
    assert body["user"]["is_active"] is True
    assert isinstance(body["csrf_token"], str) and len(body["csrf_token"]) == 64

    cookies_by_name = _set_cookie_headers_by_name(response.headers.get_list("set-cookie"))
    session_header = cookies_by_name[SESSION_COOKIE_NAME]
    csrf_header = cookies_by_name[CSRF_COOKIE_NAME]

    assert "httponly" in session_header.lower()
    assert "samesite=lax" in session_header.lower()
    assert "path=/" in session_header.lower()

    assert "httponly" not in csrf_header.lower()
    assert "samesite=lax" in csrf_header.lower()
    assert "path=/" in csrf_header.lower()
    assert client.cookies.get(CSRF_COOKIE_NAME) == body["csrf_token"]

    db_user = db_session.scalar(select(User).where(User.email == "alice.mercer@example.com"))
    assert db_user is not None
    assert db_user.role == "customer"


def test_register_forbids_role_and_rejects_case_insensitive_duplicates(
    client: TestClient,
) -> None:
    """POST /auth/register forbids role assignment (422) and case-insensitive duplicates (409)."""
    forbidden_role_resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "attacker@example.com",
            "password": DEFAULT_TEST_PASSWORD,
            "full_name": "Attacker",
            "role": "admin",
        },
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert forbidden_role_resp.status_code == 422
    assert forbidden_role_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    first_resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": DEFAULT_TEST_PASSWORD,
            "full_name": "Alice",
        },
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert first_resp.status_code == 201

    client.cookies.clear()
    duplicate_resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "ALICE@Example.COM",
            "password": DEFAULT_TEST_PASSWORD,
            "full_name": "Alice Duplicate",
        },
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert duplicate_resp.status_code == 409
    assert duplicate_resp.json()["error"]["code"] == "AUTH_EMAIL_ALREADY_EXISTS"


def test_login_rotates_prior_session_and_handles_credentials_and_disabled_users(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """POST /auth/login rotates prior session and separates invalid creds vs disabled account."""
    active_user = create_user(email="bob@example.com", full_name="Bob")
    disabled_user = create_user(
        email="disabled@example.com",
        full_name="Disabled Bob",
        is_active=False,
    )

    # 1. Unknown email -> 401 AUTH_INVALID_CREDENTIALS
    unknown_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert unknown_resp.status_code == 401
    assert unknown_resp.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"

    # 2. Wrong password on disabled user -> 401 AUTH_INVALID_CREDENTIALS (not 403)
    disabled_wrong_pw = client.post(
        "/api/v1/auth/login",
        json={"email": disabled_user.email, "password": "WrongPassword!999"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert disabled_wrong_pw.status_code == 401
    assert disabled_wrong_pw.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"

    # 3. Valid password on disabled user -> 403 AUTH_ACCOUNT_DISABLED
    disabled_valid_pw = client.post(
        "/api/v1/auth/login",
        json={"email": disabled_user.email, "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert disabled_valid_pw.status_code == 403
    assert disabled_valid_pw.json()["error"]["code"] == "AUTH_ACCOUNT_DISABLED"

    # 4. Valid login on active user -> 200 and session cookie issued
    login_1 = client.post(
        "/api/v1/auth/login",
        json={"email": "  BOB@Example.COM ", "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_1.status_code == 200
    assert login_1.json()["user"]["id"] == str(active_user.id)
    token_1 = client.cookies.get(SESSION_COOKIE_NAME)
    assert token_1 is not None

    # 5. Subsequent login while holding token_1 revokes token_1 and issues token_2
    login_2 = client.post(
        "/api/v1/auth/login",
        json={"email": active_user.email, "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_2.status_code == 200
    token_2 = client.cookies.get(SESSION_COOKIE_NAME)
    assert token_2 is not None and token_2 != token_1

    db_session.expire_all()
    prior_row = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token_1))
    )
    new_row = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token_2))
    )
    assert prior_row is not None and prior_row.revoked_at is not None
    assert new_row is not None and new_row.revoked_at is None

    # Replaying rotated-out token_1 fails closed with 401
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, token_1)
    stale_me = client.get("/api/v1/auth/me")
    assert stale_me.status_code == 401
    assert stale_me.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_rate_limiting_on_register_and_login(
    client: TestClient,
    create_user: Callable[..., User],
) -> None:
    """Repeated /auth/register and /auth/login calls return 429 RATE_LIMIT_EXCEEDED."""
    reg_email = "ratelimit-reg@example.com"
    for attempt in range(5):
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": reg_email,
                "password": DEFAULT_TEST_PASSWORD,
                "full_name": "Rate Limited",
            },
            headers={"Origin": DEFAULT_ORIGIN},
        )
        expected_status = 201 if attempt == 0 else 409
        assert resp.status_code == expected_status
        client.cookies.clear()

    reg_limited = client.post(
        "/api/v1/auth/register",
        json={
            "email": reg_email,
            "password": DEFAULT_TEST_PASSWORD,
            "full_name": "Rate Limited",
        },
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert reg_limited.status_code == 429
    assert reg_limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(reg_limited.headers["Retry-After"]) >= 1

    user = create_user(email="ratelimit-login@example.com")
    for _ in range(10):
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "WrongPassword!999"},
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert resp.status_code == 401

    login_limited = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "WrongPassword!999"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_limited.status_code == 429
    assert login_limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(login_limited.headers["Retry-After"]) >= 1
    assert login_limited.json()["error"]["details"]["retry_after_seconds"] >= 1


def test_session_expiry_clears_cookies_and_rejects_me(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """Expired session (expires_at <= now) returns 401 AUTH_SESSION_EXPIRED and clears cookies."""
    create_user(email="expiry@example.com")
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "expiry@example.com", "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_resp.status_code == 200
    raw_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert raw_token is not None

    session_row = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    )
    assert session_row is not None
    session_row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
    db_session.commit()

    me_resp = client.get("/api/v1/auth/me")
    assert me_resp.status_code == 401
    assert me_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

    cleared = _set_cookie_headers_by_name(me_resp.headers.get_list("set-cookie"))
    assert "max-age=0" in cleared[SESSION_COOKIE_NAME].lower()
    assert "max-age=0" in cleared[CSRF_COOKIE_NAME].lower()


def test_logout_returns_204_clears_cookies_and_revokes_session(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """POST /auth/logout returns 204, clears cookies, and revokes the server session."""
    create_user(email="logout@example.com")
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "logout@example.com", "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_resp.status_code == 200
    csrf_token = login_resp.json()["csrf_token"]
    raw_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert raw_token is not None

    logout_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_token},
    )
    assert logout_resp.status_code == 204
    assert logout_resp.content == b""

    cleared = _set_cookie_headers_by_name(logout_resp.headers.get_list("set-cookie"))
    assert "max-age=0" in cleared[SESSION_COOKIE_NAME].lower()
    assert "max-age=0" in cleared[CSRF_COOKIE_NAME].lower()

    db_session.expire_all()
    session_row = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    )
    assert session_row is not None and session_row.revoked_at is not None

    # Replay the old cookie manually after logout
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, raw_token)
    replay_resp = client.get("/api/v1/auth/me")
    assert replay_resp.status_code == 401
    assert replay_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_mid_session_deactivation_and_revoke_all_user_sessions(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """Deactivating a user revokes active sessions (403); revoke_all_user_sessions revokes all."""
    user = create_user(email="sessions@example.com")

    # Mint session 1
    login_1 = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_1.status_code == 200
    token_1 = client.cookies.get(SESSION_COOKIE_NAME)
    assert token_1 is not None

    # Mint session 2 separately without sending token_1 cookie so both remain active
    client.cookies.clear()
    login_2 = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_2.status_code == 200
    token_2 = client.cookies.get(SESSION_COOKIE_NAME)
    assert token_2 is not None and token_2 != token_1

    # Deactivate user mid-session and probe with token_2
    db_user = db_session.get(User, user.id)
    assert db_user is not None
    db_user.is_active = False
    db_session.commit()

    deactivated_resp = client.get("/api/v1/auth/me")
    assert deactivated_resp.status_code == 403
    assert deactivated_resp.json()["error"]["code"] == "AUTH_ACCOUNT_DISABLED"

    cleared = _set_cookie_headers_by_name(deactivated_resp.headers.get_list("set-cookie"))
    assert "max-age=0" in cleared[SESSION_COOKIE_NAME].lower()
    assert "max-age=0" in cleared[CSRF_COOKIE_NAME].lower()

    db_session.expire_all()
    session_1_row = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token_1))
    )
    session_2_row = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token_2))
    )
    assert session_1_row is not None and session_1_row.revoked_at is not None
    assert session_2_row is not None and session_2_row.revoked_at is not None

    # Re-enable user; both previously revoked sessions remain invalid
    db_user.is_active = True
    db_session.commit()

    for revoked_tok in (token_1, token_2):
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, revoked_tok)
        assert client.get("/api/v1/auth/me").status_code == 401

    # Mint two fresh active sessions (token_3, token_4) and revoke all in one call
    client.cookies.clear()
    login_3 = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_3.status_code == 200
    token_3 = client.cookies.get(SESSION_COOKIE_NAME)
    assert token_3 is not None

    client.cookies.clear()
    login_4 = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login_4.status_code == 200
    token_4 = client.cookies.get(SESSION_COOKIE_NAME)
    assert token_4 is not None and token_4 != token_3

    revoked_count = revoke_all_user_sessions(db_session, user.id)
    db_session.commit()
    assert revoked_count == 2
    assert revoke_all_user_sessions(db_session, user.id) == 0

    for tok in (token_3, token_4):
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, tok)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_dual_auth_optional_user_fails_closed_on_invalid_session_cookie(
    app_factory: Callable[..., FastAPI],
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """get_optional_user_fail_closed returns None for guests and 401 on stale/revoked cookies."""
    probe_app = app_factory()

    @probe_app.get("/_test/optional-auth")
    def optional_probe(
        user: Annotated[User | None, Depends(get_optional_user_fail_closed)],
    ) -> dict[str, str | None]:
        return {"user_id": str(user.id) if user is not None else None}

    user = create_user(email="dualauth@example.com")

    with TestClient(probe_app) as probe_client:
        # Clean anonymous request -> None (200)
        guest_resp = probe_client.get("/_test/optional-auth")
        assert guest_resp.status_code == 200
        assert guest_resp.json() == {"user_id": None}

        # Valid login -> returns user_id
        login_resp = probe_client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": DEFAULT_TEST_PASSWORD},
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert login_resp.status_code == 200
        valid_token = probe_client.cookies.get(SESSION_COOKIE_NAME)
        assert valid_token is not None

        authed_resp = probe_client.get("/_test/optional-auth")
        assert authed_resp.status_code == 200
        assert authed_resp.json() == {"user_id": str(user.id)}

        # Stale / unknown cookie -> 401 AUTH_SESSION_EXPIRED (never downgrades to guest)
        probe_client.cookies.clear()
        probe_client.cookies.set(SESSION_COOKIE_NAME, "stale-unknown-session-token")
        stale_resp = probe_client.get("/_test/optional-auth")
        assert stale_resp.status_code == 401
        assert stale_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

        # Empty cookie -> 401 AUTH_SESSION_EXPIRED
        probe_client.cookies.clear()
        probe_client.cookies.set(SESSION_COOKIE_NAME, "")
        empty_resp = probe_client.get("/_test/optional-auth")
        assert empty_resp.status_code == 401
        assert empty_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

        # Revoked cookie -> 401 AUTH_SESSION_EXPIRED
        session_row = db_session.scalar(
            select(AuthSession).where(AuthSession.token_hash == hash_token(valid_token))
        )
        assert session_row is not None
        session_row.revoked_at = datetime.now(UTC)
        db_session.commit()

        probe_client.cookies.clear()
        probe_client.cookies.set(SESSION_COOKIE_NAME, valid_token)
        revoked_resp = probe_client.get("/_test/optional-auth")
        assert revoked_resp.status_code == 401
        assert revoked_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"


@pytest.mark.parametrize("bad_name", ["Alice\x00Mercer", "Alice\x07Mercer"])
def test_full_name_rejects_control_characters_on_register_and_patch(
    client: TestClient,
    login_as: Callable[..., AuthenticatedSession],
    bad_name: str,
) -> None:
    """NUL and ASCII control characters in full_name fail with 422 VALIDATION_ERROR."""
    reg_resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "ctrl-char@example.com",
            "password": DEFAULT_TEST_PASSWORD,
            "full_name": bad_name,
        },
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert reg_resp.status_code == 422
    assert reg_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    session = login_as(client, role="customer", full_name="Safe Name")
    patch_resp = client.patch(
        "/api/v1/auth/me",
        json={"full_name": bad_name},
        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": session.csrf_token},
    )
    assert patch_resp.status_code == 422
    assert patch_resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_auth_request_schemas_hide_password_in_repr() -> None:
    """RegisterRequest and LoginRequest omit plaintext passwords from repr()."""
    secret_password = "PlaintextSecret!2026"
    register_payload = RegisterRequest(
        email="repr-user@example.com",
        password=secret_password,
        full_name="Repr User",
    )
    login_payload = LoginRequest(
        email="repr-user@example.com",
        password=secret_password,
    )

    assert register_payload.password == secret_password
    assert login_payload.password == secret_password
    assert secret_password not in repr(register_payload)
    assert secret_password not in repr(login_payload)


def test_sliding_window_rate_limiter_per_bucket_windows_and_max_keys_eviction() -> None:
    """SlidingWindowRateLimiter isolates per-bucket windows and bounds buckets at max_keys."""
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0], max_keys=3)

    # Distinct windows per bucket: short (10s) expires while long (120s) remains enforced
    assert limiter.check_and_record("short", limit=2, window_seconds=10).allowed is True
    assert limiter.check_and_record("short", limit=2, window_seconds=10).allowed is True
    short_blocked = limiter.check_and_record("short", limit=2, window_seconds=10)
    assert short_blocked.allowed is False
    assert short_blocked.retry_after_seconds == 10

    assert limiter.check_and_record("long", limit=1, window_seconds=120).allowed is True
    assert limiter.check_and_record("long", limit=1, window_seconds=120).allowed is False

    now[0] = 1011.0
    short_reset = limiter.check_and_record("short", limit=2, window_seconds=10)
    assert short_reset.allowed is True
    assert short_reset.remaining == 1

    long_still_blocked = limiter.check_and_record("long", limit=1, window_seconds=120)
    assert long_still_blocked.allowed is False
    assert long_still_blocked.retry_after_seconds == 109

    # New key at capacity prunes stale buckets before inserting into a fresh limiter
    limiter.reset()
    now[0] = 100.0
    assert limiter.check_and_record("k1-stale", limit=2, window_seconds=5).allowed is True
    assert limiter.check_and_record("k2-live", limit=2, window_seconds=60).allowed is True
    assert limiter.check_and_record("k3-live", limit=2, window_seconds=60).allowed is True

    now[0] = 110.0
    assert limiter.check_and_record("k4-live", limit=2, window_seconds=60).allowed is True
    assert len(limiter._buckets) == 3
    assert "k1-stale" not in limiter._buckets

    # Touch k2-live (2nd allowed hit) so k3-live is LRU among active keys
    k2_second = limiter.check_and_record("k2-live", limit=2, window_seconds=60)
    assert k2_second.allowed is True
    assert k2_second.remaining == 0

    # Inserting k5-live with all buckets active evicts LRU (k3-live), keeping len <= max_keys
    assert limiter.check_and_record("k5-live", limit=2, window_seconds=60).allowed is True
    assert len(limiter._buckets) == 3
    assert "k3-live" not in limiter._buckets

    assert limiter.check_and_record("k2-live", limit=2, window_seconds=60).allowed is False
    k4_second = limiter.check_and_record("k4-live", limit=2, window_seconds=60)
    assert k4_second.allowed is True
    assert k4_second.remaining == 0

    k3_fresh = limiter.check_and_record("k3-live", limit=2, window_seconds=60)
    assert k3_fresh.allowed is True
    assert k3_fresh.remaining == 1
    assert len(limiter._buckets) == 3

    with pytest.raises(ValueError, match="max_keys"):
        SlidingWindowRateLimiter(max_keys=0)


def test_m08_purge_expired_auth_sessions_and_retention(
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """M-08: purge_expired_auth_sessions deletes old expired/revoked rows and keeps active."""
    user = create_user(email="m08-purge-sess@example.com")
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)

    old_expired = AuthSession(
        user_id=user.id,
        token_hash=hash_token("tok-old-exp"),
        csrf_version=1,
        expires_at=now - timedelta(days=35),
    )
    old_revoked = AuthSession(
        user_id=user.id,
        token_hash=hash_token("tok-old-rev"),
        csrf_version=1,
        expires_at=now + timedelta(days=5),
        revoked_at=now - timedelta(days=31),
    )
    recent_expired = AuthSession(
        user_id=user.id,
        token_hash=hash_token("tok-recent-exp"),
        csrf_version=1,
        expires_at=now - timedelta(days=5),
    )
    active_sess = AuthSession(
        user_id=user.id,
        token_hash=hash_token("tok-active"),
        csrf_version=1,
        expires_at=now + timedelta(days=5),
    )
    db_session.add_all([old_expired, old_revoked, recent_expired, active_sess])
    db_session.commit()

    deleted = purge_expired_auth_sessions(
        db_session,
        retention_days=30,
        batch_size=1,
        max_batches=10,
        now=now,
    )
    assert deleted == 2

    db_session.expire_all()
    assert db_session.get(AuthSession, old_expired.id) is None
    assert db_session.get(AuthSession, old_revoked.id) is None
    assert db_session.get(AuthSession, recent_expired.id) is not None
    assert db_session.get(AuthSession, active_sess.id) is not None


def test_m10_anonymous_401_auth_required_vs_invalid_cookie_auth_session_expired(
    client: TestClient,
) -> None:
    """M-10: Missing cookie -> AUTH_REQUIRED; stale/invalid cookie -> AUTH_SESSION_EXPIRED."""
    client.cookies.clear()
    anon_resp = client.get("/api/v1/auth/me")
    assert anon_resp.status_code == 401
    assert anon_resp.json()["error"]["code"] == "AUTH_REQUIRED"

    client.cookies.set(SESSION_COOKIE_NAME, "bogus-stale-cookie")
    stale_resp = client.get("/api/v1/auth/me")
    assert stale_resp.status_code == 401
    assert stale_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"
