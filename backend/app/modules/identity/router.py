"""FastAPI routes for Module 1 (/api/v1/auth/*)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_app_settings
from app.core.database import get_db
from app.core.errors import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, clear_auth_cookie_headers
from app.core.security import (
    RateLimiterProtocol,
    enforce_rate_limit,
    get_client_ip_key,
    get_rate_limiter,
    hash_token,
    resolve_client_ip,
)
from app.modules.identity import service
from app.modules.identity.dependencies import (
    AuthContext,
    get_auth_context,
    require_csrf,
)
from app.modules.identity.schemas import (
    AuthSessionResponse,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
    UserDTO,
    UserUpdateResponse,
)

router = APIRouter(prefix="/auth", tags=["identity"])


def _set_no_store_cache_header(response: Response) -> None:
    """Prevent browser and intermediary caching of authentication responses."""
    response.headers["Cache-Control"] = "no-store"


def _set_auth_cookies(
    response: Response,
    *,
    raw_session_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    max_age_seconds = settings.session_ttl_hours * 3600
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_session_token,
        max_age=max_age_seconds,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age_seconds,
        path="/",
        httponly=False,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthSessionResponse,
)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
) -> AuthSessionResponse:
    """Register a new customer account and issue session + CSRF cookies."""
    client_ip_key = get_client_ip_key(request)
    email_digest = hash_token(payload.email)
    enforce_rate_limit(
        rate_limiter,
        key=f"auth:register:ip:{client_ip_key}",
        limit=20,
        window_seconds=60,
    )
    enforce_rate_limit(
        rate_limiter,
        key=f"auth:register:ip_email:{client_ip_key}:{email_digest}",
        limit=5,
        window_seconds=60,
    )

    user, raw_session_token, csrf_token = service.register_user(
        db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        settings=settings,
        prior_session_token=request.cookies.get(SESSION_COOKIE_NAME),
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(
        response,
        raw_session_token=raw_session_token,
        csrf_token=csrf_token,
        settings=settings,
    )
    _set_no_store_cache_header(response)
    return AuthSessionResponse(
        user=UserDTO.model_validate(user),
        csrf_token=csrf_token,
    )


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=AuthSessionResponse,
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
) -> AuthSessionResponse:
    """Authenticate an existing user account and rotate session cookies."""
    client_ip_key = get_client_ip_key(request)
    email_digest = hash_token(payload.email)
    enforce_rate_limit(
        rate_limiter,
        key=f"auth:login:ip:{client_ip_key}",
        limit=30,
        window_seconds=60,
    )
    enforce_rate_limit(
        rate_limiter,
        key=f"auth:login:ip_email:{client_ip_key}:{email_digest}",
        limit=10,
        window_seconds=60,
    )

    user, raw_session_token, csrf_token = service.authenticate_user(
        db,
        email=payload.email,
        password=payload.password,
        settings=settings,
        prior_session_token=request.cookies.get(SESSION_COOKIE_NAME),
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(
        response,
        raw_session_token=raw_session_token,
        csrf_token=csrf_token,
        settings=settings,
    )
    _set_no_store_cache_header(response)
    return AuthSessionResponse(
        user=UserDTO.model_validate(user),
        csrf_token=csrf_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout(
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    """Revoke the active server session and clear authentication cookies."""
    service.logout_session(db, auth.session)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie_headers(response, cookie_secure=settings.cookie_secure)
    _set_no_store_cache_header(response)
    return response


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=AuthSessionResponse,
)
def get_me(
    response: Response,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthSessionResponse:
    """Return the authenticated user's live profile and derived CSRF token."""
    _set_no_store_cache_header(response)
    return AuthSessionResponse(
        user=UserDTO.model_validate(auth.user),
        csrf_token=auth.csrf_token,
    )


@router.patch(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=UserUpdateResponse,
)
def update_me(
    payload: UpdateProfileRequest,
    response: Response,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> UserUpdateResponse:
    """Update the authenticated user's full_name (role/is_active forbidden)."""
    updated_user = service.update_user_profile(
        db,
        auth.user,
        full_name=payload.full_name,
    )
    _set_no_store_cache_header(response)
    return UserUpdateResponse(user=UserDTO.model_validate(updated_user))
