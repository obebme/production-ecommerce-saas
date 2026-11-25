"""Payment intent reconciliation retrieving provider status without holding DB locks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.catalog import Category, Product
from app.models.inventory import InventoryReservation, InventoryStock, ProductVariant
from app.models.order import Order
from app.models.payment import PaymentIntent
from app.modules.orders.transitions import (
    mark_order_paid,
    mark_order_payment_failed,
    mark_order_refunded,
)
from app.modules.payments.provider import (
    PaymentProviderRegistry,
    get_default_payment_registry,
)


def reconcile_payment_intent(
    db: Session,
    *,
    intent_id: uuid.UUID,
    registry: PaymentProviderRegistry | None = None,
) -> PaymentIntent:
    """Reconcile a delayed/out-of-order PaymentIntent against its registered provider adapter."""
    intent_snapshot = db.scalar(select(PaymentIntent).where(PaymentIntent.id == intent_id))
    if intent_snapshot is None:
        raise NotFoundError(
            code="PAYMENT_INTENT_NOT_FOUND",
            message="Payment intent not found.",
        )

    if intent_snapshot.status in ("SUCCEEDED", "CANCELLED", "REFUNDED"):
        return intent_snapshot

    provider_name = intent_snapshot.provider
    provider_intent_id = intent_snapshot.provider_intent_id
    order_id = intent_snapshot.order_id

    adapter = (registry or get_default_payment_registry()).get(provider_name)
    # Execute provider lookup outside any database row locks.
    provider_status = adapter.retrieve_intent(provider_intent_id=provider_intent_id)
    if provider_status.status == intent_snapshot.status:
        return intent_snapshot

    now = datetime.now(UTC)
    # Canonical lock hierarchy: PaymentIntent (id ASC) -> Order -> Reservation -> InventoryStock.
    sibling_intents = list(
        db.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.order_id == order_id)
            .order_by(PaymentIntent.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    locked_intent = next((i for i in sibling_intents if i.id == intent_id), None)
    if locked_intent is None:
        db.rollback()
        raise NotFoundError(
            code="PAYMENT_INTENT_NOT_FOUND",
            message="Payment intent not found.",
        )

    if (
        locked_intent.status in ("SUCCEEDED", "CANCELLED", "REFUNDED")
        or locked_intent.status == provider_status.status
    ):
        db.rollback()
        return locked_intent

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

    reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == locked_order.id)
            .order_by(InventoryReservation.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    variant_ids = sorted({res.variant_id for res in reservations})

    stocks_by_variant: dict[uuid.UUID, InventoryStock] = {}
    variants_by_id: dict[uuid.UUID, tuple[ProductVariant, Product, Category]] = {}
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
        variant_rows = db.execute(
            select(ProductVariant, Product, Category)
            .join(Product, Product.id == ProductVariant.product_id)
            .join(Category, Category.id == Product.category_id)
            .where(ProductVariant.id.in_(variant_ids))
            .order_by(ProductVariant.id.asc())
        ).all()
        variants_by_id = {
            variant.id: (variant, product, category)
            for variant, product, category in variant_rows
        }

    reconcile_event_id = (
        f"reconcile:{provider_name}:{provider_intent_id}:{provider_status.status.lower()}"
    )

    if provider_status.status == "SUCCEEDED":
        mark_order_paid(
            db,
            order=locked_order,
            intent=locked_intent,
            sibling_intents=sibling_intents,
            reservations=reservations,
            stocks_by_variant=stocks_by_variant,
            variants_by_id=variants_by_id,
            event_id=reconcile_event_id,
            now=now,
        )
    elif provider_status.status == "FAILED":
        mark_order_payment_failed(
            db,
            order=locked_order,
            intent=locked_intent,
            event_id=reconcile_event_id,
            failure_code=provider_status.failure_code,
            failure_message=provider_status.failure_message,
        )
    elif provider_status.status == "REFUNDED":
        mark_order_refunded(
            db,
            order=locked_order,
            intent=locked_intent,
            sibling_intents=sibling_intents,
            reservations=reservations,
            stocks_by_variant=stocks_by_variant,
            event_id=reconcile_event_id,
            now=now,
        )
    elif provider_status.status == "CANCELLED" and locked_intent.status in (
        "REQUIRES_ACTION",
        "PROCESSING",
        "FAILED",
    ):
        locked_intent.status = "CANCELLED"
    elif provider_status.status == "PROCESSING" and locked_intent.status == "REQUIRES_ACTION":
        locked_intent.status = "PROCESSING"

    db.commit()
    return locked_intent
