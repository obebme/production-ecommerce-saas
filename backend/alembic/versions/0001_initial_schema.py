"""Initial PostgreSQL 16 schema covering all 17 commerce, identity, and payment tables.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-18 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column(
            "role",
            sa.String(length=16),
            server_default=sa.text("'customer'"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "role IN ('customer','staff','admin')",
            name="role_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )

    # 2. auth_sessions
    op.create_table(
        "auth_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "csrf_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "csrf_version >= 1",
            name="csrf_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index(
        "idx_auth_sessions_user_active",
        "auth_sessions",
        ["user_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # 3. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=48), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "idx_audit_logs_created_at",
        "audit_logs",
        [sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_audit_logs_resource",
        "audit_logs",
        ["resource_type", "resource_id"],
        unique=False,
    )

    # 4. categories
    op.create_table(
        "categories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "parent_id <> id",
            name="parent_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["categories.id"],
            name="fk_categories_parent_id_categories",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("slug", name="uq_categories_slug"),
    )
    op.create_index(
        "idx_categories_active_sort",
        "categories",
        ["is_active", "sort_order"],
        unique=False,
    )

    # 5. products
    op.create_table(
        "products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=220), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "status IN ('DRAFT','ACTIVE','ARCHIVED')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name="fk_products_category_id_categories",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("slug", name="uq_products_slug"),
    )
    op.create_index(
        "idx_products_status_category",
        "products",
        ["status", "category_id", sa.text("created_at DESC")],
        unique=False,
    )

    # 6. product_images
    op.create_table(
        "product_images",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column(
            "alt_text",
            sa.String(length=200),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_images_product_id_products",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_images"),
    )
    op.create_index(
        "uq_product_primary_image",
        "product_images",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = true"),
    )

    # 7. product_variants
    op.create_table(
        "product_variants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column(
            "attributes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("compare_at_price_cents", sa.Integer(), nullable=True),
        sa.Column(
            "currency",
            sa.CHAR(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "price_cents >= 0",
            name="price_non_negative",
        ),
        sa.CheckConstraint(
            "compare_at_price_cents IS NULL OR compare_at_price_cents >= price_cents",
            name="compare_price_valid",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_variants_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_variants"),
        sa.UniqueConstraint("sku", name="uq_product_variants_sku"),
    )
    op.create_index(
        "idx_variants_product_active",
        "product_variants",
        ["product_id", "is_active"],
        unique=False,
    )

    # 8. inventory_stocks
    op.create_table(
        "inventory_stocks",
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "on_hand",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "reserved",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "low_stock_threshold",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "on_hand >= 0",
            name="on_hand_non_negative",
        ),
        sa.CheckConstraint(
            "reserved >= 0",
            name="reserved_non_negative",
        ),
        sa.CheckConstraint(
            "reserved <= on_hand",
            name="reserved_lte_on_hand",
        ),
        sa.CheckConstraint(
            "low_stock_threshold >= 0",
            name="low_stock_threshold_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name="fk_inventory_stocks_variant_id_product_variants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("variant_id", name="pk_inventory_stocks"),
    )

    # 9. carts
    op.create_table(
        "carts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guest_token_hash", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.CHAR(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('ACTIVE','CONVERTED','ABANDONED')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR guest_token_hash IS NOT NULL",
            name="owner_present",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_carts_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_carts"),
        sa.UniqueConstraint("guest_token_hash", name="uq_carts_guest_token_hash"),
    )
    op.create_index(
        "uq_active_user_cart",
        "carts",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE' AND user_id IS NOT NULL"),
    )

    # 10. cart_items
    op.create_table(
        "cart_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "quantity > 0 AND quantity <= 99",
            name="quantity_range",
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["carts.id"],
            name="fk_cart_items_cart_id_carts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name="fk_cart_items_variant_id_product_variants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cart_items"),
    )
    op.create_index(
        "uq_cart_variant",
        "cart_items",
        ["cart_id", "variant_id"],
        unique=True,
    )

    # 11. orders
    op.create_table(
        "orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_number", sa.String(length=32), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_email", sa.String(length=320), nullable=False),
        sa.Column(
            "guest_token_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("guest_access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payment_status", sa.String(length=24), nullable=False),
        sa.Column("fulfillment_status", sa.String(length=24), nullable=False),
        sa.Column(
            "currency",
            sa.CHAR(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False),
        sa.Column(
            "discount_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "shipping_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "tax_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.CHAR(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "shipping_address_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "billing_address_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "placed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "guest_token_version >= 1",
            name="guest_token_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'PENDING_PAYMENT','CONFIRMED','PROCESSING',"
            "'SHIPPED','COMPLETED','CANCELLED','REFUNDED'"
            ")",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "payment_status IN ('REQUIRES_PAYMENT','PAID','FAILED','REFUNDED','CANCELLED')",
            name="payment_status_valid",
        ),
        sa.CheckConstraint(
            "fulfillment_status IN ("
            "'UNFULFILLED','ALLOCATED','SHIPPED','DELIVERED','CANCELLED','RETURNED'"
            ")",
            name="fulfillment_status_valid",
        ),
        sa.CheckConstraint(
            "subtotal_cents >= 0",
            name="subtotal_non_negative",
        ),
        sa.CheckConstraint(
            "discount_cents >= 0 AND discount_cents <= subtotal_cents",
            name="discount_valid",
        ),
        sa.CheckConstraint(
            "shipping_cents >= 0",
            name="shipping_non_negative",
        ),
        sa.CheckConstraint(
            "tax_cents >= 0",
            name="tax_non_negative",
        ),
        sa.CheckConstraint(
            "total_cents >= 0 AND "
            "total_cents = subtotal_cents - discount_cents + shipping_cents + tax_cents",
            name="total_valid",
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["carts.id"],
            name="fk_orders_cart_id_carts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_orders_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("order_number", name="uq_orders_order_number"),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
    )
    op.create_index(
        "idx_orders_user_placed",
        "orders",
        ["user_id", sa.text("placed_at DESC")],
        unique=False,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "idx_orders_status_placed",
        "orders",
        ["status", sa.text("placed_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_orders_analytics_paid_at",
        "orders",
        ["paid_at", "status", "payment_status"],
        unique=False,
    )

    # 12. order_items
    op.create_table(
        "order_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=64), nullable=False),
        sa.Column("product_title_snapshot", sa.String(length=200), nullable=False),
        sa.Column("variant_title_snapshot", sa.String(length=160), nullable=False),
        sa.Column(
            "attributes_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "unit_price_cents >= 0",
            name="unit_price_non_negative",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="quantity_positive",
        ),
        sa.CheckConstraint(
            "line_total_cents = unit_price_cents * quantity",
            name="line_total_valid",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_items_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_order_items_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name="fk_order_items_variant_id_product_variants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
    )
    op.create_index(
        "idx_order_items_order",
        "order_items",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "idx_order_items_variant_order",
        "order_items",
        ["variant_id", "order_id"],
        unique=False,
    )

    # 13. order_events
    op.create_table(
        "order_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_order_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_events_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_order_events"),
    )
    op.create_index(
        "idx_order_events_order_created",
        "order_events",
        ["order_id", sa.text("created_at ASC")],
        unique=False,
    )

    # 14. inventory_reservations
    op.create_table(
        "inventory_reservations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint(
            "quantity > 0",
            name="quantity_positive",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','COMMITTED','RELEASED','EXPIRED')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_inventory_reservations_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name="fk_inventory_reservations_variant_id_product_variants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_reservations"),
    )
    op.create_index(
        "uq_reservation_order_variant",
        "inventory_reservations",
        ["order_id", "variant_id"],
        unique=True,
    )
    op.create_index(
        "idx_reservations_active_expiry",
        "inventory_reservations",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # 15. inventory_ledger_entries
    op.create_table(
        "inventory_ledger_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(length=24), nullable=False),
        sa.Column("delta_on_hand", sa.Integer(), nullable=False),
        sa.Column("delta_reserved", sa.Integer(), nullable=False),
        sa.Column("on_hand_after", sa.Integer(), nullable=False),
        sa.Column("reserved_after", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason IN ("
            "'INITIAL_SEED','MANUAL_ADJUSTMENT','RESERVE','COMMIT','RELEASE','RETURN_RESTOCK'"
            ")",
            name="reason_valid",
        ),
        sa.CheckConstraint(
            "on_hand_after >= 0",
            name="on_hand_after_non_negative",
        ),
        sa.CheckConstraint(
            "reserved_after >= 0 AND reserved_after <= on_hand_after",
            name="reserved_after_valid",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_inventory_ledger_entries_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_inventory_ledger_entries_order_id_orders",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name="fk_inventory_ledger_entries_variant_id_product_variants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_ledger_entries"),
    )
    op.create_index(
        "idx_ledger_variant_created",
        "inventory_ledger_entries",
        ["variant_id", sa.text("created_at DESC")],
        unique=False,
    )

    # 16. payment_intents
    op.create_table(
        "payment_intents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default=sa.text("'sandbox_simpay'"),
            nullable=False,
        ),
        sa.Column("provider_intent_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.CHAR(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.CHAR(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint(
            "amount_cents >= 0",
            name="amount_non_negative",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'REQUIRES_ACTION','PROCESSING','SUCCEEDED','FAILED','CANCELLED','REFUNDED'"
            ")",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_payment_intents_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_intents"),
        sa.UniqueConstraint(
            "provider_intent_id",
            name="uq_payment_intents_provider_intent_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_payment_intents_idempotency_key",
        ),
    )
    op.create_index(
        "idx_payment_intents_order",
        "payment_intents",
        ["order_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_active_succeeded_intent_per_order",
        "payment_intents",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'SUCCEEDED'"),
    )

    # 17. payment_webhook_events
    op.create_table(
        "payment_webhook_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "status IN ('PROCESSING','PROCESSED','FAILED')",
            name="status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_webhook_events"),
    )
    op.create_index(
        "uq_webhook_provider_event",
        "payment_webhook_events",
        ["provider", "provider_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_webhook_provider_event", table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")

    op.drop_index("uq_active_succeeded_intent_per_order", table_name="payment_intents")
    op.drop_index("idx_payment_intents_order", table_name="payment_intents")
    op.drop_table("payment_intents")

    op.drop_index("idx_ledger_variant_created", table_name="inventory_ledger_entries")
    op.drop_table("inventory_ledger_entries")

    op.drop_index("idx_reservations_active_expiry", table_name="inventory_reservations")
    op.drop_index("uq_reservation_order_variant", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")

    op.drop_index("idx_order_events_order_created", table_name="order_events")
    op.drop_table("order_events")

    op.drop_index("idx_order_items_variant_order", table_name="order_items")
    op.drop_index("idx_order_items_order", table_name="order_items")
    op.drop_table("order_items")

    op.drop_index("idx_orders_analytics_paid_at", table_name="orders")
    op.drop_index("idx_orders_status_placed", table_name="orders")
    op.drop_index("idx_orders_user_placed", table_name="orders")
    op.drop_table("orders")

    op.drop_index("uq_cart_variant", table_name="cart_items")
    op.drop_table("cart_items")

    op.drop_index("uq_active_user_cart", table_name="carts")
    op.drop_table("carts")

    op.drop_table("inventory_stocks")

    op.drop_index("idx_variants_product_active", table_name="product_variants")
    op.drop_table("product_variants")

    op.drop_index("uq_product_primary_image", table_name="product_images")
    op.drop_table("product_images")

    op.drop_index("idx_products_status_category", table_name="products")
    op.drop_table("products")

    op.drop_index("idx_categories_active_sort", table_name="categories")
    op.drop_table("categories")

    op.drop_index("idx_audit_logs_resource", table_name="audit_logs")
    op.drop_index("idx_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("idx_auth_sessions_user_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
