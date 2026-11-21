"""Shopping cart ORM models: carts, cart_items."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

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
from app.models.types import CHAR3, CHAR64, TIMESTAMPTZ, UUIDType

if TYPE_CHECKING:
    from app.models.identity import User
    from app.models.inventory import ProductVariant


class Cart(Base):
    """Guest or authenticated user shopping cart."""

    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    guest_token_hash: Mapped[str | None] = mapped_column(CHAR64, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )
    currency: Mapped[str] = mapped_column(
        CHAR3, nullable=False, server_default=text("'USD'")
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User | None] = relationship(back_populates="carts")
    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("guest_token_hash", name="uq_carts_guest_token_hash"),
        CheckConstraint(
            "status IN ('ACTIVE','CONVERTED','ABANDONED')", name="status_valid"
        ),
        CheckConstraint(
            "user_id IS NOT NULL OR guest_token_hash IS NOT NULL",
            name="owner_present",
        ),
        Index(
            "uq_active_user_cart",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE' AND user_id IS NOT NULL"),
            sqlite_where=text("status = 'ACTIVE' AND user_id IS NOT NULL"),
        ),
        Index("idx_carts_status_expires", "status", expires_at.asc()),
    )


class CartItem(Base):
    """Cart line item storing variant reference and bounded quantity (never prices)."""

    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("carts.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    cart: Mapped[Cart] = relationship(back_populates="items")
    variant: Mapped[ProductVariant] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity <= 99", name="quantity_range"),
        Index("uq_cart_variant", "cart_id", "variant_id", unique=True),
    )
