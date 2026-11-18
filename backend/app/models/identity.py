"""Identity and audit ORM models: users, auth_sessions, audit_logs."""

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
from app.models.types import CHAR64, TIMESTAMPTZ, INETType, JSONBType, UUIDType

if TYPE_CHECKING:
    from app.models.cart import Cart
    from app.models.order import Order


class User(Base):
    """Registered customer, staff, or admin user account."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'customer'")
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

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    carts: Mapped[list[Cart]] = relationship(back_populates="user")
    orders: Mapped[list[Order]] = relationship(back_populates="user")

    __table_args__ = (
        CheckConstraint("role IN ('customer','staff','admin')", name="role_valid"),
        Index("uq_users_email_lower", func.lower(email), unique=True),
    )


class AuthSession(Base):
    """Server-backed HTTP-only session record with deterministic CSRF version counter."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(CHAR64, nullable=False)
    csrf_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    ip_address: Mapped[str | None] = mapped_column(INETType, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        CheckConstraint("csrf_version >= 1", name="csrf_version_positive"),
        Index(
            "idx_auth_sessions_user_active",
            "user_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )


class AuditLog(Base):
    """Append-only audit trail for privileged admin and security mutations."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INETType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_audit_logs_created_at", created_at.desc()),
        Index("idx_audit_logs_resource", "resource_type", "resource_id"),
    )
