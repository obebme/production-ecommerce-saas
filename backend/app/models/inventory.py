"""Variant and inventory ORM models: product_variants, inventory_stocks, reservations, ledger."""

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
from app.models.types import CHAR3, TIMESTAMPTZ, JSONBType, UUIDType

if TYPE_CHECKING:
    from app.models.catalog import Product
    from app.models.order import Order


class ProductVariant(Base):
    """Purchasable SKU variant with authoritative integer minor-unit pricing."""

    __tablename__ = "product_variants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, server_default=text("'{}'")
    )
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    compare_at_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(
        CHAR3, nullable=False, server_default=text("'USD'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="variants")
    stock: Mapped[InventoryStock | None] = relationship(
        back_populates="variant", uselist=False, cascade="all, delete-orphan"
    )
    reservations: Mapped[list[InventoryReservation]] = relationship(back_populates="variant")
    ledger_entries: Mapped[list[InventoryLedgerEntry]] = relationship(
        back_populates="variant"
    )

    __table_args__ = (
        UniqueConstraint("sku", name="uq_product_variants_sku"),
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint(
            "compare_at_price_cents IS NULL OR compare_at_price_cents >= price_cents",
            name="compare_price_valid",
        ),
        Index("idx_variants_product_active", "product_id", "is_active"),
    )


class InventoryStock(Base):
    """1:1 stock level tracking with hard oversell prevention CHECK (reserved <= on_hand)."""

    __tablename__ = "inventory_stocks"

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    on_hand: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    low_stock_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    variant: Mapped[ProductVariant] = relationship(back_populates="stock")

    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="on_hand_non_negative"),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
        CheckConstraint("reserved <= on_hand", name="reserved_lte_on_hand"),
        CheckConstraint(
            "low_stock_threshold >= 0", name="low_stock_threshold_non_negative"
        ),
    )


class InventoryReservation(Base):
    """Order-scoped stock reservation held prior to payment confirmation or expiry."""

    __tablename__ = "inventory_reservations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="reservations")
    variant: Mapped[ProductVariant] = relationship(back_populates="reservations")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "status IN ('ACTIVE','COMMITTED','RELEASED','EXPIRED')", name="status_valid"
        ),
        Index("uq_reservation_order_variant", "order_id", "variant_id", unique=True),
        Index(
            "idx_reservations_active_expiry",
            "expires_at",
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "idx_reservations_variant_active_expiry",
            "variant_id",
            "expires_at",
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )


class InventoryLedgerEntry(Base):
    """Append-only stock movement audit entry."""

    __tablename__ = "inventory_ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    delta_on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    delta_reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    on_hand_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_after: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    variant: Mapped[ProductVariant] = relationship(back_populates="ledger_entries")

    __table_args__ = (
        CheckConstraint(
            "reason IN ("
            "'INITIAL_SEED','MANUAL_ADJUSTMENT','RESERVE','COMMIT','RELEASE','RETURN_RESTOCK'"
            ")",
            name="reason_valid",
        ),
        CheckConstraint("on_hand_after >= 0", name="on_hand_after_non_negative"),
        CheckConstraint(
            "reserved_after >= 0 AND reserved_after <= on_hand_after",
            name="reserved_after_valid",
        ),
        Index("idx_ledger_variant_created", "variant_id", created_at.desc()),
    )
