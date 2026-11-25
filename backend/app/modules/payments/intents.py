"""Payment intent creation and idempotent replay service."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.security import RateLimiterProtocol, hash_token
from app.models.inventory import InventoryReservation
from app.models.order import Order
from app.models.payment import PaymentIntent
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.identity.dependencies import AuthContext
from app.modules.inventory.service import reclaim_expired_reservations_for_variants
from app.modules.orders.authorization import authorize_order_access
from app.modules.orders.common import (
    ADVISORY_LOCK_NS_PAYMENT_INTENT,
    _acquire_advisory_xact_lock,
    compute_payment_intent_fingerprint,
    validate_idempotency_key,
)
from app.modules.payments.provider import get_default_payment_registry
from app.modules.payments.schemas import (
    CreatePaymentIntentRequest,
    PaymentIntentDTO,
    PaymentIntentResponse,
)


def _handle_existing_intent_idempotency(
    db: Session,
    *,
    existing_intent: PaymentIntent,
    expected_order_id: uuid.UUID,
    expected_fingerprint: str,
) -> PaymentIntentResponse:
    if existing_intent.order_id != expected_order_id or not hmac.compare_digest(
        existing_intent.request_fingerprint, expected_fingerprint
    ):
        db.rollback()
        raise ConflictError(
            code="IDEMPOTENCY_KEY_CONFLICT",
            message="Idempotency-Key was already used with a different payment request.",
        )
    dto = PaymentIntentDTO.model_validate(existing_intent)
    db.rollback()
    return PaymentIntentResponse(payment_intent=dto)


def create_payment_intent(
    db: Session,
    *,
    payload: CreatePaymentIntentRequest,
    raw_idempotency_key: str | None,
    auth: AuthContext | None,
    guest_token_header: str | None,
    guest_orders_cookie: str | None,
    settings: Settings,
    rate_limiter: RateLimiterProtocol,
    client_ip_key: str,
) -> tuple[PaymentIntentResponse, bool]:
    """Create or idempotently replay a PaymentIntent for a PENDING_PAYMENT order."""
    client_key = validate_idempotency_key(raw_idempotency_key)
    payment_adapter = get_default_payment_registry().get(settings.payment_provider)
    order = authorize_order_access(
        db,
        order_number=payload.order_number,
        auth=auth,
        guest_token_header=guest_token_header,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=client_ip_key,
    )

    if order.user_id is not None and auth is not None:
        actor_scope = f"user:{auth.user.id}"
    else:
        actor_scope = f"guest_order:{order.id}"

    scoped_key = hash_token(f"{actor_scope}:{client_key}")
    fingerprint = compute_payment_intent_fingerprint(
        order.order_number,
        order.total_cents,
        order.currency,
    )

    # Lazy expired-reservation reclamation before taking locks.
    if order.status == "PENDING_PAYMENT":
        active_variants = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id == order.id,
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if active_variants and reclaim_expired_reservations_for_variants(
            db, active_variants
        ) > 0:
            db.commit()

    _acquire_advisory_xact_lock(db, ADVISORY_LOCK_NS_PAYMENT_INTENT, scoped_key)

    existing_intent = db.scalar(
        select(PaymentIntent)
        .where(PaymentIntent.idempotency_key == scoped_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing_intent is not None:
        return (
            _handle_existing_intent_idempotency(
                db,
                existing_intent=existing_intent,
                expected_order_id=order.id,
                expected_fingerprint=fingerprint,
            ),
            True,
        )

    # Universal lock order: PaymentIntent -> Order -> InventoryReservation.
    order_intents = list(
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
    now = datetime.now(UTC)
    has_unexpired_reservation = any(
        res.status == "ACTIVE" and ensure_utc_datetime(res.expires_at) > now
        for res in reservations
    )
    if (
        locked_order.status != "PENDING_PAYMENT"
        or locked_order.payment_status not in ("REQUIRES_PAYMENT", "FAILED")
        or not has_unexpired_reservation
    ):
        db.rollback()
        raise ConflictError(
            code="INVALID_ORDER_STATE_TRANSITION",
            message="Order cannot accept a new payment intent in its current state.",
        )

    for prior_intent in order_intents:
        if prior_intent.status in ("REQUIRES_ACTION", "PROCESSING", "FAILED"):
            prior_intent.status = "CANCELLED"

    intent_result = payment_adapter.create_intent(
        order_number=locked_order.order_number,
        amount_cents=locked_order.total_cents,
        currency=locked_order.currency,
        idempotency_key=scoped_key,
    )
    new_intent = PaymentIntent(
        order_id=locked_order.id,
        provider=intent_result.provider,
        provider_intent_id=intent_result.provider_intent_id,
        idempotency_key=scoped_key,
        request_fingerprint=fingerprint,
        amount_cents=locked_order.total_cents,
        currency=locked_order.currency,
        status=intent_result.status,
    )
    db.add(new_intent)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raced_intent = db.scalar(
            select(PaymentIntent).where(PaymentIntent.idempotency_key == scoped_key)
        )
        if raced_intent is not None:
            return (
                _handle_existing_intent_idempotency(
                    db,
                    existing_intent=raced_intent,
                    expected_order_id=order.id,
                    expected_fingerprint=fingerprint,
                ),
                True,
            )
        raise ConflictError(
            code="PAYMENT_INTENT_CONFLICT",
            message="Payment intent creation conflicted with another request.",
        ) from exc

    return (
        PaymentIntentResponse(payment_intent=PaymentIntentDTO.model_validate(new_intent)),
        False,
    )
