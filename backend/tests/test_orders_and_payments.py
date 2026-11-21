"""Tests for Module 5 orders, Module 6 payments/webhooks, and live PostgreSQL concurrency."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker
from tests.conftest import (
    DEFAULT_ORIGIN,
    DEFAULT_TEST_PASSWORD,
    TEST_CSRF_SECRET,
    TEST_GUEST_ORDER_SECRET,
    TEST_WEBHOOK_SECRET,
    AuthenticatedSession,
)

from app.core.config import Settings
from app.core.database import clear_db_caches, create_engine_from_url, get_engine
from app.core.errors import (
    CSRF_COOKIE_NAME,
    GUEST_CART_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)
from app.core.security import SlidingWindowRateLimiter, hash_password, sign_webhook_payload
from app.main import create_app
from app.models.catalog import Category, Product
from app.models.identity import User
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderEvent
from app.models.payment import PaymentIntent, PaymentRefundRequest, PaymentWebhookEvent
from app.modules.inventory.service import reclaim_expired_reservations_for_variants
from app.modules.orders.service import SANDBOX_PROVIDER
from app.modules.payments.schemas import WebhookEventData, WebhookPayload
from app.modules.payments.service import (
    MAX_WEBHOOK_BODY_BYTES,
    _persist_webhook_unexpected_failure,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _csrf_headers(
    session: AuthenticatedSession | None = None,
    *,
    origin: str = DEFAULT_ORIGIN,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {"Origin": origin}
    if session is not None:
        headers["X-CSRF-Token"] = session.csrf_token
    if extra:
        headers.update(extra)
    return headers


def _use_auth_cookies(client: TestClient, session: AuthenticatedSession) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, session.session_cookie)
    client.cookies.set(CSRF_COOKIE_NAME, session.csrf_cookie)


def _sample_address() -> dict[str, Any]:
    return {
        "full_name": "Alice Mercer",
        "line1": "120 Market St",
        "line2": "Apt 4B",
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94105",
        "country": "US",
        "phone": "+14155550101",
    }


def _checkout_payload(email: str = "buyer@example.com") -> dict[str, Any]:
    return {
        "customer_email": email,
        "shipping_address": _sample_address(),
        "billing_same_as_shipping": True,
        "billing_address": None,
    }


def _seed_variant(
    db_session: Session,
    *,
    sku: str | None = None,
    price_cents: int = 5000,
    on_hand: int = 10,
    reserved: int = 0,
) -> tuple[Category, Product, ProductVariant, InventoryStock]:
    suffix = uuid.uuid4().hex[:8]
    category = Category(name=f"Cat-{suffix}", slug=f"cat-{suffix}", is_active=True, sort_order=1)
    db_session.add(category)
    db_session.flush()

    product = Product(
        category_id=category.id,
        title=f"Item-{suffix}",
        slug=f"item-{suffix}",
        description="Order/payment test item",
        status="ACTIVE",
    )
    db_session.add(product)
    db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku=sku or f"ORD-{suffix}",
        title=f"Variant-{suffix}",
        attributes_json={"color": "Black", "size": "M"},
        price_cents=price_cents,
        currency="USD",
        is_active=True,
    )
    db_session.add(variant)
    db_session.flush()

    stock = InventoryStock(
        variant_id=variant.id,
        on_hand=on_hand,
        reserved=reserved,
        low_stock_threshold=2,
        version=1,
    )
    db_session.add(stock)
    db_session.commit()
    db_session.refresh(category)
    db_session.refresh(product)
    db_session.refresh(variant)
    db_session.refresh(stock)
    return category, product, variant, stock


def _place_checkout_order(
    client: TestClient,
    *,
    variant_id: uuid.UUID,
    quantity: int,
    session: AuthenticatedSession | None = None,
    email: str = "buyer@example.com",
) -> dict[str, Any]:
    if session is None:
        client.cookies.delete(GUEST_CART_COOKIE_NAME)
    add_resp = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(variant_id), "quantity": quantity},
        headers=_csrf_headers(session),
    )
    assert add_resp.status_code == 200, add_resp.text
    chk_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(email=email),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert chk_resp.status_code == 201, chk_resp.text
    result: dict[str, Any] = chk_resp.json()
    return result


def _signed_webhook_request(
    *,
    event_id: str | None = None,
    event_type: str,
    provider_intent_id: str,
    order_number: str,
    amount_cents: int,
    currency: str = "USD",
    failure_code: str | None = None,
    failure_message: str | None = None,
    timestamp: int | None = None,
    secret: str = TEST_WEBHOOK_SECRET,
) -> tuple[bytes, dict[str, str]]:
    payload = {
        "event_id": event_id or f"evt_test_{uuid.uuid4().hex[:16]}",
        "event_type": event_type,
        "data": {
            "provider_intent_id": provider_intent_id,
            "order_number": order_number,
            "amount_cents": amount_cents,
            "currency": currency,
            "failure_code": failure_code,
            "failure_message": failure_message,
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ts = int(time.time()) if timestamp is None else timestamp
    signature = sign_webhook_payload(ts, raw_body, secret)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": str(ts),
        "X-Webhook-Signature": signature,
    }
    return raw_body, headers


def _post_signed_webhook(
    client: TestClient,
    *,
    provider: str = SANDBOX_PROVIDER,
    event_id: str | None = None,
    event_type: str,
    provider_intent_id: str,
    order_number: str,
    amount_cents: int,
    currency: str = "USD",
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> tuple[int, dict[str, Any]]:
    raw_body, headers = _signed_webhook_request(
        event_id=event_id,
        event_type=event_type,
        provider_intent_id=provider_intent_id,
        order_number=order_number,
        amount_cents=amount_cents,
        currency=currency,
        failure_code=failure_code,
        failure_message=failure_message,
    )
    resp = client.post(
        f"/api/v1/webhooks/payments/{provider}",
        content=raw_body,
        headers=headers,
    )
    return resp.status_code, resp.json()


def _expire_order_reservations(
    db_session: Session,
    *,
    order_id: uuid.UUID,
    variant_ids: list[uuid.UUID],
) -> None:
    past = datetime.now(UTC) - timedelta(minutes=20)
    reservations = list(
        db_session.scalars(
            select(InventoryReservation).where(InventoryReservation.order_id == order_id)
        ).all()
    )
    for res in reservations:
        res.expires_at = past
    db_session.commit()
    reclaimed = reclaim_expired_reservations_for_variants(db_session, variant_ids)
    assert reclaimed >= 1
    db_session.commit()


def test_orders_list_auth_scoping_sorting_pagination_and_lazy_expiry(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """GET /orders requires auth, scopes by user_id, sorts placed_at DESC, short-circuits."""
    assert client.get("/api/v1/orders").status_code == 401

    _, _, var, _ = _seed_variant(db_session, sku="LST-01", price_cents=2500, on_hand=20)

    # Seed User B order (must never appear in User A's list)
    session_b = login_as(client, role="customer", email="list-b@example.com")
    order_b = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session_b)

    # Place 3 orders for User A with distinct placed_at timestamps
    session_a = login_as(client, role="customer", email="list-a@example.com")
    orders_a = [
        _place_checkout_order(client, variant_id=var.id, quantity=1, session=session_a)
        for _ in range(3)
    ]
    base_time = datetime.now(UTC) - timedelta(hours=3)
    for idx, dto in enumerate(orders_a):
        db_order = db_session.get(Order, uuid.UUID(dto["order"]["id"]))
        assert db_order is not None
        db_order.placed_at = base_time + timedelta(minutes=idx * 10)
    # Mark middle order's reservation expired so GET /orders lazily reclaims it
    mid_res = db_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.order_id == uuid.UUID(orders_a[1]["order"]["id"])
        )
    )
    assert mid_res is not None
    mid_res.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()

    p1_resp = client.get("/api/v1/orders", params={"page": 1, "page_size": 2})
    assert p1_resp.status_code == 200, p1_resp.text
    p1 = p1_resp.json()
    assert p1["total"] == 3
    assert p1["has_next"] is True
    assert [row["order_number"] for row in p1["items"]] == [
        orders_a[2]["order"]["order_number"],
        orders_a[1]["order"]["order_number"],
    ]
    assert p1["items"][0]["status"] == "PENDING_PAYMENT"
    assert p1["items"][0]["reservation_expires_at"] is not None
    # Expired middle order was lazily cancelled during list_customer_orders
    assert p1["items"][1]["status"] == "CANCELLED"
    assert p1["items"][1]["reservation_expires_at"] is None
    assert order_b["order"]["order_number"] not in {row["order_number"] for row in p1["items"]}

    p2_resp = client.get("/api/v1/orders", params={"page": 2, "page_size": 2})
    assert p2_resp.status_code == 200
    p2 = p2_resp.json()
    assert p2["has_next"] is False
    assert [row["order_number"] for row in p2["items"]] == [
        orders_a[0]["order"]["order_number"]
    ]

    # Page limit validation (1..1_000_000) & offset short-circuit (zero row SELECT)
    page_max = client.get("/api/v1/orders", params={"page": 1_000_000})
    assert page_max.status_code == 200
    assert page_max.json()["items"] == []
    assert page_max.json()["has_next"] is False
    assert client.get("/api/v1/orders", params={"page": 1_000_001}).status_code == 422

    engine = db_session.get_bind()
    short_circuit_sql: list[str] = []

    def _capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not statement.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA")):
            short_circuit_sql.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        p50 = client.get("/api/v1/orders", params={"page": 50, "page_size": 20})
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert p50.status_code == 200
    assert p50.json() == {
        "items": [],
        "total": 3,
        "page": 50,
        "page_size": 20,
        "has_next": False,
    }
    assert len([sql for sql in short_circuit_sql if "FROM orders" in sql]) == 1


def test_order_detail_validation_strict_owner_and_guest_token_authorization(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """GET /orders/{order_number} pre-validates format and enforces strict owner/guest auth."""
    _, _, var, _ = _seed_variant(db_session, sku="DET-01", price_cents=4000, on_hand=10)

    # Pre-validation on malformed / NUL-byte order numbers -> 404 ORDER_NOT_FOUND
    for bad_num in ("ORD%00BAD", "A" * 33, "ORD!INVALID", "ORD-UNKNOWN"):
        nf = client.get(f"/api/v1/orders/{bad_num}")
        assert nf.status_code == 404
        assert nf.json()["error"]["code"] == "ORDER_NOT_FOUND"

    # User A's order
    session_a = login_as(client, role="customer", email="det-a@example.com")
    user_order = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session_a)
    user_order_num = user_order["order"]["order_number"]

    ok_owner = client.get(f"/api/v1/orders/{user_order_num}")
    assert ok_owner.status_code == 200
    assert ok_owner.json()["order_number"] == user_order_num

    # Anonymous -> 401 AUTH_SESSION_EXPIRED
    client.cookies.clear()
    assert client.get(f"/api/v1/orders/{user_order_num}").status_code == 401

    # Another customer, non-owner staff, and non-owner admin -> all strictly 403 FORBIDDEN!
    for other_role in ("customer", "staff", "admin"):
        login_as(client, role=other_role)
        forbidden = client.get(f"/api/v1/orders/{user_order_num}")
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"

    # Guest order authorization (cookie vs header vs tampered/expired/version-bumped)
    client.cookies.clear()
    guest_order = _place_checkout_order(client, variant_id=var.id, quantity=1, session=None)
    guest_num = guest_order["order"]["order_number"]
    guest_token = str(guest_order["guest_access_token"])

    # Via ecom_guest_orders cookie set by checkout
    assert client.get(f"/api/v1/orders/{guest_num}").status_code == 200

    # Without cookie or header -> 403 FORBIDDEN
    client.cookies.clear()
    assert client.get(f"/api/v1/orders/{guest_num}").status_code == 403

    # Via X-Guest-Order-Token header without any cookie -> 200 OK
    assert (
        client.get(
            f"/api/v1/orders/{guest_num}",
            headers={"X-Guest-Order-Token": guest_token},
        ).status_code
        == 200
    )

    # Tampered token -> 403 FORBIDDEN
    assert (
        client.get(
            f"/api/v1/orders/{guest_num}",
            headers={"X-Guest-Order-Token": "0" * 64},
        ).status_code
        == 403
    )

    # Stale ecom_session even WITH valid guest token -> fails closed 401 AUTH_SESSION_EXPIRED
    client.cookies.set(SESSION_COOKIE_NAME, "stale-session-cookie")
    stale = client.get(
        f"/api/v1/orders/{guest_num}",
        headers={"X-Guest-Order-Token": guest_token},
    )
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"
    client.cookies.clear()

    # Non-owner staff/admin without guest token -> 403 FORBIDDEN on storefront detail route
    login_as(client, role="admin")
    assert client.get(f"/api/v1/orders/{guest_num}").status_code == 403
    client.cookies.clear()

    # Version bump invalidates existing guest token -> 403
    db_guest_order = db_session.get(Order, uuid.UUID(guest_order["order"]["id"]))
    assert db_guest_order is not None
    db_guest_order.guest_token_version = 2
    db_session.commit()
    assert (
        client.get(
            f"/api/v1/orders/{guest_num}",
            headers={"X-Guest-Order-Token": guest_token},
        ).status_code
        == 403
    )

    # Expired guest_access_expires_at -> 403
    db_guest_order.guest_token_version = 1
    db_guest_order.guest_access_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert (
        client.get(
            f"/api/v1/orders/{guest_num}",
            headers={"X-Guest-Order-Token": guest_token},
        ).status_code
        == 403
    )


def test_order_self_cancel_auth_stock_release_and_state_guards(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /orders/{order_number}/cancel enforces auth/CSRF, releases stock, guards states."""
    _, _, var, _ = _seed_variant(db_session, sku="CXL-01", price_cents=4000, on_hand=10)

    session_a = login_as(client, role="customer", email="cxl-a@example.com")
    order_a = _place_checkout_order(client, variant_id=var.id, quantity=3, session=session_a)
    order_a_id = uuid.UUID(order_a["order"]["id"])
    order_a_num = order_a["order"]["order_number"]

    # Missing X-CSRF-Token -> 403 CSRF_INVALID
    assert (
        client.post(
            f"/api/v1/orders/{order_a_num}/cancel",
            json={"reason": "No CSRF"},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 403
    )

    # Foreign customer / staff / admin -> 403 FORBIDDEN
    for role in ("customer", "staff", "admin"):
        other_session = login_as(client, role=role)
        resp = client.post(
            f"/api/v1/orders/{order_a_num}/cancel",
            json={"reason": "Not mine"},
            headers=_csrf_headers(other_session),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    # Valid owner cancel -> 200 OK, releases reservation, writes RELEASE ledger & ORDER_CANCELLED
    _use_auth_cookies(client, session_a)
    cancel_ok = client.post(
        f"/api/v1/orders/{order_a_num}/cancel",
        json={"reason": "Ordered wrong size"},
        headers=_csrf_headers(session_a),
    )
    assert cancel_ok.status_code == 200, cancel_ok.text
    cxl_dto = cancel_ok.json()
    assert cxl_dto["status"] == "CANCELLED"
    assert cxl_dto["payment_status"] == "CANCELLED"
    assert cxl_dto["fulfillment_status"] == "CANCELLED"
    assert cxl_dto["cancellation_reason"] == "Ordered wrong size"
    assert cxl_dto["cancelled_at"] is not None
    assert cxl_dto["payment_intent"]["status"] == "CANCELLED"

    db_session.expire_all()
    stock = db_session.get(InventoryStock, var.id)
    assert stock is not None and stock.reserved == 0 and stock.on_hand == 10
    res = db_session.scalar(
        select(InventoryReservation).where(InventoryReservation.order_id == order_a_id)
    )
    assert res is not None and res.status == "RELEASED"
    ledger = db_session.scalar(
        select(InventoryLedgerEntry).where(
            InventoryLedgerEntry.order_id == order_a_id,
            InventoryLedgerEntry.reason == "RELEASE",
        )
    )
    assert ledger is not None and ledger.delta_reserved == -3 and ledger.reserved_after == 0
    ev = db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == order_a_id,
            OrderEvent.event_type == "ORDER_CANCELLED",
        )
    )
    assert ev is not None and ev.metadata_json == {"reason": "Ordered wrong size"}

    # Repeated cancel -> 409 INVALID_ORDER_STATE_TRANSITION
    repeat_cxl = client.post(
        f"/api/v1/orders/{order_a_num}/cancel",
        headers=_csrf_headers(session_a),
    )
    assert repeat_cxl.status_code == 409
    assert repeat_cxl.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Expired reservation cancel -> 409 INVALID_ORDER_STATE_TRANSITION (and releases hold)
    expired_order = _place_checkout_order(client, variant_id=var.id, quantity=2, session=session_a)
    exp_res = db_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.order_id == uuid.UUID(expired_order["order"]["id"])
        )
    )
    assert exp_res is not None
    exp_res.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()

    exp_cxl = client.post(
        f"/api/v1/orders/{expired_order['order']['order_number']}/cancel",
        headers=_csrf_headers(session_a),
    )
    assert exp_cxl.status_code == 409
    assert exp_cxl.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"
    db_session.expire_all()
    stock_after_exp = db_session.get(InventoryStock, var.id)
    assert stock_after_exp is not None and stock_after_exp.reserved == 0

    # Paid / CONFIRMED order cancel -> 409 INVALID_ORDER_STATE_TRANSITION
    paid_order = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session_a)
    wh_status, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=paid_order["payment_intent"]["provider_intent_id"],
        order_number=paid_order["order"]["order_number"],
        amount_cents=paid_order["order"]["total_cents"],
    )
    assert wh_status == 200
    paid_cxl = client.post(
        f"/api/v1/orders/{paid_order['order']['order_number']}/cancel",
        headers=_csrf_headers(session_a),
    )
    assert paid_cxl.status_code == 409
    assert paid_cxl.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Guest order cancel via X-Guest-Order-Token -> 200 OK
    client.cookies.clear()
    guest_order = _place_checkout_order(client, variant_id=var.id, quantity=1, session=None)
    client.cookies.clear()
    assert (
        client.post(
            f"/api/v1/orders/{guest_order['order']['order_number']}/cancel",
            headers={"Origin": DEFAULT_ORIGIN, "X-Guest-Order-Token": "wrong-token"},
        ).status_code
        == 403
    )
    guest_cxl = client.post(
        f"/api/v1/orders/{guest_order['order']['order_number']}/cancel",
        headers={
            "Origin": DEFAULT_ORIGIN,
            "X-Guest-Order-Token": guest_order["guest_access_token"],
        },
    )
    assert guest_cxl.status_code == 200
    assert guest_cxl.json()["status"] == "CANCELLED"


def test_payment_intent_create_idempotency_retry_and_state_guards(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /payments/intents supports idempotent replay, key conflicts, and FAILED retry."""
    _, _, var, _ = _seed_variant(db_session, sku="PI-01", price_cents=4500, on_hand=10)

    session = login_as(client, role="customer", email="pi-owner@example.com")
    order1 = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    order2 = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    o1_num = order1["order"]["order_number"]
    o2_num = order2["order"]["order_number"]

    # Missing Idempotency-Key -> 400; missing CSRF -> 403
    assert (
        client.post(
            "/api/v1/payments/intents",
            json={"order_number": o1_num},
            headers=_csrf_headers(session),
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/payments/intents",
            json={"order_number": o1_num},
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        ).status_code
        == 403
    )

    # Foreign user / non-owner staff -> 403 FORBIDDEN
    other_staff = login_as(client, role="staff")
    assert (
        client.post(
            "/api/v1/payments/intents",
            json={"order_number": o1_num},
            headers=_csrf_headers(other_staff, extra={"Idempotency-Key": uuid.uuid4().hex}),
        ).status_code
        == 403
    )

    # Fail order1's initial intent via webhook so retry creates a new REQUIRES_ACTION intent
    _use_auth_cookies(client, session)
    fail_status, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.failed",
        provider_intent_id=order1["payment_intent"]["provider_intent_id"],
        order_number=o1_num,
        amount_cents=order1["order"]["total_cents"],
        failure_code="card_declined",
        failure_message="Insufficient funds",
    )
    assert fail_status == 200

    retry_key = f"retry-pi-{uuid.uuid4().hex[:8]}"
    created_resp = client.post(
        "/api/v1/payments/intents",
        json={"order_number": o1_num},
        headers=_csrf_headers(session, extra={"Idempotency-Key": retry_key}),
    )
    assert created_resp.status_code == 201, created_resp.text
    retry_pi = created_resp.json()["payment_intent"]
    assert retry_pi["status"] == "REQUIRES_ACTION"
    assert retry_pi["provider_intent_id"] != order1["payment_intent"]["provider_intent_id"]

    # Old failed intent was cancelled when retry intent was created
    db_session.expire_all()
    old_pi = db_session.get(PaymentIntent, uuid.UUID(order1["payment_intent"]["id"]))
    assert old_pi is not None and old_pi.status == "CANCELLED"

    # Idempotent replay with same key & order_number -> 200 OK returning same intent
    replay_resp = client.post(
        "/api/v1/payments/intents",
        json={"order_number": o1_num},
        headers=_csrf_headers(session, extra={"Idempotency-Key": retry_key}),
    )
    assert replay_resp.status_code == 200
    assert replay_resp.json()["payment_intent"]["id"] == retry_pi["id"]

    # Key conflict when reusing retry_key for order2 -> 409 IDEMPOTENCY_KEY_CONFLICT
    conflict_resp = client.post(
        "/api/v1/payments/intents",
        json={"order_number": o2_num},
        headers=_csrf_headers(session, extra={"Idempotency-Key": retry_key}),
    )
    assert conflict_resp.status_code == 409
    assert conflict_resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    # Succeeding retry intent confirms order1; subsequent /payments/intents -> 409
    succ_status, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=retry_pi["provider_intent_id"],
        order_number=o1_num,
        amount_cents=order1["order"]["total_cents"],
    )
    assert succ_status == 200
    paid_reject = client.post(
        "/api/v1/payments/intents",
        json={"order_number": o1_num},
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert paid_reject.status_code == 409
    assert paid_reject.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Cancelled & expired orders reject new payment intents with 409
    assert (
        client.post(
            f"/api/v1/orders/{o2_num}/cancel",
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )
    cancelled_reject = client.post(
        "/api/v1/payments/intents",
        json={"order_number": o2_num},
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert cancelled_reject.status_code == 409
    assert cancelled_reject.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    order3 = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    o3_res = db_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.order_id == uuid.UUID(order3["order"]["id"])
        )
    )
    assert o3_res is not None
    o3_res.expires_at = datetime.now(UTC) - timedelta(minutes=2)
    db_session.commit()
    expired_reject = client.post(
        "/api/v1/payments/intents",
        json={"order_number": order3["order"]["order_number"]},
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert expired_reject.status_code == 409
    assert expired_reject.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Guest payment intent creation requires valid guest token and succeeds for unpaid guest order
    client.cookies.clear()
    guest_order = _place_checkout_order(client, variant_id=var.id, quantity=1, session=None)
    guest_num = guest_order["order"]["order_number"]
    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/payments/intents",
            json={"order_number": guest_num},
            headers={
                "Origin": DEFAULT_ORIGIN,
                "Idempotency-Key": uuid.uuid4().hex,
                "X-Guest-Order-Token": "wrong-token",
            },
        ).status_code
        == 403
    )
    guest_pi = client.post(
        "/api/v1/payments/intents",
        json={"order_number": guest_num},
        headers={
            "Origin": DEFAULT_ORIGIN,
            "Idempotency-Key": uuid.uuid4().hex,
            "X-Guest-Order-Token": guest_order["guest_access_token"],
        },
    )
    assert guest_pi.status_code == 201
    assert guest_pi.json()["payment_intent"]["status"] == "REQUIRES_ACTION"


def test_webhook_strict_six_step_verification_and_dedup(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Webhook endpoint enforces strict 6-step verification order, dedup, and mismatch logging."""
    _, _, var, _ = _seed_variant(db_session, sku="WH-SEC", price_cents=4500, on_hand=10)
    session = login_as(client, role="customer")
    placed = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    order_num = placed["order"]["order_number"]
    pi_id = placed["payment_intent"]["provider_intent_id"]
    total_cents = placed["order"]["total_cents"]

    # Step 1a: Unknown provider -> 404 PROVIDER_NOT_FOUND
    unknown_prov = client.post("/api/v1/webhooks/payments/stripe", content=b"{}")
    assert unknown_prov.status_code == 404
    assert unknown_prov.json()["error"]["code"] == "PROVIDER_NOT_FOUND"

    # Step 1b: Body > 65536 bytes -> 413 PAYLOAD_TOO_LARGE (checked before timestamp/signature)
    oversized = b"x" * (MAX_WEBHOOK_BODY_BYTES + 1)
    too_large = client.post(f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}", content=oversized)
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    # Step 2: Missing, non-integer, or >300s skewed timestamp -> 400 WEBHOOK_TIMESTAMP_EXPIRED
    raw_ok, _ = _signed_webhook_request(
        event_type="payment_intent.succeeded",
        provider_intent_id=pi_id,
        order_number=order_num,
        amount_cents=total_cents,
    )
    for bad_ts_headers in (
        {"Content-Type": "application/json", "X-Webhook-Signature": "v1=deadbeef"},
        {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": "not-an-int",
            "X-Webhook-Signature": "v1=deadbeef",
        },
        {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": str(int(time.time()) - 305),
            "X-Webhook-Signature": "v1=deadbeef",
        },
        {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": str(int(time.time()) + 305),
            "X-Webhook-Signature": "v1=deadbeef",
        },
    ):
        ts_resp = client.post(
            f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
            content=raw_ok,
            headers=bad_ts_headers,
        )
        assert ts_resp.status_code == 400
        assert ts_resp.json()["error"]["code"] == "WEBHOOK_TIMESTAMP_EXPIRED"

    # Step 3: Invalid/missing signature (checked before JSON parsing!) -> 401
    now_ts = int(time.time())
    for bad_sig in (None, "deadbeef", f"v1={'0' * 64}"):
        sig_headers = {"X-Webhook-Timestamp": str(now_ts)}
        if bad_sig is not None:
            sig_headers["X-Webhook-Signature"] = bad_sig
        sig_resp = client.post(
            f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
            content=b"{malformed-json",
            headers=sig_headers,
        )
        assert sig_resp.status_code == 401
        assert sig_resp.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"

    # Step 4: Valid signature + malformed JSON / NaN / invalid schema -> 422 VALIDATION_ERROR
    for bad_body in (
        b"{not-valid-json",
        b'{"event_id":"e1","event_type":"payment_intent.succeeded","data":{"x":NaN}}',
        b'{"event_id":"e1","event_type":"unknown.event","data":{}}',
    ):
        bad_sig = sign_webhook_payload(now_ts, bad_body, TEST_WEBHOOK_SECRET)
        val_resp = client.post(
            f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
            content=bad_body,
            headers={"X-Webhook-Timestamp": str(now_ts), "X-Webhook-Signature": bad_sig},
        )
        assert val_resp.status_code == 422
        assert val_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # Step 6 mismatch cases: unknown intent / wrong order_number / wrong amount / wrong currency
    # -> persists PaymentWebhookEvent(status='FAILED') & returns 422 WEBHOOK_PAYLOAD_MISMATCH
    mismatch_cases = [
        ("evt_mm_intent", pi_id + "_missing", order_num, total_cents, "USD"),
        ("evt_mm_order", pi_id, "ORD-WRONG-999", total_cents, "USD"),
        ("evt_mm_amount", pi_id, order_num, total_cents + 1, "USD"),
        ("evt_mm_curr", pi_id, order_num, total_cents, "EUR"),
    ]
    for ev_id, target_pi, target_ord, target_amt, target_curr in mismatch_cases:
        mm_status, mm_body = _post_signed_webhook(
            client,
            event_id=ev_id,
            event_type="payment_intent.succeeded",
            provider_intent_id=target_pi,
            order_number=target_ord,
            amount_cents=target_amt,
            currency=target_curr,
        )
        assert mm_status == 422
        assert mm_body["error"]["code"] == "WEBHOOK_PAYLOAD_MISMATCH"
        db_session.expire_all()
        ev_row = db_session.scalar(
            select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider == SANDBOX_PROVIDER,
                PaymentWebhookEvent.provider_event_id == ev_id,
            )
        )
        assert ev_row is not None
        assert ev_row.status == "FAILED"
        assert ev_row.error_message is not None

    # Step 5: Valid webhook + duplicate (provider, provider_event_id) -> IGNORED_DUPLICATE
    dup_event_id = f"evt_dup_{uuid.uuid4().hex[:10]}"
    raw_valid, valid_headers = _signed_webhook_request(
        event_id=dup_event_id,
        event_type="payment_intent.succeeded",
        provider_intent_id=pi_id,
        order_number=order_num,
        amount_cents=total_cents,
    )
    first_ok = client.post(
        f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
        content=raw_valid,
        headers=valid_headers,
    )
    assert first_ok.status_code == 200
    assert first_ok.json() == {"received": True, "status": "PROCESSED"}

    dup_ok = client.post(
        f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
        content=raw_valid,
        headers=valid_headers,
    )
    assert dup_ok.status_code == 200
    assert dup_ok.json() == {"received": True, "status": "IGNORED_DUPLICATE"}

    db_session.expire_all()
    stock = db_session.get(InventoryStock, var.id)
    assert stock is not None and stock.on_hand == 9 and stock.reserved == 0


def test_webhook_state_transition_matrix(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Verify full webhook state transition matrix across normal, failed/retry, late, & refund."""
    _, _, var, stock = _seed_variant(db_session, sku="MTX-01", price_cents=5000, on_hand=10)
    session = login_as(client, role="customer", email="matrix@example.com")

    # 1. Normal payment_intent.succeeded -> CONFIRMED / PAID / ALLOCATED, COMMIT ledger
    o_norm = _place_checkout_order(client, variant_id=var.id, quantity=2, session=session)
    o_norm_id = uuid.UUID(o_norm["order"]["id"])
    st_norm, body_norm = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=o_norm["payment_intent"]["provider_intent_id"],
        order_number=o_norm["order"]["order_number"],
        amount_cents=o_norm["order"]["total_cents"],
    )
    assert st_norm == 200 and body_norm["status"] == "PROCESSED"
    db_session.expire_all()
    db_norm = db_session.get(Order, o_norm_id)
    assert db_norm is not None
    assert (db_norm.status, db_norm.payment_status, db_norm.fulfillment_status) == (
        "CONFIRMED",
        "PAID",
        "ALLOCATED",
    )
    assert db_norm.paid_at is not None
    res_norm = db_session.scalar(
        select(InventoryReservation).where(InventoryReservation.order_id == o_norm_id)
    )
    assert res_norm is not None and res_norm.status == "COMMITTED"
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (8, 0)
    commit_ledger = db_session.scalar(
        select(InventoryLedgerEntry).where(
            InventoryLedgerEntry.order_id == o_norm_id,
            InventoryLedgerEntry.reason == "COMMIT",
        )
    )
    assert commit_ledger is not None
    assert (commit_ledger.delta_on_hand, commit_ledger.delta_reserved) == (-2, -2)

    # 2. payment_intent.failed -> FAILED intent & payment_status, reservation stays ACTIVE;
    #    retry via POST /payments/intents + succeed + duplicate success on Intent 1 -> auto-refund
    o_retry = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    o_retry_id = uuid.UUID(o_retry["order"]["id"])
    pi1_id = o_retry["payment_intent"]["provider_intent_id"]
    st_fail, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.failed",
        provider_intent_id=pi1_id,
        order_number=o_retry["order"]["order_number"],
        amount_cents=o_retry["order"]["total_cents"],
        failure_code="do_not_honor",
        failure_message="Bank declined",
    )
    assert st_fail == 200
    db_session.expire_all()
    db_retry = db_session.get(Order, o_retry_id)
    assert db_retry is not None
    assert (db_retry.status, db_retry.payment_status) == ("PENDING_PAYMENT", "FAILED")
    res_retry = db_session.scalar(
        select(InventoryReservation).where(InventoryReservation.order_id == o_retry_id)
    )
    assert res_retry is not None and res_retry.status == "ACTIVE"

    retry_create = client.post(
        "/api/v1/payments/intents",
        json={"order_number": o_retry["order"]["order_number"]},
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert retry_create.status_code == 201
    pi2_id = retry_create.json()["payment_intent"]["provider_intent_id"]

    st_pi2_ok, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=pi2_id,
        order_number=o_retry["order"]["order_number"],
        amount_cents=o_retry["order"]["total_cents"],
    )
    assert st_pi2_ok == 200

    # Delayed success on pi1_id for already-PAID order -> pi1 REFUND_PENDING + RefundRequest,
    # then verified charge.refunded -> pi1 REFUNDED
    st_dup_intent, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=pi1_id,
        order_number=o_retry["order"]["order_number"],
        amount_cents=o_retry["order"]["total_cents"],
    )
    assert st_dup_intent == 200
    db_session.expire_all()
    db_pi1 = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == pi1_id)
    )
    assert db_pi1 is not None and db_pi1.status == "REFUND_PENDING"
    dup_req = db_session.scalar(
        select(PaymentRefundRequest).where(PaymentRefundRequest.payment_intent_id == db_pi1.id)
    )
    assert dup_req is not None
    assert dup_req.status == "PENDING"
    assert dup_req.trigger_source == "DUPLICATE_PAYMENT"
    dup_ev = db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == o_retry_id,
            OrderEvent.event_type == "DUPLICATE_PAYMENT_REFUND_PENDING",
        )
    )
    assert dup_ev is not None
    db_retry_after_dup = db_session.get(Order, o_retry_id)
    assert db_retry_after_dup is not None
    assert (db_retry_after_dup.status, db_retry_after_dup.payment_status) == ("CONFIRMED", "PAID")

    st_dup_chg_ref, _ = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=pi1_id,
        order_number=o_retry["order"]["order_number"],
        amount_cents=o_retry["order"]["total_cents"],
    )
    assert st_dup_chg_ref == 200
    db_session.expire_all()
    db_session.refresh(db_pi1)
    db_session.refresh(dup_req)
    assert db_pi1.status == "REFUNDED"
    assert dup_req.status == "SUCCEEDED"
    db_session.refresh(db_retry_after_dup)
    assert (db_retry_after_dup.status, db_retry_after_dup.payment_status) == ("CONFIRMED", "PAID")
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (7, 0)

    # 3. Late payment_intent.succeeded after RESERVATION_EXPIRED when stock IS available
    o_late_ok = _place_checkout_order(client, variant_id=var.id, quantity=2, session=session)
    o_late_ok_id = uuid.UUID(o_late_ok["order"]["id"])
    _expire_order_reservations(db_session, order_id=o_late_ok_id, variant_ids=[var.id])
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (7, 0)

    st_late_ok, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=o_late_ok["payment_intent"]["provider_intent_id"],
        order_number=o_late_ok["order"]["order_number"],
        amount_cents=o_late_ok["order"]["total_cents"],
    )
    assert st_late_ok == 200
    db_session.expire_all()
    db_late_ok = db_session.get(Order, o_late_ok_id)
    assert db_late_ok is not None
    assert (db_late_ok.status, db_late_ok.payment_status, db_late_ok.fulfillment_status) == (
        "CONFIRMED",
        "PAID",
        "ALLOCATED",
    )
    assert db_late_ok.cancelled_at is None
    assert db_late_ok.cancellation_reason is None
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (5, 0)
    late_commit = db_session.scalar(
        select(InventoryLedgerEntry).where(
            InventoryLedgerEntry.order_id == o_late_ok_id,
            InventoryLedgerEntry.reason == "COMMIT",
        )
    )
    assert late_commit is not None
    assert (late_commit.delta_on_hand, late_commit.delta_reserved) == (-2, 0)

    # 4. Late payment_intent.succeeded after RESERVATION_EXPIRED when OUT of stock ->
    #    CANCELLED + REFUND_PENDING (paid_at remains None), then charge.refunded -> REFUNDED
    o_late_oos = _place_checkout_order(client, variant_id=var.id, quantity=3, session=session)
    o_late_oos_id = uuid.UUID(o_late_oos["order"]["id"])
    _expire_order_reservations(db_session, order_id=o_late_oos_id, variant_ids=[var.id])
    # Reduce stock below 3 units before late webhook arrives
    stock.on_hand = 2
    db_session.commit()

    st_late_oos, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=o_late_oos["payment_intent"]["provider_intent_id"],
        order_number=o_late_oos["order"]["order_number"],
        amount_cents=o_late_oos["order"]["total_cents"],
    )
    assert st_late_oos == 200
    db_session.expire_all()
    db_late_oos = db_session.get(Order, o_late_oos_id)
    assert db_late_oos is not None
    assert (db_late_oos.status, db_late_oos.payment_status, db_late_oos.fulfillment_status) == (
        "CANCELLED",
        "REFUND_PENDING",
        "CANCELLED",
    )
    assert db_late_oos.paid_at is None
    oos_intent = db_session.get(PaymentIntent, uuid.UUID(o_late_oos["payment_intent"]["id"]))
    assert oos_intent is not None and oos_intent.status == "REFUND_PENDING"
    oos_req = db_session.scalar(
        select(PaymentRefundRequest).where(PaymentRefundRequest.payment_intent_id == oos_intent.id)
    )
    assert oos_req is not None
    assert oos_req.status == "PENDING"
    assert oos_req.trigger_source == "LATE_PAYMENT_OUT_OF_STOCK"
    assert (
        db_session.scalar(
            select(OrderEvent).where(
                OrderEvent.order_id == o_late_oos_id,
                OrderEvent.event_type == "PAYMENT_SUCCEEDED_OUT_OF_STOCK",
            )
        )
        is not None
    )
    st_oos_ref, _ = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=o_late_oos["payment_intent"]["provider_intent_id"],
        order_number=o_late_oos["order"]["order_number"],
        amount_cents=o_late_oos["order"]["total_cents"],
    )
    assert st_oos_ref == 200
    db_session.expire_all()
    db_session.refresh(db_late_oos)
    db_session.refresh(oos_intent)
    db_session.refresh(oos_req)
    assert (db_late_oos.status, db_late_oos.payment_status, db_late_oos.fulfillment_status) == (
        "CANCELLED",
        "REFUNDED",
        "CANCELLED",
    )
    assert db_late_oos.paid_at is None
    assert oos_intent.status == "REFUNDED"
    assert oos_req.status == "SUCCEEDED"
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (2, 0)

    # 5. Late payment_intent.succeeded after explicit RELEASED cancel ->
    #    CANCELLED + REFUND_PENDING (paid_at remains None), then charge.refunded -> REFUNDED
    o_explicit = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    o_explicit_id = uuid.UUID(o_explicit["order"]["id"])
    assert (
        client.post(
            f"/api/v1/orders/{o_explicit['order']['order_number']}/cancel",
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )
    st_after_cxl, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=o_explicit["payment_intent"]["provider_intent_id"],
        order_number=o_explicit["order"]["order_number"],
        amount_cents=o_explicit["order"]["total_cents"],
    )
    assert st_after_cxl == 200
    db_session.expire_all()
    db_explicit = db_session.get(Order, o_explicit_id)
    assert db_explicit is not None
    assert (db_explicit.status, db_explicit.payment_status, db_explicit.fulfillment_status) == (
        "CANCELLED",
        "REFUND_PENDING",
        "CANCELLED",
    )
    assert db_explicit.paid_at is None
    explicit_intent = db_session.get(PaymentIntent, uuid.UUID(o_explicit["payment_intent"]["id"]))
    assert explicit_intent is not None and explicit_intent.status == "REFUND_PENDING"
    explicit_req = db_session.scalar(
        select(PaymentRefundRequest).where(
            PaymentRefundRequest.payment_intent_id == explicit_intent.id
        )
    )
    assert explicit_req is not None
    assert explicit_req.status == "PENDING"
    assert explicit_req.trigger_source == "LATE_PAYMENT_AFTER_CANCEL"
    assert (
        db_session.scalar(
            select(OrderEvent).where(
                OrderEvent.order_id == o_explicit_id,
                OrderEvent.event_type == "PAYMENT_SUCCEEDED_AFTER_CANCEL",
            )
        )
        is not None
    )
    st_explicit_ref, _ = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=o_explicit["payment_intent"]["provider_intent_id"],
        order_number=o_explicit["order"]["order_number"],
        amount_cents=o_explicit["order"]["total_cents"],
    )
    assert st_explicit_ref == 200
    db_session.expire_all()
    db_session.refresh(db_explicit)
    db_session.refresh(explicit_intent)
    db_session.refresh(explicit_req)
    assert (db_explicit.status, db_explicit.payment_status, db_explicit.fulfillment_status) == (
        "CANCELLED",
        "REFUNDED",
        "CANCELLED",
    )
    assert db_explicit.paid_at is None
    assert explicit_intent.status == "REFUNDED"
    assert explicit_req.status == "SUCCEEDED"
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (2, 0)

    # 6. charge.refunded on CONFIRMED order -> REFUNDED + CANCELLED; on SHIPPED -> RETURNED
    st_chg_ref, _ = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=o_norm["payment_intent"]["provider_intent_id"],
        order_number=o_norm["order"]["order_number"],
        amount_cents=o_norm["order"]["total_cents"],
    )
    assert st_chg_ref == 200
    db_session.expire_all()
    db_norm_ref = db_session.get(Order, o_norm_id)
    assert db_norm_ref is not None
    assert (db_norm_ref.status, db_norm_ref.payment_status, db_norm_ref.fulfillment_status) == (
        "REFUNDED",
        "REFUNDED",
        "CANCELLED",
    )

    # Simulate SHIPPED order receiving charge.refunded -> fulfillment_status == RETURNED
    db_late_ok_ship = db_session.get(Order, o_late_ok_id)
    assert db_late_ok_ship is not None
    db_late_ok_ship.status = "SHIPPED"
    db_late_ok_ship.fulfillment_status = "SHIPPED"
    db_session.commit()
    st_ship_ref, _ = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=o_late_ok["payment_intent"]["provider_intent_id"],
        order_number=o_late_ok["order"]["order_number"],
        amount_cents=o_late_ok["order"]["total_cents"],
    )
    assert st_ship_ref == 200
    db_session.expire_all()
    reloaded_ship = db_session.get(Order, o_late_ok_id)
    assert reloaded_ship is not None
    assert (
        reloaded_ship.status,
        reloaded_ship.payment_status,
        reloaded_ship.fulfillment_status,
    ) == (
        "REFUNDED",
        "REFUNDED",
        "RETURNED",
    )


def test_sandbox_payment_simulation_mode_rbac_and_outcomes(
    db_session: Session,
    app_factory: Callable[..., FastAPI],
    test_settings: Settings,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Sandbox simulate checks SANDBOX_MODE_DISABLED, owner/guest/staff RBAC, & final states."""
    _, _, var, _ = _seed_variant(db_session, sku="SBX-01", price_cents=5000, on_hand=10)

    # 1. Disabled sandbox mode -> 403 SANDBOX_MODE_DISABLED
    disabled_settings = test_settings.model_copy(update={"payment_sandbox_mode": False})
    with TestClient(app_factory(disabled_settings)) as no_sbx_client:
        dis_resp = no_sbx_client.post(
            "/api/v1/payments/sandbox/pi_sandbox_missing/simulate",
            json={"outcome": "succeeded"},
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert dis_resp.status_code == 403
        assert dis_resp.json()["error"]["code"] == "SANDBOX_MODE_DISABLED"

    # 2. Enabled sandbox mode -> auth, outcomes, & repeat-simulate guards
    with TestClient(app_factory()) as sbx_client:
        owner_session = login_as(sbx_client, role="customer", email="sbx-owner@example.com")
        cust_order = _place_checkout_order(
            sbx_client, variant_id=var.id, quantity=1, session=owner_session
        )
        pi_cust = cust_order["payment_intent"]["provider_intent_id"]

        # Unknown / malformed provider_intent_id -> 404 PAYMENT_INTENT_NOT_FOUND
        for bad_pi in ("pi_bad%00id", "pi_sandbox_not_found"):
            nf = sbx_client.post(
                f"/api/v1/payments/sandbox/{bad_pi}/simulate",
                json={"outcome": "succeeded"},
                headers=_csrf_headers(owner_session),
            )
            assert nf.status_code == 404
            assert nf.json()["error"]["code"] == "PAYMENT_INTENT_NOT_FOUND"

        # Foreign customer -> 403 FORBIDDEN; missing CSRF -> 403 CSRF_INVALID
        other_cust = login_as(sbx_client, role="customer", email="sbx-other@example.com")
        assert (
            sbx_client.post(
                f"/api/v1/payments/sandbox/{pi_cust}/simulate",
                json={"outcome": "succeeded"},
                headers=_csrf_headers(other_cust),
            ).status_code
            == 403
        )
        assert (
            sbx_client.post(
                f"/api/v1/payments/sandbox/{pi_cust}/simulate",
                json={"outcome": "succeeded"},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 403
        )

        # Staff & admin CAN simulate on customer's order; let's test fail via staff, then retry
        staff_session = login_as(sbx_client, role="staff")
        sim_fail = sbx_client.post(
            f"/api/v1/payments/sandbox/{pi_cust}/simulate",
            json={"outcome": "failed", "failure_code": "card_declined"},
            headers=_csrf_headers(staff_session),
        )
        assert sim_fail.status_code == 200, sim_fail.text
        assert sim_fail.json()["webhook_status"] == "PROCESSED"
        assert sim_fail.json()["order_status"] == "PENDING_PAYMENT"
        assert sim_fail.json()["payment_status"] == "FAILED"

        # Repeat simulate on finalized FAILED intent -> 409 PAYMENT_INTENT_ALREADY_FINAL
        repeat_fail = sbx_client.post(
            f"/api/v1/payments/sandbox/{pi_cust}/simulate",
            json={"outcome": "succeeded"},
            headers=_csrf_headers(staff_session),
        )
        assert repeat_fail.status_code == 409
        assert repeat_fail.json()["error"]["code"] == "PAYMENT_INTENT_ALREADY_FINAL"

        # Owner creates retry intent and succeeds via sandbox simulate
        _use_auth_cookies(sbx_client, owner_session)
        retry_resp = sbx_client.post(
            "/api/v1/payments/intents",
            json={"order_number": cust_order["order"]["order_number"]},
            headers=_csrf_headers(owner_session, extra={"Idempotency-Key": uuid.uuid4().hex}),
        )
        assert retry_resp.status_code == 201
        pi_retry = retry_resp.json()["payment_intent"]["provider_intent_id"]

        sim_ok = sbx_client.post(
            f"/api/v1/payments/sandbox/{pi_retry}/simulate",
            json={"outcome": "succeeded"},
            headers=_csrf_headers(owner_session),
        )
        assert sim_ok.status_code == 200
        assert sim_ok.json()["order_status"] == "CONFIRMED"
        assert sim_ok.json()["payment_status"] == "PAID"

        # Guest order sandbox simulation via X-Guest-Order-Token and admin
        sbx_client.cookies.clear()
        guest_order = _place_checkout_order(sbx_client, variant_id=var.id, quantity=1, session=None)
        pi_guest = guest_order["payment_intent"]["provider_intent_id"]
        sbx_client.cookies.clear()
        assert (
            sbx_client.post(
                f"/api/v1/payments/sandbox/{pi_guest}/simulate",
                json={"outcome": "succeeded"},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 403
        )
        guest_sim = sbx_client.post(
            f"/api/v1/payments/sandbox/{pi_guest}/simulate",
            json={"outcome": "succeeded"},
            headers={
                "Origin": DEFAULT_ORIGIN,
                "X-Guest-Order-Token": guest_order["guest_access_token"],
            },
        )
        assert guest_sim.status_code == 200
        assert guest_sim.json()["order_status"] == "CONFIRMED"

        # Admin can also simulate a guest order
        sbx_client.cookies.clear()
        guest_order_2 = _place_checkout_order(
            sbx_client, variant_id=var.id, quantity=1, session=None
        )
        admin_session = login_as(sbx_client, role="admin")
        admin_sim = sbx_client.post(
            f"/api/v1/payments/sandbox/{guest_order_2['payment_intent']['provider_intent_id']}/simulate",
            json={"outcome": "succeeded"},
            headers=_csrf_headers(admin_session),
        )
        assert admin_sim.status_code == 200
        assert admin_sim.json()["payment_status"] == "PAID"


def test_webhook_charge_refunded_guards_non_winning_and_unpaid_intents(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """MEDIUM-1: charge.refunded on non-winning or unpaid intents never corrupts order state."""
    _, _, var, stock = _seed_variant(db_session, sku="MED1-REF", price_cents=5000, on_hand=10)
    session = login_as(client, role="customer", email="med1-owner@example.com")

    # 1. Order with pi_1 (duplicate-refunded) and winning pi_2 (SUCCEEDED)
    order_dto = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    order_id = uuid.UUID(order_dto["order"]["id"])
    order_num = str(order_dto["order"]["order_number"])
    total_cents = int(order_dto["order"]["total_cents"])
    pi_1_id = str(order_dto["payment_intent"]["provider_intent_id"])

    # Fail pi_1 -> create retry pi_2 -> succeed pi_2 -> duplicate success on pi_1
    assert (
        _post_signed_webhook(
            client,
            event_type="payment_intent.failed",
            provider_intent_id=pi_1_id,
            order_number=order_num,
            amount_cents=total_cents,
            failure_code="declined",
        )[0]
        == 200
    )
    retry_resp = client.post(
        "/api/v1/payments/intents",
        json={"order_number": order_num},
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert retry_resp.status_code == 201
    pi_2_id = str(retry_resp.json()["payment_intent"]["provider_intent_id"])

    assert (
        _post_signed_webhook(
            client,
            event_type="payment_intent.succeeded",
            provider_intent_id=pi_2_id,
            order_number=order_num,
            amount_cents=total_cents,
        )[0]
        == 200
    )
    assert (
        _post_signed_webhook(
            client,
            event_type="payment_intent.succeeded",
            provider_intent_id=pi_1_id,
            order_number=order_num,
            amount_cents=total_cents,
        )[0]
        == 200
    )

    # Subsequent charge.refunded for non-winning pi_1 leaves order CONFIRMED/PAID/ALLOCATED
    non_win_status, non_win_body = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=pi_1_id,
        order_number=order_num,
        amount_cents=total_cents,
    )
    assert non_win_status == 200
    assert non_win_body["status"] == "PROCESSED"

    db_session.expire_all()
    db_order = db_session.get(Order, order_id)
    assert db_order is not None
    assert (db_order.status, db_order.payment_status, db_order.fulfillment_status) == (
        "CONFIRMED",
        "PAID",
        "ALLOCATED",
    )
    pi_1 = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == pi_1_id)
    )
    pi_2 = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == pi_2_id)
    )
    assert pi_1 is not None and pi_1.status == "REFUNDED"
    assert pi_2 is not None and pi_2.status == "SUCCEEDED"
    intent_refunded_ev = db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == order_id,
            OrderEvent.event_type == "PAYMENT_INTENT_REFUNDED",
        )
    )
    assert intent_refunded_ev is not None
    assert intent_refunded_ev.from_status == "CONFIRMED"
    assert intent_refunded_ev.to_status == "CONFIRMED"
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (9, 0)

    # charge.refunded for winning SUCCEEDED pi_2 DOES refund the order
    win_status, win_body = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=pi_2_id,
        order_number=order_num,
        amount_cents=total_cents,
    )
    assert win_status == 200
    assert win_body["status"] == "PROCESSED"

    db_session.expire_all()
    db_order = db_session.get(Order, order_id)
    assert db_order is not None
    assert (db_order.status, db_order.payment_status, db_order.fulfillment_status) == (
        "REFUNDED",
        "REFUNDED",
        "CANCELLED",
    )
    pi_2 = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == pi_2_id)
    )
    assert pi_2 is not None and pi_2.status == "REFUNDED"
    assert (
        db_session.scalar(
            select(OrderEvent).where(
                OrderEvent.order_id == order_id,
                OrderEvent.event_type == "PAYMENT_REFUNDED",
            )
        )
        is not None
    )

    # 2. Out-of-order charge.refunded on unpaid PENDING_PAYMENT order (REQUIRES_ACTION)
    unpaid_dto = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    unpaid_id = uuid.UUID(unpaid_dto["order"]["id"])
    unpaid_pi_id = str(unpaid_dto["payment_intent"]["provider_intent_id"])

    unpaid_st, unpaid_body = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=unpaid_pi_id,
        order_number=str(unpaid_dto["order"]["order_number"]),
        amount_cents=int(unpaid_dto["order"]["total_cents"]),
    )
    assert unpaid_st == 200 and unpaid_body["status"] == "PROCESSED"

    db_session.expire_all()
    db_unpaid = db_session.get(Order, unpaid_id)
    assert db_unpaid is not None
    assert (db_unpaid.status, db_unpaid.payment_status, db_unpaid.fulfillment_status) == (
        "PENDING_PAYMENT",
        "REQUIRES_PAYMENT",
        "UNFULFILLED",
    )
    db_unpaid_pi = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == unpaid_pi_id)
    )
    assert db_unpaid_pi is not None and db_unpaid_pi.status == "REFUNDED"
    unpaid_ev = db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == unpaid_id,
            OrderEvent.event_type == "PAYMENT_INTENT_REFUNDED",
        )
    )
    assert unpaid_ev is not None
    assert unpaid_ev.from_status == "PENDING_PAYMENT"
    assert unpaid_ev.to_status == "PENDING_PAYMENT"
    unpaid_res = db_session.scalar(
        select(InventoryReservation).where(InventoryReservation.order_id == unpaid_id)
    )
    assert unpaid_res is not None and unpaid_res.status == "ACTIVE"
    db_session.refresh(stock)
    assert (stock.on_hand, stock.reserved) == (9, 1)


def test_customer_guest_order_and_simulation_probe_rate_limit(
    client: TestClient,
    db_session: Session,
    app: FastAPI,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """LOW-2: Authenticated customer order & simulation probing is rate-limited per user."""
    _, _, var, _ = _seed_variant(db_session, sku="LOW2-RL", price_cents=3500, on_hand=10)
    guest_order = _place_checkout_order(client, variant_id=var.id, quantity=1, session=None)
    guest_num = str(guest_order["order"]["order_number"])
    guest_pi_id = str(guest_order["payment_intent"]["provider_intent_id"])

    limiter = app.state.rate_limiter
    assert isinstance(limiter, SlidingWindowRateLimiter)

    try:
        cust_1 = login_as(client, role="customer", email="probe-c1@example.com")

        # 1. Orders lookup probe limit (30/60s under orders:guest_lookup:user:{user.id})
        for _ in range(29):
            nf = client.get("/api/v1/orders/ORD-PROBE-404")
            assert nf.status_code == 404
        forb = client.get(f"/api/v1/orders/{guest_num}")
        assert forb.status_code == 403

        limited_order_probe = client.get("/api/v1/orders/ORD-PROBE-404")
        assert limited_order_probe.status_code == 429
        assert limited_order_probe.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert int(limited_order_probe.headers["Retry-After"]) >= 1

        limited_guest_probe = client.get(f"/api/v1/orders/{guest_num}")
        assert limited_guest_probe.status_code == 429
        assert limited_guest_probe.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

        # 2. Payment simulation probe limit (30/60s under payments:guest_lookup:user:{user.id})
        for _ in range(29):
            sim_nf = client.post(
                "/api/v1/payments/sandbox/pi_sandbox_probe_404/simulate",
                json={"outcome": "succeeded"},
                headers=_csrf_headers(cust_1),
            )
            assert sim_nf.status_code == 404
        sim_forb = client.post(
            f"/api/v1/payments/sandbox/{guest_pi_id}/simulate",
            json={"outcome": "succeeded"},
            headers=_csrf_headers(cust_1),
        )
        assert sim_forb.status_code == 403

        limited_sim_probe = client.post(
            "/api/v1/payments/sandbox/pi_sandbox_probe_404/simulate",
            json={"outcome": "succeeded"},
            headers=_csrf_headers(cust_1),
        )
        assert limited_sim_probe.status_code == 429
        assert limited_sim_probe.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert int(limited_sim_probe.headers["Retry-After"]) >= 1

        # Distinct customer session has its own independent per-user quota
        cust_2 = login_as(client, role="customer", email="probe-c2@example.com")
        assert client.get("/api/v1/orders/ORD-PROBE-404").status_code == 404
        assert (
            client.post(
                "/api/v1/payments/sandbox/pi_sandbox_probe_404/simulate",
                json={"outcome": "succeeded"},
                headers=_csrf_headers(cust_2),
            ).status_code
            == 404
        )
    finally:
        limiter.reset()


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


def _seed_pg_user_and_cart(
    pg_app: FastAPI,
    pg_session_factory: sessionmaker[Session],
    *,
    email: str,
    password_hash: str,
    cart_items: list[tuple[uuid.UUID, int]],
) -> tuple[uuid.UUID, str, str, str]:
    with pg_session_factory() as db:
        user = User(
            email=email,
            password_hash=password_hash,
            full_name="PG Concurrency Buyer",
            role="customer",
            is_active=True,
        )
        db.add(user)
        db.flush()
        user_id = user.id
        db.commit()

    with TestClient(pg_app) as client:
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": DEFAULT_TEST_PASSWORD},
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert login_resp.status_code == 200, login_resp.text
        session_cookie = client.cookies.get(SESSION_COOKIE_NAME)
        csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
        csrf_token = str(login_resp.json()["csrf_token"])
        assert session_cookie is not None and csrf_cookie is not None

        for variant_id, qty in cart_items:
            add_resp = client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(variant_id), "quantity": qty},
                headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_token},
            )
            assert add_resp.status_code == 200, add_resp.text

    return user_id, session_cookie, csrf_cookie, csrf_token


def test_h02_late_payment_refund_pending_and_analytics_revenue_not_inflated(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """H-02: Late payments stay REFUND_PENDING/CANCELLED and never inflate analytics."""
    _, _, var, stock = _seed_variant(db_session, sku="H02-ANA", price_cents=10000, on_hand=10)
    customer = login_as(client, role="customer", email="h02-buyer@example.com")

    # 1. Legitimate paid order
    paid_order = _place_checkout_order(client, variant_id=var.id, quantity=1, session=customer)
    paid_total = paid_order["order"]["total_cents"]
    st_paid, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=paid_order["payment_intent"]["provider_intent_id"],
        order_number=paid_order["order"]["order_number"],
        amount_cents=paid_total,
    )
    assert st_paid == 200

    # 2. Explicitly cancelled order receiving late payment_intent.succeeded
    late_cancel_order = _place_checkout_order(
        client, variant_id=var.id, quantity=2, session=customer
    )
    assert (
        client.post(
            f"/api/v1/orders/{late_cancel_order['order']['order_number']}/cancel",
            headers=_csrf_headers(customer),
        ).status_code
        == 200
    )
    st_late_cxl, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=late_cancel_order["payment_intent"]["provider_intent_id"],
        order_number=late_cancel_order["order"]["order_number"],
        amount_cents=late_cancel_order["order"]["total_cents"],
    )
    assert st_late_cxl == 200

    # 3. Expired out-of-stock order receiving late payment_intent.succeeded
    late_oos_order = _place_checkout_order(client, variant_id=var.id, quantity=5, session=customer)
    late_oos_id = uuid.UUID(late_oos_order["order"]["id"])
    _expire_order_reservations(db_session, order_id=late_oos_id, variant_ids=[var.id])
    stock.on_hand = 1
    db_session.commit()
    st_late_oos, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=late_oos_order["payment_intent"]["provider_intent_id"],
        order_number=late_oos_order["order"]["order_number"],
        amount_cents=late_oos_order["order"]["total_cents"],
    )
    assert st_late_oos == 200

    db_session.expire_all()
    for late_id in (uuid.UUID(late_cancel_order["order"]["id"]), late_oos_id):
        db_late = db_session.get(Order, late_id)
        assert db_late is not None
        assert db_late.status == "CANCELLED"
        assert db_late.payment_status == "REFUND_PENDING"
        assert db_late.paid_at is None

    # Analytics summary BEFORE charge.refunded: only paid_order counts
    _use_auth_cookies(client, login_as(client, role="admin", email="h02-admin@example.com"))
    today_str = datetime.now(UTC).date().isoformat()
    sum_before = client.get(
        "/api/v1/admin/analytics/summary",
        params={"from": today_str, "to": today_str},
    )
    assert sum_before.status_code == 200, sum_before.text
    assert sum_before.json()["gross_revenue_cents"] == paid_total
    assert sum_before.json()["refunded_revenue_cents"] == 0
    assert sum_before.json()["net_revenue_cents"] == paid_total
    assert sum_before.json()["paid_orders_count"] == 1

    # Settle both pending refunds via charge.refunded -> analytics STILL must not be inflated
    for late_order_data in (late_cancel_order, late_oos_order):
        st_ref, _ = _post_signed_webhook(
            client,
            event_type="charge.refunded",
            provider_intent_id=late_order_data["payment_intent"]["provider_intent_id"],
            order_number=late_order_data["order"]["order_number"],
            amount_cents=late_order_data["order"]["total_cents"],
        )
        assert st_ref == 200

    sum_after = client.get(
        "/api/v1/admin/analytics/summary",
        params={"from": today_str, "to": today_str},
    )
    assert sum_after.status_code == 200
    assert sum_after.json()["gross_revenue_cents"] == paid_total
    assert sum_after.json()["refunded_revenue_cents"] == 0
    assert sum_after.json()["net_revenue_cents"] == paid_total
    assert sum_after.json()["paid_orders_count"] == 1


def test_h03_webhook_failed_event_retry_and_in_progress_409(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """H-03: FAILED webhook retries with same event_id; PROCESSING -> 409; PROCESSED ignores."""
    _, _, var, _ = _seed_variant(db_session, sku="H03-WH", price_cents=4000, on_hand=10)
    session = login_as(client, role="customer", email="h03-buyer@example.com")
    placed = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    order_id = uuid.UUID(placed["order"]["id"])
    order_number = placed["order"]["order_number"]
    total_cents = placed["order"]["total_cents"]
    real_pi_id = placed["payment_intent"]["provider_intent_id"]

    # Temporarily rename provider_intent_id to simulate out-of-order delivery before intent exists
    db_pi = db_session.get(PaymentIntent, uuid.UUID(placed["payment_intent"]["id"]))
    assert db_pi is not None
    db_pi.provider_intent_id = f"pi_temp_{uuid.uuid4().hex[:12]}"
    db_session.commit()

    shared_event_id = f"evt_h03_retry_{uuid.uuid4().hex[:10]}"
    raw_body, headers = _signed_webhook_request(
        event_id=shared_event_id,
        event_type="payment_intent.succeeded",
        provider_intent_id=real_pi_id,
        order_number=order_number,
        amount_cents=total_cents,
    )

    first_attempt = client.post(
        f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
        content=raw_body,
        headers=headers,
    )
    assert first_attempt.status_code == 422, first_attempt.text
    assert first_attempt.json()["error"]["code"] == "WEBHOOK_PAYLOAD_MISMATCH"

    db_session.expire_all()
    wh_row = db_session.scalar(
        select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.provider == SANDBOX_PROVIDER,
            PaymentWebhookEvent.provider_event_id == shared_event_id,
        )
    )
    assert wh_row is not None
    assert wh_row.status == "FAILED"
    assert wh_row.error_message is not None and real_pi_id in wh_row.error_message

    # Restore intent ID (simulating intent commit finishing) and retry with exact same event_id
    db_pi.provider_intent_id = real_pi_id
    db_session.commit()

    retry_attempt = client.post(
        f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
        content=raw_body,
        headers=headers,
    )
    assert retry_attempt.status_code == 200, retry_attempt.text
    assert retry_attempt.json() == {"received": True, "status": "PROCESSED"}

    db_session.expire_all()
    db_session.refresh(wh_row)
    assert wh_row.status == "PROCESSED"
    assert wh_row.error_message is None
    reloaded_order = db_session.get(Order, order_id)
    assert reloaded_order is not None and reloaded_order.payment_status == "PAID"

    # Subsequent delivery of PROCESSED event_id returns IGNORED_DUPLICATE
    dup_attempt = client.post(
        f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
        content=raw_body,
        headers=headers,
    )
    assert dup_attempt.status_code == 200
    assert dup_attempt.json() == {"received": True, "status": "IGNORED_DUPLICATE"}

    # An event currently in PROCESSING returns 409 WEBHOOK_IN_PROGRESS
    in_prog_event_id = f"evt_h03_prog_{uuid.uuid4().hex[:10]}"
    db_session.add(
        PaymentWebhookEvent(
            provider=SANDBOX_PROVIDER,
            provider_event_id=in_prog_event_id,
            event_type="payment_intent.succeeded",
            signature_verified=True,
            payload_json={},
            status="PROCESSING",
        )
    )
    db_session.commit()
    prog_raw, prog_hdrs = _signed_webhook_request(
        event_id=in_prog_event_id,
        event_type="payment_intent.succeeded",
        provider_intent_id=real_pi_id,
        order_number=order_number,
        amount_cents=total_cents,
    )
    in_prog_resp = client.post(
        f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
        content=prog_raw,
        headers=prog_hdrs,
    )
    assert in_prog_resp.status_code == 409
    assert in_prog_resp.json()["error"]["code"] == "WEBHOOK_IN_PROGRESS"


def test_m02_stale_cancelled_intent_capture_and_sibling_cancellation(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """M-02: Cancelled intent cannot revive on late success/fail; winning success cancels sibs."""
    _, _, var, _ = _seed_variant(db_session, sku="M02-STALE", price_cents=6000, on_hand=10)
    session = login_as(client, role="customer", email="m02-buyer@example.com")
    placed = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    order_id = uuid.UUID(placed["order"]["id"])
    order_number = placed["order"]["order_number"]
    total_cents = placed["order"]["total_cents"]
    pi1_id = placed["payment_intent"]["provider_intent_id"]

    # Create pi2 -> supersedes/cancels pi1 to CANCELLED
    pi2_resp = client.post(
        "/api/v1/payments/intents",
        json={"order_number": order_number},
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert pi2_resp.status_code == 201
    pi2_id = pi2_resp.json()["payment_intent"]["provider_intent_id"]

    # Seed an extra open PROCESSING sibling pi3 to verify payment_intent.succeeded cancels it too
    pi3 = PaymentIntent(
        order_id=order_id,
        provider=SANDBOX_PROVIDER,
        provider_intent_id=f"pi_sandbox_sib_{uuid.uuid4().hex[:12]}",
        idempotency_key=uuid.uuid4().hex.ljust(64, "a")[:64],
        request_fingerprint=uuid.uuid4().hex.ljust(64, "b")[:64],
        amount_cents=total_cents,
        currency="USD",
        status="PROCESSING",
    )
    db_session.add(pi3)
    db_session.commit()

    # 1. payment_intent.failed on CANCELLED pi1 must NOT overwrite order.payment_status to FAILED
    st_stale_fail, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.failed",
        provider_intent_id=pi1_id,
        order_number=order_number,
        amount_cents=total_cents,
        failure_code="stale_fail",
        failure_message="Delayed failure",
    )
    assert st_stale_fail == 200
    db_session.expire_all()
    db_order = db_session.get(Order, order_id)
    db_pi1 = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == pi1_id)
    )
    assert db_order is not None and db_order.payment_status == "REQUIRES_PAYMENT"
    assert db_pi1 is not None and db_pi1.status == "CANCELLED"

    # 2. payment_intent.succeeded on CANCELLED pi1 must NOT revive pi1 or mark order PAID
    st_stale_ok, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=pi1_id,
        order_number=order_number,
        amount_cents=total_cents,
    )
    assert st_stale_ok == 200
    db_session.expire_all()
    db_session.refresh(db_order)
    db_session.refresh(db_pi1)
    assert (db_order.status, db_order.payment_status) == ("PENDING_PAYMENT", "REQUIRES_PAYMENT")
    assert db_pi1.status == "REFUND_PENDING"
    stale_req = db_session.scalar(
        select(PaymentRefundRequest).where(PaymentRefundRequest.payment_intent_id == db_pi1.id)
    )
    assert stale_req is not None
    assert stale_req.status == "PENDING"
    assert stale_req.trigger_source == "STALE_INTENT_CAPTURE"
    assert (
        db_session.scalar(
            select(OrderEvent).where(
                OrderEvent.order_id == order_id,
                OrderEvent.event_type == "STALE_INTENT_PAYMENT_REFUND_PENDING",
            )
        )
        is not None
    )

    # 3. Winning payment_intent.succeeded on pi2 marks order PAID and cancels open sibling pi3
    st_pi2_ok, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=pi2_id,
        order_number=order_number,
        amount_cents=total_cents,
    )
    assert st_pi2_ok == 200
    db_session.expire_all()
    db_session.refresh(db_order)
    db_session.refresh(pi3)
    assert (db_order.status, db_order.payment_status) == ("CONFIRMED", "PAID")
    assert pi3.status == "CANCELLED"


def test_sec_rem_p1_01_persist_webhook_unexpected_failure_preserves_terminal_or_active(
    db_session: Session,
) -> None:
    """SEC-REM-P1-01: _persist_webhook_unexpected_failure never downgrades PROCESSED/PROCESSING."""
    for initial_status in ("PROCESSED", "PROCESSING"):
        event_id = f"evt_sec01_{initial_status.lower()}_{uuid.uuid4().hex[:8]}"
        row = PaymentWebhookEvent(
            provider=SANDBOX_PROVIDER,
            provider_event_id=event_id,
            event_type="payment_intent.succeeded",
            signature_verified=True,
            payload_json={"event_id": event_id},
            status=initial_status,
            processed_at=datetime.now(UTC) if initial_status == "PROCESSED" else None,
            error_message=None,
        )
        db_session.add(row)
        db_session.commit()

        payload = WebhookPayload(
            event_id=event_id,
            event_type="payment_intent.succeeded",
            data=WebhookEventData(
                provider_intent_id="pi_sec_01",
                order_number="ORD-SEC-01",
                amount_cents=1000,
                currency="USD",
            ),
        )
        # Exercise both lookup by row.id and fallback lookup by (provider, provider_event_id)
        for lookup_id in (row.id, uuid.uuid4()):
            _persist_webhook_unexpected_failure(
                db_session,
                webhook_event_id=lookup_id,
                provider=SANDBOX_PROVIDER,
                payload=payload,
                error_message="Unexpected transient error",
                now=datetime.now(UTC),
            )
            db_session.expire_all()
            reloaded = db_session.get(PaymentWebhookEvent, row.id)
            assert reloaded is not None
            assert reloaded.status == initial_status
            assert reloaded.error_message is None


def test_sec_rem_p1_02_charge_refunded_does_not_close_order_while_sibling_refund_pending(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """SEC-REM-P1-02: charge.refunded keeps order open while a sibling intent is REFUND_PENDING."""
    _, _, var, _ = _seed_variant(db_session, sku="SEC02-SIB", price_cents=5000, on_hand=10)
    session = login_as(client, role="customer", email="sec02-buyer@example.com")
    placed = _place_checkout_order(client, variant_id=var.id, quantity=1, session=session)
    order_id = uuid.UUID(placed["order"]["id"])
    order_number = placed["order"]["order_number"]
    total_cents = placed["order"]["total_cents"]
    pi1_id = placed["payment_intent"]["provider_intent_id"]

    # Fail pi1 -> retry with pi2 -> win on pi2 -> duplicate success on pi1 (REFUND_PENDING)
    assert (
        _post_signed_webhook(
            client,
            event_type="payment_intent.failed",
            provider_intent_id=pi1_id,
            order_number=order_number,
            amount_cents=total_cents,
        )[0]
        == 200
    )
    pi2_resp = client.post(
        "/api/v1/payments/intents",
        json={"order_number": order_number},
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert pi2_resp.status_code == 201
    pi2_id = pi2_resp.json()["payment_intent"]["provider_intent_id"]

    assert (
        _post_signed_webhook(
            client,
            event_type="payment_intent.succeeded",
            provider_intent_id=pi2_id,
            order_number=order_number,
            amount_cents=total_cents,
        )[0]
        == 200
    )
    assert (
        _post_signed_webhook(
            client,
            event_type="payment_intent.succeeded",
            provider_intent_id=pi1_id,
            order_number=order_number,
            amount_cents=total_cents,
        )[0]
        == 200
    )

    # Now pi2 receives charge.refunded while duplicate sibling pi1 is still REFUND_PENDING
    st_win_ref, _ = _post_signed_webhook(
        client,
        event_type="charge.refunded",
        provider_intent_id=pi2_id,
        order_number=order_number,
        amount_cents=total_cents,
    )
    assert st_win_ref == 200

    db_session.expire_all()
    db_order = db_session.get(Order, order_id)
    db_pi1 = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == pi1_id)
    )
    db_pi2 = db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.provider_intent_id == pi2_id)
    )
    assert db_order is not None
    assert db_order.status == "CONFIRMED"
    assert db_order.payment_status == "PAID"
    assert db_pi1 is not None and db_pi1.status == "REFUND_PENDING"
    assert db_pi2 is not None and db_pi2.status == "REFUNDED"
    assert (
        db_session.scalar(
            select(OrderEvent).where(
                OrderEvent.order_id == order_id,
                OrderEvent.event_type == "PAYMENT_INTENT_REFUNDED",
            )
        )
        is not None
    )


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Live PostgreSQL TEST_DATABASE_URL not set",
)
def test_live_postgresql_be003_checkout_and_webhook_concurrency() -> None:
    """Verify PostgreSQL last-unit race, deadlock freedom, advisory idempotency, & webhook race."""
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
    pw_hash = hash_password(DEFAULT_TEST_PASSWORD)

    try:
        with pg_session_factory() as setup_db:
            cat = Category(
                name="PG BE003 Cat",
                slug=f"pg-be003-cat-{uuid.uuid4().hex[:6]}",
                is_active=True,
            )
            setup_db.add(cat)
            setup_db.flush()
            prod = Product(
                category_id=cat.id,
                title="PG Concurrency Tee",
                slug=f"pg-be003-prod-{uuid.uuid4().hex[:6]}",
                description="PG concurrency test",
                status="ACTIVE",
            )
            setup_db.add(prod)
            setup_db.flush()

            def _make_pg_variant(sku_prefix: str, on_hand: int, price_cents: int) -> ProductVariant:
                var = ProductVariant(
                    product_id=prod.id,
                    sku=f"{sku_prefix}-{uuid.uuid4().hex[:6]}",
                    title=f"{sku_prefix} Variant",
                    attributes_json={"race": sku_prefix},
                    price_cents=price_cents,
                    currency="USD",
                    is_active=True,
                )
                setup_db.add(var)
                setup_db.flush()
                setup_db.add(
                    InventoryStock(
                        variant_id=var.id,
                        on_hand=on_hand,
                        reserved=0,
                        low_stock_threshold=1,
                        version=1,
                    )
                )
                return var

            v_last = _make_pg_variant("PG-LAST", on_hand=1, price_cents=5000)
            v_a = _make_pg_variant("PG-VA", on_hand=20, price_cents=3000)
            v_b = _make_pg_variant("PG-VB", on_hand=20, price_cents=4000)
            v_idem = _make_pg_variant("PG-IDEM", on_hand=10, price_cents=4500)
            setup_db.commit()

        # ------------------------------------------------------------------
        # 1. Last-unit overselling race: 6 concurrent users vs on_hand=1
        # ------------------------------------------------------------------
        last_users = [
            _seed_pg_user_and_cart(
                pg_app,
                pg_session_factory,
                email=f"last-{idx}-{uuid.uuid4().hex[:6]}@example.com",
                password_hash=pw_hash,
                cart_items=[(v_last.id, 1)],
            )
            for idx in range(6)
        ]
        pg_app.state.rate_limiter.reset()
        last_barrier = threading.Barrier(6)

        def _last_unit_worker(
            creds: tuple[uuid.UUID, str, str, str],
        ) -> tuple[int, dict[str, Any]]:
            _uid, sess_cookie, csrf_cookie, csrf_tok = creds
            with TestClient(pg_app) as w_client:
                w_client.cookies.set(SESSION_COOKIE_NAME, sess_cookie)
                w_client.cookies.set(CSRF_COOKIE_NAME, csrf_cookie)
                last_barrier.wait(timeout=15)
                resp = w_client.post(
                    "/api/v1/checkout/orders",
                    json=_checkout_payload(email="race-last@example.com"),
                    headers={
                        "Origin": DEFAULT_ORIGIN,
                        "X-CSRF-Token": csrf_tok,
                        "Idempotency-Key": uuid.uuid4().hex,
                    },
                )
                return int(resp.status_code), dict(resp.json())

        with ThreadPoolExecutor(max_workers=6) as pool:
            last_results = [
                f.result(timeout=20)
                for f in [pool.submit(_last_unit_worker, creds) for creds in last_users]
            ]

        last_statuses = [st for st, _ in last_results]
        assert last_statuses.count(201) == 1, last_results
        assert last_statuses.count(409) == 5, last_results
        for st, body in last_results:
            if st == 409:
                assert body["error"]["code"] == "INSUFFICIENT_STOCK"

        with pg_session_factory() as verify_db:
            st_last = verify_db.get(InventoryStock, v_last.id)
            assert st_last is not None
            assert st_last.on_hand == 1
            assert st_last.reserved == 1
            assert st_last.version == 2
            res_rows = list(
                verify_db.scalars(
                    select(InventoryReservation).where(InventoryReservation.variant_id == v_last.id)
                ).all()
            )
            assert len(res_rows) == 1 and res_rows[0].status == "ACTIVE"

        # ------------------------------------------------------------------
        # 2. Inverse-order multi-SKU checkout deadlock test (4 x AB + 4 x BA)
        # ------------------------------------------------------------------
        ab_users = [
            _seed_pg_user_and_cart(
                pg_app,
                pg_session_factory,
                email=f"ab-{idx}-{uuid.uuid4().hex[:6]}@example.com",
                password_hash=pw_hash,
                cart_items=[(v_a.id, 2), (v_b.id, 1)],
            )
            for idx in range(4)
        ]
        ba_users = [
            _seed_pg_user_and_cart(
                pg_app,
                pg_session_factory,
                email=f"ba-{idx}-{uuid.uuid4().hex[:6]}@example.com",
                password_hash=pw_hash,
                cart_items=[(v_b.id, 1), (v_a.id, 2)],
            )
            for idx in range(4)
        ]
        pg_app.state.rate_limiter.reset()
        deadlock_barrier = threading.Barrier(8)

        def _multi_sku_worker(creds: tuple[uuid.UUID, str, str, str]) -> int:
            _uid, sess_cookie, csrf_cookie, csrf_tok = creds
            with TestClient(pg_app) as w_client:
                w_client.cookies.set(SESSION_COOKIE_NAME, sess_cookie)
                w_client.cookies.set(CSRF_COOKIE_NAME, csrf_cookie)
                deadlock_barrier.wait(timeout=15)
                resp = w_client.post(
                    "/api/v1/checkout/orders",
                    json=_checkout_payload(email="multi-sku@example.com"),
                    headers={
                        "Origin": DEFAULT_ORIGIN,
                        "X-CSRF-Token": csrf_tok,
                        "Idempotency-Key": uuid.uuid4().hex,
                    },
                )
                return int(resp.status_code)

        with ThreadPoolExecutor(max_workers=8) as pool:
            multi_statuses = [
                f.result(timeout=20)
                for f in [pool.submit(_multi_sku_worker, creds) for creds in (ab_users + ba_users)]
            ]

        assert multi_statuses == [201] * 8

        with pg_session_factory() as verify_db:
            st_a = verify_db.get(InventoryStock, v_a.id)
            st_b = verify_db.get(InventoryStock, v_b.id)
            assert st_a is not None and st_a.reserved == 16 and st_a.on_hand == 20
            assert st_b is not None and st_b.reserved == 8 and st_b.on_hand == 20

        # ------------------------------------------------------------------
        # 3. Concurrent identical Idempotency-Key checkout race (pg_advisory_xact_lock)
        # ------------------------------------------------------------------
        idem_user_id, idem_sess, idem_csrf_cookie, idem_csrf_tok = _seed_pg_user_and_cart(
            pg_app,
            pg_session_factory,
            email=f"idem-{uuid.uuid4().hex[:6]}@example.com",
            password_hash=pw_hash,
            cart_items=[(v_idem.id, 3)],
        )
        pg_app.state.rate_limiter.reset()
        shared_idem_key = f"pg-idem-{uuid.uuid4().hex[:12]}"
        idem_payload = _checkout_payload(email="idem-buyer@example.com")
        idem_barrier = threading.Barrier(4)

        def _idem_checkout_worker() -> tuple[int, dict[str, Any]]:
            with TestClient(pg_app) as w_client:
                w_client.cookies.set(SESSION_COOKIE_NAME, idem_sess)
                w_client.cookies.set(CSRF_COOKIE_NAME, idem_csrf_cookie)
                idem_barrier.wait(timeout=15)
                resp = w_client.post(
                    "/api/v1/checkout/orders",
                    json=idem_payload,
                    headers={
                        "Origin": DEFAULT_ORIGIN,
                        "X-CSRF-Token": idem_csrf_tok,
                        "Idempotency-Key": shared_idem_key,
                    },
                )
                return int(resp.status_code), dict(resp.json())

        with ThreadPoolExecutor(max_workers=4) as pool:
            idem_results = [
                f.result(timeout=20)
                for f in [pool.submit(_idem_checkout_worker) for _ in range(4)]
            ]

        idem_statuses = sorted(st for st, _ in idem_results)
        assert idem_statuses == [200, 200, 200, 201], idem_results
        order_ids = {body["order"]["id"] for _, body in idem_results}
        order_numbers = {body["order"]["order_number"] for _, body in idem_results}
        intent_ids = {body["payment_intent"]["provider_intent_id"] for _, body in idem_results}
        assert len(order_ids) == 1
        assert len(order_numbers) == 1
        assert len(intent_ids) == 1

        target_order_id = uuid.UUID(next(iter(order_ids)))
        target_order_number = next(iter(order_numbers))
        target_pi_id = next(iter(intent_ids))
        target_total_cents = int(idem_results[0][1]["order"]["total_cents"])

        with pg_session_factory() as verify_db:
            user_orders = list(
                verify_db.scalars(select(Order).where(Order.user_id == idem_user_id)).all()
            )
            assert len(user_orders) == 1
            st_idem = verify_db.get(InventoryStock, v_idem.id)
            assert st_idem is not None and st_idem.on_hand == 10 and st_idem.reserved == 3
            reserve_entries = list(
                verify_db.scalars(
                    select(InventoryLedgerEntry).where(
                        InventoryLedgerEntry.order_id == target_order_id,
                        InventoryLedgerEntry.reason == "RESERVE",
                    )
                ).all()
            )
            assert len(reserve_entries) == 1

        # ------------------------------------------------------------------
        # 4. Concurrent duplicate payment_intent.succeeded webhook race
        # ------------------------------------------------------------------
        shared_event_id = f"evt_pg_race_{uuid.uuid4().hex[:12]}"
        wh_raw_body, wh_headers = _signed_webhook_request(
            event_id=shared_event_id,
            event_type="payment_intent.succeeded",
            provider_intent_id=target_pi_id,
            order_number=target_order_number,
            amount_cents=target_total_cents,
        )
        wh_barrier = threading.Barrier(4)

        def _webhook_worker() -> tuple[int, dict[str, Any]]:
            with TestClient(pg_app) as w_client:
                wh_barrier.wait(timeout=15)
                resp = w_client.post(
                    f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
                    content=wh_raw_body,
                    headers=wh_headers,
                )
                return int(resp.status_code), dict(resp.json())

        with ThreadPoolExecutor(max_workers=4) as pool:
            wh_results = [
                f.result(timeout=20)
                for f in [pool.submit(_webhook_worker) for _ in range(4)]
            ]

        assert all(st == 200 for st, _ in wh_results), wh_results
        wh_statuses = [body["status"] for _, body in wh_results]
        assert wh_statuses.count("PROCESSED") == 1, wh_results
        assert wh_statuses.count("IGNORED_DUPLICATE") == 3, wh_results

        with pg_session_factory() as verify_db:
            final_order = verify_db.get(Order, target_order_id)
            assert final_order is not None
            assert final_order.status == "CONFIRMED"
            assert final_order.payment_status == "PAID"
            assert final_order.fulfillment_status == "ALLOCATED"

            final_stock = verify_db.get(InventoryStock, v_idem.id)
            assert final_stock is not None
            assert final_stock.on_hand == 7
            assert final_stock.reserved == 0

            commit_entries = list(
                verify_db.scalars(
                    select(InventoryLedgerEntry).where(
                        InventoryLedgerEntry.order_id == target_order_id,
                        InventoryLedgerEntry.reason == "COMMIT",
                    )
                ).all()
            )
            assert len(commit_entries) == 1

            wh_rows = list(
                verify_db.scalars(
                    select(PaymentWebhookEvent).where(
                        PaymentWebhookEvent.provider == SANDBOX_PROVIDER,
                        PaymentWebhookEvent.provider_event_id == shared_event_id,
                    )
                ).all()
            )
            assert len(wh_rows) == 1 and wh_rows[0].status == "PROCESSED"

        # ------------------------------------------------------------------
        # 5. H-06: 6 concurrent guest checkouts from same IP -> strictly 3x 201 + 3x 429
        # ------------------------------------------------------------------
        v_gcap = None
        with pg_session_factory() as setup_db:
            prod_gcap = setup_db.scalar(select(Product).limit(1))
            assert prod_gcap is not None
            v_gcap = ProductVariant(
                product_id=prod_gcap.id,
                sku=f"PG-GCAP-{uuid.uuid4().hex[:6]}",
                title="PG Guest Cap Variant",
                attributes_json={"cap": "guest"},
                price_cents=2500,
                currency="USD",
                is_active=True,
            )
            setup_db.add(v_gcap)
            setup_db.flush()
            setup_db.add(
                InventoryStock(
                    variant_id=v_gcap.id,
                    on_hand=30,
                    reserved=0,
                    low_stock_threshold=2,
                    version=1,
                )
            )
            setup_db.commit()

        guest_cart_tokens: list[str] = []
        for _ in range(6):
            with TestClient(pg_app) as gc_setup:
                add_r = gc_setup.post(
                    "/api/v1/cart/items",
                    json={"variant_id": str(v_gcap.id), "quantity": 1},
                    headers={"Origin": DEFAULT_ORIGIN},
                )
                assert add_r.status_code == 200, add_r.text
                tok = gc_setup.cookies.get(GUEST_CART_COOKIE_NAME)
                assert tok is not None
                guest_cart_tokens.append(tok)

        pg_app.state.rate_limiter.reset()
        pg_app.state.guest_pending_tracker.reset()
        gcap_barrier = threading.Barrier(6)

        def _guest_cap_worker(idx_and_tok: tuple[int, str]) -> tuple[int, dict[str, Any]]:
            idx, cart_tok = idx_and_tok
            with TestClient(pg_app) as w_client:
                w_client.cookies.set(GUEST_CART_COOKIE_NAME, cart_tok)
                gcap_barrier.wait(timeout=15)
                resp = w_client.post(
                    "/api/v1/checkout/orders",
                    json=_checkout_payload(email=f"pg-gcap-{idx}@example.com"),
                    headers={
                        "Origin": DEFAULT_ORIGIN,
                        "Idempotency-Key": uuid.uuid4().hex,
                    },
                )
                return int(resp.status_code), dict(resp.json())

        with ThreadPoolExecutor(max_workers=6) as pool:
            gcap_results = [
                f.result(timeout=20)
                for f in [
                    pool.submit(_guest_cap_worker, (idx, tok))
                    for idx, tok in enumerate(guest_cart_tokens)
                ]
            ]

        gcap_statuses = [st for st, _ in gcap_results]
        assert gcap_statuses.count(201) == 3, gcap_results
        assert gcap_statuses.count(429) == 3, gcap_results
        for st, body in gcap_results:
            if st == 429:
                assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"

        with pg_session_factory() as verify_db:
            from app.core.security import hash_token

            expected_ip_hash = hash_token("guest_ip:testclient")
            gcap_orders = list(
                verify_db.scalars(
                    select(Order).where(
                        Order.guest_client_key_hash == expected_ip_hash,
                        Order.status == "PENDING_PAYMENT",
                    )
                ).all()
            )
            assert len(gcap_orders) == 3

        # ------------------------------------------------------------------
        # 6. M-06: Concurrent expired-reservation reclaim vs payment_intent.succeeded webhook
        # ------------------------------------------------------------------
        m06_user = _seed_pg_user_and_cart(
            pg_app,
            pg_session_factory,
            email=f"m06-{uuid.uuid4().hex[:6]}@example.com",
            password_hash=pw_hash,
            cart_items=[(v_gcap.id, 1)],
        )
        with TestClient(pg_app) as m06_client:
            m06_client.cookies.set(SESSION_COOKIE_NAME, m06_user[1])
            m06_client.cookies.set(CSRF_COOKIE_NAME, m06_user[2])
            m06_co = m06_client.post(
                "/api/v1/checkout/orders",
                json=_checkout_payload(email="m06-race@example.com"),
                headers={
                    "Origin": DEFAULT_ORIGIN,
                    "X-CSRF-Token": m06_user[3],
                    "Idempotency-Key": uuid.uuid4().hex,
                },
            )
            assert m06_co.status_code == 201, m06_co.text
            m06_body = m06_co.json()
            m06_order_id = uuid.UUID(m06_body["order"]["id"])

        with pg_session_factory() as exp_db:
            m06_res = exp_db.scalar(
                select(InventoryReservation).where(
                    InventoryReservation.order_id == m06_order_id
                )
            )
            assert m06_res is not None
            m06_res.expires_at = datetime.now(UTC) - timedelta(minutes=10)
            exp_db.commit()

        m06_raw, m06_hdrs = _signed_webhook_request(
            event_id=f"evt_m06_{uuid.uuid4().hex[:10]}",
            event_type="payment_intent.succeeded",
            provider_intent_id=m06_body["payment_intent"]["provider_intent_id"],
            order_number=m06_body["order"]["order_number"],
            amount_cents=int(m06_body["order"]["total_cents"]),
        )
        m06_barrier = threading.Barrier(2)

        def _m06_reclaim_worker() -> int:
            with pg_session_factory() as r_db:
                m06_barrier.wait(timeout=15)
                reclaimed = reclaim_expired_reservations_for_variants(r_db, [v_gcap.id])
                r_db.commit()
                return reclaimed

        def _m06_webhook_worker() -> int:
            with TestClient(pg_app) as w_client:
                m06_barrier.wait(timeout=15)
                resp = w_client.post(
                    f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
                    content=m06_raw,
                    headers=m06_hdrs,
                )
                return int(resp.status_code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_rec = pool.submit(_m06_reclaim_worker)
            f_wh = pool.submit(_m06_webhook_worker)
            _rec_cnt = f_rec.result(timeout=20)
            wh_status = f_wh.result(timeout=20)

        assert wh_status == 200
        with pg_session_factory() as verify_db:
            m06_final = verify_db.get(Order, m06_order_id)
            assert m06_final is not None
            # Must end consistently in CONFIRMED/PAID (never both CANCELLED and PAID)
            assert m06_final.status == "CONFIRMED"
            assert m06_final.payment_status == "PAID"
            m06_final_res = verify_db.scalar(
                select(InventoryReservation).where(InventoryReservation.order_id == m06_order_id)
            )
            assert m06_final_res is not None and m06_final_res.status == "COMMITTED"
    finally:
        pg_engine.dispose()
        get_engine(pg_settings).dispose()
        clear_db_caches()
        _run_alembic("downgrade", "base", database_url=test_db_url)
