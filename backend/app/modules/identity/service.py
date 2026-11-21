"""Business logic for identity registration, login, session lifecycle, and profile updates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import ts_lt
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import (
    derive_csrf_token,
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
    verify_password_dummy,
)
from app.models.identity import AuthSession, User


def _revoke_session_by_raw_token(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    token_digest = hash_token(raw_token)
    db.execute(
        update(AuthSession)
        .where(
            AuthSession.token_hash == token_digest,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


def _create_session(
    db: Session,
    *,
    user: User,
    settings: Settings,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[AuthSession, str, str]:
    raw_token = generate_session_token()
    expires_at = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        csrf_version=1,
        ip_address=ip_address,
        user_agent=user_agent[:512] if user_agent else None,
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()
    csrf_token = derive_csrf_token(
        session.id,
        session.csrf_version,
        settings.csrf_hmac_secret,
    )
    return session, raw_token, csrf_token


def register_user(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str,
    settings: Settings,
    prior_session_token: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[User, str, str]:
    """Register a new customer account, rotate any prior session, and mint fresh auth cookies."""
    canonical_email = email.strip().lower()
    existing_id = db.scalar(
        select(User.id).where(func.lower(User.email) == canonical_email)
    )
    if existing_id is not None:
        raise ConflictError(
            code="AUTH_EMAIL_ALREADY_EXISTS",
            message="An account with this email address already exists.",
        )

    _revoke_session_by_raw_token(db, prior_session_token)

    user = User(
        email=canonical_email,
        password_hash=hash_password(password),
        full_name=full_name,
        role="customer",
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            code="AUTH_EMAIL_ALREADY_EXISTS",
            message="An account with this email address already exists.",
        ) from exc

    _, raw_token, csrf_token = _create_session(
        db,
        user=user,
        settings=settings,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(user)
    return user, raw_token, csrf_token


def authenticate_user(
    db: Session,
    *,
    email: str,
    password: str,
    settings: Settings,
    prior_session_token: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[User, str, str]:
    """Authenticate credentials with timing protection, active check, and session rotation."""
    canonical_email = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == canonical_email))
    if user is None:
        verify_password_dummy()
        raise UnauthorizedError(
            code="AUTH_INVALID_CREDENTIALS",
            message="Invalid email or password.",
        )

    if not verify_password(password, user.password_hash):
        raise UnauthorizedError(
            code="AUTH_INVALID_CREDENTIALS",
            message="Invalid email or password.",
        )

    if not user.is_active:
        raise ForbiddenError(
            code="AUTH_ACCOUNT_DISABLED",
            message="This account has been disabled.",
        )

    _revoke_session_by_raw_token(db, prior_session_token)
    _, raw_token, csrf_token = _create_session(
        db,
        user=user,
        settings=settings,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()
    return user, raw_token, csrf_token


def logout_session(db: Session, session: AuthSession) -> None:
    """Revoke the active authentication session."""
    session.revoked_at = datetime.now(UTC)
    db.commit()


def update_user_profile(db: Session, user: User, *, full_name: str) -> User:
    """Update allowed self-service profile fields (full_name only)."""
    user.full_name = full_name
    db.commit()
    db.refresh(user)
    return user


def purge_expired_auth_sessions(
    db: Session,
    *,
    retention_days: int = 30,
    batch_size: int = 500,
    max_batches: int = 20,
    now: datetime | None = None,
) -> int:
    """Delete expired or long-revoked auth_sessions past retention_days in bounded batches."""
    if now is None:
        current_time = datetime.now(UTC)
    elif now.tzinfo is None:
        current_time = now.replace(tzinfo=UTC)
    else:
        current_time = now.astimezone(UTC)
    cutoff = current_time - timedelta(days=retention_days)
    total_deleted = 0

    for _ in range(max_batches):
        batch_ids = list(
            db.scalars(
                select(AuthSession.id)
                .where(
                    or_(
                        ts_lt(db, AuthSession.expires_at, cutoff),
                        AuthSession.revoked_at.is_not(None)
                        & ts_lt(db, AuthSession.revoked_at, cutoff),
                    )
                )
                .order_by(AuthSession.id.asc())
                .limit(batch_size)
            ).all()
        )
        if not batch_ids:
            break
        result = db.execute(delete(AuthSession).where(AuthSession.id.in_(batch_ids)))
        deleted = int(getattr(result, "rowcount", 0) or 0)
        db.commit()
        total_deleted += deleted
        if deleted == 0:
            break

    return total_deleted
