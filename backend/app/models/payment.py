"""Payments, refunds, and webhook deduplication ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import CHAR3, CHAR64, TIMESTAMPTZ, JSONBType, UUIDType

if TYPE_CHECKING:
    from app.models.order import Order


class PaymentIntent(Base):
    """Sandbox payment intent tied to an order with idempotency and single-capture guarantee."""

    __tablename__ = "payment_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'sandbox_simpay'")
    )
    provider_intent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(CHAR64, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(CHAR64, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR3, nullable=False, server_default=text("'USD'")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="payment_intents")
    refund_request: Mapped[PaymentRefundRequest | None] = relationship(
        back_populates="payment_intent", uselist=False
    )

    __table_args__ = (
        UniqueConstraint(
            "provider_intent_id", name="uq_payment_intents_provider_intent_id"
        ),
        UniqueConstraint("idempotency_key", name="uq_payment_intents_idempotency_key"),
        CheckConstraint("amount_cents >= 0", name="amount_non_negative"),
        CheckConstraint(
            "status IN ("
            "'REQUIRES_ACTION','PROCESSING','SUCCEEDED','FAILED',"
            "'CANCELLED','REFUND_PENDING','REFUNDED'"
            ")",
            name="status_valid",
        ),
        Index("idx_payment_intents_order", "order_id", created_at.desc()),
        Index(
            "uq_active_succeeded_intent_per_order",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'SUCCEEDED'"),
            sqlite_where=text("status = 'SUCCEEDED'"),
        ),
    )


class PaymentWebhookEvent(Base):
    """Idempotent webhook delivery log keyed by (provider, provider_event_id)."""

    __tablename__ = "payment_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PROCESSING','PROCESSED','FAILED')", name="status_valid"
        ),
        Index(
            "uq_webhook_provider_event",
            "provider",
            "provider_event_id",
            unique=True,
        ),
    )


class PaymentRefundRequest(Base):
    """Durable refund intent record tracking pending/completed/failed provider refunds."""

    __tablename__ = "payment_refund_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("payment_intents.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_intent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR3, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    restock_on_confirm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)

    order: Mapped[Order] = relationship(back_populates="refund_requests")
    payment_intent: Mapped[PaymentIntent] = relationship(back_populates="refund_request")

    __table_args__ = (
        UniqueConstraint(
            "payment_intent_id",
            name="uq_payment_refund_requests_payment_intent_id",
        ),
        CheckConstraint("amount_cents >= 0", name="amount_non_negative"),
        CheckConstraint(
            "trigger_source IN ("
            "'ADMIN_REFUND','DUPLICATE_PAYMENT','LATE_PAYMENT_OUT_OF_STOCK',"
            "'LATE_PAYMENT_AFTER_CANCEL','STALE_INTENT_CAPTURE'"
            ")",
            name="trigger_source_valid",
        ),
        CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','FAILED')",
            name="status_valid",
        ),
        Index("idx_refund_requests_status_created", "status", created_at.asc()),
    )
