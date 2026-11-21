"""Phase 2 operational indexes for catalog sorting, orders, audit logs, sessions, and carts.

Revision ID: 0003_phase2_indexes_and_cleanup
Revises: 0002_phase1_commerce_integrity
Create Date: 2026-11-21 13:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase2_indexes_and_cleanup"
down_revision: str | Sequence[str] | None = "0002_phase1_commerce_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_product_images_product_sort",
        "product_images",
        ["product_id", sa.text("sort_order ASC")],
        unique=False,
    )
    op.create_index(
        "idx_orders_placed_at",
        "orders",
        [sa.text("placed_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_audit_logs_actor_created",
        "audit_logs",
        ["actor_user_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_audit_logs_action_created",
        "audit_logs",
        ["action", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_auth_sessions_expires_at",
        "auth_sessions",
        [sa.text("expires_at ASC")],
        unique=False,
    )
    op.create_index(
        "idx_carts_status_expires",
        "carts",
        ["status", sa.text("expires_at ASC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_carts_status_expires", table_name="carts")
    op.drop_index("idx_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("idx_audit_logs_action_created", table_name="audit_logs")
    op.drop_index("idx_audit_logs_actor_created", table_name="audit_logs")
    op.drop_index("idx_orders_placed_at", table_name="orders")
    op.drop_index("idx_product_images_product_sort", table_name="product_images")
