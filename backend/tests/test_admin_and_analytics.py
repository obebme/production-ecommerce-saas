"""Tests for Module 7 Admin Console, Module 8 Analytics, Operational CLI, and live PG parity."""

from __future__ import annotations

import ipaddress
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
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker
from tests.conftest import (
    DEFAULT_ORIGIN,
    DEFAULT_TEST_PASSWORD,
    TEST_CSRF_SECRET,
    TEST_GUEST_ORDER_SECRET,
    TEST_WEBHOOK_SECRET,
    AuthenticatedSession,
)

from app import cli
from app.core.audit import REDACTED_VALUE, record_audit_log
from app.core.config import Settings, get_settings
from app.core.database import clear_db_caches, create_engine_from_url, get_engine
from app.core.errors import (
    CSRF_COOKIE_NAME,
    GUEST_CART_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    ConflictError,
)
from app.core.security import hash_password, sign_webhook_payload, verify_password
from app.main import create_app
from app.models.catalog import Category, Product, ProductImage
from app.models.identity import AuditLog, User
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import PaymentIntent
from app.modules.admin import service as admin_service
from app.modules.admin.schemas import UpdateUserRoleRequest, UpdateUserStatusRequest
from app.modules.orders.service import SANDBOX_PROVIDER

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
    product_title: str | None = None,
    variant_title: str | None = None,
    price_cents: int = 5000,
    on_hand: int = 20,
    reserved: int = 0,
    low_stock_threshold: int = 5,
    category_active: bool = True,
    product_status: str = "ACTIVE",
    variant_active: bool = True,
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
        title=product_title or f"Item-{suffix}",
        slug=f"item-{suffix}",
        description="Admin and analytics test item",
        status=product_status,
    )
    db_session.add(product)
    db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku=sku or f"ADM-{suffix}",
        title=variant_title or f"Variant-{suffix}",
        attributes_json={"color": "Charcoal", "size": "M"},
        price_cents=price_cents,
        currency="USD",
        is_active=variant_active,
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


def _post_signed_webhook(
    client: TestClient,
    *,
    event_type: str,
    provider_intent_id: str,
    order_number: str,
    amount_cents: int,
    currency: str = "USD",
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> tuple[int, dict[str, Any]]:
    payload = {
        "event_id": f"evt_adm_{uuid.uuid4().hex[:16]}",
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
    ts = int(time.time())
    signature = sign_webhook_payload(ts, raw_body, TEST_WEBHOOK_SECRET)
    resp = client.post(
        f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": str(ts),
            "X-Webhook-Signature": signature,
        },
    )
    return int(resp.status_code), dict(resp.json())


def _place_checkout_order(
    client: TestClient,
    *,
    items: list[tuple[uuid.UUID, int]],
    session: AuthenticatedSession | None = None,
    email: str = "buyer@example.com",
    pay: bool = False,
) -> dict[str, Any]:
    if session is None:
        client.cookies.clear()
        client.cookies.delete(GUEST_CART_COOKIE_NAME)
    else:
        _use_auth_cookies(client, session)

    for variant_id, qty in items:
        add_resp = client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(variant_id), "quantity": qty},
            headers=_csrf_headers(session),
        )
        assert add_resp.status_code == 200, add_resp.text

    chk_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(email=email),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert chk_resp.status_code == 201, chk_resp.text
    dto: dict[str, Any] = chk_resp.json()

    if pay:
        wh_status, wh_body = _post_signed_webhook(
            client,
            event_type="payment_intent.succeeded",
            provider_intent_id=str(dto["payment_intent"]["provider_intent_id"]),
            order_number=str(dto["order"]["order_number"]),
            amount_cents=int(dto["order"]["total_cents"]),
        )
        assert wh_status == 200 and wh_body["status"] == "PROCESSED", wh_body
    return dto


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


def _format_utc_ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_admin_and_analytics_rbac_and_csrf_matrix(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Enforce 401/403 RBAC & CSRF matrix across every /api/v1/admin/* and analytics route."""
    _, _, var, _ = _seed_variant(db_session, sku="RBAC-01", price_cents=4000, on_hand=20)
    buyer_session = login_as(client, role="customer", email="rbac-buyer@example.com")
    unpaid_dto = _place_checkout_order(
        client,
        items=[(var.id, 1)],
        session=buyer_session,
        email="rbac-buyer@example.com",
        pay=False,
    )
    paid_dto = _place_checkout_order(
        client, items=[(var.id, 1)], session=buyer_session, email="rbac-buyer@example.com", pay=True
    )
    unpaid_num = str(unpaid_dto["order"]["order_number"])
    paid_num = str(paid_dto["order"]["order_number"])
    target_user = create_user(email="rbac-target@example.com", role="customer")

    all_endpoints: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/api/v1/admin/orders", None),
        ("GET", f"/api/v1/admin/orders/{paid_num}", None),
        ("POST", f"/api/v1/admin/orders/{paid_num}/fulfill", {"action": "START_PROCESSING"}),
        ("POST", f"/api/v1/admin/orders/{unpaid_num}/cancel", {"reason": "RBAC probe"}),
        (
            "POST",
            f"/api/v1/admin/orders/{paid_num}/refund",
            {"reason": "RBAC probe", "restock": True},
        ),
        ("GET", "/api/v1/admin/users", None),
        ("PATCH", f"/api/v1/admin/users/{target_user.id}/role", {"role": "staff"}),
        ("PATCH", f"/api/v1/admin/users/{target_user.id}/status", {"is_active": False}),
        ("GET", "/api/v1/admin/audit-logs", None),
        ("GET", "/api/v1/admin/analytics/summary", None),
        ("GET", "/api/v1/admin/analytics/timeseries", None),
        ("GET", "/api/v1/admin/analytics/top-products", None),
        ("GET", "/api/v1/admin/analytics/inventory-health", None),
    ]

    # 1. Anonymous and stale session cookie -> 401 AUTH_SESSION_EXPIRED on all 13 routes
    for stale_cookie in (None, "invalid-stale-session-token"):
        client.cookies.clear()
        if stale_cookie is not None:
            client.cookies.set(SESSION_COOKIE_NAME, stale_cookie)
        for method, path, body in all_endpoints:
            resp = client.request(
                method,
                path,
                json=body,
                headers={"Origin": DEFAULT_ORIGIN} if method != "GET" else None,
            )
            assert resp.status_code == 401, (stale_cookie, method, path, resp.text)
            assert resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

    # 2. Customer with valid CSRF -> 403 FORBIDDEN_ROLE on all 13 routes
    cust_session = login_as(client, role="customer", email="rbac-cust@example.com")
    for method, path, body in all_endpoints:
        _use_auth_cookies(client, cust_session)
        resp = client.request(
            method,
            path,
            json=body,
            headers=_csrf_headers(cust_session) if method != "GET" else None,
        )
        assert resp.status_code == 403, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # 3. Missing/invalid X-CSRF-Token & bad Origin on every admin POST/PATCH -> 403 CSRF_INVALID
    admin_session = login_as(client, role="admin", email="rbac-admin@example.com")
    mutation_endpoints: list[tuple[str, str, dict[str, Any]]] = [
        ("POST", f"/api/v1/admin/orders/{paid_num}/fulfill", {"action": "START_PROCESSING"}),
        ("POST", f"/api/v1/admin/orders/{unpaid_num}/cancel", {"reason": "CSRF probe"}),
        ("POST", f"/api/v1/admin/orders/{paid_num}/refund", {"reason": "CSRF probe"}),
        ("PATCH", f"/api/v1/admin/users/{target_user.id}/role", {"role": "staff"}),
        ("PATCH", f"/api/v1/admin/users/{target_user.id}/status", {"is_active": False}),
    ]
    for method, path, body in mutation_endpoints:
        _use_auth_cookies(client, admin_session)
        missing_csrf = client.request(method, path, json=body, headers={"Origin": DEFAULT_ORIGIN})
        assert missing_csrf.status_code == 403, (method, path, missing_csrf.text)
        assert missing_csrf.json()["error"]["code"] == "CSRF_INVALID"

        bad_csrf = client.request(
            method,
            path,
            json=body,
            headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": "0" * 64},
        )
        assert bad_csrf.status_code == 403, (method, path, bad_csrf.text)
        assert bad_csrf.json()["error"]["code"] == "CSRF_INVALID"

        bad_origin = client.request(
            method,
            path,
            json=body,
            headers={
                "Origin": "https://evil.example.com",
                "X-CSRF-Token": admin_session.csrf_token,
            },
        )
        assert bad_origin.status_code == 403, (method, path, bad_origin.text)
        assert bad_origin.json()["error"]["code"] == "CSRF_INVALID"

    # 4. Staff role: allowed on orders/fulfill/cancel/analytics, strictly 403 on admin-only routes
    staff_session = login_as(client, role="staff", email="rbac-staff@example.com")

    # Staff missing CSRF on staff mutations also fails with CSRF_INVALID
    for staff_mut_path, staff_mut_body in (
        (f"/api/v1/admin/orders/{paid_num}/fulfill", {"action": "START_PROCESSING"}),
        (f"/api/v1/admin/orders/{unpaid_num}/cancel", {"reason": "Missing CSRF"}),
    ):
        no_csrf_staff = client.post(
            staff_mut_path, json=staff_mut_body, headers={"Origin": DEFAULT_ORIGIN}
        )
        assert no_csrf_staff.status_code == 403
        assert no_csrf_staff.json()["error"]["code"] == "CSRF_INVALID"

    staff_allowed: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/api/v1/admin/orders", None),
        ("GET", f"/api/v1/admin/orders/{paid_num}", None),
        ("GET", "/api/v1/admin/analytics/summary", None),
        ("GET", "/api/v1/admin/analytics/timeseries", None),
        ("GET", "/api/v1/admin/analytics/top-products", None),
        ("GET", "/api/v1/admin/analytics/inventory-health", None),
        ("POST", f"/api/v1/admin/orders/{paid_num}/fulfill", {"action": "START_PROCESSING"}),
        ("POST", f"/api/v1/admin/orders/{unpaid_num}/cancel", {"reason": "Staff cancelled"}),
    ]
    for method, path, body in staff_allowed:
        resp = client.request(
            method,
            path,
            json=body,
            headers=_csrf_headers(staff_session) if method != "GET" else None,
        )
        assert resp.status_code == 200, (method, path, resp.text)

    staff_forbidden: list[tuple[str, str, dict[str, Any] | None]] = [
        ("POST", f"/api/v1/admin/orders/{paid_num}/refund", {"reason": "Staff attempt"}),
        ("GET", "/api/v1/admin/users", None),
        ("PATCH", f"/api/v1/admin/users/{target_user.id}/role", {"role": "staff"}),
        ("PATCH", f"/api/v1/admin/users/{target_user.id}/status", {"is_active": False}),
        ("GET", "/api/v1/admin/audit-logs", None),
    ]
    for method, path, body in staff_forbidden:
        resp = client.request(
            method,
            path,
            json=body,
            headers=_csrf_headers(staff_session) if method != "GET" else None,
        )
        assert resp.status_code == 403, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # Rejected staff role/status/refund requests did not mutate target user or paid order
    db_session.expire_all()
    reloaded_target = db_session.get(User, target_user.id)
    assert reloaded_target is not None
    assert reloaded_target.role == "customer" and reloaded_target.is_active is True
    reloaded_paid = db_session.scalar(select(Order).where(Order.order_number == paid_num))
    assert reloaded_paid is not None and reloaded_paid.status == "PROCESSING"


def test_admin_orders_list_filters_escaped_ilike_date_range_pagination_and_detail(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """GET /admin/orders & /admin/orders/{order_number} filter, escape ILIKE, and paginate."""
    _, _, v1, _ = _seed_variant(db_session, sku="LST-V1", price_cents=2500, on_hand=30)
    _, _, v2, _ = _seed_variant(db_session, sku="LST-V2", price_cents=1500, on_hand=30)

    cust_session = login_as(client, role="customer", email="alice_vip+100%@example.com")
    # Order 1: multi-item paid customer order (qty 2 + 3 = 5) with failed intent then retry intent
    o1_dto = _place_checkout_order(
        client,
        items=[(v1.id, 2), (v2.id, 3)],
        session=cust_session,
        email="alice_vip+100%@example.com",
        pay=False,
    )
    o1_num = str(o1_dto["order"]["order_number"])
    o1_id = uuid.UUID(o1_dto["order"]["id"])
    o1_total = int(o1_dto["order"]["total_cents"])
    pi1_id = str(o1_dto["payment_intent"]["id"])
    pi1_provider_id = str(o1_dto["payment_intent"]["provider_intent_id"])

    fail_status, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.failed",
        provider_intent_id=pi1_provider_id,
        order_number=o1_num,
        amount_cents=o1_total,
        failure_code="card_declined",
    )
    assert fail_status == 200

    _use_auth_cookies(client, cust_session)
    retry_resp = client.post(
        "/api/v1/payments/intents",
        json={"order_number": o1_num},
        headers=_csrf_headers(cust_session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert retry_resp.status_code == 201
    pi2_id = str(retry_resp.json()["payment_intent"]["id"])
    pi2_provider_id = str(retry_resp.json()["payment_intent"]["provider_intent_id"])

    succ_status, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.succeeded",
        provider_intent_id=pi2_provider_id,
        order_number=o1_num,
        amount_cents=o1_total,
    )
    assert succ_status == 200

    # Order 2: decoy customer order with similar characters (no literal % or _ or \)
    o2_dto = _place_checkout_order(
        client,
        items=[(v1.id, 1)],
        session=cust_session,
        email="aliceXvip+100Ylead@example.com",
        pay=False,
    )
    o2_num = str(o2_dto["order"]["order_number"])

    # Order 3: guest order (user_id is None)
    o3_dto = _place_checkout_order(
        client,
        items=[(v2.id, 2)],
        session=None,
        email="guest-shopper@example.com",
        pay=False,
    )
    o3_num = str(o3_dto["order"]["order_number"])

    # Adjust timestamps deterministically for placed_at DESC and from_ts/to_ts range assertions
    t0 = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 12, 14, 0, 0, tzinfo=UTC)
    for num, placed in ((o1_num, t0), (o2_num, t1), (o3_num, t2)):
        row = db_session.scalar(select(Order).where(Order.order_number == num))
        assert row is not None
        row.placed_at = placed
    # Make retry PaymentIntent strictly newer than initial PaymentIntent for deterministic ordering
    pi1 = db_session.get(PaymentIntent, uuid.UUID(pi1_id))
    pi2 = db_session.get(PaymentIntent, uuid.UUID(pi2_id))
    assert pi1 is not None and pi2 is not None
    pi1.created_at = t0
    pi2.created_at = t0 + timedelta(minutes=5)
    db_session.commit()

    staff_session = login_as(client, role="staff", email="orders-list-staff@example.com")
    _use_auth_cookies(client, staff_session)

    # Full list + item_count sum check + placed_at DESC ordering + zero N+1 SQL check
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
        all_resp = client.get("/api/v1/admin/orders", params={"page": 1, "page_size": 2})
    finally:
        event.remove(engine, "before_cursor_execute", _capture_sql)

    assert all_resp.status_code == 200, all_resp.text
    all_body = all_resp.json()
    assert all_body["total"] == 3
    assert all_body["has_next"] is True
    assert [row["order_number"] for row in all_body["items"]] == [o3_num, o2_num]
    assert all_body["items"][0]["user_id"] is None
    assert all_body["items"][0]["item_count"] == 2
    assert len([s for s in executed_sql if "FROM orders" in s or "FROM order_items" in s]) <= 3

    page2_resp = client.get("/api/v1/admin/orders", params={"page": 2, "page_size": 2})
    assert page2_resp.status_code == 200
    assert [row["order_number"] for row in page2_resp.json()["items"]] == [o1_num]
    assert page2_resp.json()["items"][0]["item_count"] == 5
    assert page2_resp.json()["has_next"] is False

    # Status / payment_status / fulfillment_status filters
    paid_filter = client.get(
        "/api/v1/admin/orders",
        params={
            "status": "CONFIRMED",
            "payment_status": "PAID",
            "fulfillment_status": "ALLOCATED",
        },
    )
    assert paid_filter.status_code == 200
    assert [r["order_number"] for r in paid_filter.json()["items"]] == [o1_num]

    # Escaped ILIKE q with %, _, \ matches ONLY literal email/order_number (never decoy!)
    for literal_q in ("100%", "_vip"):
        q_resp = client.get("/api/v1/admin/orders", params={"q": literal_q})
        assert q_resp.status_code == 200, literal_q
        assert [r["order_number"] for r in q_resp.json()["items"]] == [o1_num], (
            literal_q,
            q_resp.json(),
        )

    backslash_q = client.get("/api/v1/admin/orders", params={"q": "alice\\"})
    assert backslash_q.status_code == 200
    assert backslash_q.json()["items"] == []

    order_num_q = client.get("/api/v1/admin/orders", params={"q": o3_num[-6:]})
    assert order_num_q.status_code == 200
    assert [r["order_number"] for r in order_num_q.json()["items"]] == [o3_num]

    # Date range filters & invalid date range -> 400 INVALID_DATE_RANGE
    range_resp = client.get(
        "/api/v1/admin/orders",
        params={"from_ts": _format_utc_ts(t0), "to_ts": _format_utc_ts(t1)},
    )
    assert range_resp.status_code == 200
    assert [r["order_number"] for r in range_resp.json()["items"]] == [o2_num, o1_num]

    bad_range = client.get(
        "/api/v1/admin/orders",
        params={"from_ts": _format_utc_ts(t2), "to_ts": _format_utc_ts(t0)},
    )
    assert bad_range.status_code == 400
    assert bad_range.json()["error"]["code"] == "INVALID_DATE_RANGE"

    # Pagination max page & validation bounds
    assert client.get("/api/v1/admin/orders", params={"page": 1_000_000}).status_code == 200
    assert client.get("/api/v1/admin/orders", params={"page": 1_000_000}).json()["items"] == []
    assert client.get("/api/v1/admin/orders", params={"page": 1_000_001}).status_code == 422
    assert client.get("/api/v1/admin/orders", params={"q": "x" * 101}).status_code == 422
    assert client.get("/api/v1/admin/orders", params={"q": "bad\x00q"}).status_code == 422

    # Order detail: full items, events, payment_intents (newest first), reservations
    detail_resp = client.get(f"/api/v1/admin/orders/{o1_num}")
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["id"] == str(o1_id)
    assert len(detail["items"]) == 2
    assert {item["sku_snapshot"] for item in detail["items"]} == {"LST-V1", "LST-V2"}
    assert [pi["id"] for pi in detail["payment_intents"]] == [pi2_id, pi1_id]
    assert detail["payment_intent"]["id"] == pi2_id
    assert len(detail["reservations"]) == 2
    assert all(res["status"] == "COMMITTED" for res in detail["reservations"])
    assert [ev["event_type"] for ev in detail["events"]] == [
        "ORDER_PLACED",
        "PAYMENT_FAILED",
        "PAYMENT_SUCCEEDED",
    ]
    assert detail["updated_at"] is not None

    # Malformed / NUL-byte / missing order numbers -> 404 ORDER_NOT_FOUND
    for bad_num in ("ORD%00BAD", "A" * 33, "ORD!INVALID", "ORD-DOES-NOT-EXIST"):
        nf = client.get(f"/api/v1/admin/orders/{bad_num}")
        assert nf.status_code == 404, (bad_num, nf.text)
        assert nf.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_admin_order_fulfillment_lifecycle_and_audit_trail(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /admin/orders/{num}/fulfill enforces valid transitions, metadata, and AuditLog."""
    _, _, var, _ = _seed_variant(db_session, sku="FUL-01", price_cents=5500, on_hand=20)
    buyer_session = login_as(client, role="customer", email="ful-buyer@example.com")

    unpaid = _place_checkout_order(client, items=[(var.id, 1)], session=buyer_session, pay=False)
    paid_stepwise = _place_checkout_order(
        client, items=[(var.id, 1)], session=buyer_session, pay=True
    )
    paid_direct = _place_checkout_order(
        client, items=[(var.id, 1)], session=buyer_session, pay=True
    )
    unpaid_num = str(unpaid["order"]["order_number"])
    stepwise_num = str(paid_stepwise["order"]["order_number"])
    direct_num = str(paid_direct["order"]["order_number"])

    staff_session = login_as(client, role="staff", email="ful-staff@example.com")
    _use_auth_cookies(client, staff_session)

    # Unpaid PENDING_PAYMENT rejects fulfillment actions with 409 INVALID_ORDER_STATE_TRANSITION
    for action in ("START_PROCESSING", "SHIP", "COMPLETE"):
        unpaid_rej = client.post(
            f"/api/v1/admin/orders/{unpaid_num}/fulfill",
            json={"action": action},
            headers=_csrf_headers(staff_session),
        )
        assert unpaid_rej.status_code == 409
        assert unpaid_rej.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Malformed / missing order -> 404 ORDER_NOT_FOUND; invalid action literal -> 422
    assert (
        client.post(
            "/api/v1/admin/orders/ORD%00BAD/fulfill",
            json={"action": "START_PROCESSING"},
            headers=_csrf_headers(staff_session),
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/admin/orders/ORD-MISSING-01/fulfill",
            json={"action": "START_PROCESSING"},
            headers=_csrf_headers(staff_session),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/admin/orders/{stepwise_num}/fulfill",
            json={"action": "COMPLETE_NOW"},
            headers=_csrf_headers(staff_session),
        ).status_code
        == 422
    )

    # CONFIRMED -> COMPLETE is invalid
    conf_complete = client.post(
        f"/api/v1/admin/orders/{stepwise_num}/fulfill",
        json={"action": "COMPLETE"},
        headers=_csrf_headers(staff_session),
    )
    assert conf_complete.status_code == 409
    assert conf_complete.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Stepwise: CONFIRMED -> PROCESSING -> SHIPPED -> COMPLETED
    proc_resp = client.post(
        f"/api/v1/admin/orders/{stepwise_num}/fulfill",
        json={"action": "START_PROCESSING", "note": "Picked in warehouse"},
        headers=_csrf_headers(staff_session),
    )
    assert proc_resp.status_code == 200, proc_resp.text
    assert proc_resp.json()["status"] == "PROCESSING"
    assert proc_resp.json()["fulfillment_status"] == "ALLOCATED"

    # Invalid from PROCESSING: START_PROCESSING or COMPLETE -> 409
    for bad_from_proc in ("START_PROCESSING", "COMPLETE"):
        rej = client.post(
            f"/api/v1/admin/orders/{stepwise_num}/fulfill",
            json={"action": bad_from_proc},
            headers=_csrf_headers(staff_session),
        )
        assert rej.status_code == 409
        assert rej.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    ship_resp = client.post(
        f"/api/v1/admin/orders/{stepwise_num}/fulfill",
        json={
            "action": "SHIP",
            "carrier": "UPS",
            "tracking_number": "1Z999AA10123456784",
            "note": "Dispatched priority",
        },
        headers=_csrf_headers(staff_session),
    )
    assert ship_resp.status_code == 200, ship_resp.text
    ship_dto = ship_resp.json()
    assert ship_dto["status"] == "SHIPPED"
    assert ship_dto["fulfillment_status"] == "SHIPPED"
    assert ship_dto["shipped_at"] is not None

    # Invalid from SHIPPED: START_PROCESSING or SHIP -> 409
    for bad_from_ship in ("START_PROCESSING", "SHIP"):
        rej = client.post(
            f"/api/v1/admin/orders/{stepwise_num}/fulfill",
            json={"action": bad_from_ship},
            headers=_csrf_headers(staff_session),
        )
        assert rej.status_code == 409
        assert rej.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    comp_resp = client.post(
        f"/api/v1/admin/orders/{stepwise_num}/fulfill",
        json={"action": "COMPLETE", "note": "Delivered and signed"},
        headers=_csrf_headers(staff_session),
    )
    assert comp_resp.status_code == 200, comp_resp.text
    comp_dto = comp_resp.json()
    assert comp_dto["status"] == "COMPLETED"
    assert comp_dto["fulfillment_status"] == "DELIVERED"
    assert comp_dto["completed_at"] is not None

    # Invalid from COMPLETED: any fulfill action -> 409
    for bad_from_done in ("START_PROCESSING", "SHIP", "COMPLETE"):
        rej = client.post(
            f"/api/v1/admin/orders/{stepwise_num}/fulfill",
            json={"action": bad_from_done},
            headers=_csrf_headers(staff_session),
        )
        assert rej.status_code == 409
        assert rej.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Direct CONFIRMED -> SHIPPED -> COMPLETED
    admin_session = login_as(client, role="admin", email="ful-admin@example.com")
    direct_ship = client.post(
        f"/api/v1/admin/orders/{direct_num}/fulfill",
        json={"action": "SHIP", "carrier": "FedEx", "tracking_number": "FX-778899"},
        headers=_csrf_headers(admin_session),
    )
    assert direct_ship.status_code == 200
    assert direct_ship.json()["status"] == "SHIPPED"
    assert direct_ship.json()["fulfillment_status"] == "SHIPPED"

    # Verify OrderEvent metadata_json & AuditLog records on stepwise order
    db_session.expire_all()
    stepwise_order = db_session.scalar(select(Order).where(Order.order_number == stepwise_num))
    assert stepwise_order is not None
    events = list(
        db_session.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == stepwise_order.id)
            .order_by(OrderEvent.created_at.asc(), OrderEvent.id.asc())
        ).all()
    )
    ship_ev = next(ev for ev in events if ev.event_type == "ORDER_SHIPPED")
    assert ship_ev.from_status == "PROCESSING"
    assert ship_ev.to_status == "SHIPPED"
    assert ship_ev.actor_user_id == uuid.UUID(staff_session.user_id)
    assert ship_ev.metadata_json == {
        "carrier": "UPS",
        "tracking_number": "1Z999AA10123456784",
        "note": "Dispatched priority",
    }
    comp_ev = next(ev for ev in events if ev.event_type == "ORDER_COMPLETED")
    assert comp_ev.metadata_json == {"note": "Delivered and signed"}

    audit_rows = list(
        db_session.scalars(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "order",
                AuditLog.resource_id == stepwise_num,
                AuditLog.action == "ORDER_FULFILLMENT_UPDATED",
            )
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        ).all()
    )
    assert len(audit_rows) == 3
    ship_audit = next(
        row for row in audit_rows if row.after_state and row.after_state["action"] == "SHIP"
    )
    assert ship_audit.actor_user_id == uuid.UUID(staff_session.user_id)
    assert ship_audit.before_state is not None
    assert ship_audit.before_state["status"] == "PROCESSING"
    assert ship_audit.after_state is not None
    assert ship_audit.after_state["status"] == "SHIPPED"
    assert ship_audit.after_state["carrier"] == "UPS"
    assert ship_audit.after_state["tracking_number"] == "1Z999AA10123456784"
    assert ship_audit.after_state["note"] == "Dispatched priority"


def test_admin_order_cancel_releases_stock_and_rejects_paid_orders(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /admin/orders/{num}/cancel releases unpaid holds with RELEASE ledger, rejects PAID."""
    _, _, v1, _ = _seed_variant(db_session, sku="CXL-V1", price_cents=3000, on_hand=15)
    _, _, v2, _ = _seed_variant(db_session, sku="CXL-V2", price_cents=4500, on_hand=12)

    buyer_session = login_as(client, role="customer", email="cxl-buyer@example.com")
    # Order 1: unpaid multi-item order with failed initial intent
    o1 = _place_checkout_order(client, items=[(v1.id, 3), (v2.id, 2)], session=buyer_session)
    o1_num = str(o1["order"]["order_number"])
    o1_id = uuid.UUID(o1["order"]["id"])
    st_fail, _ = _post_signed_webhook(
        client,
        event_type="payment_intent.failed",
        provider_intent_id=str(o1["payment_intent"]["provider_intent_id"]),
        order_number=o1_num,
        amount_cents=int(o1["order"]["total_cents"]),
        failure_code="insufficient_funds",
    )
    assert st_fail == 200

    # Order 2: unpaid order cancelled with empty body (no JSON payload -> default ADMIN_CANCELLED)
    o2 = _place_checkout_order(client, items=[(v1.id, 1)], session=buyer_session)
    o2_num = str(o2["order"]["order_number"])

    # Order 3: paid order (must reject admin cancel with 409)
    o3 = _place_checkout_order(client, items=[(v1.id, 2)], session=buyer_session, pay=True)
    o3_num = str(o3["order"]["order_number"])

    staff_session = login_as(client, role="staff", email="cxl-staff@example.com")
    _use_auth_cookies(client, staff_session)

    # Cancel Order 1 with explicit reason
    cxl_o1 = client.post(
        f"/api/v1/admin/orders/{o1_num}/cancel",
        json={"reason": "Manual fraud screen"},
        headers=_csrf_headers(staff_session),
    )
    assert cxl_o1.status_code == 200, cxl_o1.text
    cxl_dto = cxl_o1.json()
    assert cxl_dto["status"] == "CANCELLED"
    assert cxl_dto["payment_status"] == "CANCELLED"
    assert cxl_dto["fulfillment_status"] == "CANCELLED"
    assert cxl_dto["cancellation_reason"] == "Manual fraud screen"
    assert cxl_dto["cancelled_at"] is not None
    assert all(pi["status"] == "CANCELLED" for pi in cxl_dto["payment_intents"])
    assert all(res["status"] == "RELEASED" for res in cxl_dto["reservations"])

    db_session.expire_all()
    stock_v1 = db_session.get(InventoryStock, v1.id)
    stock_v2 = db_session.get(InventoryStock, v2.id)
    assert stock_v1 is not None and stock_v1.on_hand == 13 and stock_v1.reserved == 1
    assert stock_v2 is not None and stock_v2.on_hand == 12 and stock_v2.reserved == 0

    release_ledgers = list(
        db_session.scalars(
            select(InventoryLedgerEntry).where(
                InventoryLedgerEntry.order_id == o1_id,
                InventoryLedgerEntry.reason == "RELEASE",
            )
        ).all()
    )
    assert len(release_ledgers) == 2
    by_variant = {entry.variant_id: entry for entry in release_ledgers}
    assert by_variant[v1.id].delta_on_hand == 0
    assert by_variant[v1.id].delta_reserved == -3
    assert by_variant[v1.id].actor_user_id == uuid.UUID(staff_session.user_id)
    assert by_variant[v2.id].delta_on_hand == 0
    assert by_variant[v2.id].delta_reserved == -2
    assert by_variant[v2.id].actor_user_id == uuid.UUID(staff_session.user_id)

    cxl_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_type == "order",
            AuditLog.resource_id == o1_num,
            AuditLog.action == "ORDER_CANCELLED",
        )
    )
    assert cxl_audit is not None
    assert cxl_audit.actor_user_id == uuid.UUID(staff_session.user_id)
    assert cxl_audit.before_state is not None
    assert cxl_audit.before_state["status"] == "PENDING_PAYMENT"
    assert cxl_audit.after_state is not None and cxl_audit.after_state["status"] == "CANCELLED"

    # Cancel Order 2 with no JSON body -> defaults to ADMIN_CANCELLED
    cxl_o2 = client.post(
        f"/api/v1/admin/orders/{o2_num}/cancel",
        headers=_csrf_headers(staff_session),
    )
    assert cxl_o2.status_code == 200
    assert cxl_o2.json()["cancellation_reason"] == "ADMIN_CANCELLED"
    db_session.expire_all()
    stock_v1_after = db_session.get(InventoryStock, v1.id)
    assert stock_v1_after is not None and stock_v1_after.reserved == 0

    # Re-cancel CANCELLED order -> 409 INVALID_ORDER_STATE_TRANSITION
    re_cxl = client.post(
        f"/api/v1/admin/orders/{o1_num}/cancel",
        headers=_csrf_headers(staff_session),
    )
    assert re_cxl.status_code == 409
    assert re_cxl.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"

    # Paid order rejects admin cancel -> 409 INVALID_ORDER_STATE_TRANSITION
    paid_cxl = client.post(
        f"/api/v1/admin/orders/{o3_num}/cancel",
        json={"reason": "Should fail"},
        headers=_csrf_headers(staff_session),
    )
    assert paid_cxl.status_code == 409
    assert paid_cxl.json()["error"]["code"] == "INVALID_ORDER_STATE_TRANSITION"
    db_session.expire_all()
    reloaded_o3 = db_session.scalar(select(Order).where(Order.order_number == o3_num))
    assert reloaded_o3 is not None and reloaded_o3.status == "CONFIRMED"

    # Missing / malformed order_number -> 404 ORDER_NOT_FOUND
    assert (
        client.post(
            "/api/v1/admin/orders/ORD-NOPE/cancel",
            headers=_csrf_headers(staff_session),
        ).status_code
        == 404
    )


def test_admin_order_refund_restock_ledger_and_state_guards(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /admin/orders/{num}/refund enforces restock=true/false, status, and 409 guards."""
    _, _, v1, _ = _seed_variant(db_session, sku="REF-V1", price_cents=5000, on_hand=20)
    _, _, v2, _ = _seed_variant(db_session, sku="REF-V2", price_cents=3500, on_hand=15)

    buyer_session = login_as(client, role="customer", email="ref-buyer@example.com")
    # 1. CONFIRMED multi-SKU order -> refund with restock=True
    o_conf = _place_checkout_order(
        client, items=[(v1.id, 3), (v2.id, 2)], session=buyer_session, pay=True
    )
    o_conf_num = str(o_conf["order"]["order_number"])
    o_conf_id = uuid.UUID(o_conf["order"]["id"])

    # 2. PROCESSING order -> refund with restock=True
    o_proc = _place_checkout_order(client, items=[(v1.id, 2)], session=buyer_session, pay=True)
    o_proc_num = str(o_proc["order"]["order_number"])

    # 3. SHIPPED order -> refund with restock=False
    o_ship = _place_checkout_order(client, items=[(v1.id, 1)], session=buyer_session, pay=True)
    o_ship_num = str(o_ship["order"]["order_number"])
    o_ship_id = uuid.UUID(o_ship["order"]["id"])

    # 4. COMPLETED order -> refund with restock=False
    o_comp = _place_checkout_order(client, items=[(v2.id, 1)], session=buyer_session, pay=True)
    o_comp_num = str(o_comp["order"]["order_number"])
    o_comp_id = uuid.UUID(o_comp["order"]["id"])

    # 5. Unpaid PENDING_PAYMENT & CANCELLED orders -> must reject with 409 REFUND_NOT_ALLOWED
    o_unpaid = _place_checkout_order(client, items=[(v1.id, 1)], session=buyer_session, pay=False)
    o_unpaid_num = str(o_unpaid["order"]["order_number"])
    o_cancelled = _place_checkout_order(
        client, items=[(v2.id, 1)], session=buyer_session, pay=False
    )
    o_cancelled_num = str(o_cancelled["order"]["order_number"])

    admin_session = login_as(client, role="admin", email="ref-admin@example.com")
    _use_auth_cookies(client, admin_session)

    # Advance o_proc -> PROCESSING, o_ship -> SHIPPED, o_comp -> COMPLETED, cancel o_cancelled
    assert (
        client.post(
            f"/api/v1/admin/orders/{o_proc_num}/fulfill",
            json={"action": "START_PROCESSING"},
            headers=_csrf_headers(admin_session),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/orders/{o_ship_num}/fulfill",
            json={"action": "SHIP", "carrier": "DHL", "tracking_number": "DHL-100"},
            headers=_csrf_headers(admin_session),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/orders/{o_comp_num}/fulfill",
            json={"action": "SHIP"},
            headers=_csrf_headers(admin_session),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/orders/{o_comp_num}/fulfill",
            json={"action": "COMPLETE"},
            headers=_csrf_headers(admin_session),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/orders/{o_cancelled_num}/cancel",
            headers=_csrf_headers(admin_session),
        ).status_code
        == 200
    )

    # Stock before refunds: v1 had 20 - (3+2+1) committed - 1 reserved = 14 on_hand, 1 reserved
    # v2 had 15 - (2+1) committed = 12 on_hand, 0 reserved
    db_session.expire_all()
    s1_pre = db_session.get(InventoryStock, v1.id)
    s2_pre = db_session.get(InventoryStock, v2.id)
    assert s1_pre is not None and s1_pre.on_hand == 14 and s1_pre.reserved == 1
    assert s2_pre is not None and s2_pre.on_hand == 12 and s2_pre.reserved == 0

    # Refund CONFIRMED order with restock=True -> fulfillment_status=CANCELLED, on_hand += qty
    ref_conf = client.post(
        f"/api/v1/admin/orders/{o_conf_num}/refund",
        json={"reason": "Customer requested pre-shipment refund", "restock": True},
        headers=_csrf_headers(admin_session),
    )
    assert ref_conf.status_code == 200, ref_conf.text
    ref_conf_dto = ref_conf.json()
    assert ref_conf_dto["status"] == "REFUNDED"
    assert ref_conf_dto["payment_status"] == "REFUNDED"
    assert ref_conf_dto["fulfillment_status"] == "CANCELLED"
    assert ref_conf_dto["payment_intent"]["status"] == "REFUNDED"

    db_session.expire_all()
    s1_post_conf = db_session.get(InventoryStock, v1.id)
    s2_post_conf = db_session.get(InventoryStock, v2.id)
    assert s1_post_conf is not None and s1_post_conf.on_hand == 17 and s1_post_conf.reserved == 1
    assert s2_post_conf is not None and s2_post_conf.on_hand == 14 and s2_post_conf.reserved == 0

    restock_entries = list(
        db_session.scalars(
            select(InventoryLedgerEntry).where(
                InventoryLedgerEntry.order_id == o_conf_id,
                InventoryLedgerEntry.reason == "RETURN_RESTOCK",
            )
        ).all()
    )
    assert len(restock_entries) == 2
    by_var = {e.variant_id: e for e in restock_entries}
    assert by_var[v1.id].delta_on_hand == 3 and by_var[v1.id].on_hand_after == 17
    assert by_var[v1.id].actor_user_id == uuid.UUID(admin_session.user_id)
    assert by_var[v2.id].delta_on_hand == 2 and by_var[v2.id].on_hand_after == 14

    ref_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_type == "order",
            AuditLog.resource_id == o_conf_num,
            AuditLog.action == "ORDER_REFUNDED",
        )
    )
    assert ref_audit is not None
    assert ref_audit.actor_user_id == uuid.UUID(admin_session.user_id)
    assert ref_audit.before_state is not None and ref_audit.before_state["status"] == "CONFIRMED"
    assert ref_audit.after_state is not None and ref_audit.after_state["restock"] is True

    # Refund PROCESSING order with restock=True -> fulfillment_status=CANCELLED, on_hand += 2 (19)
    ref_proc = client.post(
        f"/api/v1/admin/orders/{o_proc_num}/refund",
        json={"reason": "Damaged during packing", "restock": True},
        headers=_csrf_headers(admin_session),
    )
    assert ref_proc.status_code == 200
    assert ref_proc.json()["fulfillment_status"] == "CANCELLED"
    db_session.expire_all()
    s1_post_proc = db_session.get(InventoryStock, v1.id)
    assert s1_post_proc is not None and s1_post_proc.on_hand == 19

    # Refund SHIPPED & COMPLETED orders with restock=False -> RETURNED, on_hand unchanged
    for num, oid in ((o_ship_num, o_ship_id), (o_comp_num, o_comp_id)):
        ref_post_ship = client.post(
            f"/api/v1/admin/orders/{num}/refund",
            json={"reason": "Opened item return", "restock": False},
            headers=_csrf_headers(admin_session),
        )
        assert ref_post_ship.status_code == 200, (num, ref_post_ship.text)
        assert ref_post_ship.json()["status"] == "REFUNDED"
        assert ref_post_ship.json()["payment_status"] == "REFUNDED"
        assert ref_post_ship.json()["fulfillment_status"] == "RETURNED"
        assert ref_post_ship.json()["payment_intent"]["status"] == "REFUNDED"
        assert (
            db_session.scalar(
                select(InventoryLedgerEntry).where(
                    InventoryLedgerEntry.order_id == oid,
                    InventoryLedgerEntry.reason == "RETURN_RESTOCK",
                )
            )
            is None
        )

    db_session.expire_all()
    s1_final = db_session.get(InventoryStock, v1.id)
    s2_final = db_session.get(InventoryStock, v2.id)
    assert s1_final is not None and s1_final.on_hand == 19
    assert s2_final is not None and s2_final.on_hand == 14

    # Reject refund on PENDING_PAYMENT, CANCELLED, and REFUNDED orders -> 409 REFUND_NOT_ALLOWED
    for bad_num in (o_unpaid_num, o_cancelled_num, o_conf_num):
        rej = client.post(
            f"/api/v1/admin/orders/{bad_num}/refund",
            json={"reason": "Invalid refund"},
            headers=_csrf_headers(admin_session),
        )
        assert rej.status_code == 409, (bad_num, rej.text)
        assert rej.json()["error"]["code"] == "REFUND_NOT_ALLOWED"

    # Malformed / missing order -> 404 ORDER_NOT_FOUND
    assert (
        client.post(
            "/api/v1/admin/orders/ORD%00BAD/refund",
            json={"reason": "Missing"},
            headers=_csrf_headers(admin_session),
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/admin/orders/ORD-NOPE-404/refund",
            json={"reason": "Missing"},
            headers=_csrf_headers(admin_session),
        ).status_code
        == 404
    )

    # LOW-3: Refund with restock=True exceeding INT32_MAX rolls back cleanly -> 422
    _, _, v_ov, s_ov = _seed_variant(db_session, sku="REF-INT32", price_cents=2500, on_hand=10)
    o_ov = _place_checkout_order(client, items=[(v_ov.id, 2)], session=buyer_session, pay=True)
    o_ov_num = str(o_ov["order"]["order_number"])
    o_ov_id = uuid.UUID(o_ov["order"]["id"])
    o_ov_pi_id = uuid.UUID(o_ov["payment_intent"]["id"])

    db_session.expire_all()
    ov_stock = db_session.get(InventoryStock, s_ov.variant_id)
    assert ov_stock is not None
    ov_stock.on_hand = 2_147_483_646
    db_session.commit()

    _use_auth_cookies(client, admin_session)
    ov_resp = client.post(
        f"/api/v1/admin/orders/{o_ov_num}/refund",
        json={"reason": "Overflow check", "restock": True},
        headers=_csrf_headers(admin_session),
    )
    assert ov_resp.status_code == 422, ov_resp.text
    assert ov_resp.json()["error"]["code"] == "INVALID_STOCK_ADJUSTMENT"

    db_session.expire_all()
    reloaded_ov_order = db_session.get(Order, o_ov_id)
    reloaded_ov_pi = db_session.get(PaymentIntent, o_ov_pi_id)
    reloaded_ov_stock = db_session.get(InventoryStock, v_ov.id)
    assert reloaded_ov_order is not None
    assert reloaded_ov_order.status == "CONFIRMED"
    assert reloaded_ov_order.payment_status == "PAID"
    assert reloaded_ov_pi is not None and reloaded_ov_pi.status == "SUCCEEDED"
    assert reloaded_ov_stock is not None and reloaded_ov_stock.on_hand == 2_147_483_646
    assert (
        db_session.scalar(
            select(InventoryLedgerEntry).where(
                InventoryLedgerEntry.order_id == o_ov_id,
                InventoryLedgerEntry.reason == "RETURN_RESTOCK",
            )
        )
        is None
    )


def test_admin_users_management_last_admin_guards_and_session_revocation(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Manage users with role/status filters, self/last-admin guards, and immediate revocation."""
    # Seed distinct users (including literal %, _, \ in email/name for ILIKE escaping)
    cust_special = create_user(
        email="spec_user+50%\\ops@example.com",
        full_name="Special_50%\\User",
        role="customer",
        is_active=True,
    )
    create_user(
        email="specXuser+50Yops@example.com",
        full_name="SpecialX50YUser",
        role="customer",
        is_active=False,
    )
    staff_user = create_user(email="team-staff@example.com", full_name="Team Staff", role="staff")

    admin1_session = login_as(client, role="admin", email="primary-admin@example.com")
    admin2_session_1 = login_as(client, role="admin", email="second-admin@example.com")
    # Open a second independent active session for the same admin2 user
    client.cookies.clear()
    login2 = client.post(
        "/api/v1/auth/login",
        json={"email": "second-admin@example.com", "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert login2.status_code == 200
    admin2_second_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    assert admin2_second_cookie is not None

    # Create a 3rd admin to test deactivation + reactivation + login block
    admin3_session = login_as(client, role="admin", email="third-admin@example.com")

    # Authenticate as admin1
    _use_auth_cookies(client, admin1_session)

    # 1. GET /admin/users filters & escaped ILIKE & password_hash absence
    users_all = client.get("/api/v1/admin/users", params={"page": 1, "page_size": 20})
    assert users_all.status_code == 200
    for item in users_all.json()["items"]:
        assert "password_hash" not in item

    role_filtered = client.get("/api/v1/admin/users", params={"role": "staff", "is_active": True})
    assert role_filtered.status_code == 200
    assert [u["id"] for u in role_filtered.json()["items"]] == [str(staff_user.id)]

    inactive_filtered = client.get("/api/v1/admin/users", params={"is_active": False})
    assert inactive_filtered.status_code == 200
    assert len(inactive_filtered.json()["items"]) == 1
    assert inactive_filtered.json()["items"][0]["email"] == "specxuser+50yops@example.com"

    for q_literal in ("50%", "spec_user", "\\ops"):
        q_resp = client.get("/api/v1/admin/users", params={"q": q_literal})
        assert q_resp.status_code == 200
        assert [u["id"] for u in q_resp.json()["items"]] == [str(cust_special.id)], q_literal

    assert client.get("/api/v1/admin/users", params={"page": 1_000_000}).status_code == 200
    assert client.get("/api/v1/admin/users", params={"page": 1_000_001}).status_code == 422

    # 2. Missing target user -> 404 USER_NOT_FOUND
    missing_id = uuid.uuid4()
    assert (
        client.patch(
            f"/api/v1/admin/users/{missing_id}/role",
            json={"role": "staff"},
            headers=_csrf_headers(admin1_session),
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/admin/users/{missing_id}/status",
            json={"is_active": False},
            headers=_csrf_headers(admin1_session),
        ).status_code
        == 404
    )

    # 3. Self-demotion & self-deactivation guards
    self_demote = client.patch(
        f"/api/v1/admin/users/{admin1_session.user_id}/role",
        json={"role": "staff"},
        headers=_csrf_headers(admin1_session),
    )
    assert self_demote.status_code == 409
    assert self_demote.json()["error"]["code"] == "CANNOT_DEMOTE_SELF"

    self_deact = client.patch(
        f"/api/v1/admin/users/{admin1_session.user_id}/status",
        json={"is_active": False},
        headers=_csrf_headers(admin1_session),
    )
    assert self_deact.status_code == 409
    assert self_deact.json()["error"]["code"] == "CANNOT_DEACTIVATE_SELF"

    # 4. Demoting admin2 (>= 2 active admins) succeeds and immediately revokes all active sessions
    demote_a2 = client.patch(
        f"/api/v1/admin/users/{admin2_session_1.user_id}/role",
        json={"role": "staff"},
        headers=_csrf_headers(admin1_session),
    )
    assert demote_a2.status_code == 200, demote_a2.text
    assert demote_a2.json()["user"]["role"] == "staff"
    assert demote_a2.json()["revoked_sessions_count"] == 2

    # Immediate session revocation: both active sessions of admin2 get 401 AUTH_SESSION_EXPIRED
    for revoked_cookie in (admin2_session_1.session_cookie, admin2_second_cookie):
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, revoked_cookie)
        me_resp = client.get("/api/v1/auth/me")
        assert me_resp.status_code == 401
        assert me_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

    # 5. Deactivating admin3 revokes active session immediately and blocks login
    _use_auth_cookies(client, admin1_session)
    deact_a3 = client.patch(
        f"/api/v1/admin/users/{admin3_session.user_id}/status",
        json={"is_active": False},
        headers=_csrf_headers(admin1_session),
    )
    assert deact_a3.status_code == 200, deact_a3.text
    assert deact_a3.json()["user"]["is_active"] is False
    assert deact_a3.json()["revoked_sessions_count"] >= 1

    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, admin3_session.session_cookie)
    revoked_a3 = client.get("/api/v1/auth/me")
    assert revoked_a3.status_code == 401
    assert revoked_a3.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

    disabled_login = client.post(
        "/api/v1/auth/login",
        json={"email": "third-admin@example.com", "password": DEFAULT_TEST_PASSWORD},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert disabled_login.status_code == 403
    assert disabled_login.json()["error"]["code"] == "AUTH_ACCOUNT_DISABLED"

    # Reactivate admin3 -> allows login again
    _use_auth_cookies(client, admin1_session)
    react_a3 = client.patch(
        f"/api/v1/admin/users/{admin3_session.user_id}/status",
        json={"is_active": True},
        headers=_csrf_headers(admin1_session),
    )
    assert react_a3.status_code == 200 and react_a3.json()["user"]["is_active"] is True
    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "third-admin@example.com", "password": DEFAULT_TEST_PASSWORD},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )

    # Deactivate admin3 again so ONLY admin1 remains as active admin
    _use_auth_cookies(client, admin1_session)
    assert (
        client.patch(
            f"/api/v1/admin/users/{admin3_session.user_id}/status",
            json={"is_active": False},
            headers=_csrf_headers(admin1_session),
        ).status_code
        == 200
    )

    # 6. Last active admin guard (both role demotion & status deactivation)
    active_admin_ids = list(
        db_session.scalars(
            select(User.id).where(User.role == "admin", User.is_active.is_(True))
        ).all()
    )
    assert active_admin_ids == [uuid.UUID(admin1_session.user_id)]

    with pytest.raises(ConflictError) as role_exc:
        admin_service.update_admin_user_role(
            db_session,
            target_user_id=uuid.UUID(admin1_session.user_id),
            payload=UpdateUserRoleRequest(role="staff"),
            actor_user_id=uuid.uuid4(),
            ip_address="127.0.0.1",
        )
    assert role_exc.value.code == "CANNOT_REMOVE_LAST_ADMIN"

    with pytest.raises(ConflictError) as status_exc:
        admin_service.update_admin_user_status(
            db_session,
            target_user_id=uuid.UUID(admin1_session.user_id),
            payload=UpdateUserStatusRequest(is_active=False),
            actor_user_id=uuid.uuid4(),
            ip_address="127.0.0.1",
        )
    assert status_exc.value.code == "CANNOT_REMOVE_LAST_ADMIN"

    db_session.expire_all()
    sole_admin = db_session.get(User, uuid.UUID(admin1_session.user_id))
    assert sole_admin is not None and sole_admin.role == "admin" and sole_admin.is_active is True


def test_admin_audit_logs_filters_redaction_and_actor_join(
    client: TestClient,
    db_session: Session,
    create_user: Callable[..., User],
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """GET /admin/audit-logs filters, joins actor metadata, redacts secrets, and validates dates."""
    target = create_user(email="audit-target@example.com", role="customer")
    admin_session = login_as(
        client,
        role="admin",
        email="audit-actor@example.com",
        full_name="Audit Actor",
    )

    # Perform a real admin mutation to generate USER_ROLE_UPDATED log
    role_resp = client.patch(
        f"/api/v1/admin/users/{target.id}/role",
        json={"role": "staff"},
        headers=_csrf_headers(admin_session),
    )
    assert role_resp.status_code == 200

    # Record a secret-bearing synthetic audit row & a system row (actor_user_id=None)
    secret_entry = record_audit_log(
        db_session,
        actor_user_id=uuid.UUID(admin_session.user_id),
        action="SECRET_TEST_EVENT",
        resource_type="system_config",
        resource_id="cfg-1",
        before_state={
            "password": "PlaintextPassword123",
            "password_hash": "$argon2id$secret",
            "nested": {"csrf_token": "tok-123", "api_key": "key-xyz", "safe_flag": True},
        },
        after_state={
            "payment_webhook_secret": "whsec_secret",
            "cookies": {"ecom_session": "raw-cookie"},
            "environment": "test",
        },
        ip_address="203.0.113.55",
    )
    sys_entry = record_audit_log(
        db_session,
        actor_user_id=None,
        action="CLI_USER_CREATED",
        resource_type="user",
        resource_id=str(target.id),
        before_state=None,
        after_state={"email": "audit-target@example.com"},
        ip_address=None,
    )
    db_session.commit()

    t_old = datetime(2026, 11, 1, 9, 0, 0, tzinfo=UTC)
    t_mid = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    t_new = datetime(2026, 11, 10, 15, 0, 0, tzinfo=UTC)
    sys_entry.created_at = t_old
    secret_entry.created_at = t_mid
    role_entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "USER_ROLE_UPDATED",
            AuditLog.resource_id == str(target.id),
        )
    )
    assert role_entry is not None
    role_entry.created_at = t_new
    db_session.commit()

    _use_auth_cookies(client, admin_session)

    # Filter by resource_type, actor_user_id, action
    secret_resp = client.get(
        "/api/v1/admin/audit-logs",
        params={
            "resource_type": "system_config",
            "actor_user_id": admin_session.user_id,
            "action": "SECRET_TEST_EVENT",
        },
    )
    assert secret_resp.status_code == 200, secret_resp.text
    secret_items = secret_resp.json()["items"]
    assert len(secret_items) == 1
    row = secret_items[0]
    assert row["actor_email"] == "audit-actor@example.com"
    assert row["actor_full_name"] == "Audit Actor"
    assert row["ip_address"] == "203.0.113.55"
    assert row["before_state"]["password"] == REDACTED_VALUE
    assert row["before_state"]["password_hash"] == REDACTED_VALUE
    assert row["before_state"]["nested"]["csrf_token"] == REDACTED_VALUE
    assert row["before_state"]["nested"]["api_key"] == REDACTED_VALUE
    assert row["before_state"]["nested"]["safe_flag"] is True
    assert row["after_state"]["payment_webhook_secret"] == REDACTED_VALUE
    assert row["after_state"]["cookies"] == REDACTED_VALUE
    assert row["after_state"]["environment"] == "test"

    # System row with actor_user_id=None has null actor metadata
    cli_resp = client.get("/api/v1/admin/audit-logs", params={"action": "CLI_USER_CREATED"})
    assert cli_resp.status_code == 200
    assert cli_resp.json()["items"][0]["actor_user_id"] is None
    assert cli_resp.json()["items"][0]["actor_email"] is None
    assert cli_resp.json()["items"][0]["actor_full_name"] is None

    # Date range filter & invalid date range -> 400 INVALID_DATE_RANGE
    range_resp = client.get(
        "/api/v1/admin/audit-logs",
        params={"from_ts": _format_utc_ts(t_old), "to_ts": _format_utc_ts(t_mid)},
    )
    assert range_resp.status_code == 200
    assert [it["action"] for it in range_resp.json()["items"]] == [
        "SECRET_TEST_EVENT",
        "CLI_USER_CREATED",
    ]

    bad_range = client.get(
        "/api/v1/admin/audit-logs",
        params={"from_ts": _format_utc_ts(t_new), "to_ts": _format_utc_ts(t_old)},
    )
    assert bad_range.status_code == 400
    assert bad_range.json()["error"]["code"] == "INVALID_DATE_RANGE"

    assert client.get("/api/v1/admin/audit-logs", params={"page": 1_000_000}).status_code == 200
    assert client.get("/api/v1/admin/audit-logs", params={"page": 1_000_001}).status_code == 422


def test_analytics_summary_timeseries_top_products_and_inventory_health(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Verify analytics KPI math, 0-filled timeseries, top-products, and inventory health."""
    staff_session = login_as(client, role="staff", email="analytics-staff@example.com")
    _use_auth_cookies(client, staff_session)

    # 1. Empty window returns zeroes across KPIs, statuses, timeseries, and top-products
    empty_sum = client.get(
        "/api/v1/admin/analytics/summary",
        params={"from": "2026-01-01", "to": "2026-01-03"},
    )
    assert empty_sum.status_code == 200, empty_sum.text
    assert empty_sum.json() == {
        "from_date": "2026-01-01",
        "to_date": "2026-01-03",
        "gross_revenue_cents": 0,
        "refunded_revenue_cents": 0,
        "net_revenue_cents": 0,
        "paid_orders_count": 0,
        "average_order_value_cents": 0,
        "orders_by_status": {
            "PENDING_PAYMENT": 0,
            "CONFIRMED": 0,
            "PROCESSING": 0,
            "SHIPPED": 0,
            "COMPLETED": 0,
            "CANCELLED": 0,
            "REFUNDED": 0,
        },
    }
    empty_ts = client.get(
        "/api/v1/admin/analytics/timeseries",
        params={"from": "2026-01-01", "to": "2026-01-03"},
    )
    assert empty_ts.status_code == 200
    assert empty_ts.json()["points"] == [
        {"date": "2026-01-01", "net_revenue_cents": 0, "paid_orders_count": 0},
        {"date": "2026-01-02", "net_revenue_cents": 0, "paid_orders_count": 0},
        {"date": "2026-01-03", "net_revenue_cents": 0, "paid_orders_count": 0},
    ]

    # 2. Date range validation bounds (> 366 days & from > to -> 400 INVALID_DATE_RANGE)
    for ep in ("summary", "timeseries", "top-products"):
        assert (
            client.get(
                f"/api/v1/admin/analytics/{ep}",
                params={"from": "2026-11-12", "to": "2026-11-10"},
            ).status_code
            == 400
        )
        over_span = client.get(
            f"/api/v1/admin/analytics/{ep}",
            params={"from": "2025-01-01", "to": "2026-01-03"},
        )
        assert over_span.status_code == 400
        assert over_span.json()["error"]["code"] == "INVALID_DATE_RANGE"

        exact_366 = client.get(
            f"/api/v1/admin/analytics/{ep}",
            params={"from": "2025-01-01", "to": "2026-01-02"},
        )
        assert exact_366.status_code == 200

        bad_fmt = client.get(
            f"/api/v1/admin/analytics/{ep}",
            params={"from": "2026-02-30", "to": "2026-03-01"},
        )
        assert bad_fmt.status_code == 400
        assert bad_fmt.json()["error"]["code"] == "INVALID_DATE_RANGE"

        # LOW-2: extreme calendar bounds -> 400 INVALID_DATE_RANGE (never 500), >64 chars -> 422
        for extreme_params in (
            {"from": "9999-12-31", "to": "9999-12-31"},
            {"to": "0001-01-01"},
            {"from": "1969-12-31"},
            {"from": "1969-12-31", "to": "1970-01-01"},
        ):
            extreme_resp = client.get(
                f"/api/v1/admin/analytics/{ep}",
                params=extreme_params,
            )
            assert extreme_resp.status_code == 400, (ep, extreme_params, extreme_resp.text)
            assert extreme_resp.json()["error"]["code"] == "INVALID_DATE_RANGE"

        too_long = client.get(
            f"/api/v1/admin/analytics/{ep}",
            params={"from": "x" * 65},
        )
        assert too_long.status_code == 422
        assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"

    # 3. Seed catalog & inventory health cases
    # Active SKUs: 4 total (1 OOS, 2 low stock, 1 healthy; total reserved = 3+3+0+2 = 8)
    _, p_a, v_a, _ = _seed_variant(
        db_session,
        sku="ANL-OOS",
        product_title="Out of Stock Coat",
        variant_title="Black / S",
        price_cents=10000,
        on_hand=3,
        reserved=3,
        low_stock_threshold=5,
    )
    _, p_b, v_b, _ = _seed_variant(
        db_session,
        sku="ANL-LOW-1",
        product_title="Low Stock Knit",
        variant_title="Moss / M",
        price_cents=2000,
        on_hand=4,
        reserved=3,
        low_stock_threshold=2,
    )
    _, p_c, v_c, _ = _seed_variant(
        db_session,
        sku="ANL-LOW-EQ",
        product_title="Threshold Exact Scarf",
        variant_title="Oat / OS",
        price_cents=5000,
        on_hand=5,
        reserved=0,
        low_stock_threshold=5,
    )
    _, p_d, v_d, _ = _seed_variant(
        db_session,
        sku="ANL-OK",
        product_title="Healthy Tote",
        variant_title="Stone / OS",
        price_cents=1500,
        on_hand=10,
        reserved=2,
        low_stock_threshold=5,
    )
    # Inactive / draft / inactive-category variants must be excluded from inventory-health
    _seed_variant(
        db_session,
        sku="ANL-INACT-VAR",
        on_hand=4,
        reserved=4,
        variant_active=False,
    )
    _seed_variant(
        db_session,
        sku="ANL-DRAFT-PROD",
        on_hand=0,
        reserved=0,
        product_status="DRAFT",
    )
    _seed_variant(
        db_session,
        sku="ANL-INACT-CAT",
        on_hand=0,
        reserved=0,
        category_active=False,
    )

    # Verify inventory-health summary and paginated alert ordering (available ASC)
    ih_p1 = client.get(
        "/api/v1/admin/analytics/inventory-health", params={"page": 1, "page_size": 2}
    )
    assert ih_p1.status_code == 200, ih_p1.text
    ih1_body = ih_p1.json()
    assert ih1_body["summary"] == {
        "active_skus_count": 4,
        "out_of_stock_count": 1,
        "low_stock_count": 2,
        "total_reserved_units": 8,
    }
    assert ih1_body["alerts"]["total"] == 3
    assert ih1_body["alerts"]["has_next"] is True
    assert [a["sku"] for a in ih1_body["alerts"]["items"]] == ["ANL-OOS", "ANL-LOW-1"]
    assert ih1_body["alerts"]["items"][0]["available_quantity"] == 0
    assert ih1_body["alerts"]["items"][0]["in_stock"] is False
    assert ih1_body["alerts"]["items"][0]["low_stock"] is False
    assert ih1_body["alerts"]["items"][1]["available_quantity"] == 1
    assert ih1_body["alerts"]["items"][1]["low_stock"] is True

    ih_p2 = client.get(
        "/api/v1/admin/analytics/inventory-health", params={"page": 2, "page_size": 2}
    )
    assert ih_p2.status_code == 200
    assert ih_p2.json()["alerts"]["has_next"] is False
    assert [a["sku"] for a in ih_p2.json()["alerts"]["items"]] == ["ANL-LOW-EQ"]

    # 4. Seed orders across statuses & UTC boundaries for window 2026-11-10 .. 2026-11-12
    def _insert_order(
        *,
        status: str,
        payment_status: str,
        fulfillment_status: str,
        placed_at: datetime,
        paid_at: datetime | None,
        shipping_cents: int,
        lines: list[tuple[Product, ProductVariant, int]],
    ) -> Order:
        subtotal = sum(var.price_cents * qty for _, var, qty in lines)
        total = subtotal + shipping_cents
        order = Order(
            order_number=f"ORD-ANL-{uuid.uuid4().hex[:8].upper()}",
            customer_email="anl@example.com",
            status=status,
            payment_status=payment_status,
            fulfillment_status=fulfillment_status,
            currency="USD",
            subtotal_cents=subtotal,
            discount_cents=0,
            shipping_cents=shipping_cents,
            tax_cents=0,
            total_cents=total,
            idempotency_key=uuid.uuid4().hex.ljust(64, "0")[:64],
            request_fingerprint=uuid.uuid4().hex.ljust(64, "1")[:64],
            shipping_address_json=_sample_address(),
            billing_address_json=_sample_address(),
            placed_at=placed_at,
            paid_at=paid_at,
        )
        db_session.add(order)
        db_session.flush()
        for prod, var, qty in lines:
            db_session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=prod.id,
                    variant_id=var.id,
                    sku_snapshot=var.sku,
                    product_title_snapshot=prod.title,
                    variant_title_snapshot=var.title,
                    attributes_snapshot_json=dict(var.attributes_json),
                    unit_price_cents=var.price_cents,
                    quantity=qty,
                    line_total_cents=var.price_cents * qty,
                )
            )
        return order

    # Window [2026-11-10T00:00:00Z, 2026-11-13T00:00:00Z)
    # Paid Order 1 (2026-11-10T23:59:59Z): ANL-OOS x 1 = 10000 subtotal + 0 ship = 10000
    _insert_order(
        status="CONFIRMED",
        payment_status="PAID",
        fulfillment_status="ALLOCATED",
        placed_at=datetime(2026, 11, 10, 23, 55, 0, tzinfo=UTC),
        paid_at=datetime(2026, 11, 10, 23, 59, 59, tzinfo=UTC),
        shipping_cents=0,
        lines=[(p_a, v_a, 1)],
    )
    # Paid Order 2 (2026-11-12T00:00:00Z): ANL-LOW-1 x 4 = 8000 + 1000 ship = 9000
    _insert_order(
        status="SHIPPED",
        payment_status="PAID",
        fulfillment_status="SHIPPED",
        placed_at=datetime(2026, 11, 12, 0, 0, 0, tzinfo=UTC),
        paid_at=datetime(2026, 11, 12, 0, 0, 0, tzinfo=UTC),
        shipping_cents=1000,
        lines=[(p_b, v_b, 4)],
    )
    # Paid Order 3 (placed BEFORE window on 2026-11-09, paid INSIDE window 2026-11-12T10:00:00Z):
    # ANL-OK x 1 = 1500 + 1000 ship = 2500 -> non-divisible AOV: 21500 // 3 = 7166 (remainder 2).
    _insert_order(
        status="COMPLETED",
        payment_status="PAID",
        fulfillment_status="DELIVERED",
        placed_at=datetime(2026, 11, 9, 23, 30, 0, tzinfo=UTC),
        paid_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC),
        shipping_cents=1000,
        lines=[(p_d, v_d, 1)],
    )
    # Refunded Order 4 (2026-11-11T12:00:00Z): ANL-LOW-EQ x 1 = 5000 + 1000 ship = 6000
    _insert_order(
        status="REFUNDED",
        payment_status="REFUNDED",
        fulfillment_status="CANCELLED",
        placed_at=datetime(2026, 11, 11, 11, 0, 0, tzinfo=UTC),
        paid_at=datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC),
        shipping_cents=1000,
        lines=[(p_c, v_c, 1)],
    )
    # Unpaid PENDING_PAYMENT Order 5 & CANCELLED Order 6 inside placed_at window
    _insert_order(
        status="PENDING_PAYMENT",
        payment_status="REQUIRES_PAYMENT",
        fulfillment_status="UNFULFILLED",
        placed_at=datetime(2026, 11, 11, 14, 0, 0, tzinfo=UTC),
        paid_at=None,
        shipping_cents=1000,
        lines=[(p_b, v_b, 2)],
    )
    _insert_order(
        status="CANCELLED",
        payment_status="CANCELLED",
        fulfillment_status="CANCELLED",
        placed_at=datetime(2026, 11, 11, 15, 0, 0, tzinfo=UTC),
        paid_at=None,
        shipping_cents=1000,
        lines=[(p_c, v_c, 3)],
    )
    # Outside window after end (2026-11-13T00:00:00Z) -> excluded
    _insert_order(
        status="PROCESSING",
        payment_status="PAID",
        fulfillment_status="ALLOCATED",
        placed_at=datetime(2026, 11, 13, 0, 0, 0, tzinfo=UTC),
        paid_at=datetime(2026, 11, 13, 0, 0, 0, tzinfo=UTC),
        shipping_cents=0,
        lines=[(p_a, v_a, 1)],
    )
    db_session.commit()

    sum_resp = client.get(
        "/api/v1/admin/analytics/summary",
        params={"from": "2026-11-10", "to": "2026-11-12"},
    )
    assert sum_resp.status_code == 200, sum_resp.text
    sum_body = sum_resp.json()
    assert sum_body["gross_revenue_cents"] == 27500  # 10000 + 9000 + 2500 + 6000
    assert sum_body["refunded_revenue_cents"] == 6000
    assert sum_body["net_revenue_cents"] == 21500
    assert sum_body["paid_orders_count"] == 3
    assert sum_body["average_order_value_cents"] == 7166  # 21500 // 3
    # orders_by_status uses placed_at in [2026-11-10, 2026-11-13) (COMPLETED placed 11-09):
    assert sum_body["orders_by_status"] == {
        "PENDING_PAYMENT": 1,
        "CONFIRMED": 1,
        "PROCESSING": 0,
        "SHIPPED": 1,
        "COMPLETED": 0,
        "CANCELLED": 1,
        "REFUNDED": 1,
    }

    # Timeseries across 2026-11-09..2026-11-12 (2026-11-09 and 2026-11-11 are 0-filled!)
    ts_resp = client.get(
        "/api/v1/admin/analytics/timeseries",
        params={"from": "2026-11-09", "to": "2026-11-12"},
    )
    assert ts_resp.status_code == 200, ts_resp.text
    points = ts_resp.json()["points"]
    assert points == [
        {"date": "2026-11-09", "net_revenue_cents": 0, "paid_orders_count": 0},
        {"date": "2026-11-10", "net_revenue_cents": 10000, "paid_orders_count": 1},
        {"date": "2026-11-11", "net_revenue_cents": 0, "paid_orders_count": 0},
        {"date": "2026-11-12", "net_revenue_cents": 11500, "paid_orders_count": 2},
    ]
    assert sum(p["net_revenue_cents"] for p in points) == sum_body["net_revenue_cents"]
    assert sum(p["paid_orders_count"] for p in points) == sum_body["paid_orders_count"]

    # Top products: sort_by=revenue puts ANL-OOS (10000) before ANL-LOW-1 (8000);
    # sort_by=units puts ANL-LOW-1 (4) before ANL-OOS (1). Excludes REFUNDED/PENDING/CANCELLED.
    top_rev = client.get(
        "/api/v1/admin/analytics/top-products",
        params={"from": "2026-11-10", "to": "2026-11-12", "sort_by": "revenue", "limit": 10},
    )
    assert top_rev.status_code == 200, top_rev.text
    assert [it["sku_snapshot"] for it in top_rev.json()["items"]] == [
        "ANL-OOS",
        "ANL-LOW-1",
        "ANL-OK",
    ]
    assert top_rev.json()["items"][0]["revenue_cents"] == 10000
    assert top_rev.json()["items"][0]["units_sold"] == 1

    top_units = client.get(
        "/api/v1/admin/analytics/top-products",
        params={"from": "2026-11-10", "to": "2026-11-12", "sort_by": "units", "limit": 2},
    )
    assert top_units.status_code == 200
    assert [it["sku_snapshot"] for it in top_units.json()["items"]] == ["ANL-LOW-1", "ANL-OOS"]
    assert top_units.json()["items"][0]["units_sold"] == 4
    assert top_units.json()["items"][0]["revenue_cents"] == 8000

    # limit bounds -> 422
    assert (
        client.get("/api/v1/admin/analytics/top-products", params={"limit": 0}).status_code == 422
    )
    assert (
        client.get("/api/v1/admin/analytics/top-products", params={"limit": 51}).status_code == 422
    )


def test_cli_create_user_and_seed_demo(
    client: TestClient,
    db_session: Session,
    db_session_factory: sessionmaker[Session],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify app.cli create-user (Argon2id, --update-if-exists, revocation) and seed-demo."""
    # 1. create-user validation errors
    assert (
        cli.main(
            [
                "create-user",
                "--email",
                "short@example.com",
                "--full-name",
                "Short Pw",
                "--role",
                "admin",
                "--password",
                "Short!123",
            ],
            session_factory=db_session_factory,
            settings=test_settings,
        )
        == 1
    )
    assert (
        cli.main(
            [
                "create-user",
                "--email",
                "long@example.com",
                "--full-name",
                "Long Pw",
                "--role",
                "admin",
                "--password",
                "x" * 129,
            ],
            session_factory=db_session_factory,
            settings=test_settings,
        )
        == 1
    )
    assert (
        cli.main(
            [
                "create-user",
                "--email",
                "missing-env@example.com",
                "--full-name",
                "Missing Env",
                "--role",
                "staff",
                "--password-env",
                "NONEXISTENT_CLI_PASSWORD_ENV",
            ],
            session_factory=db_session_factory,
            settings=test_settings,
        )
        == 1
    )
    assert (
        cli.main(
            [
                "create-user",
                "--email",
                "both@example.com",
                "--full-name",
                "Both Flags",
                "--role",
                "customer",
                "--password",
                "ValidPass!2026",
                "--password-env",
                "SOME_ENV",
            ],
            session_factory=db_session_factory,
            settings=test_settings,
        )
        == 1
    )

    # 2. create-user creates admin/staff/customer with Argon2id hash & CLI_USER_CREATED audit log
    monkeypatch.setenv("TEST_CLI_ADMIN_PW", "BootstrapAdmin!2026")
    rc_admin = cli.main(
        [
            "create-user",
            "--email",
            "CLI-Admin@Example.com",
            "--full-name",
            "CLI Admin",
            "--role",
            "admin",
            "--password-env",
            "TEST_CLI_ADMIN_PW",
        ],
        session_factory=db_session_factory,
        settings=test_settings,
    )
    assert rc_admin == 0

    rc_cust = cli.main(
        [
            "create-user",
            "--email",
            "cli-user@example.com",
            "--full-name",
            "CLI User",
            "--role",
            "customer",
            "--password",
            "CustomerPass!2026",
        ],
        session_factory=db_session_factory,
        settings=test_settings,
    )
    assert rc_cust == 0

    db_admin = db_session.scalar(select(User).where(User.email == "cli-admin@example.com"))
    assert db_admin is not None
    assert db_admin.role == "admin"
    assert db_admin.password_hash.startswith("$argon2id$")
    assert verify_password("BootstrapAdmin!2026", db_admin.password_hash)

    # Verify CLI-created admin can authenticate via API
    admin_login = client.post(
        "/api/v1/auth/login",
        json={"email": "cli-admin@example.com", "password": "BootstrapAdmin!2026"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert admin_login.status_code == 200
    assert admin_login.json()["user"]["role"] == "admin"

    # Duplicate email without --update-if-exists -> non-zero exit
    rc_dup = cli.main(
        [
            "create-user",
            "--email",
            "cli-user@example.com",
            "--full-name",
            "Dup",
            "--role",
            "staff",
            "--password",
            "CustomerPass!2026",
        ],
        session_factory=db_session_factory,
        settings=test_settings,
    )
    assert rc_dup == 1

    # Log in as cli-user to create active session, then promote via --update-if-exists
    client.cookies.clear()
    cust_login = client.post(
        "/api/v1/auth/login",
        json={"email": "cli-user@example.com", "password": "CustomerPass!2026"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert cust_login.status_code == 200
    old_cust_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    assert old_cust_cookie is not None

    rc_update = cli.main(
        [
            "create-user",
            "--email",
            "cli-user@example.com",
            "--full-name",
            "Promoted Staff",
            "--role",
            "staff",
            "--password",
            "UpdatedStaffPass!2026",
            "--update-if-exists",
        ],
        session_factory=db_session_factory,
        settings=test_settings,
    )
    assert rc_update == 0

    # Old session immediately revoked (401), new credentials succeed with updated role
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, old_cust_cookie)
    assert client.get("/api/v1/auth/me").status_code == 401

    client.cookies.clear()
    new_staff_login = client.post(
        "/api/v1/auth/login",
        json={"email": "cli-user@example.com", "password": "UpdatedStaffPass!2026"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert new_staff_login.status_code == 200
    assert new_staff_login.json()["user"]["role"] == "staff"
    assert new_staff_login.json()["user"]["full_name"] == "Promoted Staff"

    db_session.expire_all()
    cli_user_row = db_session.scalar(select(User).where(User.email == "cli-user@example.com"))
    assert cli_user_row is not None
    upd_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "CLI_USER_UPDATED",
            AuditLog.resource_id == str(cli_user_row.id),
        )
    )
    assert upd_audit is not None
    assert upd_audit.after_state is not None
    assert upd_audit.after_state["revoked_sessions_count"] >= 1

    # 3. seed-demo refuses with non-zero exit when ENVIRONMENT=production (writes zero rows)
    prod_settings = Settings(
        database_url="postgresql+psycopg://produser:prod-strong-password-val@db:5432/ecom_prod",
        environment="production",
        cookie_secure=True,
        payment_sandbox_mode=False,
        allowed_origins=["https://shop.example.com"],
        csrf_hmac_secret="prod-csrf-secret-value-at-least-32-chars-01",
        guest_order_hmac_secret="prod-guest-secret-value-at-least-32-chars-02",
        payment_webhook_secret="prod-webhook-secret-value-at-least-32-chars-03",
    )
    assert (
        cli.main(
            ["seed-demo"],
            session_factory=db_session_factory,
            settings=prod_settings,
        )
        == 1
    )
    assert db_session.scalar(select(func.count(Category.id))) == 0

    # Also verify via ENVIRONMENT=production env resolution (get_settings cache cleared)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://produser:prod-strong-password-val@db:5432/ecom_prod"
    )
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("PAYMENT_SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://shop.example.com")
    monkeypatch.setenv("CSRF_HMAC_SECRET", "prod-csrf-secret-value-at-least-32-chars-01")
    monkeypatch.setenv("GUEST_ORDER_HMAC_SECRET", "prod-guest-secret-value-at-least-32-chars-02")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "prod-webhook-secret-value-at-least-32-chars-03")
    get_settings.cache_clear()
    try:
        assert cli.main(["seed-demo"], session_factory=db_session_factory) == 1
    finally:
        get_settings.cache_clear()
    assert db_session.scalar(select(func.count(Category.id))) == 0

    # 4. seed-demo idempotency (3 categories, 8 products, 11 variants, + optional 3 demo users)
    assert (
        cli.main(
            ["seed-demo"],
            session_factory=db_session_factory,
            settings=test_settings,
        )
        == 0
    )
    assert db_session.scalar(select(func.count(Category.id))) == 3
    assert db_session.scalar(select(func.count(Product.id))) == 8
    assert db_session.scalar(select(func.count(ProductVariant.id))) == 11
    assert db_session.scalar(select(func.count(ProductImage.id))) == 8
    assert db_session.scalar(select(func.count(InventoryStock.variant_id))) == 11
    assert (
        db_session.scalar(
            select(func.count(InventoryLedgerEntry.id)).where(
                InventoryLedgerEntry.reason == "INITIAL_SEED"
            )
        )
        == 11
    )

    # Second run is strictly idempotent (zero duplicate rows)
    assert (
        cli.main(
            ["seed-demo", "--with-demo-users"],
            session_factory=db_session_factory,
            settings=test_settings,
        )
        == 0
    )
    assert (
        cli.main(
            ["seed-demo", "--with-demo-users"],
            session_factory=db_session_factory,
            settings=test_settings,
        )
        == 0
    )
    assert db_session.scalar(select(func.count(Category.id))) == 3
    assert db_session.scalar(select(func.count(Product.id))) == 8
    assert db_session.scalar(select(func.count(ProductVariant.id))) == 11
    assert db_session.scalar(select(func.count(ProductImage.id))) == 8
    assert db_session.scalar(select(func.count(InventoryLedgerEntry.id))) == 11
    demo_emails = list(
        db_session.scalars(
            select(User.email).where(
                User.email.in_(
                    [
                        "admin@atelier.example",
                        "staff@atelier.example",
                        "customer@atelier.example",
                    ]
                )
            )
        ).all()
    )
    assert sorted(demo_emails) == [
        "admin@atelier.example",
        "customer@atelier.example",
        "staff@atelier.example",
    ]


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Live PostgreSQL TEST_DATABASE_URL not set",
)
def test_live_postgresql_be004_concurrency_and_parity() -> None:
    """Verify PostgreSQL last-admin concurrency lock, analytics UTC queries, INET, and refund."""
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
        # ------------------------------------------------------------------
        # 1. Seed 2 active admins and verify deterministic PG FOR UPDATE race
        # ------------------------------------------------------------------
        with pg_session_factory() as setup_db:
            svc_a1 = User(
                email=f"pg-a1-{uuid.uuid4().hex[:6]}@example.com",
                password_hash=pw_hash,
                full_name="PG Admin One",
                role="admin",
                is_active=True,
            )
            svc_a2 = User(
                email=f"pg-a2-{uuid.uuid4().hex[:6]}@example.com",
                password_hash=pw_hash,
                full_name="PG Admin Two",
                role="admin",
                is_active=True,
            )
            setup_db.add_all([svc_a1, svc_a2])
            setup_db.flush()
            svc_a1_id = svc_a1.id
            svc_a2_id = svc_a2.id
            setup_db.commit()

        # 1a. Service-layer Barrier(2): deterministic SELECT ... ORDER BY id ASC FOR UPDATE
        svc_barrier = threading.Barrier(2)

        def _svc_role_worker() -> tuple[str, str]:
            with pg_session_factory() as db:
                svc_barrier.wait(timeout=15)
                try:
                    admin_service.update_admin_user_role(
                        db,
                        target_user_id=svc_a2_id,
                        payload=UpdateUserRoleRequest(role="staff"),
                        actor_user_id=svc_a1_id,
                        ip_address="203.0.113.10",
                    )
                    return ("OK", "role_updated")
                except ConflictError as exc:
                    return ("CONFLICT", exc.code)

        def _svc_status_worker() -> tuple[str, str]:
            with pg_session_factory() as db:
                svc_barrier.wait(timeout=15)
                try:
                    admin_service.update_admin_user_status(
                        db,
                        target_user_id=svc_a1_id,
                        payload=UpdateUserStatusRequest(is_active=False),
                        actor_user_id=svc_a2_id,
                        ip_address="2001:db8::1",
                    )
                    return ("OK", "status_updated")
                except ConflictError as exc:
                    return ("CONFLICT", exc.code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_role = pool.submit(_svc_role_worker)
            f_status = pool.submit(_svc_status_worker)
            svc_results = [f_role.result(timeout=20), f_status.result(timeout=20)]

        ok_count = sum(1 for status_tag, _ in svc_results if status_tag == "OK")
        conflict_codes = [code for status_tag, code in svc_results if status_tag == "CONFLICT"]
        assert ok_count == 1, svc_results
        assert conflict_codes == ["CANNOT_REMOVE_LAST_ADMIN"], svc_results

        with pg_session_factory() as verify_db:
            remaining_admins = list(
                verify_db.scalars(
                    select(User).where(User.role == "admin", User.is_active.is_(True))
                ).all()
            )
            assert len(remaining_admins) == 1
            surviving_admin_id = remaining_admins[0].id
            surviving_admin_email = remaining_admins[0].email

        # 1b. HTTP-level Barrier(2): promote second admin back to 2 active admins and race via PATCH
        with pg_session_factory() as prep_db:
            other_id = svc_a2_id if surviving_admin_id == svc_a1_id else svc_a1_id
            other_user = prep_db.get(User, other_id)
            assert other_user is not None
            other_user.role = "admin"
            other_user.is_active = True
            prep_db.commit()
            other_email = other_user.email

        def _pg_login(email: str) -> tuple[str, str, str]:
            with TestClient(pg_app) as c:
                resp = c.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": DEFAULT_TEST_PASSWORD},
                    headers={"Origin": DEFAULT_ORIGIN},
                )
                assert resp.status_code == 200, resp.text
                s_cookie = c.cookies.get(SESSION_COOKIE_NAME)
                c_cookie = c.cookies.get(CSRF_COOKIE_NAME)
                assert s_cookie is not None and c_cookie is not None
                return s_cookie, c_cookie, str(resp.json()["csrf_token"])

        creds_1 = _pg_login(surviving_admin_email)
        creds_2 = _pg_login(other_email)
        http_barrier = threading.Barrier(2)

        def _http_race_worker(
            creds: tuple[str, str, str],
            method_path: str,
            payload: dict[str, Any],
        ) -> tuple[int, str]:
            s_cookie, c_cookie, csrf_tok = creds
            with TestClient(pg_app) as w_client:
                w_client.cookies.set(SESSION_COOKIE_NAME, s_cookie)
                w_client.cookies.set(CSRF_COOKIE_NAME, c_cookie)
                http_barrier.wait(timeout=15)
                resp = w_client.patch(
                    method_path,
                    json=payload,
                    headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": csrf_tok},
                )
                body = resp.json()
                code = (
                    str(body.get("error", {}).get("code", "OK"))
                    if resp.status_code != 200
                    else "OK"
                )
                return int(resp.status_code), code

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(
                _http_race_worker,
                creds_1,
                f"/api/v1/admin/users/{other_id}/role",
                {"role": "staff"},
            )
            f2 = pool.submit(
                _http_race_worker,
                creds_2,
                f"/api/v1/admin/users/{surviving_admin_id}/status",
                {"is_active": False},
            )
            http_results = [f1.result(timeout=20), f2.result(timeout=20)]

        http_200 = [r for r in http_results if r[0] == 200]
        http_fail = [r for r in http_results if r[0] != 200]
        assert len(http_200) == 1, http_results
        assert len(http_fail) == 1, http_results
        assert http_fail[0] in (
            (409, "CANNOT_REMOVE_LAST_ADMIN"),
            (401, "AUTH_SESSION_EXPIRED"),
        ), http_results

        with pg_session_factory() as verify_db:
            final_admins = list(
                verify_db.scalars(
                    select(User).where(User.role == "admin", User.is_active.is_(True))
                ).all()
            )
            assert len(final_admins) == 1
            pg_admin_email = final_admins[0].email

        # ------------------------------------------------------------------
        # 2. PostgreSQL parity: Refund + Restock, Analytics UTC, INET AuditLog
        # ------------------------------------------------------------------
        with pg_session_factory() as setup_db:
            cat = Category(
                name="PG BE004 Cat",
                slug=f"pg-be004-cat-{uuid.uuid4().hex[:6]}",
                is_active=True,
            )
            setup_db.add(cat)
            setup_db.flush()
            prod = Product(
                category_id=cat.id,
                title="PG Analytics Wool Scarf",
                slug=f"pg-be004-prod-{uuid.uuid4().hex[:6]}",
                description="PG parity product",
                status="ACTIVE",
            )
            setup_db.add(prod)
            setup_db.flush()
            var = ProductVariant(
                product_id=prod.id,
                sku=f"PG-ANL-{uuid.uuid4().hex[:6]}",
                title="Charcoal / OS",
                attributes_json={"color": "Charcoal"},
                price_cents=12000,
                currency="USD",
                is_active=True,
            )
            setup_db.add(var)
            setup_db.flush()
            setup_db.add(
                InventoryStock(
                    variant_id=var.id,
                    on_hand=10,
                    reserved=0,
                    low_stock_threshold=5,
                    version=1,
                )
            )
            buyer = User(
                email=f"pg-buyer-{uuid.uuid4().hex[:6]}@example.com",
                password_hash=pw_hash,
                full_name="PG Buyer",
                role="customer",
                is_active=True,
            )
            setup_db.add(buyer)
            setup_db.commit()
            var_id = var.id
            buyer_email = buyer.email

        pg_app.state.rate_limiter.reset()
        buyer_s, buyer_c, buyer_csrf = _pg_login(buyer_email)
        admin_s, admin_c, admin_csrf = _pg_login(pg_admin_email)

        with TestClient(pg_app) as pg_client:
            # Place and pay 2 orders as buyer: Order 1 stays PAID; Order 2 refunded (restock=True)
            pg_client.cookies.set(SESSION_COOKIE_NAME, buyer_s)
            pg_client.cookies.set(CSRF_COOKIE_NAME, buyer_c)

            def _pg_paid_order(qty: int) -> str:
                assert (
                    pg_client.post(
                        "/api/v1/cart/items",
                        json={"variant_id": str(var_id), "quantity": qty},
                        headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": buyer_csrf},
                    ).status_code
                    == 200
                )
                chk = pg_client.post(
                    "/api/v1/checkout/orders",
                    json=_checkout_payload(email=buyer_email),
                    headers={
                        "Origin": DEFAULT_ORIGIN,
                        "X-CSRF-Token": buyer_csrf,
                        "Idempotency-Key": uuid.uuid4().hex,
                    },
                )
                assert chk.status_code == 201, chk.text
                dto = chk.json()
                wh_st, _ = _post_signed_webhook(
                    pg_client,
                    event_type="payment_intent.succeeded",
                    provider_intent_id=str(dto["payment_intent"]["provider_intent_id"]),
                    order_number=str(dto["order"]["order_number"]),
                    amount_cents=int(dto["order"]["total_cents"]),
                )
                assert wh_st == 200
                return str(dto["order"]["order_number"])

            kept_order_num = _pg_paid_order(2)
            refunded_order_num = _pg_paid_order(3)

            # Switch to surviving PG admin
            pg_client.cookies.clear()
            pg_client.cookies.set(SESSION_COOKIE_NAME, admin_s)
            pg_client.cookies.set(CSRF_COOKIE_NAME, admin_c)

            ref_resp = pg_client.post(
                f"/api/v1/admin/orders/{refunded_order_num}/refund",
                json={"reason": "PG refund restock parity", "restock": True},
                headers={"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": admin_csrf},
            )
            assert ref_resp.status_code == 200, ref_resp.text
            assert ref_resp.json()["status"] == "REFUNDED"
            assert ref_resp.json()["fulfillment_status"] == "CANCELLED"

            with pg_session_factory() as verify_db:
                stock_row = verify_db.get(InventoryStock, var_id)
                assert stock_row is not None and stock_row.on_hand == 8 and stock_row.reserved == 0
                restock_ledger = verify_db.scalar(
                    select(InventoryLedgerEntry)
                    .join(Order, Order.id == InventoryLedgerEntry.order_id)
                    .where(
                        Order.order_number == refunded_order_num,
                        InventoryLedgerEntry.reason == "RETURN_RESTOCK",
                    )
                )
                assert restock_ledger is not None and restock_ledger.delta_on_hand == 3

            # Analytics summary & timeseries (exercises PG AT TIME ZONE 'UTC' + TIMESTAMPTZ)
            today_utc = datetime.now(UTC).date().isoformat()
            pg_sum = pg_client.get(
                "/api/v1/admin/analytics/summary",
                params={"from": today_utc, "to": today_utc},
            )
            assert pg_sum.status_code == 200, pg_sum.text
            sum_data = pg_sum.json()
            assert sum_data["paid_orders_count"] == 1
            assert sum_data["orders_by_status"]["CONFIRMED"] == 1
            assert sum_data["orders_by_status"]["REFUNDED"] == 1
            assert sum_data["net_revenue_cents"] > 0
            assert sum_data["refunded_revenue_cents"] > 0
            assert sum_data["gross_revenue_cents"] == (
                sum_data["net_revenue_cents"] + sum_data["refunded_revenue_cents"]
            )

            pg_ts = pg_client.get(
                "/api/v1/admin/analytics/timeseries",
                params={"from": today_utc, "to": today_utc},
            )
            assert pg_ts.status_code == 200, pg_ts.text
            assert pg_ts.json()["points"] == [
                {
                    "date": today_utc,
                    "net_revenue_cents": sum_data["net_revenue_cents"],
                    "paid_orders_count": 1,
                }
            ]

            pg_top = pg_client.get(
                "/api/v1/admin/analytics/top-products",
                params={"from": today_utc, "to": today_utc},
            )
            assert pg_top.status_code == 200
            assert len(pg_top.json()["items"]) == 1
            assert pg_top.json()["items"][0]["units_sold"] == 2

            # Audit logs INET serialization + actor_email join on live PostgreSQL
            pg_audits = pg_client.get("/api/v1/admin/audit-logs", params={"page_size": 50})
            assert pg_audits.status_code == 200, pg_audits.text
            audit_items = pg_audits.json()["items"]
            assert any(
                it["action"] == "ORDER_REFUNDED"
                and it["resource_id"] == refunded_order_num
                and it["actor_email"] == pg_admin_email
                for it in audit_items
            )
            inet_ips = [it["ip_address"] for it in audit_items if it["ip_address"] is not None]
            assert len(inet_ips) >= 1
            assert all(isinstance(val, str) for val in inet_ips)
            parsed_ips = {str(ipaddress.ip_interface(val).ip) for val in inet_ips}
            assert parsed_ips & {"203.0.113.10", "2001:db8::1"}
            assert kept_order_num != refunded_order_num
    finally:
        pg_engine.dispose()
        get_engine(pg_settings).dispose()
        clear_db_caches()
        _run_alembic("downgrade", "base", database_url=test_db_url)
