"""Tests for ORM schema metadata parity (18 tables, checks, partial/unique indexes) and Alembic."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect

import app.models  # noqa: F401 — populate Base.metadata with all 18 ORM models
from app.core.database import Base, create_engine_from_url

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
        "payment_refund_requests",
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
    "payment_refund_requests": {
        "ck_payment_refund_requests_amount_non_negative",
        "ck_payment_refund_requests_trigger_source_valid",
        "ck_payment_refund_requests_status_valid",
    },
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
    "payment_refund_requests": {"uq_payment_refund_requests_payment_intent_id"},
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
    "inventory_reservations": {
        "idx_reservations_active_expiry",
        "idx_reservations_variant_active_expiry",
    },
    "carts": {"uq_active_user_cart"},
    "orders": {"idx_orders_user_placed", "idx_orders_guest_pending_client"},
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
    """Base.metadata must expose all 18 tables, CHECK constraints, unique and partial indexes."""
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
    """Alembic has one head (0003_phase2_indexes_and_cleanup) and compiles offline SQL."""
    heads_res = _run_alembic("heads")
    assert heads_res.returncode == 0, heads_res.stderr
    head_lines = [line.strip() for line in heads_res.stdout.splitlines() if line.strip()]
    assert len(head_lines) == 1
    assert head_lines[0].startswith("0003_phase2_indexes_and_cleanup")

    upgrade_res = _run_alembic("upgrade", "head", "--sql")
    assert upgrade_res.returncode == 0, upgrade_res.stderr
    for table_name in EXPECTED_TABLES:
        assert f"CREATE TABLE {table_name}" in upgrade_res.stdout
    for expected_checks in EXPECTED_CHECK_CONSTRAINTS.values():
        for ck_name in expected_checks:
            assert f"CONSTRAINT {ck_name} CHECK" in upgrade_res.stdout
    for idx_name in (
        "idx_product_images_product_sort",
        "idx_orders_placed_at",
        "idx_audit_logs_actor_created",
        "idx_audit_logs_action_created",
        "idx_auth_sessions_expires_at",
        "idx_carts_status_expires",
    ):
        assert idx_name in upgrade_res.stdout

    downgrade_res = _run_alembic("downgrade", "0003_phase2_indexes_and_cleanup:base", "--sql")
    assert downgrade_res.returncode == 0, downgrade_res.stderr
    for table_name in EXPECTED_TABLES:
        assert f"DROP TABLE {table_name}" in downgrade_res.stdout


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Live PostgreSQL TEST_DATABASE_URL is not configured in this environment.",
)
def test_alembic_live_postgresql_upgrade_and_downgrade() -> None:
    """Verify 0003 migration indexes, downgrade to 0002/0001, re-upgrade head, & downgrade base."""
    test_db_url = os.environ["TEST_DATABASE_URL"]
    pg_engine = create_engine_from_url(test_db_url)
    expected_0003_indexes = {
        "product_images": {"idx_product_images_product_sort"},
        "orders": {"idx_orders_placed_at"},
        "audit_logs": {"idx_audit_logs_actor_created", "idx_audit_logs_action_created"},
        "auth_sessions": {"idx_auth_sessions_expires_at"},
        "carts": {"idx_carts_status_expires"},
    }
    try:
        up_res = _run_alembic("upgrade", "head", database_url=test_db_url)
        assert up_res.returncode == 0, up_res.stderr

        insp_head = inspect(pg_engine)
        assert set(insp_head.get_table_names()) >= EXPECTED_TABLES
        assert "payment_refund_requests" in insp_head.get_table_names()
        order_cols_head = {col["name"] for col in insp_head.get_columns("orders")}
        assert "guest_client_key_hash" in order_cols_head
        order_indexes_head = {idx["name"] for idx in insp_head.get_indexes("orders")}
        assert "idx_orders_guest_pending_client" in order_indexes_head
        res_indexes_head = {
            idx["name"] for idx in insp_head.get_indexes("inventory_reservations")
        }
        assert "idx_reservations_variant_active_expiry" in res_indexes_head
        for table_name, req_idxs in expected_0003_indexes.items():
            actual = {idx["name"] for idx in insp_head.get_indexes(table_name)}
            assert req_idxs <= actual, f"{table_name} missing 0003 indexes: {req_idxs - actual}"

        # Downgrade 0003 -> 0002_phase1_commerce_integrity
        down_0002 = _run_alembic(
            "downgrade", "0002_phase1_commerce_integrity", database_url=test_db_url
        )
        assert down_0002.returncode == 0, down_0002.stderr
        insp_0002 = inspect(pg_engine)
        for table_name, req_idxs in expected_0003_indexes.items():
            actual_0002 = {idx["name"] for idx in insp_0002.get_indexes(table_name)}
            assert not (req_idxs & actual_0002), (
                f"{table_name} still has 0003 indexes after downgrade: {req_idxs & actual_0002}"
            )

        # Re-upgrade 0002 -> head (0003_phase2_indexes_and_cleanup)
        reup_0003 = _run_alembic("upgrade", "head", database_url=test_db_url)
        assert reup_0003.returncode == 0, reup_0003.stderr
        insp_reup3 = inspect(pg_engine)
        for table_name, req_idxs in expected_0003_indexes.items():
            actual_reup3 = {idx["name"] for idx in insp_reup3.get_indexes(table_name)}
            assert req_idxs <= actual_reup3

        # Downgrade 0003 -> 0001_initial_schema
        down_0001 = _run_alembic("downgrade", "0001_initial_schema", database_url=test_db_url)
        assert down_0001.returncode == 0, down_0001.stderr

        insp_0001 = inspect(pg_engine)
        assert "payment_refund_requests" not in insp_0001.get_table_names()
        order_cols_0001 = {col["name"] for col in insp_0001.get_columns("orders")}
        assert "guest_client_key_hash" not in order_cols_0001

        # Re-upgrade 0001 -> head (0003_phase2_indexes_and_cleanup)
        reup_res = _run_alembic("upgrade", "head", database_url=test_db_url)
        assert reup_res.returncode == 0, reup_res.stderr

        insp_reup = inspect(pg_engine)
        assert "payment_refund_requests" in insp_reup.get_table_names()
        order_cols_reup = {col["name"] for col in insp_reup.get_columns("orders")}
        assert "guest_client_key_hash" in order_cols_reup
        for table_name, req_idxs in expected_0003_indexes.items():
            assert req_idxs <= {idx["name"] for idx in insp_reup.get_indexes(table_name)}

        down_res = _run_alembic("downgrade", "base", database_url=test_db_url)
        assert down_res.returncode == 0, down_res.stderr
    finally:
        pg_engine.dispose()
