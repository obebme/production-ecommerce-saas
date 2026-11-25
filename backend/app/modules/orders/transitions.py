"""Transactional order payment state transitions under canonical row locks."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.order_events import append_order_event
from app.models.catalog import Category, Product
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order
from app.models.payment import PaymentIntent, PaymentRefundRequest


def _create_pending_refund_request(
    db: Session,
    *,
    order: Order,
    intent: PaymentIntent,
    trigger_source: str,
    reason: str | None,
    actor_user_id: uuid.UUID | None = None,
    restock_on_confirm: bool = False,
) -> PaymentRefundRequest:
    """Idempotently create a PENDING PaymentRefundRequest row for an automated/admin refund."""
    existing = db.scalar(
        select(PaymentRefundRequest)
        .where(PaymentRefundRequest.payment_intent_id == intent.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return existing

    refund_request = PaymentRefundRequest(
        order_id=order.id,
        payment_intent_id=intent.id,
        provider=intent.provider,
        provider_intent_id=intent.provider_intent_id,
        amount_cents=intent.amount_cents,
        currency=intent.currency,
        reason=reason[:255] if reason else None,
        trigger_source=trigger_source,
        status="PENDING",
        actor_user_id=actor_user_id,
        restock_on_confirm=restock_on_confirm,
    )
    db.add(refund_request)
    db.flush()
    return refund_request


def _cancel_other_open_intents(
    intent: PaymentIntent,
    sibling_intents: Sequence[PaymentIntent],
) -> None:
    for other in sibling_intents:
        if other.id != intent.id and other.status in (
            "REQUIRES_ACTION",
            "PROCESSING",
            "FAILED",
        ):
            other.status = "CANCELLED"


def mark_order_paid(
    db: Session,
    *,
    order: Order,
    intent: PaymentIntent,
    sibling_intents: Sequence[PaymentIntent] = (),
    reservations: Sequence[InventoryReservation],
    stocks_by_variant: dict[uuid.UUID, InventoryStock],
    variants_by_id: dict[uuid.UUID, tuple[ProductVariant, Product, Category]],
    event_id: str,
    now: datetime,
) -> None:
    """Apply payment_intent.succeeded state transition matrix under existing row locks."""
    if order.payment_status == "PAID":
        if intent.status not in ("SUCCEEDED", "REFUND_PENDING", "REFUNDED"):
            intent.status = "REFUND_PENDING"
            _create_pending_refund_request(
                db,
                order=order,
                intent=intent,
                trigger_source="DUPLICATE_PAYMENT",
                reason="Duplicate payment captured after order was already paid",
            )
            append_order_event(
                db,
                order_id=order.id,
                actor_user_id=None,
                event_type="DUPLICATE_PAYMENT_REFUND_PENDING",
                from_status=order.status,
                to_status=order.status,
                metadata_json={
                    "provider_intent_id": intent.provider_intent_id,
                    "event_id": event_id,
                },
                now=now,
            )
        return

    if order.status == "PENDING_PAYMENT":
        if intent.status == "CANCELLED":
            intent.status = "REFUND_PENDING"
            _create_pending_refund_request(
                db,
                order=order,
                intent=intent,
                trigger_source="STALE_INTENT_CAPTURE",
                reason="Captured payment intent had already been cancelled",
            )
            append_order_event(
                db,
                order_id=order.id,
                actor_user_id=None,
                event_type="STALE_INTENT_PAYMENT_REFUND_PENDING",
                from_status=order.status,
                to_status=order.status,
                metadata_json={
                    "provider_intent_id": intent.provider_intent_id,
                    "event_id": event_id,
                },
                now=now,
            )
            return

        intent.status = "SUCCEEDED"
        intent.failure_code = None
        intent.failure_message = None
        _cancel_other_open_intents(intent, sibling_intents)
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

        append_order_event(
            db,
            order_id=order.id,
            actor_user_id=None,
            event_type="PAYMENT_SUCCEEDED",
            from_status="PENDING_PAYMENT",
            to_status="CONFIRMED",
            metadata_json={
                "provider_intent_id": intent.provider_intent_id,
                "event_id": event_id,
            },
            now=now,
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
                _cancel_other_open_intents(intent, sibling_intents)
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

                append_order_event(
                    db,
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
                    now=now,
                )
                return

            intent.status = "REFUND_PENDING"
            order.payment_status = "REFUND_PENDING"
            order.fulfillment_status = "CANCELLED"
            _create_pending_refund_request(
                db,
                order=order,
                intent=intent,
                trigger_source="LATE_PAYMENT_OUT_OF_STOCK",
                reason="Late payment succeeded after reservation expired and stock was unavailable",
            )
            append_order_event(
                db,
                order_id=order.id,
                actor_user_id=None,
                event_type="PAYMENT_SUCCEEDED_OUT_OF_STOCK",
                from_status="CANCELLED",
                to_status="CANCELLED",
                metadata_json={
                    "provider_intent_id": intent.provider_intent_id,
                    "event_id": event_id,
                },
                now=now,
            )
            return

        # Explicit cancel (RELEASED reservations) -> pending refund, order remains CANCELLED.
        intent.status = "REFUND_PENDING"
        order.payment_status = "REFUND_PENDING"
        order.fulfillment_status = "CANCELLED"
        _create_pending_refund_request(
            db,
            order=order,
            intent=intent,
            trigger_source="LATE_PAYMENT_AFTER_CANCEL",
            reason="Late payment succeeded after order was cancelled",
        )
        append_order_event(
            db,
            order_id=order.id,
            actor_user_id=None,
            event_type="PAYMENT_SUCCEEDED_AFTER_CANCEL",
            from_status="CANCELLED",
            to_status="CANCELLED",
            metadata_json={
                "provider_intent_id": intent.provider_intent_id,
                "event_id": event_id,
            },
            now=now,
        )
        return

    if intent.status not in ("SUCCEEDED", "REFUND_PENDING", "REFUNDED"):
        intent.status = "REFUND_PENDING"
        _create_pending_refund_request(
            db,
            order=order,
            intent=intent,
            trigger_source="DUPLICATE_PAYMENT",
            reason="Payment captured on order in non-payable terminal state",
        )
        append_order_event(
            db,
            order_id=order.id,
            actor_user_id=None,
            event_type="DUPLICATE_PAYMENT_REFUND_PENDING",
            from_status=order.status,
            to_status=order.status,
            metadata_json={
                "provider_intent_id": intent.provider_intent_id,
                "event_id": event_id,
            },
            now=now,
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
    if (
        order.status != "PENDING_PAYMENT"
        or intent.status not in ("REQUIRES_ACTION", "PROCESSING")
    ):
        return

    intent.status = "FAILED"
    intent.failure_code = failure_code
    intent.failure_message = failure_message
    order.payment_status = "FAILED"

    append_order_event(
        db,
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


def mark_order_refunded(
    db: Session,
    *,
    order: Order,
    intent: PaymentIntent,
    sibling_intents: Sequence[PaymentIntent],
    reservations: Sequence[InventoryReservation],
    stocks_by_variant: dict[uuid.UUID, InventoryStock],
    event_id: str,
    now: datetime | None = None,
) -> None:
    """Apply charge.refunded webhook transition under existing row locks."""
    current_time = now if now is not None else datetime.now(UTC)
    refund_request = db.scalar(
        select(PaymentRefundRequest)
        .where(PaymentRefundRequest.payment_intent_id == intent.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if refund_request is not None and refund_request.status != "SUCCEEDED":
        refund_request.status = "SUCCEEDED"
        refund_request.error_message = None
        refund_request.completed_at = current_time

    if order.status == "REFUNDED" and order.payment_status == "REFUNDED":
        if intent.status != "REFUNDED":
            intent.status = "REFUNDED"
        return

    if order.status == "CANCELLED":
        intent.status = "REFUNDED"
        has_pending_or_paid_sibling = any(
            other.id != intent.id and other.status in ("SUCCEEDED", "REFUND_PENDING")
            for other in sibling_intents
        )
        if order.payment_status == "REFUND_PENDING" and not has_pending_or_paid_sibling:
            order.payment_status = "REFUNDED"
        append_order_event(
            db,
            order_id=order.id,
            actor_user_id=None,
            event_type=(
                "PAYMENT_INTENT_REFUNDED"
                if has_pending_or_paid_sibling
                else "PAYMENT_REFUNDED"
            ),
            from_status="CANCELLED",
            to_status="CANCELLED",
            metadata_json={
                "provider_intent_id": intent.provider_intent_id,
                "event_id": event_id,
            },
            now=current_time,
        )
        return

    has_other_succeeded_or_pending = any(
        other.id != intent.id and other.status in ("SUCCEEDED", "REFUND_PENDING")
        for other in sibling_intents
    )
    if (
        has_other_succeeded_or_pending
        or order.payment_status not in ("PAID", "REFUND_PENDING")
        or intent.status not in ("SUCCEEDED", "REFUND_PENDING")
    ):
        intent.status = "REFUNDED"
        append_order_event(
            db,
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
            now=current_time,
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

    append_order_event(
        db,
        order_id=order.id,
        actor_user_id=None,
        event_type="PAYMENT_REFUNDED",
        from_status=from_status,
        to_status="REFUNDED",
        metadata_json={
            "provider_intent_id": intent.provider_intent_id,
            "event_id": event_id,
        },
        now=current_time,
    )
