"""Order ORM models: orders, order_items, order_events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
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
    from app.models.identity import User
    from app.models.inventory import InventoryReservation
    from app.models.payment import PaymentIntent


class Order(Base):
    """Persistent customer or guest order with server-computed totals and idempotency key."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cart_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("carts.id", ondelete="SET NULL"), nullable=True
    )
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    guest_token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    guest_access_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ, nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(24), nullable=False)
    fulfillment_status: Mapped[str] = mapped_column(String(24), nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR3, nullable=False, server_default=text("'USD'")
    )
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    shipping_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tax_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(CHAR64, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(CHAR64, nullable=False)
    shipping_address_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    billing_address_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User | None] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    events: Mapped[list[OrderEvent]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.created_at",
    )
    reservations: Mapped[list[InventoryReservation]] = relationship(back_populates="order")
    payment_intents: Mapped[list[PaymentIntent]] = relationship(back_populates="order")

    __table_args__ = (
        UniqueConstraint("order_number", name="uq_orders_order_number"),
        UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        CheckConstraint("guest_token_version >= 1", name="guest_token_version_positive"),
        CheckConstraint(
            "status IN ("
            "'PENDING_PAYMENT','CONFIRMED','PROCESSING',"
            "'SHIPPED','COMPLETED','CANCELLED','REFUNDED'"
            ")",
            name="status_valid",
        ),
        CheckConstraint(
            "payment_status IN ('REQUIRES_PAYMENT','PAID','FAILED','REFUNDED','CANCELLED')",
            name="payment_status_valid",
        ),
        CheckConstraint(
            "fulfillment_status IN ("
            "'UNFULFILLED','ALLOCATED','SHIPPED','DELIVERED','CANCELLED','RETURNED'"
            ")",
            name="fulfillment_status_valid",
        ),
        CheckConstraint("subtotal_cents >= 0", name="subtotal_non_negative"),
        CheckConstraint(
            "discount_cents >= 0 AND discount_cents <= subtotal_cents",
            name="discount_valid",
        ),
        CheckConstraint("shipping_cents >= 0", name="shipping_non_negative"),
        CheckConstraint("tax_cents >= 0", name="tax_non_negative"),
        CheckConstraint(
            "total_cents >= 0 AND "
            "total_cents = subtotal_cents - discount_cents + shipping_cents + tax_cents",
            name="total_valid",
        ),
        Index(
            "idx_orders_user_placed",
            "user_id",
            placed_at.desc(),
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index("idx_orders_status_placed", "status", placed_at.desc()),
        Index("idx_orders_analytics_paid_at", "paid_at", "status", "payment_status"),
    )


class OrderItem(Base):
    """Immutable checkout line-item snapshot."""

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False
    )
    sku_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    product_title_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    variant_title_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    attributes_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False
    )
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("unit_price_cents >= 0", name="unit_price_non_negative"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "line_total_cents = unit_price_cents * quantity", name="line_total_valid"
        ),
        Index("idx_order_items_order", "order_id"),
        Index("idx_order_items_variant_order", "variant_id", "order_id"),
    )


class OrderEvent(Base):
    """Append-only order lifecycle transition log."""

    __tablename__ = "order_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="events")

    __table_args__ = (
        Index("idx_order_events_order_created", "order_id", created_at.asc()),
    )
