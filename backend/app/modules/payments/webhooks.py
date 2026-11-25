"""Verified webhook ingestion, deduplication, retry handling, and order state application."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, NoReturn

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, ValidationAppError
from app.core.observability import payment_webhook_events_total
from app.models.catalog import Category, Product
from app.models.inventory import InventoryReservation, InventoryStock, ProductVariant
from app.models.order import Order
from app.models.payment import PaymentIntent, PaymentWebhookEvent
from app.modules.orders.transitions import (
    mark_order_paid,
    mark_order_payment_failed,
    mark_order_refunded,
)
from app.modules.payments.provider import get_default_payment_registry
from app.modules.payments.schemas import WebhookPayload, WebhookResponse


def _verify_and_parse_webhook(
    *,
    provider: str,
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    webhook_secret: str,
    now_ts: int | None = None,
) -> WebhookPayload:
    """Delegate webhook signature, timestamp, and schema verification to provider adapter."""
    adapter = get_default_payment_registry().get(provider)
    return adapter.verify_and_parse_webhook(
        raw_body=raw_body,
        timestamp_header=timestamp_header,
        signature_header=signature_header,
        webhook_secret=webhook_secret,
        now_ts=now_ts,
    )


def _claim_webhook_event_for_processing(
    db: Session,
    *,
    provider: str,
    payload: WebhookPayload,
    now: datetime,
) -> tuple[Literal["CLAIMED", "IGNORED_DUPLICATE"], uuid.UUID | None]:
    """Claim a webhook event for processing, allowing safe retries of FAILED events."""
    event_row_id = uuid.uuid4()
    payload_dict = payload.model_dump(mode="json")
    values: dict[str, Any] = {
        "id": event_row_id,
        "provider": provider,
        "provider_event_id": payload.event_id,
        "event_type": payload.event_type,
        "signature_verified": True,
        "payload_json": payload_dict,
        "status": "PROCESSING",
        "received_at": now,
    }
    dialect_name = db.get_bind().dialect.name
    inserted_id: uuid.UUID | None = None
    if dialect_name == "postgresql":
        stmt = (
            pg_insert(PaymentWebhookEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
            .returning(PaymentWebhookEvent.id)
        )
        inserted_id = db.scalar(stmt)
        db.flush()
    elif dialect_name == "sqlite":
        stmt_sqlite = (
            sqlite_insert(PaymentWebhookEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
            .returning(PaymentWebhookEvent.id)
        )
        inserted_id = db.scalar(stmt_sqlite)
        db.flush()
    else:
        existing_id = db.scalar(
            select(PaymentWebhookEvent.id).where(
                PaymentWebhookEvent.provider == provider,
                PaymentWebhookEvent.provider_event_id == payload.event_id,
            )
        )
        if existing_id is None:
            db.add(PaymentWebhookEvent(**values))
            db.flush()
            inserted_id = event_row_id

    if inserted_id is not None:
        return "CLAIMED", inserted_id

    existing = db.scalar(
        select(PaymentWebhookEvent)
        .where(
            PaymentWebhookEvent.provider == provider,
            PaymentWebhookEvent.provider_event_id == payload.event_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is None:
        db.rollback()
        return "IGNORED_DUPLICATE", None

    if existing.status == "PROCESSED":
        db.rollback()
        return "IGNORED_DUPLICATE", None

    if existing.status == "PROCESSING":
        db.rollback()
        raise ConflictError(
            code="WEBHOOK_IN_PROGRESS",
            message="Webhook event is currently being processed.",
        )

    # status == "FAILED": reclaim for retry in this transaction.
    existing.status = "PROCESSING"
    existing.error_message = None
    existing.processed_at = None
    existing.received_at = now
    existing.event_type = payload.event_type
    existing.signature_verified = True
    existing.payload_json = payload_dict
    db.flush()
    return "CLAIMED", existing.id


def _record_webhook_failure_and_raise(
    db: Session,
    *,
    webhook_event_id: uuid.UUID,
    error_message: str,
    now: datetime,
) -> NoReturn:
    event_row = db.scalar(
        select(PaymentWebhookEvent).where(PaymentWebhookEvent.id == webhook_event_id)
    )
    if event_row is not None:
        event_row.status = "FAILED"
        event_row.error_message = error_message[:500]
        event_row.processed_at = now
        db.commit()
    else:
        db.rollback()
    raise ValidationAppError(
        code="WEBHOOK_PAYLOAD_MISMATCH",
        message=error_message,
    )


def _persist_webhook_unexpected_failure(
    db: Session,
    *,
    webhook_event_id: uuid.UUID,
    provider: str,
    payload: WebhookPayload,
    error_message: str,
    now: datetime,
) -> None:
    """Best-effort persistence of FAILED webhook status after rollback of an unexpected error."""
    truncated = (error_message or "Unexpected webhook processing failure.")[:500]
    try:
        event_row = db.scalar(
            select(PaymentWebhookEvent)
            .where(PaymentWebhookEvent.id == webhook_event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if event_row is None:
            event_row = db.scalar(
                select(PaymentWebhookEvent)
                .where(
                    PaymentWebhookEvent.provider == provider,
                    PaymentWebhookEvent.provider_event_id == payload.event_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if event_row is not None:
            if event_row.status in ("PROCESSED", "PROCESSING"):
                db.rollback()
                return
            event_row.status = "FAILED"
            event_row.error_message = truncated
            event_row.processed_at = now
        else:
            db.add(
                PaymentWebhookEvent(
                    id=webhook_event_id,
                    provider=provider,
                    provider_event_id=payload.event_id,
                    event_type=payload.event_type,
                    signature_verified=True,
                    payload_json=payload.model_dump(mode="json"),
                    status="FAILED",
                    received_at=now,
                    processed_at=now,
                    error_message=truncated,
                )
            )
        db.commit()
    except Exception:
        db.rollback()


def _process_verified_webhook_tx(
    db: Session,
    *,
    provider: str,
    payload: WebhookPayload,
    prelocked_intent: PaymentIntent | None = None,
) -> tuple[WebhookResponse, Order | None]:
    """Execute steps 5 and 6 of webhook processing inside the active DB transaction."""
    now = datetime.now(UTC)
    claim_status, webhook_event_id = _claim_webhook_event_for_processing(
        db,
        provider=provider,
        payload=payload,
        now=now,
    )
    if claim_status == "IGNORED_DUPLICATE" or webhook_event_id is None:
        payment_webhook_events_total.labels(
            provider=provider,
            event_type=payload.event_type,
            outcome="ignored_duplicate",
        ).inc()
        return WebhookResponse(received=True, status="IGNORED_DUPLICATE"), None

    try:
        # Step 6: Universal lock hierarchy:
        # PaymentIntent (id ASC) -> Order -> InventoryReservation -> InventoryStock.
        sibling_intents: list[PaymentIntent] = []
        intent = prelocked_intent
        if intent is None:
            target_order_id = db.scalar(
                select(PaymentIntent.order_id).where(
                    PaymentIntent.provider == provider,
                    PaymentIntent.provider_intent_id == payload.data.provider_intent_id,
                )
            )
            if target_order_id is not None:
                sibling_intents = list(
                    db.scalars(
                        select(PaymentIntent)
                        .where(PaymentIntent.order_id == target_order_id)
                        .order_by(PaymentIntent.id.asc())
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    ).all()
                )
                for candidate_intent in sibling_intents:
                    if (
                        candidate_intent.provider == provider
                        and candidate_intent.provider_intent_id
                        == payload.data.provider_intent_id
                    ):
                        intent = candidate_intent
                        break
        else:
            other_siblings = list(
                db.scalars(
                    select(PaymentIntent)
                    .where(
                        PaymentIntent.order_id == intent.order_id,
                        PaymentIntent.id != intent.id,
                    )
                    .order_by(PaymentIntent.id.asc())
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).all()
            )
            sibling_intents = sorted(
                [intent, *other_siblings],
                key=lambda item: item.id,
            )

        if intent is None:
            _record_webhook_failure_and_raise(
                db,
                webhook_event_id=webhook_event_id,
                error_message=(
                    f"Payment intent '{payload.data.provider_intent_id}' "
                    f"not found for provider '{provider}'."
                ),
                now=now,
            )

        order = db.scalar(
            select(Order)
            .where(Order.id == intent.order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if order is None:
            _record_webhook_failure_and_raise(
                db,
                webhook_event_id=webhook_event_id,
                error_message="Order associated with payment intent was not found.",
                now=now,
            )

        if payload.data.order_number != order.order_number:
            _record_webhook_failure_and_raise(
                db,
                webhook_event_id=webhook_event_id,
                error_message="Webhook order_number does not match payment intent order.",
                now=now,
            )
        if (
            payload.data.amount_cents != order.total_cents
            or payload.data.amount_cents != intent.amount_cents
        ):
            _record_webhook_failure_and_raise(
                db,
                webhook_event_id=webhook_event_id,
                error_message="Webhook amount_cents does not match authoritative order total.",
                now=now,
            )
        if payload.data.currency != order.currency or payload.data.currency != intent.currency:
            _record_webhook_failure_and_raise(
                db,
                webhook_event_id=webhook_event_id,
                error_message="Webhook currency does not match authoritative order currency.",
                now=now,
            )

        reservations = list(
            db.scalars(
                select(InventoryReservation)
                .where(InventoryReservation.order_id == order.id)
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

        if payload.event_type == "payment_intent.succeeded":
            mark_order_paid(
                db,
                order=order,
                intent=intent,
                sibling_intents=sibling_intents,
                reservations=reservations,
                stocks_by_variant=stocks_by_variant,
                variants_by_id=variants_by_id,
                event_id=payload.event_id,
                now=now,
            )
        elif payload.event_type == "payment_intent.failed":
            mark_order_payment_failed(
                db,
                order=order,
                intent=intent,
                event_id=payload.event_id,
                failure_code=payload.data.failure_code,
                failure_message=payload.data.failure_message,
            )
        elif payload.event_type == "charge.refunded":
            mark_order_refunded(
                db,
                order=order,
                intent=intent,
                sibling_intents=sibling_intents,
                reservations=reservations,
                stocks_by_variant=stocks_by_variant,
                event_id=payload.event_id,
                now=now,
            )

        webhook_row = db.scalar(
            select(PaymentWebhookEvent).where(PaymentWebhookEvent.id == webhook_event_id)
        )
        if webhook_row is not None:
            webhook_row.status = "PROCESSED"
            webhook_row.processed_at = now

        db.commit()
        payment_webhook_events_total.labels(
            provider=provider,
            event_type=payload.event_type,
            outcome="processed",
        ).inc()
        return WebhookResponse(received=True, status="PROCESSED"), order
    except Exception as exc:
        payment_webhook_events_total.labels(
            provider=provider,
            event_type=payload.event_type,
            outcome="failed",
        ).inc()
        if isinstance(exc, ValidationAppError) and exc.code == "WEBHOOK_PAYLOAD_MISMATCH":
            raise
        db.rollback()
        _persist_webhook_unexpected_failure(
            db,
            webhook_event_id=webhook_event_id,
            provider=provider,
            payload=payload,
            error_message=str(exc) or exc.__class__.__name__,
            now=datetime.now(UTC),
        )
        raise


def process_webhook_bytes(
    db: Session,
    *,
    provider: str,
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    settings: Settings,
) -> WebhookResponse:
    """Verify raw webhook request and apply idempotent transactional state update."""
    payload = _verify_and_parse_webhook(
        provider=provider,
        raw_body=raw_body,
        timestamp_header=timestamp_header,
        signature_header=signature_header,
        webhook_secret=settings.payment_webhook_secret,
    )
    response, _order = _process_verified_webhook_tx(
        db,
        provider=provider,
        payload=payload,
    )
    return response
