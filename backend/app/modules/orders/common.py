"""Shared constants, validation, lock helpers, and DTO mappers for Module 5 (Orders)."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import BadRequestError
from app.core.security import derive_guest_order_token, hash_token
from app.models.inventory import InventoryReservation
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import PaymentIntent
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.orders.schemas import (
    AddressDTO,
    CheckoutOrderResponseDTO,
    FulfillmentStatus,
    OrderCheckoutSummaryDTO,
    OrderDetailDTO,
    OrderEventDTO,
    OrderItemDTO,
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

ADVISORY_LOCK_NS_CHECKOUT = 1
ADVISORY_LOCK_NS_GUEST_PENDING_CAP = 2
ADVISORY_LOCK_NS_PAYMENT_INTENT = 3

_SQLITE_GUEST_CAP_LOCKS_GUARD = threading.Lock()
_SQLITE_GUEST_CAP_LOCKS: dict[str, threading.Lock] = {}


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


def _acquire_advisory_xact_lock(
    db: Session,
    namespace: int,
    scoped_key: str,
) -> None:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"),
            {"ns": namespace, "key": scoped_key},
        )


@contextmanager
def _sqlite_guest_cap_guard(
    db: Session,
    guest_client_key_hash: str | None,
) -> Iterator[None]:
    """Serialize SQLite guest-cap checks per client hash to match PG advisory locks in tests."""
    if guest_client_key_hash is None or db.get_bind().dialect.name == "postgresql":
        yield
        return
    with _SQLITE_GUEST_CAP_LOCKS_GUARD:
        lock = _SQLITE_GUEST_CAP_LOCKS.get(guest_client_key_hash)
        if lock is None:
            lock = threading.Lock()
            _SQLITE_GUEST_CAP_LOCKS[guest_client_key_hash] = lock
    with lock:
        yield


def to_order_item_dto(item: OrderItem) -> OrderItemDTO:
    """Map an OrderItem ORM row to OrderItemDTO."""
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


def to_order_event_dto(event: OrderEvent) -> OrderEventDTO:
    """Map an OrderEvent ORM row to OrderEventDTO."""
    return OrderEventDTO(
        id=event.id,
        actor_user_id=event.actor_user_id,
        event_type=event.event_type,
        from_status=event.from_status,
        to_status=event.to_status,
        metadata=dict(event.metadata_json),
        created_at=event.created_at,
    )


_to_order_item_dto = to_order_item_dto
_to_order_event_dto = to_order_event_dto


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
