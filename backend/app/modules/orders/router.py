"""FastAPI routes for Module 5 (/api/v1/checkout/orders and /api/v1/orders/*)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_app_settings
from app.core.database import get_db
from app.core.errors import GUEST_CART_COOKIE_NAME, GUEST_ORDERS_COOKIE_NAME
from app.core.pagination import MAX_PAGINATION_PAGE, PaginatedResponse, PaginationParams
from app.core.security import (
    GuestPendingOrderTracker,
    RateLimiterProtocol,
    enforce_rate_limit,
    get_client_ip_key,
    get_guest_pending_tracker,
    get_rate_limiter,
)
from app.models.identity import User
from app.modules.identity.dependencies import (
    AuthContext,
    get_current_user,
    get_optional_auth_context_fail_closed,
    get_optional_auth_context_with_csrf,
)
from app.modules.orders import service
from app.modules.orders.schemas import (
    CancelOrderRequest,
    CheckoutOrderResponseDTO,
    CreateCheckoutOrderRequest,
    OrderDetailDTO,
    OrderListItemDTO,
)

router = APIRouter(tags=["orders"])


def _set_guest_orders_cookie(
    response: Response,
    *,
    cookie_value: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=GUEST_ORDERS_COOKIE_NAME,
        value=cookie_value,
        max_age=service.GUEST_ORDERS_MAX_AGE_SECONDS,
        path="/api/v1",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.post(
    "/checkout/orders",
    status_code=status.HTTP_201_CREATED,
    response_model=CheckoutOrderResponseDTO,
)
def create_checkout_order(
    payload: CreateCheckoutOrderRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
    tracker: Annotated[GuestPendingOrderTracker, Depends(get_guest_pending_tracker)],
) -> CheckoutOrderResponseDTO:
    """Idempotently create an order from the active cart with server totals and stock lock."""
    client_ip_key = get_client_ip_key(request)
    enforce_rate_limit(
        rate_limiter,
        key=f"checkout:orders:ip:{client_ip_key}",
        limit=30,
        window_seconds=60,
    )
    idempotency_key = service.validate_idempotency_key(request.headers.get("Idempotency-Key"))
    existing_guest_orders_cookie = request.cookies.get(GUEST_ORDERS_COOKIE_NAME)

    dto, is_replay = service.create_checkout_order(
        db,
        auth=auth,
        raw_guest_cart_token=request.cookies.get(GUEST_CART_COOKIE_NAME),
        guest_orders_cookie=existing_guest_orders_cookie,
        idempotency_key=idempotency_key,
        payload=payload,
        settings=settings,
        tracker=tracker,
        client_ip_key=client_ip_key,
    )
    if is_replay:
        response.status_code = status.HTTP_200_OK

    if dto.guest_access_token is not None:
        cookie_val = service.build_guest_orders_cookie_value(
            existing_guest_orders_cookie,
            order_number=dto.order.order_number,
            guest_token=dto.guest_access_token,
        )
        _set_guest_orders_cookie(response, cookie_value=cookie_val, settings=settings)

    return dto


@router.get(
    "/orders",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[OrderListItemDTO],
)
def list_my_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[OrderListItemDTO]:
    """List paginated orders owned by the authenticated user (placed_at DESC)."""
    return service.list_customer_orders(
        db,
        user_id=current_user.id,
        pagination=PaginationParams(page=page, page_size=page_size),
    )


@router.get(
    "/orders/{order_number}",
    status_code=status.HTTP_200_OK,
    response_model=OrderDetailDTO,
)
def get_order_detail(
    order_number: str,
    request: Request,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_fail_closed)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
) -> OrderDetailDTO:
    """Return full order details strictly to the owner or valid guest order token."""
    return service.get_order_detail(
        db,
        order_number=order_number,
        auth=auth,
        guest_token_header=request.headers.get("X-Guest-Order-Token"),
        guest_orders_cookie=request.cookies.get(GUEST_ORDERS_COOKIE_NAME),
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=get_client_ip_key(request),
    )


@router.post(
    "/orders/{order_number}/cancel",
    status_code=status.HTTP_200_OK,
    response_model=OrderDetailDTO,
)
def cancel_order(
    order_number: str,
    request: Request,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
    payload: Annotated[CancelOrderRequest | None, Body()] = None,
) -> OrderDetailDTO:
    """Self-cancel an unpaid PENDING_PAYMENT order and release reserved stock."""
    return service.cancel_customer_order(
        db,
        order_number=order_number,
        reason=payload.reason if payload is not None else None,
        auth=auth,
        guest_token_header=request.headers.get("X-Guest-Order-Token"),
        guest_orders_cookie=request.cookies.get(GUEST_ORDERS_COOKIE_NAME),
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=get_client_ip_key(request),
    )
