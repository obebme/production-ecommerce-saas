"""Tests for Module 3 variants, public availability, reservation expiry, and inventory."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from tests.conftest import (
    DEFAULT_ORIGIN,
    DEFAULT_TEST_PASSWORD,
    TEST_CSRF_SECRET,
    TEST_GUEST_ORDER_SECRET,
    TEST_WEBHOOK_SECRET,
    AuthenticatedSession,
)

from app.core.audit import REDACTED_VALUE, record_audit_log, sanitize_audit_payload
from app.core.config import Settings
from app.core.database import clear_db_caches, create_engine_from_url, get_engine
from app.core.errors import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.core.security import hash_password
from app.main import create_app
from app.models.catalog import Category, Product, ProductImage
from app.models.identity import AuditLog, User
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import PaymentIntent
from app.modules.inventory.service import reclaim_expired_reservations_for_variants

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _csrf_headers(session: AuthenticatedSession) -> dict[str, str]:
    return {"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": session.csrf_token}


def _seed_catalog_for_inventory(
    db_session: Session,
    *,
    category_active: bool = True,
    product_status: str = "ACTIVE",
    sku: str = "VAR-001",
    product_title: str = "Canvas Jacket",
    variant_title: str = "Olive / M",
    on_hand: int = 10,
    reserved: int = 0,
    low_stock_threshold: int = 5,
    is_active: bool = True,
) -> tuple[Category, Product, ProductVariant, InventoryStock]:
    suffix = uuid.uuid4().hex[:8]
    category = Category(
        name=f"Cat-{suffix}",
        slug=f"cat-{suffix}",
        is_active=category_active,
        sort_order=1,
    )
    db_session.add(category)
    db_session.flush()

    product = Product(
        category_id=category.id,
        title=product_title,
        slug=f"prod-{suffix}",
        description="Test product",
        status=product_status,
    )
    db_session.add(product)
    db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        title=variant_title,
        attributes_json={"color": "Olive", "size": "M"},
        price_cents=6500,
        compare_at_price_cents=8000,
        currency="USD",
        is_active=is_active,
    )
    db_session.add(variant)
    db_session.flush()

    stock = InventoryStock(
        variant_id=variant.id,
        on_hand=on_hand,
        reserved=reserved,
        low_stock_threshold=low_stock_threshold,
        version=1,
    )
    db_session.add(stock)
    db_session.commit()
    db_session.refresh(category)
    db_session.refresh(product)
    db_session.refresh(variant)
    db_session.refresh(stock)
    return category, product, variant, stock


def _seed_order_with_reservations(
    db_session: Session,
    *,
    order_number: str,
    status: str = "PENDING_PAYMENT",
    payment_status: str = "REQUIRES_PAYMENT",
    fulfillment_status: str = "UNFULFILLED",
    items: list[tuple[Product, ProductVariant, int, datetime]],
    create_payment_intent: bool = True,
) -> tuple[Order, list[InventoryReservation], PaymentIntent | None]:
    subtotal = sum(variant.price_cents * qty for _, variant, qty, _ in items)
    order = Order(
        order_number=order_number,
        customer_email="buyer@example.com",
        status=status,
        payment_status=payment_status,
        fulfillment_status=fulfillment_status,
        currency="USD",
        subtotal_cents=subtotal,
        discount_cents=0,
        shipping_cents=0,
        tax_cents=0,
        total_cents=subtotal,
        idempotency_key=uuid.uuid4().hex.ljust(64, "0")[:64],
        request_fingerprint=uuid.uuid4().hex.ljust(64, "1")[:64],
        shipping_address_json={"full_name": "Buyer", "city": "Austin", "country": "US"},
        billing_address_json={"full_name": "Buyer", "city": "Austin", "country": "US"},
    )
    db_session.add(order)
    db_session.flush()

    reservations: list[InventoryReservation] = []
    for product, variant, qty, expires_at in items:
        db_session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                variant_id=variant.id,
                sku_snapshot=variant.sku,
                product_title_snapshot=product.title,
                variant_title_snapshot=variant.title,
                attributes_snapshot_json=dict(variant.attributes_json),
                unit_price_cents=variant.price_cents,
                quantity=qty,
                line_total_cents=variant.price_cents * qty,
            )
        )
        res = InventoryReservation(
            order_id=order.id,
            variant_id=variant.id,
            quantity=qty,
            status="ACTIVE",
            expires_at=expires_at,
        )
        db_session.add(res)
        reservations.append(res)

    intent: PaymentIntent | None = None
    if create_payment_intent:
        intent = PaymentIntent(
            order_id=order.id,
            provider="sandbox_simpay",
            provider_intent_id=f"pi_sandbox_{uuid.uuid4().hex[:16]}",
            idempotency_key=uuid.uuid4().hex.ljust(64, "2")[:64],
            request_fingerprint=uuid.uuid4().hex.ljust(64, "3")[:64],
            amount_cents=subtotal,
            currency="USD",
            status="REQUIRES_ACTION",
        )
        db_session.add(intent)

    db_session.commit()
    return order, reservations, intent


def test_admin_variant_create_rbac_csrf_atomic_seed_and_validation(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /admin/products/{id}/variants enforces RBAC/CSRF and seeds stock/ledger/audit."""
    cat = Category(name="Outerwear", slug="outerwear", is_active=True)
    db_session.add(cat)
    db_session.flush()
    prod = Product(category_id=cat.id, title="Parka", slug="parka", status="DRAFT")
    db_session.add(prod)
    db_session.commit()

    payload = {
        "sku": "PRK-BLK-M",
        "title": "Black / Medium",
        "attributes": {"color": "Black", "size": "M"},
        "price_cents": 14900,
        "compare_at_price_cents": 17900,
        "currency": "usd",
        "is_active": True,
        "initial_on_hand": 25,
        "low_stock_threshold": 4,
    }

    # Anonymous -> 401; customer -> 403
    unauth = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json=payload,
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert unauth.status_code == 401

    cust_session = login_as(client, role="customer")
    cust_resp = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json=payload,
        headers=_csrf_headers(cust_session),
    )
    assert cust_resp.status_code == 403
    assert cust_resp.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # Staff without CSRF or bad Origin -> 403
    staff_session = login_as(client, role="staff")
    no_csrf = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json=payload,
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_INVALID"

    bad_origin = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json=payload,
        headers={"Origin": "https://evil.example.com", "X-CSRF-Token": staff_session.csrf_token},
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["error"]["code"] == "CSRF_INVALID"

    # Missing product -> 404 PRODUCT_NOT_FOUND
    missing_prod = client.post(
        f"/api/v1/admin/products/{uuid.uuid4()}/variants",
        json=payload,
        headers=_csrf_headers(staff_session),
    )
    assert missing_prod.status_code == 404
    assert missing_prod.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    # Valid creation -> 201 Created
    create_resp = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json=payload,
        headers=_csrf_headers(staff_session),
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    variant_id = uuid.UUID(created["id"])
    assert created["sku"] == "PRK-BLK-M"
    assert created["currency"] == "USD"
    assert created["on_hand"] == 25
    assert created["reserved"] == 0
    assert created["available_quantity"] == 25
    assert created["low_stock_threshold"] == 4
    assert created["in_stock"] is True
    assert created["low_stock"] is False

    # Verify atomic InventoryStock, INITIAL_SEED InventoryLedgerEntry, and AuditLog
    db_session.expire_all()
    stock = db_session.get(InventoryStock, variant_id)
    assert stock is not None
    assert stock.on_hand == 25 and stock.reserved == 0 and stock.version == 1

    ledger = db_session.scalar(
        select(InventoryLedgerEntry).where(InventoryLedgerEntry.variant_id == variant_id)
    )
    assert ledger is not None
    assert ledger.reason == "INITIAL_SEED"
    assert ledger.delta_on_hand == 25
    assert ledger.delta_reserved == 0
    assert ledger.on_hand_after == 25
    assert ledger.reserved_after == 0
    assert ledger.actor_user_id == uuid.UUID(staff_session.user_id)

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_type == "product_variant",
            AuditLog.resource_id == str(variant_id),
            AuditLog.action == "VARIANT_CREATED",
        )
    )
    assert audit is not None
    assert audit.before_state is None
    assert audit.after_state is not None
    assert audit.after_state["sku"] == "PRK-BLK-M"
    assert audit.after_state["on_hand"] == 25

    # Boundary: initial_on_hand=0 creates valid zero-stock ledger & stock row
    zero_resp = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json={
            "sku": "PRK-BLK-S",
            "title": "Black / Small",
            "price_cents": 14900,
            "initial_on_hand": 0,
        },
        headers=_csrf_headers(staff_session),
    )
    assert zero_resp.status_code == 201
    assert zero_resp.json()["available_quantity"] == 0
    assert zero_resp.json()["in_stock"] is False

    # Duplicate SKU -> 409 SKU_CONFLICT
    dup_resp = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json={**payload, "title": "Dup SKU"},
        headers=_csrf_headers(staff_session),
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "SKU_CONFLICT"

    # Validation failures -> 422 VALIDATION_ERROR
    for bad_payload in (
        {**payload, "sku": "PRK-NEG", "price_cents": -1},
        {**payload, "sku": "PRK-CMP", "price_cents": 5000, "compare_at_price_cents": 4999},
        {**payload, "sku": "PRK-CUR", "currency": "US1"},
        {
            **payload,
            "sku": "PRK-DEEP",
            "attributes": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}},
        },
    ):
        bad_resp = client.post(
            f"/api/v1/admin/products/{prod.id}/variants",
            json=bad_payload,
            headers=_csrf_headers(staff_session),
        )
        assert bad_resp.status_code == 422
        assert bad_resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "bad_attributes",
    [
        pytest.param({"desc": "x" * 1001}, id="string-over-1000-chars"),
        pytest.param({"note": "bad\x00value"}, id="string-control-char-nul"),
        pytest.param({"color": "red", " color ": "blue"}, id="stripped-duplicate-keys"),
        pytest.param(
            {f"k{i}": list(range(45)) for i in range(6)},
            id="over-250-total-nodes",
        ),
        pytest.param(
            {f"k{i:02d}": "a" * 900 for i in range(20)},
            id="over-16384-utf8-bytes",
        ),
    ],
)
def test_admin_variant_attributes_strict_bounds(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
    bad_attributes: dict[str, Any],
) -> None:
    """POST /admin/products/{id}/variants enforces strict bounds on variant attributes JSON."""
    cat = Category(name="Attr Bounds", slug="attr-bounds", is_active=True)
    db_session.add(cat)
    db_session.flush()
    prod = Product(category_id=cat.id, title="Bounded Tee", slug="bounded-tee", status="DRAFT")
    db_session.add(prod)
    db_session.commit()

    staff_session = login_as(client, role="staff")
    resp = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json={
            "sku": f"BND-{uuid.uuid4().hex[:6]}",
            "title": "Bounded Variant",
            "attributes": bad_attributes,
            "price_cents": 2500,
        },
        headers=_csrf_headers(staff_session),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_variant_update_guards_active_product_last_variant_and_validations(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """PATCH /admin/variants/{id} guards last active variant on ACTIVE products and validates."""
    _, active_prod, v1, _ = _seed_catalog_for_inventory(
        db_session,
        product_status="ACTIVE",
        sku="ACT-ONE",
    )
    v2 = ProductVariant(
        product_id=active_prod.id,
        sku="ACT-TWO",
        title="Second Variant",
        attributes_json={},
        price_cents=7500,
        currency="USD",
        is_active=True,
    )
    db_session.add(v2)
    db_session.flush()
    db_session.add(InventoryStock(variant_id=v2.id, on_hand=5, reserved=0, version=1))
    _, draft_prod, v_draft, _ = _seed_catalog_for_inventory(
        db_session,
        product_status="DRAFT",
        sku="DFT-ONE",
    )
    db_session.commit()

    # Customer -> 403; staff without CSRF -> 403
    cust_session = login_as(client, role="customer")
    assert (
        client.patch(
            f"/api/v1/admin/variants/{v1.id}",
            json={"title": "Nope"},
            headers=_csrf_headers(cust_session),
        ).status_code
        == 403
    )

    staff_session = login_as(client, role="staff")
    assert (
        client.patch(
            f"/api/v1/admin/variants/{v1.id}",
            json={"title": "No CSRF"},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/admin/variants/{uuid.uuid4()}",
            json={"title": "Missing"},
            headers=_csrf_headers(staff_session),
        ).status_code
        == 404
    )

    # Deactivating one variant when another active variant remains -> 200 OK
    deact_v2 = client.patch(
        f"/api/v1/admin/variants/{v2.id}",
        json={"is_active": False},
        headers=_csrf_headers(staff_session),
    )
    assert deact_v2.status_code == 200
    assert deact_v2.json()["is_active"] is False

    # Deactivating the LAST active variant on ACTIVE product -> 422 guard
    deact_last = client.patch(
        f"/api/v1/admin/variants/{v1.id}",
        json={"is_active": False},
        headers=_csrf_headers(staff_session),
    )
    assert deact_last.status_code == 422
    assert deact_last.json()["error"]["code"] == "PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT"
    db_session.expire_all()
    assert db_session.get(ProductVariant, v1.id) is not None
    assert db_session.get(ProductVariant, v1.id).is_active is True  # type: ignore[union-attr]

    # Deactivating the sole active variant on a DRAFT product IS permitted -> 200 OK
    deact_draft = client.patch(
        f"/api/v1/admin/variants/{v_draft.id}",
        json={"is_active": False},
        headers=_csrf_headers(staff_session),
    )
    assert deact_draft.status_code == 200
    assert deact_draft.json()["is_active"] is False
    assert draft_prod.status == "DRAFT"

    # SKU conflict -> 409 SKU_CONFLICT
    sku_conflict = client.patch(
        f"/api/v1/admin/variants/{v1.id}",
        json={"sku": "ACT-TWO"},
        headers=_csrf_headers(staff_session),
    )
    assert sku_conflict.status_code == 409
    assert sku_conflict.json()["error"]["code"] == "SKU_CONFLICT"

    # Raising price_cents above existing compare_at_price_cents (8000) -> 422 VALIDATION_ERROR
    bad_price = client.patch(
        f"/api/v1/admin/variants/{v1.id}",
        json={"price_cents": 9000},
        headers=_csrf_headers(staff_session),
    )
    assert bad_price.status_code == 422
    assert bad_price.json()["error"]["code"] == "VALIDATION_ERROR"

    # Valid update: clear compare_at_price_cents to null, update threshold & attributes
    admin_session = login_as(client, role="admin")
    ok_update = client.patch(
        f"/api/v1/admin/variants/{v1.id}",
        json={
            "title": "Olive / Medium Updated",
            "attributes": {"color": "Forest", "size": "M"},
            "price_cents": 8500,
            "compare_at_price_cents": None,
            "low_stock_threshold": 12,
        },
        headers=_csrf_headers(admin_session),
    )
    assert ok_update.status_code == 200, ok_update.text
    updated = ok_update.json()
    assert updated["title"] == "Olive / Medium Updated"
    assert updated["price_cents"] == 8500
    assert updated["compare_at_price_cents"] is None
    assert updated["low_stock_threshold"] == 12
    assert updated["low_stock"] is True

    db_session.expire_all()
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_type == "product_variant",
            AuditLog.resource_id == str(v1.id),
            AuditLog.action == "VARIANT_UPDATED",
        )
    )
    assert audit is not None
    assert audit.before_state is not None and audit.before_state["price_cents"] == 6500
    assert audit.after_state is not None and audit.after_state["price_cents"] == 8500


def test_public_variant_availability_reclaims_expired_multiline_order_and_preserves_active(
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /variants/{id}/availability atomically reclaims expired reservations, keeps active."""
    cat, prod1, v1, s1 = _seed_catalog_for_inventory(
        db_session,
        sku="AV-ONE",
        on_hand=10,
        reserved=0,
        low_stock_threshold=5,
    )
    prod2 = Product(category_id=cat.id, title="Second Item", slug="second-item", status="ACTIVE")
    db_session.add(prod2)
    db_session.flush()
    v2 = ProductVariant(
        product_id=prod2.id,
        sku="AV-TWO",
        title="Default",
        attributes_json={},
        price_cents=3000,
        currency="USD",
        is_active=True,
    )
    db_session.add(v2)
    db_session.flush()
    s2 = InventoryStock(variant_id=v2.id, on_hand=8, reserved=0, low_stock_threshold=2, version=1)
    db_session.add(s2)

    # Inactive variant under ACTIVE product + hidden products for 404/inactive checks
    v_inactive = ProductVariant(
        product_id=prod1.id,
        sku="AV-INACTIVE",
        title="Disabled",
        attributes_json={},
        price_cents=5000,
        currency="USD",
        is_active=False,
    )
    db_session.add(v_inactive)
    db_session.flush()
    db_session.add(InventoryStock(variant_id=v_inactive.id, on_hand=50, reserved=0, version=1))

    _, _, v_draft, _ = _seed_catalog_for_inventory(
        db_session, product_status="DRAFT", sku="AV-DRAFT"
    )
    _, _, v_archived, _ = _seed_catalog_for_inventory(
        db_session, product_status="ARCHIVED", sku="AV-ARCH"
    )
    _, _, v_inact_cat, _ = _seed_catalog_for_inventory(
        db_session, category_active=False, product_status="ACTIVE", sku="AV-CATOFF"
    )
    db_session.commit()

    now = datetime.now(UTC)
    expired_at = now - timedelta(minutes=5)
    future_at = now + timedelta(minutes=20)

    # Multi-line expired PENDING_PAYMENT order touching BOTH v1 (qty=3) and v2 (qty=2)
    expired_order, expired_res_list, expired_intent = _seed_order_with_reservations(
        db_session,
        order_number="ORD-20261118-EXP001",
        items=[
            (prod1, v1, 3, expired_at),
            (prod2, v2, 2, expired_at),
        ],
    )
    # Unexpired PENDING_PAYMENT order touching v1 (qty=4)
    active_order, active_res_list, active_intent = _seed_order_with_reservations(
        db_session,
        order_number="ORD-20261118-ACT001",
        items=[(prod1, v1, 4, future_at)],
    )
    # Reflect reserved stock totals matching ACTIVE reservations (v1: 3+4=7, v2: 2)
    s1.reserved = 7
    s2.reserved = 2
    db_session.commit()

    # Trigger lazy reclamation via public GET /variants/{v1.id}/availability
    avail_resp = client.get(f"/api/v1/variants/{v1.id}/availability")
    assert avail_resp.status_code == 200, avail_resp.text
    avail_data = avail_resp.json()
    assert avail_data == {
        "variant_id": str(v1.id),
        "sku": "AV-ONE",
        "is_active": True,
        "available_quantity": 6,  # 10 on_hand - 4 unexpired reserved (reclaimed 3)
        "in_stock": True,
        "low_stock": False,
    }

    db_session.expire_all()
    # Verify expired multi-line order, intent, event, both reservations, stocks, and ledger entries
    reloaded_exp_order = db_session.get(Order, expired_order.id)
    assert reloaded_exp_order is not None
    assert reloaded_exp_order.status == "CANCELLED"
    assert reloaded_exp_order.payment_status == "CANCELLED"
    assert reloaded_exp_order.fulfillment_status == "CANCELLED"
    assert reloaded_exp_order.cancellation_reason == "RESERVATION_EXPIRED"
    assert reloaded_exp_order.cancelled_at is not None

    assert expired_intent is not None
    reloaded_exp_intent = db_session.get(PaymentIntent, expired_intent.id)
    assert reloaded_exp_intent is not None and reloaded_exp_intent.status == "CANCELLED"

    order_event = db_session.scalar(
        select(OrderEvent).where(OrderEvent.order_id == expired_order.id)
    )
    assert order_event is not None
    assert order_event.event_type == "ORDER_CANCELLED"
    assert order_event.from_status == "PENDING_PAYMENT"
    assert order_event.to_status == "CANCELLED"
    assert order_event.metadata_json == {"reason": "RESERVATION_EXPIRED"}

    for res in expired_res_list:
        reloaded_res = db_session.get(InventoryReservation, res.id)
        assert reloaded_res is not None and reloaded_res.status == "EXPIRED"

    reloaded_s1 = db_session.get(InventoryStock, v1.id)
    reloaded_s2 = db_session.get(InventoryStock, v2.id)
    assert reloaded_s1 is not None and reloaded_s1.reserved == 4 and reloaded_s1.version == 2
    assert reloaded_s2 is not None and reloaded_s2.reserved == 0 and reloaded_s2.version == 2

    release_entries = list(
        db_session.scalars(
            select(InventoryLedgerEntry).where(InventoryLedgerEntry.order_id == expired_order.id)
        ).all()
    )
    assert len(release_entries) == 2
    by_var = {entry.variant_id: entry for entry in release_entries}
    assert by_var[v1.id].reason == "RELEASE"
    assert by_var[v1.id].delta_on_hand == 0
    assert by_var[v1.id].delta_reserved == -3
    assert by_var[v1.id].on_hand_after == 10
    assert by_var[v1.id].reserved_after == 4
    assert by_var[v1.id].note == "RESERVATION_EXPIRED"

    assert by_var[v2.id].reason == "RELEASE"
    assert by_var[v2.id].delta_reserved == -2
    assert by_var[v2.id].on_hand_after == 8
    assert by_var[v2.id].reserved_after == 0
    assert by_var[v2.id].note == "RESERVATION_EXPIRED"

    # Unexpired order, reservation, and payment intent remain untouched
    reloaded_act_order = db_session.get(Order, active_order.id)
    assert reloaded_act_order is not None and reloaded_act_order.status == "PENDING_PAYMENT"
    reloaded_act_res = db_session.get(InventoryReservation, active_res_list[0].id)
    assert reloaded_act_res is not None and reloaded_act_res.status == "ACTIVE"
    assert active_intent is not None
    reloaded_act_intent = db_session.get(PaymentIntent, active_intent.id)
    assert reloaded_act_intent is not None and reloaded_act_intent.status == "REQUIRES_ACTION"

    # Inactive variant under ACTIVE product -> 200 with 0 available and is_active=False
    inact_resp = client.get(f"/api/v1/variants/{v_inactive.id}/availability")
    assert inact_resp.status_code == 200
    assert inact_resp.json() == {
        "variant_id": str(v_inactive.id),
        "sku": "AV-INACTIVE",
        "is_active": False,
        "available_quantity": 0,
        "in_stock": False,
        "low_stock": False,
    }

    # Draft / archived / inactive category / unknown -> 404 VARIANT_NOT_FOUND
    for hidden_id in (v_draft.id, v_archived.id, v_inact_cat.id, uuid.uuid4()):
        nf = client.get(f"/api/v1/variants/{hidden_id}/availability")
        assert nf.status_code == 404
        assert nf.json()["error"]["code"] == "VARIANT_NOT_FOUND"


def test_reclaim_expired_reservations_multi_order_overlap_and_paid_safety(
    client: TestClient,
    db_session: Session,
) -> None:
    """Availability check reclaims overlapping expired orders while skipping PAID/SUCCEEDED."""
    cat, prod1, v1, s1 = _seed_catalog_for_inventory(
        db_session,
        sku="OVR-1",
        on_hand=20,
        reserved=0,
        low_stock_threshold=3,
    )
    prod2 = Product(category_id=cat.id, title="Overlap Two", slug="overlap-two", status="ACTIVE")
    prod3 = Product(
        category_id=cat.id, title="Overlap Three", slug="overlap-three", status="ACTIVE"
    )
    db_session.add_all([prod2, prod3])
    db_session.flush()
    v2 = ProductVariant(
        product_id=prod2.id,
        sku="OVR-2",
        title="V2",
        attributes_json={},
        price_cents=4000,
        currency="USD",
        is_active=True,
    )
    v3 = ProductVariant(
        product_id=prod3.id,
        sku="OVR-3",
        title="V3",
        attributes_json={},
        price_cents=5000,
        currency="USD",
        is_active=True,
    )
    db_session.add_all([v2, v3])
    db_session.flush()
    s2 = InventoryStock(variant_id=v2.id, on_hand=10, reserved=0, low_stock_threshold=2, version=1)
    s3 = InventoryStock(variant_id=v3.id, on_hand=10, reserved=0, low_stock_threshold=2, version=1)
    db_session.add_all([s2, s3])
    db_session.commit()

    past_10m = datetime.now(UTC) - timedelta(minutes=10)
    past_5m = datetime.now(UTC) - timedelta(minutes=5)

    # Two expired PENDING_PAYMENT orders sharing v1 and touching v2 / v3
    exp_o1, exp_res1, exp_int1 = _seed_order_with_reservations(
        db_session,
        order_number="ORD-20261118-OVR1",
        items=[(prod1, v1, 2, past_10m), (prod2, v2, 1, past_10m)],
    )
    exp_o2, exp_res2, exp_int2 = _seed_order_with_reservations(
        db_session,
        order_number="ORD-20261118-OVR2",
        items=[(prod1, v1, 3, past_5m), (prod3, v3, 2, past_5m)],
    )
    # Expired timestamp but order.payment_status == PAID -> must NOT be reclaimed
    paid_order, paid_res, _ = _seed_order_with_reservations(
        db_session,
        order_number="ORD-20261118-PAID1",
        status="PENDING_PAYMENT",
        payment_status="PAID",
        items=[(prod1, v1, 1, past_5m)],
    )
    # Expired timestamp + REQUIRES_PAYMENT on order, but SUCCEEDED PaymentIntent -> must NOT reclaim
    succ_order, succ_res, succ_intent = _seed_order_with_reservations(
        db_session,
        order_number="ORD-20261118-SUCC1",
        status="PENDING_PAYMENT",
        payment_status="REQUIRES_PAYMENT",
        items=[(prod1, v1, 1, past_5m)],
    )
    assert succ_intent is not None
    succ_intent.status = "SUCCEEDED"

    s1.reserved = 7  # 2 + 3 + 1 + 1
    s2.reserved = 1
    s3.reserved = 2
    db_session.commit()

    resp = client.get(f"/api/v1/variants/{v1.id}/availability")
    assert resp.status_code == 200, resp.text
    assert resp.json()["available_quantity"] == 18  # 20 - 2 (paid_order + succ_order preserved)

    db_session.expire_all()
    for exp_order, exp_res_list, exp_intent in (
        (exp_o1, exp_res1, exp_int1),
        (exp_o2, exp_res2, exp_int2),
    ):
        reloaded = db_session.get(Order, exp_order.id)
        assert reloaded is not None and reloaded.status == "CANCELLED"
        assert reloaded.cancellation_reason == "RESERVATION_EXPIRED"
        assert exp_intent is not None
        reloaded_int = db_session.get(PaymentIntent, exp_intent.id)
        assert reloaded_int is not None and reloaded_int.status == "CANCELLED"
        for res in exp_res_list:
            reloaded_res = db_session.get(InventoryReservation, res.id)
            assert reloaded_res is not None and reloaded_res.status == "EXPIRED"

    reloaded_s1 = db_session.get(InventoryStock, v1.id)
    reloaded_s2 = db_session.get(InventoryStock, v2.id)
    reloaded_s3 = db_session.get(InventoryStock, v3.id)
    assert reloaded_s1 is not None and reloaded_s1.reserved == 2
    assert reloaded_s2 is not None and reloaded_s2.reserved == 0
    assert reloaded_s3 is not None and reloaded_s3.reserved == 0

    for protected_order, protected_res_list in (
        (paid_order, paid_res),
        (succ_order, succ_res),
    ):
        reloaded_prot = db_session.get(Order, protected_order.id)
        assert reloaded_prot is not None and reloaded_prot.status == "PENDING_PAYMENT"
        reloaded_prot_res = db_session.get(InventoryReservation, protected_res_list[0].id)
        assert reloaded_prot_res is not None and reloaded_prot_res.status == "ACTIVE"

    reloaded_succ_int = db_session.get(PaymentIntent, succ_intent.id)
    assert reloaded_succ_int is not None and reloaded_succ_int.status == "SUCCEEDED"


def test_admin_inventory_list_search_escaping_stock_status_and_pagination(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """GET /admin/inventory supports RBAC, escaped ILIKE search, stock_status, and pagination."""
    assert client.get("/api/v1/admin/inventory").status_code == 401
    login_as(client, role="customer")
    assert client.get("/api/v1/admin/inventory").status_code == 403

    _seed_catalog_for_inventory(
        db_session,
        sku="INV-100%",
        product_title="Cotton 100% Shirt",
        variant_title="White / S",
        on_hand=20,
        reserved=0,
        low_stock_threshold=5,
    )
    _seed_catalog_for_inventory(
        db_session,
        sku="INV-1000",
        product_title="Cotton 1000 Near Miss",
        variant_title="White / M",
        on_hand=5,
        reserved=2,
        low_stock_threshold=3,  # available == 3 == threshold -> low_stock
    )
    _seed_catalog_for_inventory(
        db_session,
        sku="INV_TAG_1",
        product_title="Wool Beanie",
        variant_title=r"Batch A\B",
        on_hand=4,
        reserved=4,  # available == 0 -> out_of_stock even though on_hand == 4
        low_stock_threshold=2,
    )
    _seed_catalog_for_inventory(
        db_session,
        sku="INVXTAGX1",
        product_title="Nylon Cap",
        variant_title="Batch AB",
        on_hand=0,
        reserved=0,
        low_stock_threshold=2,
    )

    login_as(client, role="staff")

    # stock_status filtering accuracy
    all_items = client.get("/api/v1/admin/inventory").json()
    assert all_items["total"] == 4

    in_stock = client.get("/api/v1/admin/inventory", params={"stock_status": "in_stock"}).json()
    assert {row["sku"] for row in in_stock["items"]} == {"INV-100%", "INV-1000"}

    low_stock = client.get("/api/v1/admin/inventory", params={"stock_status": "low_stock"}).json()
    assert [row["sku"] for row in low_stock["items"]] == ["INV-1000"]
    assert low_stock["items"][0]["low_stock"] is True

    oos = client.get("/api/v1/admin/inventory", params={"stock_status": "out_of_stock"}).json()
    assert {row["sku"] for row in oos["items"]} == {"INV_TAG_1", "INVXTAGX1"}

    # Wildcard escaping: '%', '_', and '\' match literals only
    assert [
        row["sku"]
        for row in client.get("/api/v1/admin/inventory", params={"q": "100%"}).json()["items"]
    ] == ["INV-100%"]
    assert [
        row["sku"]
        for row in client.get("/api/v1/admin/inventory", params={"q": "inv_tag_1"}).json()["items"]
    ] == ["INV_TAG_1"]
    assert [
        row["sku"]
        for row in client.get("/api/v1/admin/inventory", params={"q": r"a\b"}).json()["items"]
    ] == ["INV_TAG_1"]

    # Pagination
    p1 = client.get("/api/v1/admin/inventory", params={"page": 1, "page_size": 2}).json()
    assert p1["total"] == 4 and p1["has_next"] is True and len(p1["items"]) == 2
    p2 = client.get("/api/v1/admin/inventory", params={"page": 2, "page_size": 2}).json()
    assert p2["total"] == 4 and p2["has_next"] is False and len(p2["items"]) == 2

    # Single-joined-query budget check (1 auth query + count + rows <= 3 total, zero N+1)
    engine = db_session.get_bind()
    executed_sql: list[str] = []

    def _capture_sql(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not statement.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA")):
            executed_sql.append(statement)

    event.listen(engine, "before_cursor_execute", _capture_sql)
    try:
        budget_resp = client.get("/api/v1/admin/inventory", params={"page": 1, "page_size": 20})
    finally:
        event.remove(engine, "before_cursor_execute", _capture_sql)
    assert budget_resp.status_code == 200
    assert len(executed_sql) <= 3
    inventory_queries = [sql for sql in executed_sql if "inventory_stocks" in sql]
    assert len(inventory_queries) <= 2

    # Bounded page validation & offset >= total short-circuit without row query
    over_page = client.get("/api/v1/admin/inventory", params={"page": 1_000_001})
    assert over_page.status_code == 422
    assert over_page.json()["error"]["code"] == "VALIDATION_ERROR"

    short_circuit_sql: list[str] = []

    def _capture_short_circuit(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not statement.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA")):
            short_circuit_sql.append(statement)

    event.listen(engine, "before_cursor_execute", _capture_short_circuit)
    try:
        page_50 = client.get("/api/v1/admin/inventory", params={"page": 50, "page_size": 20})
    finally:
        event.remove(engine, "before_cursor_execute", _capture_short_circuit)
    assert page_50.status_code == 200
    assert page_50.json() == {
        "items": [],
        "total": 4,
        "page": 50,
        "page_size": 20,
        "has_next": False,
    }
    assert len([sql for sql in short_circuit_sql if "inventory_stocks" in sql]) == 1


def test_admin_inventory_adjust_ledger_and_audit_redaction(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /admin/inventory/{id}/adjust & GET .../ledger enforce invariants, ledger, and audit."""
    _, prod, variant, stock = _seed_catalog_for_inventory(
        db_session,
        sku="ADJ-001",
        on_hand=10,
        reserved=4,
        low_stock_threshold=5,
    )
    # Seed initial ledger row dated earlier so newest-first ordering is deterministic
    t0 = datetime.now(UTC) - timedelta(hours=1)
    db_session.add(
        InventoryLedgerEntry(
            variant_id=variant.id,
            reason="INITIAL_SEED",
            delta_on_hand=10,
            delta_reserved=0,
            on_hand_after=10,
            reserved_after=0,
            note="Seeded",
            created_at=t0,
        )
    )
    db_session.commit()

    # RBAC & CSRF checks
    cust_session = login_as(client, role="customer")
    assert (
        client.post(
            f"/api/v1/admin/inventory/{variant.id}/adjust",
            json={"delta_on_hand": 2, "note": "Denied"},
            headers=_csrf_headers(cust_session),
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/admin/inventory/{variant.id}/ledger").status_code == 403

    staff_session = login_as(client, role="staff")
    assert (
        client.post(
            f"/api/v1/admin/inventory/{variant.id}/adjust",
            json={"delta_on_hand": 2, "note": "No CSRF"},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/admin/inventory/{uuid.uuid4()}/adjust",
            json={"delta_on_hand": 2, "note": "Missing"},
            headers=_csrf_headers(staff_session),
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/admin/inventory/{uuid.uuid4()}/ledger").status_code == 404

    # Invalid adjustments: delta == 0, blank note, below 0, below reserved
    zero_delta = client.post(
        f"/api/v1/admin/inventory/{variant.id}/adjust",
        json={"delta_on_hand": 0, "note": "No-op"},
        headers=_csrf_headers(staff_session),
    )
    assert zero_delta.status_code == 422
    assert zero_delta.json()["error"]["code"] == "INVALID_STOCK_ADJUSTMENT"

    blank_note = client.post(
        f"/api/v1/admin/inventory/{variant.id}/adjust",
        json={"delta_on_hand": 1, "note": "   "},
        headers=_csrf_headers(staff_session),
    )
    assert blank_note.status_code == 422
    assert blank_note.json()["error"]["code"] == "VALIDATION_ERROR"

    below_zero = client.post(
        f"/api/v1/admin/inventory/{variant.id}/adjust",
        json={"delta_on_hand": -11, "note": "Too negative"},
        headers=_csrf_headers(staff_session),
    )
    assert below_zero.status_code == 422
    assert below_zero.json()["error"]["code"] == "INVALID_STOCK_ADJUSTMENT"

    below_reserved = client.post(
        f"/api/v1/admin/inventory/{variant.id}/adjust",
        json={"delta_on_hand": -7, "note": "Would drop below reserved=4"},
        headers=_csrf_headers(staff_session),
    )
    assert below_reserved.status_code == 422
    assert below_reserved.json()["error"]["code"] == "INVALID_STOCK_ADJUSTMENT"

    db_session.expire_all()
    unchanged_stock = db_session.get(InventoryStock, variant.id)
    assert unchanged_stock is not None
    assert unchanged_stock.on_hand == 10
    assert unchanged_stock.version == 1

    # Valid negative adjustment bringing on_hand down to exact reserved=4 -> available_quantity=0
    neg_ok = client.post(
        f"/api/v1/admin/inventory/{variant.id}/adjust",
        json={"delta_on_hand": -6, "note": "Damaged units write-off"},
        headers=_csrf_headers(staff_session),
    )
    assert neg_ok.status_code == 200, neg_ok.text
    assert neg_ok.json()["on_hand"] == 4
    assert neg_ok.json()["reserved"] == 4
    assert neg_ok.json()["available_quantity"] == 0
    assert neg_ok.json()["in_stock"] is False

    # Shift prior ledger timestamp back to guarantee deterministic newest-first order in SQLite
    first_adj = db_session.scalar(
        select(InventoryLedgerEntry).where(
            InventoryLedgerEntry.variant_id == variant.id,
            InventoryLedgerEntry.reason == "MANUAL_ADJUSTMENT",
        )
    )
    assert first_adj is not None
    first_adj.created_at = t0 + timedelta(seconds=10)
    db_session.commit()

    # Valid positive adjustment by admin -> on_hand=14, available=10, version=3
    admin_session = login_as(client, role="admin")
    pos_ok = client.post(
        f"/api/v1/admin/inventory/{variant.id}/adjust",
        json={"delta_on_hand": 10, "note": "Restock PO-1042"},
        headers=_csrf_headers(admin_session),
    )
    assert pos_ok.status_code == 200, pos_ok.text
    assert pos_ok.json()["on_hand"] == 14
    assert pos_ok.json()["available_quantity"] == 10
    assert pos_ok.json()["in_stock"] is True

    db_session.expire_all()
    final_stock = db_session.get(InventoryStock, variant.id)
    assert final_stock is not None and final_stock.on_hand == 14 and final_stock.version == 3

    # Verify AuditLog rows for STOCK_ADJUSTED
    stock_audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "inventory_stock",
                AuditLog.resource_id == str(variant.id),
                AuditLog.action == "STOCK_ADJUSTED",
            )
            .order_by(AuditLog.created_at.asc())
        ).all()
    )
    assert len(stock_audits) == 2
    by_actor = {row.actor_user_id: row for row in stock_audits}
    staff_audit = by_actor[uuid.UUID(staff_session.user_id)]
    admin_audit = by_actor[uuid.UUID(admin_session.user_id)]
    assert staff_audit.before_state is not None and staff_audit.before_state["on_hand"] == 10
    assert staff_audit.after_state is not None and staff_audit.after_state["on_hand"] == 4
    assert admin_audit.before_state is not None and admin_audit.before_state["on_hand"] == 4
    assert admin_audit.after_state is not None and admin_audit.after_state["on_hand"] == 14

    # Verify secret redaction in audit_logs (via shared utility and variant attribute snapshot)
    sanitized = sanitize_audit_payload(
        {
            "note": "ok",
            "password": "plain",
            "nested": {"csrf_token": "secret-tok", "api_secret": "s", "ok": 1},
        }
    )
    assert sanitized == {
        "note": "ok",
        "password": REDACTED_VALUE,
        "nested": {"csrf_token": REDACTED_VALUE, "api_secret": REDACTED_VALUE, "ok": 1},
    }
    redacted_var_resp = client.post(
        f"/api/v1/admin/products/{prod.id}/variants",
        json={
            "sku": "ADJ-REDACT",
            "title": "Secret Key Attr Check",
            "attributes": {"vendor_token": "tok_123", "color": "Blue"},
            "price_cents": 2000,
        },
        headers=_csrf_headers(admin_session),
    )
    assert redacted_var_resp.status_code == 201
    redact_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_type == "product_variant",
            AuditLog.resource_id == redacted_var_resp.json()["id"],
        )
    )
    assert redact_audit is not None and redact_audit.after_state is not None
    assert redact_audit.after_state["attributes"]["vendor_token"] == REDACTED_VALUE
    assert redact_audit.after_state["attributes"]["color"] == "Blue"

    # Also confirm record_audit_log masks X-Guest-Order-Token / secret fields cleanly
    manual_log = record_audit_log(
        db_session,
        actor_user_id=uuid.UUID(admin_session.user_id),
        action="TEST_REDACT",
        resource_type="inventory_stock",
        resource_id=str(variant.id),
        before_state={"x_guest_order_token": "secret"},
        after_state={"cookie": "raw"},
    )
    assert manual_log.before_state == {"x_guest_order_token": REDACTED_VALUE}
    assert manual_log.after_state == {"cookie": REDACTED_VALUE}
    db_session.rollback()

    # Verify paginated newest-first ledger history
    ledger_p1 = client.get(
        f"/api/v1/admin/inventory/{variant.id}/ledger",
        params={"page": 1, "page_size": 2},
    )
    assert ledger_p1.status_code == 200
    p1_data = ledger_p1.json()
    assert p1_data["total"] == 3 and p1_data["has_next"] is True
    assert [entry["note"] for entry in p1_data["items"]] == [
        "Restock PO-1042",
        "Damaged units write-off",
    ]
    assert p1_data["items"][0]["actor_email"] == admin_session.email
    assert p1_data["items"][1]["actor_email"] == staff_session.email

    ledger_p2 = client.get(
        f"/api/v1/admin/inventory/{variant.id}/ledger",
        params={"page": 2, "page_size": 2},
    ).json()
    assert ledger_p2["has_next"] is False
    assert [entry["reason"] for entry in ledger_p2["items"]] == ["INITIAL_SEED"]

    # Ledger pagination bound & offset >= total short-circuit
    bad_ledger_page = client.get(
        f"/api/v1/admin/inventory/{variant.id}/ledger",
        params={"page": 1_000_001},
    )
    assert bad_ledger_page.status_code == 422
    assert bad_ledger_page.json()["error"]["code"] == "VALIDATION_ERROR"

    ledger_p50 = client.get(
        f"/api/v1/admin/inventory/{variant.id}/ledger",
        params={"page": 50, "page_size": 20},
    )
    assert ledger_p50.status_code == 200
    assert ledger_p50.json() == {
        "items": [],
        "total": 3,
        "page": 50,
        "page_size": 20,
        "has_next": False,
    }


def test_audit_log_defensive_sanitization_and_secret_redaction(
    db_session: Session,
    create_user: Callable[..., User],
) -> None:
    """record_audit_log converts NaN/Inf, strips NUL from keys, truncates >16, redacts secrets."""
    actor = create_user(role="admin")
    deep: dict[str, Any] = {"leaf": "should-truncate"}
    for _ in range(17):
        deep = {"lvl": deep}

    entry = record_audit_log(
        db_session,
        actor_user_id=actor.id,
        action="DEFENSIVE_SANITIZE_TEST",
        resource_type="audit_test",
        resource_id=str(uuid.uuid4()),
        before_state={
            "nan_val": float("nan"),
            "pos_inf": float("inf"),
            "neg_inf": float("-inf"),
            "bad\x00key": "val\x00ue",
        },
        after_state={
            "api_key": "ak_live_123",
            "private_key": "-----BEGIN PRIVATE KEY-----",
            "credential": "cred_secret",
            "passphrase": "horse-battery-staple",
            "deep": deep,
        },
        ip_address="203.0.113.9",
    )
    db_session.commit()
    db_session.expire_all()

    persisted = db_session.get(AuditLog, entry.id)
    assert persisted is not None
    assert persisted.before_state == {
        "nan_val": "nan",
        "pos_inf": "inf",
        "neg_inf": "-inf",
        "badkey": "value",
    }
    assert persisted.after_state is not None
    assert persisted.after_state["api_key"] == REDACTED_VALUE
    assert persisted.after_state["private_key"] == REDACTED_VALUE
    assert persisted.after_state["credential"] == REDACTED_VALUE
    assert persisted.after_state["passphrase"] == REDACTED_VALUE

    cursor: dict[str, Any] = persisted.after_state["deep"]
    for _ in range(15):
        cursor = cursor["lvl"]
    assert cursor["lvl"] == "[TRUNCATED]"


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
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


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Live PostgreSQL TEST_DATABASE_URL not set",
)
def test_live_postgresql_be002_constraints_jsonb_partial_index_and_concurrency() -> None:
    """Verify PostgreSQL JSONB, CHECK constraints, partial unique index, and FOR UPDATE locking."""
    test_db_url = os.environ["TEST_DATABASE_URL"]
    up_res = _run_alembic("upgrade", "head", database_url=test_db_url)
    assert up_res.returncode == 0, up_res.stderr

    pg_settings = Settings(
        database_url=test_db_url,
        environment="test",
        cookie_secure=False,
        allowed_origins=[DEFAULT_ORIGIN],
        csrf_hmac_secret=TEST_CSRF_SECRET,
        guest_order_hmac_secret=TEST_GUEST_ORDER_SECRET,
        payment_webhook_secret=TEST_WEBHOOK_SECRET,
        payment_sandbox_mode=True,
    )
    pg_engine = create_engine_from_url(test_db_url)
    pg_session_factory = sessionmaker(
        bind=pg_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    pg_app = create_app(pg_settings)
    pg_app.state.db_session_factory = pg_session_factory

    try:
        with pg_session_factory() as setup_db:
            admin_email = f"pg-admin-{uuid.uuid4().hex[:8]}@example.com"
            admin_user = User(
                email=admin_email,
                password_hash=hash_password(DEFAULT_TEST_PASSWORD),
                full_name="PG Admin",
                role="admin",
                is_active=True,
            )
            setup_db.add(admin_user)
            setup_db.flush()

            cat = Category(
                name="PG Category",
                slug=f"pg-cat-{uuid.uuid4().hex[:8]}",
                is_active=True,
            )
            setup_db.add(cat)
            setup_db.flush()

            prod = Product(
                category_id=cat.id,
                title="PG Merino Tee",
                slug=f"pg-tee-{uuid.uuid4().hex[:8]}",
                description="PostgreSQL integration item",
                status="ACTIVE",
            )
            setup_db.add(prod)
            setup_db.flush()

            setup_db.add(
                ProductImage(
                    product_id=prod.id,
                    url="https://cdn.example.com/pg-primary.webp",
                    alt_text="Primary",
                    sort_order=0,
                    is_primary=True,
                )
            )

            variant = ProductVariant(
                product_id=prod.id,
                sku=f"PG-SKU-{uuid.uuid4().hex[:8]}",
                title="Slate / L",
                attributes_json={"color": "Slate", "specs": {"gsm": 190, "organic": True}},
                price_cents=5500,
                currency="USD",
                is_active=True,
            )
            setup_db.add(variant)
            setup_db.flush()

            stock = InventoryStock(
                variant_id=variant.id,
                on_hand=20,
                reserved=5,
                low_stock_threshold=5,
                version=1,
            )
            setup_db.add(stock)
            setup_db.add(
                InventoryLedgerEntry(
                    variant_id=variant.id,
                    actor_user_id=admin_user.id,
                    reason="INITIAL_SEED",
                    delta_on_hand=20,
                    delta_reserved=0,
                    on_hand_after=20,
                    reserved_after=0,
                    note="PG initial seed",
                )
            )
            setup_db.commit()

            # 1. Verify JSONB column type and operator persistence
            pg_type = str(
                setup_db.execute(
                    text(
                        "SELECT pg_typeof(attributes_json)::text "
                        "FROM product_variants WHERE id = :vid"
                    ),
                    {"vid": variant.id},
                ).scalar_one()
            )
            assert pg_type == "jsonb"
            color_val = str(
                setup_db.execute(
                    text(
                        "SELECT attributes_json ->> 'color' FROM product_variants "
                        "WHERE attributes_json @> '{\"specs\": {\"gsm\": 190}}'::jsonb "
                        "AND id = :vid"
                    ),
                    {"vid": variant.id},
                ).scalar_one()
            )
            assert color_val == "Slate"

            # 2. Verify PostgreSQL CHECK constraints on inventory_stocks
            with pytest.raises(IntegrityError) as exc_neg_hand, setup_db.begin_nested():
                setup_db.execute(
                    text("UPDATE inventory_stocks SET on_hand = -1 WHERE variant_id = :vid"),
                    {"vid": variant.id},
                )
            assert "ck_inventory_stocks_on_hand_non_negative" in str(exc_neg_hand.value)

            with pytest.raises(IntegrityError) as exc_neg_res, setup_db.begin_nested():
                setup_db.execute(
                    text("UPDATE inventory_stocks SET reserved = -1 WHERE variant_id = :vid"),
                    {"vid": variant.id},
                )
            assert "ck_inventory_stocks_reserved_non_negative" in str(exc_neg_res.value)

            with pytest.raises(IntegrityError) as exc_res_gt_hand, setup_db.begin_nested():
                setup_db.execute(
                    text("UPDATE inventory_stocks SET reserved = 25 WHERE variant_id = :vid"),
                    {"vid": variant.id},
                )
            assert "ck_inventory_stocks_reserved_lte_on_hand" in str(exc_res_gt_hand.value)

            # 3. Verify uq_product_primary_image partial unique index
            with pytest.raises(IntegrityError) as exc_dup_primary, setup_db.begin_nested():
                setup_db.add(
                    ProductImage(
                        product_id=prod.id,
                        url="https://cdn.example.com/pg-primary-dup.webp",
                        alt_text="Dup",
                        sort_order=1,
                        is_primary=True,
                    )
                )
                setup_db.flush()
            assert "uq_product_primary_image" in str(exc_dup_primary.value)

            # Multiple non-primary images are allowed by partial unique index
            setup_db.add_all(
                [
                    ProductImage(
                        product_id=prod.id,
                        url="/assets/pg-extra-1.webp",
                        alt_text="Extra 1",
                        sort_order=1,
                        is_primary=False,
                    ),
                    ProductImage(
                        product_id=prod.id,
                        url="/assets/pg-extra-2.webp",
                        alt_text="Extra 2",
                        sort_order=2,
                        is_primary=False,
                    ),
                ]
            )
            setup_db.commit()

            # Seed expired PENDING_PAYMENT order holding reserved=5 on variant
            expired_order, _, _ = _seed_order_with_reservations(
                setup_db,
                order_number=f"ORD-PG-{uuid.uuid4().hex[:8]}",
                items=[(prod, variant, 5, datetime.now(UTC) - timedelta(minutes=5))],
            )

        # Login admin and verify PATCH image replacement + capture session/CSRF for workers
        with TestClient(pg_app) as pg_client:
            login_resp = pg_client.post(
                "/api/v1/auth/login",
                json={"email": admin_email, "password": DEFAULT_TEST_PASSWORD},
                headers={"Origin": DEFAULT_ORIGIN},
            )
            assert login_resp.status_code == 200
            session_cookie = pg_client.cookies.get(SESSION_COOKIE_NAME)
            csrf_cookie = pg_client.cookies.get(CSRF_COOKIE_NAME)
            csrf_token = str(login_resp.json()["csrf_token"])
            assert session_cookie is not None and csrf_cookie is not None

            replace_img = pg_client.patch(
                f"/api/v1/admin/products/{prod.id}",
                json={
                    "images": [
                        {
                            "url": "/assets/pg-primary-v2.webp",
                            "alt_text": "Replaced Primary",
                            "sort_order": 0,
                            "is_primary": True,
                        }
                    ]
                },
                headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_token},
            )
            assert replace_img.status_code == 200, replace_img.text

        # 4. Verify FOR UPDATE SKIP LOCKED skips locked order cleanly without blocking
        with pg_session_factory() as lock_db, pg_session_factory() as probe_db:
            locked = lock_db.scalar(
                select(Order).where(Order.id == expired_order.id).with_for_update()
            )
            assert locked is not None
            skipped_count = reclaim_expired_reservations_for_variants(probe_db, [variant.id])
            assert skipped_count == 0
            lock_db.rollback()

        # 5. Concurrent POST /admin/inventory/{id}/adjust + reservation reclamation
        deltas = [3, 5, -2, 4]  # Net +10; never violates on_hand >= reserved in any order
        barrier = threading.Barrier(len(deltas) + 2)

        def _adjust_worker(delta: int) -> int:
            barrier.wait(timeout=10)
            with TestClient(pg_app) as worker_client:
                worker_client.cookies.set(SESSION_COOKIE_NAME, session_cookie)
                worker_client.cookies.set(CSRF_COOKIE_NAME, csrf_cookie)
                resp = worker_client.post(
                    f"/api/v1/admin/inventory/{variant.id}/adjust",
                    json={"delta_on_hand": delta, "note": f"Concurrent delta {delta}"},
                    headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_token},
                )
                return int(resp.status_code)

        def _reclaim_worker() -> int:
            barrier.wait(timeout=10)
            with pg_session_factory() as worker_db:
                reclaimed = reclaim_expired_reservations_for_variants(worker_db, [variant.id])
                if reclaimed > 0:
                    worker_db.commit()
                return reclaimed

        with ThreadPoolExecutor(max_workers=len(deltas) + 2) as pool:
            adjust_futures = [pool.submit(_adjust_worker, d) for d in deltas]
            reclaim_futures = [pool.submit(_reclaim_worker), pool.submit(_reclaim_worker)]
            adjust_statuses = [f.result(timeout=20) for f in adjust_futures]
            reclaim_counts = [f.result(timeout=20) for f in reclaim_futures]

        assert adjust_statuses == [200, 200, 200, 200]
        assert sum(reclaim_counts) == 1

        with pg_session_factory() as verify_db:
            final_stock = verify_db.get(InventoryStock, variant.id)
            assert final_stock is not None
            assert final_stock.on_hand == 30  # 20 + (3 + 5 - 2 + 4)
            assert final_stock.reserved == 0
            assert final_stock.version == 6  # 1 initial + 1 reclaim + 4 adjustments

            final_order = verify_db.get(Order, expired_order.id)
            assert final_order is not None and final_order.status == "CANCELLED"
            final_res = verify_db.scalar(
                select(InventoryReservation).where(
                    InventoryReservation.order_id == expired_order.id
                )
            )
            assert final_res is not None and final_res.status == "EXPIRED"

            ledger_count = len(
                list(
                    verify_db.scalars(
                        select(InventoryLedgerEntry).where(
                            InventoryLedgerEntry.variant_id == variant.id
                        )
                    ).all()
                )
            )
            assert ledger_count == 6  # 1 INITIAL_SEED + 1 RELEASE + 4 MANUAL_ADJUSTMENT
    finally:
        pg_engine.dispose()
        get_engine(pg_settings).dispose()
        clear_db_caches()
        _run_alembic("downgrade", "base", database_url=test_db_url)
