"""Order locking, customer cancellation, and inline reservation expiry."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.order_events import append_order_event
from app.core.security import RateLimiterProtocol
from app.models.inventory import InventoryLedgerEntry, InventoryReservation, InventoryStock
from app.models.order import Order
from app.models.payment import PaymentIntent
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.identity.dependencies import AuthContext
from app.modules.inventory.service import reclaim_expired_reservations_for_variants
from app.modules.orders.authorization import authorize_order_access
from app.modules.orders.common import _load_order_detail_dto
from app.modules.orders.schemas import OrderDetailDTO


def lock_order_and_payment_intents_for_update(
    db: Session,
    *,
    order_id: uuid.UUID,
    max_attempts: int = 2,
) -> tuple[Order, list[PaymentIntent]]:
    """Acquire PaymentIntent -> Order FOR UPDATE locks, retrying on concurrent new intents."""
    for attempt in range(max_attempts):
        intents = list(
            db.scalars(
                select(PaymentIntent)
                .where(PaymentIntent.order_id == order_id)
                .order_by(PaymentIntent.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        locked_order = db.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_order is None:
            db.rollback()
            raise NotFoundError(
                code="ORDER_NOT_FOUND",
                message="Order not found.",
            )

        locked_intent_ids = {intent.id for intent in intents}
        current_intent_ids = set(
            db.scalars(
                select(PaymentIntent.id).where(PaymentIntent.order_id == locked_order.id)
            ).all()
        )
        if current_intent_ids - locked_intent_ids:
            db.rollback()
            if attempt + 1 < max_attempts:
                continue
            raise ConflictError(
                code="INVALID_ORDER_STATE_TRANSITION",
                message="Order payment state updated concurrently; please retry.",
            )
        return locked_order, intents

    raise ConflictError(
        code="INVALID_ORDER_STATE_TRANSITION",
        message="Order payment state updated concurrently; please retry.",
    )


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

    append_order_event(
        db,
        order_id=order.id,
        actor_user_id=None,
        event_type="ORDER_CANCELLED",
        from_status="PENDING_PAYMENT",
        to_status="CANCELLED",
        metadata_json={"reason": "RESERVATION_EXPIRED"},
        now=now,
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
    order_id = order.id
    actor_user_id = auth.user.id if auth is not None else None

    if order.status == "PENDING_PAYMENT":
        pre_variants = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id == order_id,
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if pre_variants and reclaim_expired_reservations_for_variants(db, pre_variants) > 0:
            db.commit()

    # Universal lock hierarchy: PaymentIntent -> Order -> InventoryReservation -> InventoryStock.
    locked_order, intents = lock_order_and_payment_intents_for_update(
        db,
        order_id=order_id,
    )

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

    append_order_event(
        db,
        order_id=locked_order.id,
        actor_user_id=actor_user_id,
        event_type="ORDER_CANCELLED",
        from_status="PENDING_PAYMENT",
        to_status="CANCELLED",
        metadata_json={"reason": cancel_reason},
        now=now,
    )
    db.commit()
    return _load_order_detail_dto(db, locked_order)
