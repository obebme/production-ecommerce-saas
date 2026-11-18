"""FastAPI routes for Module 4 (/api/v1/cart*)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_app_settings
from app.core.database import get_db
from app.core.errors import GUEST_CART_COOKIE_NAME
from app.core.security import (
    RateLimiterProtocol,
    enforce_rate_limit,
    get_client_ip_key,
    get_rate_limiter,
)
from app.modules.cart import service
from app.modules.cart.schemas import AddCartItemRequest, CartDTO, UpdateCartItemRequest
from app.modules.identity.dependencies import (
    AuthContext,
    get_optional_auth_context_fail_closed,
    get_optional_auth_context_with_csrf,
    require_csrf,
)

router = APIRouter(prefix="/cart", tags=["cart"])


def _set_guest_cart_cookie(
    response: Response,
    *,
    raw_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=GUEST_CART_COOKIE_NAME,
        value=raw_token,
        max_age=service.GUEST_CART_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def _clear_guest_cart_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=GUEST_CART_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=CartDTO,
)
def get_cart(
    request: Request,
    response: Response,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_fail_closed)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CartDTO:
    """Fetch the active guest or authenticated user cart with live price/stock projection."""
    cart_dto, new_guest_token = service.get_cart(
        db,
        user_id=auth.user.id if auth is not None else None,
        raw_guest_token=request.cookies.get(GUEST_CART_COOKIE_NAME),
        settings=settings,
    )
    if new_guest_token is not None:
        _set_guest_cart_cookie(response, raw_token=new_guest_token, settings=settings)
    return cart_dto


@router.post(
    "/items",
    status_code=status.HTTP_200_OK,
    response_model=CartDTO,
)
def add_cart_item(
    payload: AddCartItemRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
) -> CartDTO:
    """Add an active variant quantity to the caller's cart."""
    client_ip_key = get_client_ip_key(request)
    enforce_rate_limit(
        rate_limiter,
        key=f"cart:add:ip:{client_ip_key}",
        limit=60,
        window_seconds=60,
    )
    cart_dto, new_guest_token = service.add_cart_item(
        db,
        user_id=auth.user.id if auth is not None else None,
        raw_guest_token=request.cookies.get(GUEST_CART_COOKIE_NAME),
        payload=payload,
        settings=settings,
    )
    if new_guest_token is not None:
        _set_guest_cart_cookie(response, raw_token=new_guest_token, settings=settings)
    return cart_dto


@router.patch(
    "/items/{item_id}",
    status_code=status.HTTP_200_OK,
    response_model=CartDTO,
)
def update_cart_item(
    item_id: uuid.UUID,
    payload: UpdateCartItemRequest,
    request: Request,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CartDTO:
    """Set exact quantity (1..99) for a line item owned by the caller's cart."""
    return service.update_cart_item(
        db,
        item_id=item_id,
        user_id=auth.user.id if auth is not None else None,
        raw_guest_token=request.cookies.get(GUEST_CART_COOKIE_NAME),
        payload=payload,
        settings=settings,
    )


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_200_OK,
    response_model=CartDTO,
)
def delete_cart_item(
    item_id: uuid.UUID,
    request: Request,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CartDTO:
    """Remove a line item from the caller's cart."""
    return service.delete_cart_item(
        db,
        item_id=item_id,
        user_id=auth.user.id if auth is not None else None,
        raw_guest_token=request.cookies.get(GUEST_CART_COOKIE_NAME),
        settings=settings,
    )


@router.delete(
    "",
    status_code=status.HTTP_200_OK,
    response_model=CartDTO,
)
def clear_cart(
    request: Request,
    response: Response,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CartDTO:
    """Clear all items from the caller's active cart."""
    cart_dto, new_guest_token = service.clear_cart(
        db,
        user_id=auth.user.id if auth is not None else None,
        raw_guest_token=request.cookies.get(GUEST_CART_COOKIE_NAME),
        settings=settings,
    )
    if new_guest_token is not None:
        _set_guest_cart_cookie(response, raw_token=new_guest_token, settings=settings)
    return cart_dto


@router.post(
    "/merge",
    status_code=status.HTTP_200_OK,
    response_model=CartDTO,
)
def merge_cart(
    request: Request,
    response: Response,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CartDTO:
    """Idempotently merge any active guest cart into the authenticated user's cart."""
    cart_dto, clear_cookie = service.merge_guest_cart(
        db,
        user_id=auth.user.id,
        raw_guest_token=request.cookies.get(GUEST_CART_COOKIE_NAME),
        settings=settings,
    )
    if clear_cookie:
        _clear_guest_cart_cookie(response, settings=settings)
    return cart_dto
