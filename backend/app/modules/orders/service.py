"""Checkout orchestration, order authorization, cancellation, and state transitions."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitExceededError,
    UnauthorizedError,
    ValidationAppError,
)
from app.core.pagination import PaginatedResponse, PaginationParams
from app.core.security import (
    GuestPendingOrderTracker,
    RateLimiterProtocol,
    derive_guest_order_token,
    enforce_rate_limit,
    hash_token,
    verify_guest_order_token,
)
from app.models.cart import Cart, CartItem
from app.models.catalog import Category, Product
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import PaymentIntent
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.identity.dependencies import AuthContext
from app.modules.inventory.service import reclaim_expired_reservations_for_variants
from app.modules.orders.schemas import (
    AddressDTO,
    CheckoutOrderResponseDTO,
    CreateCheckoutOrderRequest,
    FulfillmentStatus,
    OrderCheckoutSummaryDTO,
    OrderDetailDTO,
    OrderEventDTO,
    OrderItemDTO,
    OrderListItemDTO,
    OrderStatus,
    PaymentStatus,
)
from app.modules.payments.schemas import PaymentIntentDTO

RESERVATION_TTL = timedelta(minutes=15)
GUEST_ACCESS_TTL = timedelta(days=30)
GUEST_ORDERS_MAX_AGE_SECONDS = int(GUEST_ACCESS_TTL.total_seconds())
MAX_GUEST_ORDERS_COOKIE_PAIRS = 5
MAX_ACTIVE_GUEST_PENDING_ORDERS = 3
SANDBOX_PROVIDER = "sandbox_simpay"

_ORDER_NUMBER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ORDER_NUMBER_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_INT32_MAX = 2_147_483_647


def validate_idempotency_key(raw_key: str | None) -> str:
    """Validate required Idempotency-Key header (1..64 printable ASCII characters)."""
    if raw_key is None:
        raise BadRequestError(
            code="IDEMPOTENCY_KEY_REQUIRED",
            message="Idempotency-Key header is required.",
        )
    stripped = raw_key.strip()
    if (
        not stripped
        or len(stripped) > 64
        or not all(0x20 <= ord(ch) <= 0x7E for ch in stripped)
    ):
        raise BadRequestError(
            code="IDEMPOTENCY_KEY_REQUIRED",
            message="Idempotency-Key header must be 1 to 64 printable ASCII characters.",
        )
    return stripped


def normalize_valid_order_number_or_none(raw_order_number: str | None) -> str | None:
    """Validate order_number format prior to querying PostgreSQL (rejects NUL/control chars)."""
    if raw_order_number is None or len(raw_order_number) > 32:
        return None
    if not _ORDER_NUMBER_RE.fullmatch(raw_order_number):
        return None
    return raw_order_number


def parse_guest_orders_cookie(raw_cookie: str | None) -> list[tuple[str, str]]:
    """Parse bounded 'order_number:token|...' cookie pairs cleanly."""
    if not raw_cookie:
        return []
    cleaned = raw_cookie.strip().strip('"')
    if not cleaned:
        return []
    separator = "|" if "|" in cleaned else ","
    deduped: dict[str, str] = {}
    for segment in cleaned.split(separator):
        part = segment.strip()
        if ":" not in part:
            continue
        order_num, token = part.split(":", 1)
        valid_num = normalize_valid_order_number_or_none(order_num.strip())
        clean_token = token.strip()
        if (
            valid_num is None
            or not clean_token
            or len(clean_token) > 128
            or not clean_token.isascii()
        ):
            continue
        deduped.pop(valid_num, None)
        deduped[valid_num] = clean_token
    return list(deduped.items())


def build_guest_orders_cookie_value(
    existing_cookie: str | None,
    *,
    order_number: str,
    guest_token: str,
) -> str:
    """Update bounded guest orders cookie keeping at most the 5 most recent pairs."""
    pairs = [
        (num, tok)
        for num, tok in parse_guest_orders_cookie(existing_cookie)
        if num != order_number
    ]
    pairs.append((order_number, guest_token))
    bounded = pairs[-MAX_GUEST_ORDERS_COOKIE_PAIRS:]
    return "|".join(f"{num}:{tok}" for num, tok in bounded)


def compute_payment_intent_fingerprint(
    order_number: str,
    amount_cents: int,
    currency: str,
) -> str:
    """Compute canonical SHA-256 fingerprint for a payment intent attempt."""
    canonical = {
        "order_number": order_number,
        "amount_cents": amount_cents,
        "currency": currency,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _compute_checkout_fingerprint(
    *,
    cart_id: uuid.UUID,
    items: Sequence[tuple[uuid.UUID, int]],
    customer_email: str,
    shipping_address: dict[str, Any],
    billing_address: dict[str, Any],
) -> str:
    canonical = {
        "cart_id": str(cart_id),
        "items": sorted([str(vid), qty] for vid, qty in items),
        "customer_email": customer_email,
        "shipping_address": shipping_address,
        "billing_address": billing_address,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _generate_order_number(now: datetime) -> str:
    date_part = now.astimezone(UTC).strftime("%Y%m%d")
    suffix = "".join(secrets.choice(_ORDER_NUMBER_ALPHABET) for _ in range(8))
    return f"ORD-{date_part}-{suffix}"


def _acquire_advisory_xact_lock(db: Session, scoped_key: str) -> None:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": scoped_key},
        )


def _to_order_item_dto(item: OrderItem) -> OrderItemDTO:
    return OrderItemDTO(
        id=item.id,
        product_id=item.product_id,
        variant_id=item.variant_id,
        sku_snapshot=item.sku_snapshot,
        product_title_snapshot=item.product_title_snapshot,
        variant_title_snapshot=item.variant_title_snapshot,
        attributes_snapshot=dict(item.attributes_snapshot_json),
        unit_price_cents=item.unit_price_cents,
        quantity=item.quantity,
        line_total_cents=item.line_total_cents,
    )


def _to_order_event_dto(event: OrderEvent) -> OrderEventDTO:
    return OrderEventDTO(
        id=event.id,
        actor_user_id=event.actor_user_id,
        event_type=event.event_type,
        from_status=event.from_status,
        to_status=event.to_status,
        metadata=dict(event.metadata_json),
        created_at=event.created_at,
    )


def _load_order_items(db: Session, order_id: uuid.UUID) -> list[OrderItem]:
    return list(
        db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.variant_id.asc(), OrderItem.id.asc())
            .execution_options(populate_existing=True)
        ).all()
    )


def _load_order_detail_dto(
    db: Session,
    order: Order,
) -> OrderDetailDTO:
    items = _load_order_items(db, order.id)
    events = list(
        db.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == order.id)
            .order_by(OrderEvent.created_at.asc(), OrderEvent.id.asc())
            .execution_options(populate_existing=True)
        ).all()
    )
    latest_intent = db.scalar(
        select(PaymentIntent)
        .where(PaymentIntent.order_id == order.id)
        .order_by(PaymentIntent.created_at.desc(), PaymentIntent.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    active_expiry = db.scalar(
        select(func.min(InventoryReservation.expires_at)).where(
            InventoryReservation.order_id == order.id,
            InventoryReservation.status == "ACTIVE",
        )
    )
    return OrderDetailDTO(
        id=order.id,
        order_number=order.order_number,
        user_id=order.user_id,
        status=cast(OrderStatus, order.status),
        payment_status=cast(PaymentStatus, order.payment_status),
        fulfillment_status=cast(FulfillmentStatus, order.fulfillment_status),
        customer_email=order.customer_email,
        currency=order.currency,
        subtotal_cents=order.subtotal_cents,
        discount_cents=order.discount_cents,
        shipping_cents=order.shipping_cents,
        tax_cents=order.tax_cents,
        total_cents=order.total_cents,
        shipping_address=AddressDTO.model_validate(order.shipping_address_json),
        billing_address=AddressDTO.model_validate(order.billing_address_json),
        items=[_to_order_item_dto(item) for item in items],
        payment_intent=(
            PaymentIntentDTO.model_validate(latest_intent)
            if latest_intent is not None
            else None
        ),
        reservation_expires_at=active_expiry,
        events=[_to_order_event_dto(event) for event in events],
        placed_at=order.placed_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        completed_at=order.completed_at,
        cancelled_at=order.cancelled_at,
        cancellation_reason=order.cancellation_reason,
    )


def _build_checkout_response_dto(
    db: Session,
    *,
    order: Order,
    settings: Settings,
    intent: PaymentIntent | None = None,
    items: Sequence[OrderItem] | None = None,
    reservation_expires_at: datetime | None = None,
) -> CheckoutOrderResponseDTO:
    resolved_items = list(items) if items is not None else _load_order_items(db, order.id)
    resolved_intent = intent
    if resolved_intent is None:
        init_key = hash_token(f"checkout-init:{order.id}")
        resolved_intent = db.scalar(
            select(PaymentIntent).where(
                PaymentIntent.order_id == order.id,
                PaymentIntent.idempotency_key == init_key,
            )
        )
        if resolved_intent is None:
            resolved_intent = db.scalar(
                select(PaymentIntent)
                .where(PaymentIntent.order_id == order.id)
                .order_by(PaymentIntent.created_at.desc(), PaymentIntent.id.desc())
                .limit(1)
            )
    assert resolved_intent is not None

    resolved_expiry = reservation_expires_at
    if resolved_expiry is None:
        resolved_expiry = db.scalar(
            select(func.max(InventoryReservation.expires_at)).where(
                InventoryReservation.order_id == order.id,
            )
        )
    if resolved_expiry is None:
        resolved_expiry = ensure_utc_datetime(order.placed_at) + RESERVATION_TTL

    guest_token: str | None = None
    if order.user_id is None:
        guest_token = derive_guest_order_token(
            order.id,
            order.order_number,
            order.guest_token_version,
            settings.guest_order_hmac_secret,
        )

    return CheckoutOrderResponseDTO(
        order=OrderCheckoutSummaryDTO(
            id=order.id,
            order_number=order.order_number,
            status=cast(OrderStatus, order.status),
            payment_status=cast(PaymentStatus, order.payment_status),
            fulfillment_status=cast(FulfillmentStatus, order.fulfillment_status),
            customer_email=order.customer_email,
            currency=order.currency,
            subtotal_cents=order.subtotal_cents,
            discount_cents=order.discount_cents,
            shipping_cents=order.shipping_cents,
            tax_cents=order.tax_cents,
            total_cents=order.total_cents,
            items=[_to_order_item_dto(item) for item in resolved_items],
            placed_at=order.placed_at,
            paid_at=order.paid_at,
        ),
        payment_intent=PaymentIntentDTO.model_validate(resolved_intent),
        reservation_expires_at=resolved_expiry,
        guest_access_token=guest_token,
    )


def _find_actor_active_cart(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    guest_token_hash: str | None,
    now: datetime,
) -> Cart | None:
    if user_id is not None:
        cart = db.scalar(
            select(Cart).where(Cart.user_id == user_id, Cart.status == "ACTIVE")
        )
    elif guest_token_hash is not None:
        cart = db.scalar(
            select(Cart).where(
                Cart.guest_token_hash == guest_token_hash,
                Cart.status == "ACTIVE",
            )
        )
    else:
        return None

    if cart is not None and ensure_utc_datetime(cart.expires_at) <= now:
        return None
    return cart


def _enforce_guest_pending_order_cap(
    db: Session,
    tracker: GuestPendingOrderTracker,
    *,
    client_ip_key: str,
    guest_orders_cookie: str | None,
    settings: Settings,
) -> None:
    tracked_ids = tracker.get_ids(client_ip_key)
    cookie_token_map = dict(parse_guest_orders_cookie(guest_orders_cookie))
    cookie_order_numbers = list(cookie_token_map.keys())
    if not tracked_ids and not cookie_order_numbers:
        return

    match_clauses = []
    if tracked_ids:
        match_clauses.append(Order.id.in_(tracked_ids))
    if cookie_order_numbers:
        match_clauses.append(Order.order_number.in_(cookie_order_numbers))

    candidate_rows = db.execute(
        select(
            Order.id,
            Order.order_number,
            Order.guest_token_version,
            Order.guest_access_expires_at,
        ).where(
            Order.user_id.is_(None),
            Order.status == "PENDING_PAYMENT",
            or_(*match_clauses),
        )
    ).all()

    now = datetime.now(UTC)
    tracked_id_set = set(tracked_ids)
    candidate_order_ids: list[uuid.UUID] = []
    for oid, order_num, guest_ver, guest_exp in candidate_rows:
        if oid in tracked_id_set:
            candidate_order_ids.append(oid)
            continue
        cookie_token = cookie_token_map.get(order_num)
        if (
            cookie_token is not None
            and guest_exp is not None
            and ensure_utc_datetime(guest_exp) > now
            and verify_guest_order_token(
                cookie_token,
                oid,
                order_num,
                guest_ver,
                settings.guest_order_hmac_secret,
            )
        ):
            candidate_order_ids.append(oid)

    if not candidate_order_ids:
        tracker.retain_active(client_ip_key, set())
        return
    res_rows = db.execute(
        select(
            InventoryReservation.order_id,
            InventoryReservation.variant_id,
            InventoryReservation.expires_at,
        ).where(
            InventoryReservation.order_id.in_(candidate_order_ids),
            InventoryReservation.status == "ACTIVE",
        )
    ).all()

    expired_variants: list[uuid.UUID] = []
    for _oid, vid, exp in res_rows:
        if ensure_utc_datetime(exp) < now:
            expired_variants.append(vid)

    if expired_variants:
        if reclaim_expired_reservations_for_variants(db, expired_variants, now=now) > 0:
            db.commit()
        now = datetime.now(UTC)
        res_rows = db.execute(
            select(
                InventoryReservation.order_id,
                InventoryReservation.variant_id,
                InventoryReservation.expires_at,
            )
            .join(Order, Order.id == InventoryReservation.order_id)
            .where(
                Order.id.in_(candidate_order_ids),
                Order.status == "PENDING_PAYMENT",
                InventoryReservation.status == "ACTIVE",
            )
        ).all()

    active_pending_ids: set[uuid.UUID] = set()
    for oid, _vid, exp in res_rows:
        if ensure_utc_datetime(exp) >= now:
            active_pending_ids.add(oid)

    tracker.retain_active(client_ip_key, active_pending_ids)
    if len(active_pending_ids) >= MAX_ACTIVE_GUEST_PENDING_ORDERS:
        raise RateLimitExceededError(
            code="RATE_LIMIT_EXCEEDED",
            message=(
                "Maximum of 3 active pending guest orders reached. "
                "Complete or cancel an existing pending order."
            ),
            details={"max_active_pending_orders": MAX_ACTIVE_GUEST_PENDING_ORDERS},
        )


def create_checkout_order(
    db: Session,
    *,
    auth: AuthContext | None,
    raw_guest_cart_token: str | None,
    guest_orders_cookie: str | None,
    idempotency_key: str,
    payload: CreateCheckoutOrderRequest,
    settings: Settings,
    tracker: GuestPendingOrderTracker,
    client_ip_key: str,
) -> tuple[CheckoutOrderResponseDTO, bool]:
    """Convert active cart to PENDING_PAYMENT order idempotently with stock reservation.

    Returns (CheckoutOrderResponseDTO, is_replay).
    """
    now = datetime.now(UTC)
    guest_token_hash: str | None = None
    if auth is not None:
        actor_scope = f"user:{auth.user.id}"
    else:
        if not raw_guest_cart_token:
            raise ConflictError(
                code="CART_EMPTY",
                message="Your cart is empty.",
            )
        guest_token_hash = hash_token(raw_guest_cart_token)
        actor_scope = f"guest:{guest_token_hash}"

    scoped_key = hash_token(f"{actor_scope}:{idempotency_key}")

    # Pre-step: lazily reclaim expired reservations for cart items before taking checkout locks.
    pre_cart = _find_actor_active_cart(
        db,
        user_id=auth.user.id if auth is not None else None,
        guest_token_hash=guest_token_hash,
        now=now,
    )
    if pre_cart is not None:
        pre_variant_ids = list(
            db.scalars(
                select(CartItem.variant_id).where(CartItem.cart_id == pre_cart.id)
            ).all()
        )
        if pre_variant_ids and reclaim_expired_reservations_for_variants(
            db, pre_variant_ids, now=now
        ) > 0:
            db.commit()

    if auth is None and db.scalar(
        select(Order.id).where(Order.idempotency_key == scoped_key)
    ) is None:
        _enforce_guest_pending_order_cap(
            db,
            tracker,
            client_ip_key=client_ip_key,
            guest_orders_cookie=guest_orders_cookie,
            settings=settings,
        )

    shipping_dict = payload.shipping_address.model_dump()
    billing_dict = payload.effective_billing_address().model_dump()

    _acquire_advisory_xact_lock(db, scoped_key)

    existing_order = db.scalar(
        select(Order)
        .where(Order.idempotency_key == scoped_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    active_cart = _find_actor_active_cart(
        db,
        user_id=auth.user.id if auth is not None else None,
        guest_token_hash=guest_token_hash,
        now=datetime.now(UTC),
    )

    if existing_order is not None:
        return (
            _handle_existing_checkout_idempotency(
                db,
                existing_order=existing_order,
                active_cart=active_cart,
                customer_email=payload.customer_email,
                shipping_dict=shipping_dict,
                billing_dict=billing_dict,
                settings=settings,
            ),
            True,
        )

    if active_cart is None:
        db.rollback()
        raise ConflictError(
            code="CART_EMPTY",
            message="Your cart is empty.",
        )

    locked_cart = db.scalar(
        select(Cart)
        .where(Cart.id == active_cart.id, Cart.status == "ACTIVE")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_cart is None or ensure_utc_datetime(locked_cart.expires_at) <= datetime.now(UTC):
        db.rollback()
        raise ConflictError(
            code="CART_EMPTY",
            message="Your cart is empty.",
        )

    cart_items = list(
        db.scalars(
            select(CartItem)
            .where(CartItem.cart_id == locked_cart.id)
            .order_by(CartItem.variant_id.asc())
        ).all()
    )
    if not cart_items:
        db.rollback()
        raise ConflictError(
            code="CART_EMPTY",
            message="Your cart is empty.",
        )

    variant_ids = sorted({item.variant_id for item in cart_items})

    # Canonical lock order: InventoryStock rows locked in ascending variant_id order.
    locked_stocks = list(
        db.scalars(
            select(InventoryStock)
            .where(InventoryStock.variant_id.in_(variant_ids))
            .order_by(InventoryStock.variant_id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    stocks_by_variant = {stock.variant_id: stock for stock in locked_stocks}

    variant_rows = db.execute(
        select(ProductVariant, Product, Category)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(ProductVariant.id.in_(variant_ids))
        .order_by(ProductVariant.id.asc())
    ).all()
    catalog_by_variant = {
        variant.id: (variant, product, category)
        for variant, product, category in variant_rows
    }

    subtotal_cents = 0
    for item in cart_items:
        catalog_entry = catalog_by_variant.get(item.variant_id)
        if catalog_entry is None:
            db.rollback()
            raise ConflictError(
                code="VARIANT_UNAVAILABLE",
                message="A cart item is no longer available.",
            )
        variant, product, category = catalog_entry
        if (
            not category.is_active
            or product.status != "ACTIVE"
            or not variant.is_active
            or variant.currency != locked_cart.currency
        ):
            db.rollback()
            raise ConflictError(
                code="VARIANT_UNAVAILABLE",
                message=f"Item {variant.sku} is no longer available.",
                details={"variant_id": str(variant.id)},
            )

        stock = stocks_by_variant.get(variant.id)
        available = max(0, stock.on_hand - stock.reserved) if stock is not None else 0
        if available < item.quantity:
            db.rollback()
            raise ConflictError(
                code="INSUFFICIENT_STOCK",
                message=f"Requested quantity exceeds available stock for {variant.sku}.",
                details={
                    "variant_id": str(variant.id),
                    "requested": item.quantity,
                    "available": available,
                },
            )
        line_total = variant.price_cents * item.quantity
        if line_total > _INT32_MAX:
            db.rollback()
            raise ValidationAppError(
                code="VALIDATION_ERROR",
                message="Order total exceeds maximum supported amount.",
            )
        subtotal_cents += line_total
        if subtotal_cents > _INT32_MAX:
            db.rollback()
            raise ValidationAppError(
                code="VALIDATION_ERROR",
                message="Order total exceeds maximum supported amount.",
            )

    discount_cents = 0
    shipping_cents = (
        0
        if subtotal_cents >= settings.free_shipping_threshold_cents
        else settings.standard_shipping_cents
    )
    tax_cents = int(
        (
            Decimal(subtotal_cents - discount_cents)
            * Decimal(settings.tax_rate_bps)
            / Decimal(10000)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    total_cents = subtotal_cents - discount_cents + shipping_cents + tax_cents
    if any(
        amount > _INT32_MAX
        for amount in (subtotal_cents, shipping_cents, tax_cents, total_cents)
    ):
        db.rollback()
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="Order total exceeds maximum supported amount.",
        )

    checkout_now = datetime.now(UTC)
    reservation_expires_at = checkout_now + RESERVATION_TTL
    guest_access_expires_at = (
        checkout_now + GUEST_ACCESS_TTL if auth is None else None
    )
    request_fingerprint = _compute_checkout_fingerprint(
        cart_id=locked_cart.id,
        items=[(item.variant_id, item.quantity) for item in cart_items],
        customer_email=payload.customer_email,
        shipping_address=shipping_dict,
        billing_address=billing_dict,
    )

    order = Order(
        order_number=_generate_order_number(checkout_now),
        user_id=auth.user.id if auth is not None else None,
        cart_id=locked_cart.id,
        customer_email=payload.customer_email,
        guest_token_version=1,
        guest_access_expires_at=guest_access_expires_at,
        status="PENDING_PAYMENT",
        payment_status="REQUIRES_PAYMENT",
        fulfillment_status="UNFULFILLED",
        currency=locked_cart.currency,
        subtotal_cents=subtotal_cents,
        discount_cents=discount_cents,
        shipping_cents=shipping_cents,
        tax_cents=tax_cents,
        total_cents=total_cents,
        idempotency_key=scoped_key,
        request_fingerprint=request_fingerprint,
        shipping_address_json=shipping_dict,
        billing_address_json=billing_dict,
        placed_at=checkout_now,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raced_order = db.scalar(
            select(Order).where(Order.idempotency_key == scoped_key)
        )
        if raced_order is not None:
            raced_cart = _find_actor_active_cart(
                db,
                user_id=auth.user.id if auth is not None else None,
                guest_token_hash=guest_token_hash,
                now=datetime.now(UTC),
            )
            return (
                _handle_existing_checkout_idempotency(
                    db,
                    existing_order=raced_order,
                    active_cart=raced_cart,
                    customer_email=payload.customer_email,
                    shipping_dict=shipping_dict,
                    billing_dict=billing_dict,
                    settings=settings,
                ),
                True,
            )
        raise ConflictError(
            code="CHECKOUT_CONFLICT",
            message="Order checkout conflicted with another request; please retry.",
        ) from exc

    actor_user_id = auth.user.id if auth is not None else None
    created_items: list[OrderItem] = []

    for item in cart_items:
        variant, product, _category = catalog_by_variant[item.variant_id]
        stock = stocks_by_variant[item.variant_id]
        line_total = variant.price_cents * item.quantity

        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            sku_snapshot=variant.sku,
            product_title_snapshot=product.title,
            variant_title_snapshot=variant.title,
            attributes_snapshot_json=dict(variant.attributes_json),
            unit_price_cents=variant.price_cents,
            quantity=item.quantity,
            line_total_cents=line_total,
        )
        db.add(order_item)
        created_items.append(order_item)

        stock.reserved += item.quantity
        stock.version += 1

        db.add(
            InventoryReservation(
                order_id=order.id,
                variant_id=variant.id,
                quantity=item.quantity,
                status="ACTIVE",
                expires_at=reservation_expires_at,
            )
        )
        db.add(
            InventoryLedgerEntry(
                variant_id=variant.id,
                order_id=order.id,
                actor_user_id=actor_user_id,
                reason="RESERVE",
                delta_on_hand=0,
                delta_reserved=item.quantity,
                on_hand_after=stock.on_hand,
                reserved_after=stock.reserved,
                note=f"Order {order.order_number} checkout reservation",
            )
        )

    db.add(
        OrderEvent(
            order_id=order.id,
            actor_user_id=actor_user_id,
            event_type="ORDER_PLACED",
            from_status=None,
            to_status="PENDING_PAYMENT",
            metadata_json={
                "item_count": sum(item.quantity for item in cart_items),
                "total_cents": total_cents,
            },
        )
    )

    locked_cart.status = "CONVERTED"

    initial_intent = PaymentIntent(
        order_id=order.id,
        provider=SANDBOX_PROVIDER,
        provider_intent_id=f"pi_sandbox_{uuid.uuid4().hex}",
        idempotency_key=hash_token(f"checkout-init:{order.id}"),
        request_fingerprint=compute_payment_intent_fingerprint(
            order.order_number,
            total_cents,
            order.currency,
        ),
        amount_cents=total_cents,
        currency=order.currency,
        status="REQUIRES_ACTION",
    )
    db.add(initial_intent)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced_order = db.scalar(
            select(Order).where(Order.idempotency_key == scoped_key)
        )
        if raced_order is not None:
            raced_cart = _find_actor_active_cart(
                db,
                user_id=auth.user.id if auth is not None else None,
                guest_token_hash=guest_token_hash,
                now=datetime.now(UTC),
            )
            return (
                _handle_existing_checkout_idempotency(
                    db,
                    existing_order=raced_order,
                    active_cart=raced_cart,
                    customer_email=payload.customer_email,
                    shipping_dict=shipping_dict,
                    billing_dict=billing_dict,
                    settings=settings,
                ),
                True,
            )
        raise

    if auth is None:
        tracker.record(client_ip_key, order.id)

    return (
        _build_checkout_response_dto(
            db,
            order=order,
            settings=settings,
            intent=initial_intent,
            items=created_items,
            reservation_expires_at=reservation_expires_at,
        ),
        False,
    )


def _handle_existing_checkout_idempotency(
    db: Session,
    *,
    existing_order: Order,
    active_cart: Cart | None,
    customer_email: str,
    shipping_dict: dict[str, Any],
    billing_dict: dict[str, Any],
    settings: Settings,
) -> CheckoutOrderResponseDTO:
    if active_cart is not None and active_cart.id != existing_order.cart_id:
        active_items = list(
            db.scalars(
                select(CartItem).where(CartItem.cart_id == active_cart.id)
            ).all()
        )
        if active_items:
            db.rollback()
            raise ConflictError(
                code="IDEMPOTENCY_KEY_CONFLICT",
                message="Idempotency-Key was already used for a different cart.",
            )

    order_items = _load_order_items(db, existing_order.id)
    replay_fingerprint = _compute_checkout_fingerprint(
        cart_id=existing_order.cart_id or uuid.UUID(int=0),
        items=[(item.variant_id, item.quantity) for item in order_items],
        customer_email=customer_email,
        shipping_address=shipping_dict,
        billing_address=billing_dict,
    )
    if not hmac.compare_digest(replay_fingerprint, existing_order.request_fingerprint):
        db.rollback()
        raise ConflictError(
            code="IDEMPOTENCY_KEY_CONFLICT",
            message="Idempotency-Key was already used with a different checkout payload.",
        )

    dto = _build_checkout_response_dto(
        db,
        order=existing_order,
        settings=settings,
        items=order_items,
    )
    db.rollback()
    return dto


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
                code="AUTH_SESSION_EXPIRED",
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


def list_customer_orders(
    db: Session,
    *,
    user_id: uuid.UUID,
    pagination: PaginationParams,
) -> PaginatedResponse[OrderListItemDTO]:
    """List paginated orders owned by user_id (newest first), lazily reclaiming expired stock."""
    total = int(
        db.scalar(
            select(func.count(Order.id)).where(Order.user_id == user_id)
        )
        or 0
    )
    if total == 0 or pagination.offset >= total:
        return PaginatedResponse(
            items=[],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            has_next=False,
        )

    orders = list(
        db.scalars(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.placed_at.desc(), Order.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
    )

    pending_ids = [o.id for o in orders if o.status == "PENDING_PAYMENT"]
    if pending_ids:
        pending_variants = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id.in_(pending_ids),
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if pending_variants and reclaim_expired_reservations_for_variants(
            db, pending_variants
        ) > 0:
            db.commit()
            orders = list(
                db.scalars(
                    select(Order)
                    .where(Order.user_id == user_id)
                    .order_by(Order.placed_at.desc(), Order.id.desc())
                    .offset(pagination.offset)
                    .limit(pagination.page_size)
                    .execution_options(populate_existing=True)
                ).all()
            )

    order_ids = [o.id for o in orders]
    item_counts: dict[uuid.UUID, int] = {oid: 0 for oid in order_ids}
    for oid, cnt in db.execute(
        select(OrderItem.order_id, func.coalesce(func.sum(OrderItem.quantity), 0))
        .where(OrderItem.order_id.in_(order_ids))
        .group_by(OrderItem.order_id)
    ).all():
        item_counts[oid] = int(cnt)

    active_expiries: dict[uuid.UUID, datetime] = {}
    for oid, exp in db.execute(
        select(
            InventoryReservation.order_id,
            func.min(InventoryReservation.expires_at),
        )
        .where(
            InventoryReservation.order_id.in_(order_ids),
            InventoryReservation.status == "ACTIVE",
        )
        .group_by(InventoryReservation.order_id)
    ).all():
        if exp is not None:
            active_expiries[oid] = exp

    items = [
        OrderListItemDTO(
            id=order.id,
            order_number=order.order_number,
            status=cast(OrderStatus, order.status),
            payment_status=cast(PaymentStatus, order.payment_status),
            fulfillment_status=cast(FulfillmentStatus, order.fulfillment_status),
            currency=order.currency,
            subtotal_cents=order.subtotal_cents,
            discount_cents=order.discount_cents,
            shipping_cents=order.shipping_cents,
            tax_cents=order.tax_cents,
            total_cents=order.total_cents,
            item_count=item_counts.get(order.id, 0),
            placed_at=order.placed_at,
            paid_at=order.paid_at,
            reservation_expires_at=active_expiries.get(order.id),
        )
        for order in orders
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )


def get_order_detail(
    db: Session,
    *,
    order_number: str,
    auth: AuthContext | None,
    guest_token_header: str | None,
    guest_orders_cookie: str | None,
    settings: Settings,
    rate_limiter: RateLimiterProtocol,
    client_ip_key: str,
) -> OrderDetailDTO:
    """Fetch full OrderDetailDTO after verifying strict owner/guest access and lazy expiry."""
    order = authorize_order_access(
        db,
        order_number=order_number,
        auth=auth,
        guest_token_header=guest_token_header,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=client_ip_key,
    )

    if order.status == "PENDING_PAYMENT":
        variant_ids = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id == order.id,
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if variant_ids and reclaim_expired_reservations_for_variants(db, variant_ids) > 0:
            db.commit()
            db.refresh(order)

    return _load_order_detail_dto(db, order)


def cancel_customer_order(
    db: Session,
    *,
    order_number: str,
    reason: str | None,
    auth: AuthContext | None,
    guest_token_header: str | None,
    guest_orders_cookie: str | None,
    settings: Settings,
    rate_limiter: RateLimiterProtocol,
    client_ip_key: str,
) -> OrderDetailDTO:
    """Cancel an unpaid PENDING_PAYMENT order and atomically release active reservations."""
    order = authorize_order_access(
        db,
        order_number=order_number,
        auth=auth,
        guest_token_header=guest_token_header,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=client_ip_key,
    )

    if order.status == "PENDING_PAYMENT":
        pre_variants = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id == order.id,
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if pre_variants and reclaim_expired_reservations_for_variants(db, pre_variants) > 0:
            db.commit()

    # Universal lock hierarchy: PaymentIntent -> Order -> InventoryReservation -> InventoryStock.
    intents = list(
        db.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.order_id == order.id)
            .order_by(PaymentIntent.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    locked_order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert locked_order is not None

    reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == locked_order.id)
            .order_by(InventoryReservation.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    active_reservations = [res for res in reservations if res.status == "ACTIVE"]
    variant_ids = sorted({res.variant_id for res in active_reservations})
    stocks_by_variant: dict[uuid.UUID, InventoryStock] = {}
    if variant_ids:
        stocks_by_variant = {
            stock.variant_id: stock
            for stock in db.scalars(
                select(InventoryStock)
                .where(InventoryStock.variant_id.in_(variant_ids))
                .order_by(InventoryStock.variant_id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }

    now = datetime.now(UTC)
    if locked_order.status == "PENDING_PAYMENT" and any(
        ensure_utc_datetime(res.expires_at) < now for res in active_reservations
    ):
        _expire_locked_order_inline(
            db,
            order=locked_order,
            intents=intents,
            active_reservations=active_reservations,
            stocks_by_variant=stocks_by_variant,
            now=now,
        )
        db.commit()
        raise ConflictError(
            code="INVALID_ORDER_STATE_TRANSITION",
            message="Order reservation has already expired.",
        )

    if (
        locked_order.status != "PENDING_PAYMENT"
        or locked_order.payment_status not in ("REQUIRES_PAYMENT", "FAILED")
    ):
        db.rollback()
        raise ConflictError(
            code="INVALID_ORDER_STATE_TRANSITION",
            message="Only unpaid orders awaiting payment can be cancelled.",
        )

    cancel_reason = reason or "CUSTOMER_CANCELLED"
    actor_user_id = auth.user.id if auth is not None else None

    for intent in intents:
        if intent.status in ("REQUIRES_ACTION", "PROCESSING", "FAILED"):
            intent.status = "CANCELLED"

    locked_order.status = "CANCELLED"
    locked_order.payment_status = "CANCELLED"
    locked_order.fulfillment_status = "CANCELLED"
    locked_order.cancelled_at = now
    locked_order.cancellation_reason = cancel_reason

    for res in sorted(active_reservations, key=lambda r: (r.variant_id, str(r.id))):
        res.status = "RELEASED"
        stock = stocks_by_variant.get(res.variant_id)
        if stock is None:
            continue
        actual_release = min(res.quantity, max(0, stock.reserved))
        stock.reserved -= actual_release
        stock.version += 1
        db.add(
            InventoryLedgerEntry(
                variant_id=res.variant_id,
                order_id=locked_order.id,
                actor_user_id=actor_user_id,
                reason="RELEASE",
                delta_on_hand=0,
                delta_reserved=-actual_release,
                on_hand_after=stock.on_hand,
                reserved_after=stock.reserved,
                note=f"Order {locked_order.order_number} cancelled",
            )
        )

    db.add(
        OrderEvent(
            order_id=locked_order.id,
            actor_user_id=actor_user_id,
            event_type="ORDER_CANCELLED",
            from_status="PENDING_PAYMENT",
            to_status="CANCELLED",
            metadata_json={"reason": cancel_reason},
        )
    )
    db.commit()
    return _load_order_detail_dto(db, locked_order)


def _expire_locked_order_inline(
    db: Session,
    *,
    order: Order,
    intents: Sequence[PaymentIntent],
    active_reservations: Sequence[InventoryReservation],
    stocks_by_variant: dict[uuid.UUID, InventoryStock],
    now: datetime,
) -> None:
    for intent in intents:
        if intent.status in ("REQUIRES_ACTION", "PROCESSING", "FAILED"):
            intent.status = "CANCELLED"
    order.status = "CANCELLED"
    order.payment_status = "CANCELLED"
    order.fulfillment_status = "CANCELLED"
    order.cancelled_at = now
    order.cancellation_reason = "RESERVATION_EXPIRED"

    db.add(
        OrderEvent(
            order_id=order.id,
            actor_user_id=None,
            event_type="ORDER_CANCELLED",
            from_status="PENDING_PAYMENT",
            to_status="CANCELLED",
            metadata_json={"reason": "RESERVATION_EXPIRED"},
        )
    )

    for res in sorted(active_reservations, key=lambda r: (r.variant_id, str(r.id))):
        res.status = "EXPIRED"
        stock = stocks_by_variant.get(res.variant_id)
        if stock is None:
            continue
        actual_release = min(res.quantity, max(0, stock.reserved))
        stock.reserved -= actual_release
        stock.version += 1
        db.add(
            InventoryLedgerEntry(
                variant_id=res.variant_id,
                order_id=order.id,
                actor_user_id=None,
                reason="RELEASE",
                delta_on_hand=0,
                delta_reserved=-actual_release,
                on_hand_after=stock.on_hand,
                reserved_after=stock.reserved,
                note="RESERVATION_EXPIRED",
            )
        )


def mark_order_paid(
    db: Session,
    *,
    order: Order,
    intent: PaymentIntent,
    reservations: Sequence[InventoryReservation],
    stocks_by_variant: dict[uuid.UUID, InventoryStock],
    variants_by_id: dict[uuid.UUID, tuple[ProductVariant, Product, Category]],
    event_id: str,
    now: datetime,
) -> None:
    """Apply payment_intent.succeeded state transition matrix under existing row locks."""
    if order.payment_status == "PAID":
        if intent.status != "SUCCEEDED":
            intent.status = "REFUNDED"
            db.add(
                OrderEvent(
                    order_id=order.id,
                    actor_user_id=None,
                    event_type="DUPLICATE_PAYMENT_REFUNDED",
                    from_status=order.status,
                    to_status=order.status,
                    metadata_json={
                        "provider_intent_id": intent.provider_intent_id,
                        "event_id": event_id,
                    },
                )
            )
        return

    if order.status == "PENDING_PAYMENT":
        intent.status = "SUCCEEDED"
        intent.failure_code = None
        intent.failure_message = None
        order.status = "CONFIRMED"
        order.payment_status = "PAID"
        order.fulfillment_status = "ALLOCATED"
        order.paid_at = now

        for res in sorted(reservations, key=lambda r: (r.variant_id, str(r.id))):
            if res.status != "ACTIVE":
                continue
            res.status = "COMMITTED"
            stock = stocks_by_variant.get(res.variant_id)
            if stock is None:
                continue
            stock.reserved -= res.quantity
            stock.on_hand -= res.quantity
            stock.version += 1
            db.add(
                InventoryLedgerEntry(
                    variant_id=res.variant_id,
                    order_id=order.id,
                    actor_user_id=None,
                    reason="COMMIT",
                    delta_on_hand=-res.quantity,
                    delta_reserved=-res.quantity,
                    on_hand_after=stock.on_hand,
                    reserved_after=stock.reserved,
                    note=f"Order {order.order_number} payment confirmed",
                )
            )

        db.add(
            OrderEvent(
                order_id=order.id,
                actor_user_id=None,
                event_type="PAYMENT_SUCCEEDED",
                from_status="PENDING_PAYMENT",
                to_status="CONFIRMED",
                metadata_json={
                    "provider_intent_id": intent.provider_intent_id,
                    "event_id": event_id,
                },
            )
        )
        return

    if order.status == "CANCELLED":
        is_expired_cancel = (
            order.cancellation_reason == "RESERVATION_EXPIRED"
            and bool(reservations)
            and all(res.status == "EXPIRED" for res in reservations)
        )
        if is_expired_cancel:
            can_reacquire = True
            for res in reservations:
                catalog_entry = variants_by_id.get(res.variant_id)
                stock = stocks_by_variant.get(res.variant_id)
                if catalog_entry is None or stock is None:
                    can_reacquire = False
                    break
                variant, product, category = catalog_entry
                sku_active = (
                    category.is_active
                    and product.status == "ACTIVE"
                    and variant.is_active
                    and variant.currency == order.currency
                )
                if not sku_active or (stock.on_hand - stock.reserved) < res.quantity:
                    can_reacquire = False
                    break

            if can_reacquire:
                intent.status = "SUCCEEDED"
                intent.failure_code = None
                intent.failure_message = None
                order.status = "CONFIRMED"
                order.payment_status = "PAID"
                order.fulfillment_status = "ALLOCATED"
                order.cancelled_at = None
                order.cancellation_reason = None
                order.paid_at = now

                for res in sorted(reservations, key=lambda r: (r.variant_id, str(r.id))):
                    res.status = "COMMITTED"
                    stock = stocks_by_variant[res.variant_id]
                    stock.on_hand -= res.quantity
                    stock.version += 1
                    db.add(
                        InventoryLedgerEntry(
                            variant_id=res.variant_id,
                            order_id=order.id,
                            actor_user_id=None,
                            reason="COMMIT",
                            delta_on_hand=-res.quantity,
                            delta_reserved=0,
                            on_hand_after=stock.on_hand,
                            reserved_after=stock.reserved,
                            note=f"Order {order.order_number} late payment re-acquired stock",
                        )
                    )

                db.add(
                    OrderEvent(
                        order_id=order.id,
                        actor_user_id=None,
                        event_type="PAYMENT_SUCCEEDED",
                        from_status="CANCELLED",
                        to_status="CONFIRMED",
                        metadata_json={
                            "provider_intent_id": intent.provider_intent_id,
                            "event_id": event_id,
                            "reacquired_after_expiry": True,
                        },
                    )
                )
                return

            intent.status = "REFUNDED"
            order.status = "REFUNDED"
            order.payment_status = "REFUNDED"
            order.fulfillment_status = "CANCELLED"
            order.paid_at = now
            db.add(
                OrderEvent(
                    order_id=order.id,
                    actor_user_id=None,
                    event_type="PAYMENT_SUCCEEDED_OUT_OF_STOCK",
                    from_status="CANCELLED",
                    to_status="REFUNDED",
                    metadata_json={
                        "provider_intent_id": intent.provider_intent_id,
                        "event_id": event_id,
                    },
                )
            )
            return

        # Explicit cancel (RELEASED reservations) -> auto-refund.
        intent.status = "REFUNDED"
        order.status = "REFUNDED"
        order.payment_status = "REFUNDED"
        order.fulfillment_status = "CANCELLED"
        order.paid_at = now
        db.add(
            OrderEvent(
                order_id=order.id,
                actor_user_id=None,
                event_type="PAYMENT_SUCCEEDED_AFTER_CANCEL",
                from_status="CANCELLED",
                to_status="REFUNDED",
                metadata_json={
                    "provider_intent_id": intent.provider_intent_id,
                    "event_id": event_id,
                },
            )
        )
        return

    if intent.status not in ("SUCCEEDED", "REFUNDED"):
        intent.status = "REFUNDED"
        db.add(
            OrderEvent(
                order_id=order.id,
                actor_user_id=None,
                event_type="DUPLICATE_PAYMENT_REFUNDED",
                from_status=order.status,
                to_status=order.status,
                metadata_json={
                    "provider_intent_id": intent.provider_intent_id,
                    "event_id": event_id,
                },
            )
        )


def mark_order_payment_failed(
    db: Session,
    *,
    order: Order,
    intent: PaymentIntent,
    event_id: str,
    failure_code: str | None,
    failure_message: str | None,
) -> None:
    """Apply payment_intent.failed state transition under existing row locks."""
    if order.status != "PENDING_PAYMENT" or intent.status == "SUCCEEDED":
        return

    intent.status = "FAILED"
    intent.failure_code = failure_code
    intent.failure_message = failure_message
    order.payment_status = "FAILED"

    db.add(
        OrderEvent(
            order_id=order.id,
            actor_user_id=None,
            event_type="PAYMENT_FAILED",
            from_status="PENDING_PAYMENT",
            to_status="PENDING_PAYMENT",
            metadata_json={
                "provider_intent_id": intent.provider_intent_id,
                "event_id": event_id,
                "failure_code": failure_code,
                "failure_message": failure_message,
            },
        )
    )


def mark_order_refunded(
    db: Session,
    *,
    order: Order,
    intent: PaymentIntent,
    sibling_intents: Sequence[PaymentIntent],
    reservations: Sequence[InventoryReservation],
    stocks_by_variant: dict[uuid.UUID, InventoryStock],
    event_id: str,
) -> None:
    """Apply charge.refunded webhook transition under existing row locks."""
    if order.status == "REFUNDED" and order.payment_status == "REFUNDED":
        if intent.status != "REFUNDED":
            intent.status = "REFUNDED"
        return

    has_other_succeeded = any(
        other.id != intent.id and other.status == "SUCCEEDED"
        for other in sibling_intents
    )
    if (
        has_other_succeeded
        or order.payment_status != "PAID"
        or intent.status != "SUCCEEDED"
    ):
        intent.status = "REFUNDED"
        db.add(
            OrderEvent(
                order_id=order.id,
                actor_user_id=None,
                event_type="PAYMENT_INTENT_REFUNDED",
                from_status=order.status,
                to_status=order.status,
                metadata_json={
                    "provider_intent_id": intent.provider_intent_id,
                    "event_id": event_id,
                    "reason": "non_winning_or_unpaid_intent_refunded",
                },
            )
        )
        return

    from_status = order.status
    intent.status = "REFUNDED"
    order.status = "REFUNDED"
    order.payment_status = "REFUNDED"
    order.fulfillment_status = (
        "RETURNED"
        if order.fulfillment_status in ("SHIPPED", "DELIVERED")
        else "CANCELLED"
    )

    for res in sorted(reservations, key=lambda r: (r.variant_id, str(r.id))):
        if res.status != "ACTIVE":
            continue
        res.status = "RELEASED"
        stock = stocks_by_variant.get(res.variant_id)
        if stock is None:
            continue
        actual_release = min(res.quantity, max(0, stock.reserved))
        stock.reserved -= actual_release
        stock.version += 1
        db.add(
            InventoryLedgerEntry(
                variant_id=res.variant_id,
                order_id=order.id,
                actor_user_id=None,
                reason="RELEASE",
                delta_on_hand=0,
                delta_reserved=-actual_release,
                on_hand_after=stock.on_hand,
                reserved_after=stock.reserved,
                note=f"Order {order.order_number} refunded",
            )
        )

    db.add(
        OrderEvent(
            order_id=order.id,
            actor_user_id=None,
            event_type="PAYMENT_REFUNDED",
            from_status=from_status,
            to_status="REFUNDED",
            metadata_json={
                "provider_intent_id": intent.provider_intent_id,
                "event_id": event_id,
            },
        )
    )
