"""Order access authorization and guest lookup rate limiting."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import RateLimiterProtocol, enforce_rate_limit, verify_guest_order_token
from app.models.order import Order
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.identity.dependencies import AuthContext
from app.modules.orders.common import (
    normalize_valid_order_number_or_none,
    parse_guest_orders_cookie,
)


def _enforce_guest_order_lookup_rate_limit(
    rate_limiter: RateLimiterProtocol,
    *,
    auth: AuthContext | None,
    client_ip_key: str,
    allow_staff_admin: bool,
) -> None:
    if auth is None:
        enforce_rate_limit(
            rate_limiter,
            key=f"orders:guest_lookup:ip:{client_ip_key}",
            limit=30,
            window_seconds=60,
        )
        return
    if not (allow_staff_admin and auth.user.role in ("staff", "admin")):
        enforce_rate_limit(
            rate_limiter,
            key=f"orders:guest_lookup:user:{auth.user.id}",
            limit=30,
            window_seconds=60,
        )


def authorize_order_access(
    db: Session,
    *,
    order_number: str,
    auth: AuthContext | None,
    guest_token_header: str | None,
    guest_orders_cookie: str | None,
    settings: Settings,
    rate_limiter: RateLimiterProtocol,
    client_ip_key: str,
    allow_staff_admin: bool = False,
    skip_guest_rate_limit: bool = False,
) -> Order:
    """Resolve Order by order_number and strictly verify owner or guest HMAC token access."""
    if not skip_guest_rate_limit and auth is None:
        _enforce_guest_order_lookup_rate_limit(
            rate_limiter,
            auth=None,
            client_ip_key=client_ip_key,
            allow_staff_admin=allow_staff_admin,
        )

    valid_order_number = normalize_valid_order_number_or_none(order_number)
    if valid_order_number is None:
        if not skip_guest_rate_limit and auth is not None:
            _enforce_guest_order_lookup_rate_limit(
                rate_limiter,
                auth=auth,
                client_ip_key=client_ip_key,
                allow_staff_admin=allow_staff_admin,
            )
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    order = db.scalar(select(Order).where(Order.order_number == valid_order_number))
    if order is None:
        if not skip_guest_rate_limit and auth is not None:
            _enforce_guest_order_lookup_rate_limit(
                rate_limiter,
                auth=auth,
                client_ip_key=client_ip_key,
                allow_staff_admin=allow_staff_admin,
            )
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    if order.user_id is not None:
        if auth is None:
            raise UnauthorizedError(
                code="AUTH_REQUIRED",
                message="Authentication required.",
            )
        is_owner = auth.user.id == order.user_id
        is_privileged = allow_staff_admin and auth.user.role in ("staff", "admin")
        if not (is_owner or is_privileged):
            raise ForbiddenError(
                code="FORBIDDEN",
                message="You do not have permission to access this order.",
            )
        return order

    if allow_staff_admin and auth is not None and auth.user.role in ("staff", "admin"):
        return order

    if not skip_guest_rate_limit and auth is not None:
        _enforce_guest_order_lookup_rate_limit(
            rate_limiter,
            auth=auth,
            client_ip_key=client_ip_key,
            allow_staff_admin=allow_staff_admin,
        )

    candidate_token: str | None
    if guest_token_header is not None:
        candidate_token = guest_token_header.strip() or None
    else:
        cookie_map = dict(parse_guest_orders_cookie(guest_orders_cookie))
        candidate_token = cookie_map.get(order.order_number)

    now = datetime.now(UTC)
    if (
        order.guest_access_expires_at is None
        or ensure_utc_datetime(order.guest_access_expires_at) <= now
        or not verify_guest_order_token(
            candidate_token,
            order.id,
            order.order_number,
            order.guest_token_version,
            settings.guest_order_hmac_secret,
        )
    ):
        raise ForbiddenError(
            code="FORBIDDEN",
            message="You do not have permission to access this order.",
        )

    return order
