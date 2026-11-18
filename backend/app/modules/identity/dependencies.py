"""Reusable authentication, CSRF, Origin, and RBAC dependencies."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_app_settings
from app.core.database import get_db
from app.core.errors import SESSION_COOKIE_NAME, ForbiddenError, UnauthorizedError
from app.core.security import (
    derive_csrf_token,
    hash_token,
    validate_request_origin,
    verify_csrf_token,
)
from app.models.identity import AuthSession, User


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Live authenticated user + backing session + derived CSRF token."""

    user: User
    session: AuthSession
    csrf_token: str


def require_valid_origin(request: Request) -> None:
    """Enforce strict Origin/Referer validation on state-changing requests."""
    settings = get_app_settings(request)
    validate_request_origin(request, settings.allowed_origins)


def _normalize_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _resolve_auth_context_from_cookie(
    request: Request,
    db: Session,
    settings: Settings,
) -> AuthContext | None:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token is None:
        return None
    if not raw_token:
        raise UnauthorizedError(
            code="AUTH_SESSION_EXPIRED",
            message="Authentication session is invalid or has expired.",
            clear_auth_cookies=True,
        )

    token_digest = hash_token(raw_token)
    stmt = (
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(AuthSession.token_hash == token_digest)
    )
    row = db.execute(stmt).first()
    if row is None:
        raise UnauthorizedError(
            code="AUTH_SESSION_EXPIRED",
            message="Authentication session is invalid or has expired.",
            clear_auth_cookies=True,
        )

    session, user = row
    now = datetime.now(UTC)
    if session.revoked_at is not None or _normalize_utc(session.expires_at) <= now:
        raise UnauthorizedError(
            code="AUTH_SESSION_EXPIRED",
            message="Authentication session has expired.",
            clear_auth_cookies=True,
        )

    if not user.is_active:
        revoke_all_user_sessions(db, user.id)
        db.commit()
        raise ForbiddenError(
            code="AUTH_ACCOUNT_DISABLED",
            message="This account has been disabled.",
            clear_auth_cookies=True,
        )

    csrf_token = derive_csrf_token(
        session.id,
        session.csrf_version,
        settings.csrf_hmac_secret,
    )
    return AuthContext(user=user, session=session, csrf_token=csrf_token)


def get_auth_context(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthContext:
    """Require a valid, active authenticated session with live DB user revalidation."""
    auth = _resolve_auth_context_from_cookie(request, db, settings)
    if auth is None:
        raise UnauthorizedError(
            code="AUTH_SESSION_EXPIRED",
            message="Authentication required.",
        )
    return auth


def get_current_user(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> User:
    """Return the authenticated and active User for the current request."""
    return auth.user


def get_optional_auth_context_fail_closed(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthContext | None:
    """Resolve optional auth context on dual-auth routes, failing closed on invalid cookie."""
    return _resolve_auth_context_from_cookie(request, db, settings)


def get_optional_user_fail_closed(
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_fail_closed)],
) -> User | None:
    """Return User when authenticated, None for clean anonymous, or 401 on invalid session."""
    return auth.user if auth is not None else None


def require_csrf(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthContext:
    """Require a valid X-CSRF-Token matching the authenticated session."""
    provided_token = request.headers.get("X-CSRF-Token")
    if not verify_csrf_token(
        provided_token,
        auth.session.id,
        auth.session.csrf_version,
        settings.csrf_hmac_secret,
    ):
        raise ForbiddenError(
            code="CSRF_INVALID",
            message="Invalid or missing CSRF token.",
        )
    return auth


def require_roles(*roles: str) -> Callable[..., User]:
    """Dependency factory enforcing live user role membership."""

    def _role_guard(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise ForbiddenError(
                code="FORBIDDEN_ROLE",
                message="You do not have permission to perform this action.",
            )
        return user

    return _role_guard


def require_roles_with_csrf(*roles: str) -> Callable[..., AuthContext]:
    """Dependency factory enforcing BOTH X-CSRF-Token validation and live user role membership."""

    def _role_csrf_guard(
        auth: Annotated[AuthContext, Depends(require_csrf)],
    ) -> AuthContext:
        if auth.user.role not in roles:
            raise ForbiddenError(
                code="FORBIDDEN_ROLE",
                message="You do not have permission to perform this action.",
            )
        return auth

    return _role_csrf_guard


def revoke_all_user_sessions(db: Session, user_id: uuid.UUID) -> int:
    """Revoke all active auth_sessions for a user within the caller's transaction."""
    now = datetime.now(UTC)
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    result: CursorResult[Any] = db.execute(stmt)  # type: ignore[assignment]
    return int(result.rowcount or 0)
