"""Phase 1 commerce integrity: guest order key, REFUND_PENDING, refund requests, and indexes.

Revision ID: 0002_phase1_commerce_integrity
Revises: 0001_initial_schema
Create Date: 2026-11-21 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase1_commerce_integrity"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. orders: add guest_client_key_hash, update payment_status CHECK, add partial index
    op.add_column(
        "orders",
        sa.Column("guest_client_key_hash", sa.CHAR(length=64), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_orders_payment_status_valid"),
        "orders",
        type_="check",
    )
    op.create_check_constraint(
        "payment_status_valid",
        "orders",
        "payment_status IN ("
        "'REQUIRES_PAYMENT','PAID','FAILED','REFUND_PENDING','REFUNDED','CANCELLED'"
        ")",
    )
    op.create_index(
        "idx_orders_guest_pending_client",
        "orders",
        ["guest_client_key_hash"],
        unique=False,
        postgresql_where=sa.text(
            "user_id IS NULL AND status = 'PENDING_PAYMENT' AND guest_client_key_hash IS NOT NULL"
        ),
    )

    # 2. payment_intents: update status CHECK to include REFUND_PENDING
    op.drop_constraint(
        op.f("ck_payment_intents_status_valid"),
        "payment_intents",
        type_="check",
    )
    op.create_check_constraint(
        "status_valid",
        "payment_intents",
        "status IN ("
        "'REQUIRES_ACTION','PROCESSING','SUCCEEDED','FAILED',"
        "'CANCELLED','REFUND_PENDING','REFUNDED'"
        ")",
    )

    # 3. payment_refund_requests
    op.create_table(
        "payment_refund_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_intent_id", sa.String(length=128), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=128), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "restock_on_confirm",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_cents >= 0", name="amount_non_negative"),
        sa.CheckConstraint(
            "trigger_source IN ("
            "'ADMIN_REFUND','DUPLICATE_PAYMENT','LATE_PAYMENT_OUT_OF_STOCK',"
            "'LATE_PAYMENT_AFTER_CANCEL','STALE_INTENT_CAPTURE'"
            ")",
            name="trigger_source_valid",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','FAILED')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_payment_refund_requests_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_payment_refund_requests_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payment_intent_id"],
            ["payment_intents.id"],
            name="fk_payment_refund_requests_payment_intent_id_payment_intents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_refund_requests"),
        sa.UniqueConstraint(
            "payment_intent_id",
            name="uq_payment_refund_requests_payment_intent_id",
        ),
    )
    op.create_index(
        "idx_refund_requests_status_created",
        "payment_refund_requests",
        ["status", sa.text("created_at ASC")],
        unique=False,
    )

    # 4. inventory_reservations: add variant-scoped active expiry partial index
    op.create_index(
        "idx_reservations_variant_active_expiry",
        "inventory_reservations",
        ["variant_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_reservations_variant_active_expiry",
        table_name="inventory_reservations",
    )

    op.drop_index(
        "idx_refund_requests_status_created",
        table_name="payment_refund_requests",
    )
    op.drop_table("payment_refund_requests")

    op.drop_constraint(
        op.f("ck_payment_intents_status_valid"),
        "payment_intents",
        type_="check",
    )
    op.create_check_constraint(
        "status_valid",
        "payment_intents",
        "status IN ("
        "'REQUIRES_ACTION','PROCESSING','SUCCEEDED','FAILED','CANCELLED','REFUNDED'"
        ")",
    )

    op.drop_index("idx_orders_guest_pending_client", table_name="orders")
    op.drop_constraint(
        op.f("ck_orders_payment_status_valid"),
        "orders",
        type_="check",
    )
    op.create_check_constraint(
        "payment_status_valid",
        "orders",
        "payment_status IN ('REQUIRES_PAYMENT','PAID','FAILED','REFUNDED','CANCELLED')",
    )
    op.drop_column("orders", "guest_client_key_hash")
