"""Tests for ORM schema metadata parity (17 tables, checks, partial/unique indexes) and Alembic."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

import app.models  # noqa: F401 — populate Base.metadata with all 17 ORM models
from app.core.database import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "users",
        "auth_sessions",
        "audit_logs",
        "categories",
        "products",
        "product_images",
        "product_variants",
        "inventory_stocks",
        "inventory_reservations",
        "inventory_ledger_entries",
        "carts",
        "cart_items",
        "orders",
        "order_items",
        "order_events",
        "payment_intents",
        "payment_webhook_events",
    }
)

EXPECTED_CHECK_CONSTRAINTS: dict[str, set[str]] = {
    "users": {"ck_users_role_valid"},
    "auth_sessions": {"ck_auth_sessions_csrf_version_positive"},
    "categories": {"ck_categories_parent_not_self"},
    "products": {"ck_products_status_valid"},
    "product_variants": {
        "ck_product_variants_price_non_negative",
        "ck_product_variants_compare_price_valid",
    },
    "inventory_stocks": {
        "ck_inventory_stocks_on_hand_non_negative",
        "ck_inventory_stocks_reserved_non_negative",
        "ck_inventory_stocks_reserved_lte_on_hand",
        "ck_inventory_stocks_low_stock_threshold_non_negative",
    },
    "inventory_reservations": {
        "ck_inventory_reservations_quantity_positive",
        "ck_inventory_reservations_status_valid",
    },
    "inventory_ledger_entries": {
        "ck_inventory_ledger_entries_reason_valid",
        "ck_inventory_ledger_entries_on_hand_after_non_negative",
        "ck_inventory_ledger_entries_reserved_after_valid",
    },
    "carts": {
        "ck_carts_status_valid",
        "ck_carts_owner_present",
    },
    "cart_items": {"ck_cart_items_quantity_range"},
    "orders": {
        "ck_orders_guest_token_version_positive",
        "ck_orders_status_valid",
        "ck_orders_payment_status_valid",
        "ck_orders_fulfillment_status_valid",
        "ck_orders_subtotal_non_negative",
        "ck_orders_discount_valid",
        "ck_orders_shipping_non_negative",
        "ck_orders_tax_non_negative",
        "ck_orders_total_valid",
    },
    "order_items": {
        "ck_order_items_unit_price_non_negative",
        "ck_order_items_quantity_positive",
        "ck_order_items_line_total_valid",
    },
    "payment_intents": {
        "ck_payment_intents_amount_non_negative",
        "ck_payment_intents_status_valid",
    },
    "payment_webhook_events": {"ck_payment_webhook_events_status_valid"},
}

EXPECTED_UNIQUE_CONSTRAINTS: dict[str, set[str]] = {
    "auth_sessions": {"uq_auth_sessions_token_hash"},
    "categories": {"uq_categories_slug"},
    "products": {"uq_products_slug"},
    "product_variants": {"uq_product_variants_sku"},
    "carts": {"uq_carts_guest_token_hash"},
    "orders": {"uq_orders_order_number", "uq_orders_idempotency_key"},
    "payment_intents": {
        "uq_payment_intents_provider_intent_id",
        "uq_payment_intents_idempotency_key",
    },
}

EXPECTED_UNIQUE_INDEXES: dict[str, set[str]] = {
    "users": {"uq_users_email_lower"},
    "product_images": {"uq_product_primary_image"},
    "inventory_reservations": {"uq_reservation_order_variant"},
    "carts": {"uq_active_user_cart"},
    "cart_items": {"uq_cart_variant"},
    "payment_intents": {"uq_active_succeeded_intent_per_order"},
    "payment_webhook_events": {"uq_webhook_provider_event"},
}

EXPECTED_PARTIAL_INDEXES: dict[str, set[str]] = {
    "auth_sessions": {"idx_auth_sessions_user_active"},
    "product_images": {"uq_product_primary_image"},
    "inventory_reservations": {"idx_reservations_active_expiry"},
    "carts": {"uq_active_user_cart"},
    "orders": {"idx_orders_user_placed"},
    "payment_intents": {"uq_active_succeeded_intent_per_order"},
}


def _run_alembic(*args: str, database_url: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_orm_metadata_contains_all_17_tables_checks_and_indexes() -> None:
    """Base.metadata must expose all 17 tables, CHECK constraints, unique and partial indexes."""
    tables = Base.metadata.tables
    assert set(tables.keys()) == EXPECTED_TABLES

    for table_name, expected_checks in EXPECTED_CHECK_CONSTRAINTS.items():
        actual_checks = {
            c.name for c in tables[table_name].constraints if isinstance(c, CheckConstraint)
        }
        assert expected_checks <= actual_checks, (
            f"{table_name} missing CHECK constraints: {expected_checks - actual_checks}"
        )

    for table_name, expected_uqs in EXPECTED_UNIQUE_CONSTRAINTS.items():
        actual_uqs = {
            c.name for c in tables[table_name].constraints if isinstance(c, UniqueConstraint)
        }
        assert expected_uqs <= actual_uqs, (
            f"{table_name} missing UNIQUE constraints: {expected_uqs - actual_uqs}"
        )

    for table_name, expected_uq_idxs in EXPECTED_UNIQUE_INDEXES.items():
        actual_uq_idxs = {idx.name for idx in tables[table_name].indexes if idx.unique}
        assert expected_uq_idxs <= actual_uq_idxs, (
            f"{table_name} missing unique indexes: {expected_uq_idxs - actual_uq_idxs}"
        )

    for table_name, expected_partials in EXPECTED_PARTIAL_INDEXES.items():
        partial_idxs = {
            idx.name
            for idx in tables[table_name].indexes
            if idx.dialect_options.get("postgresql", {}).get("where") is not None
        }
        assert expected_partials <= partial_idxs, (
            f"{table_name} missing partial indexes: {expected_partials - partial_idxs}"
        )


def test_alembic_single_head_and_offline_upgrade_downgrade_sql() -> None:
    """Alembic has one head (0001_initial_schema) and compiles offline upgrade/downgrade SQL."""
    heads_res = _run_alembic("heads")
    assert heads_res.returncode == 0, heads_res.stderr
    head_lines = [line.strip() for line in heads_res.stdout.splitlines() if line.strip()]
    assert len(head_lines) == 1
    assert head_lines[0].startswith("0001_initial_schema")

    upgrade_res = _run_alembic("upgrade", "head", "--sql")
    assert upgrade_res.returncode == 0, upgrade_res.stderr
    for table_name in EXPECTED_TABLES:
        assert f"CREATE TABLE {table_name}" in upgrade_res.stdout
    for expected_checks in EXPECTED_CHECK_CONSTRAINTS.values():
        for ck_name in expected_checks:
            assert f"CONSTRAINT {ck_name} CHECK" in upgrade_res.stdout

    downgrade_res = _run_alembic("downgrade", "0001_initial_schema:base", "--sql")
    assert downgrade_res.returncode == 0, downgrade_res.stderr
    for table_name in EXPECTED_TABLES:
        assert f"DROP TABLE {table_name}" in downgrade_res.stdout


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Live PostgreSQL TEST_DATABASE_URL is not configured in this environment.",
)
def test_alembic_live_postgresql_upgrade_and_downgrade() -> None:
    """Apply and rollback 0001_initial_schema against a live PostgreSQL database when configured."""
    test_db_url = os.environ["TEST_DATABASE_URL"]
    up_res = _run_alembic("upgrade", "head", database_url=test_db_url)
    assert up_res.returncode == 0, up_res.stderr

    down_res = _run_alembic("downgrade", "base", database_url=test_db_url)
    assert down_res.returncode == 0, down_res.stderr
