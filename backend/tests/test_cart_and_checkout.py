"""Tests for Module 4 (Shopping Cart) and Module 5 checkout orchestration."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from tests.conftest import DEFAULT_ORIGIN, AuthenticatedSession

from app.core.config import Settings
from app.core.errors import (
    CSRF_COOKIE_NAME,
    GUEST_CART_COOKIE_NAME,
    GUEST_ORDERS_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)
from app.core.security import GuestPendingOrderTracker, hash_token
from app.models.cart import Cart, CartItem
from app.models.catalog import Category, Product, ProductImage
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import PaymentIntent
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.orders.service import parse_guest_orders_cookie


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


def _set_cookie_headers_by_name(headers: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in headers:
        cookie_name = raw.split("=", 1)[0].strip()
        result[cookie_name] = raw
    return result


def _sample_address(*, line2: str | None = "Apt 4B", city: str = "San Francisco") -> dict[str, Any]:
    return {
        "full_name": "Alice Mercer",
        "line1": "120 Market St",
        "line2": line2,
        "city": city,
        "state": "CA",
        "postal_code": "94105",
        "country": "US",
        "phone": "+14155550101",
    }


def _checkout_payload(
    *,
    email: str = "alice@example.com",
    city: str = "San Francisco",
    billing_same_as_shipping: bool = True,
    billing_address: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "customer_email": email,
        "shipping_address": _sample_address(city=city),
        "billing_same_as_shipping": billing_same_as_shipping,
        "billing_address": billing_address,
    }


def _seed_variant(
    db_session: Session,
    *,
    sku: str | None = None,
    price_cents: int = 4500,
    currency: str = "USD",
    on_hand: int = 10,
    reserved: int = 0,
    low_stock_threshold: int = 3,
    category_active: bool = True,
    product_status: str = "ACTIVE",
    variant_active: bool = True,
    image_url: str | None = "https://cdn.example.com/item.webp",
) -> tuple[Category, Product, ProductVariant, InventoryStock]:
    suffix = uuid.uuid4().hex[:8]
    category = Category(
        name=f"Category-{suffix}",
        slug=f"cat-{suffix}",
        is_active=category_active,
        sort_order=1,
    )
    db_session.add(category)
    db_session.flush()

    product = Product(
        category_id=category.id,
        title=f"Product-{suffix}",
        slug=f"prod-{suffix}",
        description="Test catalog item",
        status=product_status,
    )
    db_session.add(product)
    db_session.flush()

    if image_url is not None:
        db_session.add(
            ProductImage(
                product_id=product.id,
                url=image_url,
                alt_text="Front",
                sort_order=0,
                is_primary=True,
            )
        )

    variant = ProductVariant(
        product_id=product.id,
        sku=sku or f"SKU-{suffix}",
        title=f"Option-{suffix}",
        attributes_json={"color": "Slate", "size": "M"},
        price_cents=price_cents,
        compare_at_price_cents=None,
        currency=currency,
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


def _seed_expired_pending_order(
    db_session: Session,
    *,
    product: Product,
    variant: ProductVariant,
    stock: InventoryStock,
    quantity: int,
    expired_minutes_ago: int = 5,
) -> Order:
    now = datetime.now(UTC)
    expired_at = now - timedelta(minutes=expired_minutes_ago)
    subtotal = variant.price_cents * quantity
    order = Order(
        order_number=f"ORD-EXP-{uuid.uuid4().hex[:8].upper()}",
        user_id=None,
        customer_email="expired@example.com",
        status="PENDING_PAYMENT",
        payment_status="REQUIRES_PAYMENT",
        fulfillment_status="UNFULFILLED",
        currency=variant.currency,
        subtotal_cents=subtotal,
        discount_cents=0,
        shipping_cents=0,
        tax_cents=0,
        total_cents=subtotal,
        idempotency_key=uuid.uuid4().hex.ljust(64, "a")[:64],
        request_fingerprint=uuid.uuid4().hex.ljust(64, "b")[:64],
        shipping_address_json=_sample_address(),
        billing_address_json=_sample_address(),
        placed_at=expired_at - timedelta(minutes=15),
    )
    db_session.add(order)
    db_session.flush()

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
            quantity=quantity,
            line_total_cents=subtotal,
        )
    )
    db_session.add(
        InventoryReservation(
            order_id=order.id,
            variant_id=variant.id,
            quantity=quantity,
            status="ACTIVE",
            expires_at=expired_at,
        )
    )
    db_session.add(
        PaymentIntent(
            order_id=order.id,
            provider="sandbox_simpay",
            provider_intent_id=f"pi_sandbox_{uuid.uuid4().hex[:16]}",
            idempotency_key=uuid.uuid4().hex.ljust(64, "c")[:64],
            request_fingerprint=uuid.uuid4().hex.ljust(64, "d")[:64],
            amount_cents=subtotal,
            currency=variant.currency,
            status="REQUIRES_ACTION",
        )
    )
    stock.reserved += quantity
    db_session.commit()
    return order


def test_cart_get_guest_cookie_creation_reuse_and_auth_fail_closed(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """GET /cart issues hashed HttpOnly guest cookie, binds one user cart, and fails closed."""
    # 1. Anonymous GET /cart issues ecom_guest_cart cookie and hashes token in DB
    guest_resp = client.get("/api/v1/cart")
    assert guest_resp.status_code == 200, guest_resp.text
    guest_body = guest_resp.json()
    assert guest_body["currency"] == "USD"
    assert guest_body["items"] == []
    assert guest_body["item_count"] == 0
    assert guest_body["subtotal_cents"] == 0
    assert guest_body["estimated_shipping_cents"] == 0
    assert guest_body["estimated_total_cents"] == 0

    cookies_by_name = _set_cookie_headers_by_name(guest_resp.headers.get_list("set-cookie"))
    guest_cookie_header = cookies_by_name[GUEST_CART_COOKIE_NAME]
    assert "httponly" in guest_cookie_header.lower()
    assert "samesite=lax" in guest_cookie_header.lower()
    assert "path=/" in guest_cookie_header.lower()
    assert "max-age=2592000" in guest_cookie_header.lower()

    raw_guest_token = client.cookies.get(GUEST_CART_COOKIE_NAME)
    assert raw_guest_token is not None and len(raw_guest_token) > 20
    db_guest_cart = db_session.scalar(
        select(Cart).where(Cart.guest_token_hash == hash_token(raw_guest_token))
    )
    assert db_guest_cart is not None
    assert db_guest_cart.id == uuid.UUID(guest_body["id"])
    assert db_guest_cart.user_id is None
    assert db_guest_cart.status == "ACTIVE"

    # 2. Subsequent GET /cart reuses existing guest cart without re-issuing cookie
    reuse_resp = client.get("/api/v1/cart")
    assert reuse_resp.status_code == 200
    assert reuse_resp.json()["id"] == guest_body["id"]
    assert GUEST_CART_COOKIE_NAME not in _set_cookie_headers_by_name(
        reuse_resp.headers.get_list("set-cookie")
    )

    # 3. Expired guest cart transitions to ABANDONED and issues a fresh ACTIVE cart
    db_guest_cart.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    renewed_resp = client.get("/api/v1/cart")
    assert renewed_resp.status_code == 200
    assert renewed_resp.json()["id"] != guest_body["id"]
    assert GUEST_CART_COOKIE_NAME in _set_cookie_headers_by_name(
        renewed_resp.headers.get_list("set-cookie")
    )
    renewed_guest_token = client.cookies.get(GUEST_CART_COOKIE_NAME)
    assert renewed_guest_token is not None
    db_session.expire_all()
    expired_old = db_session.get(Cart, db_guest_cart.id)
    assert expired_old is not None and expired_old.status == "ABANDONED"

    # 4. Stale/invalid ecom_session fails closed (401 + clears auth cookies) even with guest cart
    client.cookies.set(SESSION_COOKIE_NAME, "stale-invalid-session-token")
    stale_resp = client.get("/api/v1/cart")
    assert stale_resp.status_code == 401
    assert stale_resp.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"
    cleared = _set_cookie_headers_by_name(stale_resp.headers.get_list("set-cookie"))
    assert SESSION_COOKIE_NAME in cleared
    assert CSRF_COOKIE_NAME in cleared

    # 5. Authenticated user gets single ACTIVE cart and no guest cookie
    auth_session = login_as(client, role="customer")
    user_cart_resp = client.get("/api/v1/cart")
    assert user_cart_resp.status_code == 200
    user_cart_id = user_cart_resp.json()["id"]
    assert GUEST_CART_COOKIE_NAME not in _set_cookie_headers_by_name(
        user_cart_resp.headers.get_list("set-cookie")
    )
    assert client.get("/api/v1/cart").json()["id"] == user_cart_id
    db_user_carts = list(
        db_session.scalars(
            select(Cart).where(
                Cart.user_id == uuid.UUID(auth_session.user_id),
                Cart.status == "ACTIVE",
            )
        ).all()
    )
    assert len(db_user_carts) == 1
    assert db_user_carts[0].guest_token_hash is None


def test_cart_get_bounded_sql_query_budget_and_lazy_reclaim(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """GET /cart executes bounded SQL (zero N+1 across items) and reclaims expired holds."""
    session = login_as(client, role="customer")
    seeded = [
        _seed_variant(
            db_session,
            sku=f"BUDGET-{idx}",
            price_cents=1500 + idx * 100,
            on_hand=5,
            reserved=0,
            image_url=f"https://cdn.example.com/item-{idx}.webp",
        )
        for idx in range(5)
    ]

    # Add 1st item and record SQL query count for GET /cart
    _, first_prod, first_var, first_stock = seeded[0]
    add_first = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(first_var.id), "quantity": 1},
        headers=_csrf_headers(session),
    )
    assert add_first.status_code == 200

    engine = db_session.get_bind()

    def _measure_get_cart_sql() -> tuple[int, dict[str, Any]]:
        queries: list[str] = []

        def _capture(
            _conn: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            if not statement.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA")):
                queries.append(statement)

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resp = client.get("/api/v1/cart")
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
        assert resp.status_code == 200
        return len(queries), resp.json()

    count_1_item, _ = _measure_get_cart_sql()

    # Add 4 more distinct items (total 5 items with images & stocks)
    for _, _, var, _ in seeded[1:]:
        add_resp = client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers=_csrf_headers(session),
        )
        assert add_resp.status_code == 200

    count_5_items, body_5_items = _measure_get_cart_sql()
    assert len(body_5_items["items"]) == 5
    # Exact same statement count for 1 item vs 5 items (auth + cart + joined projection + reclaim)
    assert count_5_items == count_1_item
    assert count_5_items <= 4

    # Seed expired reservation holding all remaining 5 units of first_var -> GET /cart reclaims it
    _seed_expired_pending_order(
        db_session,
        product=first_prod,
        variant=first_var,
        stock=first_stock,
        quantity=5,
    )
    reclaimed_resp = client.get("/api/v1/cart")
    assert reclaimed_resp.status_code == 200
    items_by_vid = {item["variant_id"]: item for item in reclaimed_resp.json()["items"]}
    assert items_by_vid[str(first_var.id)]["available_quantity"] == 5
    assert items_by_vid[str(first_var.id)]["is_available"] is True


def test_cart_mutations_csrf_origin_and_availability_guards(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Cart mutations enforce CSRF/Origin (auth + guest) and reject missing/unavailable SKUs."""
    _, _, active_var, _ = _seed_variant(db_session, sku="CART-ACT", on_hand=10)
    _, _, inactive_cat_var, _ = _seed_variant(
        db_session, sku="CART-CATOFF", category_active=False
    )
    _, _, draft_prod_var, _ = _seed_variant(
        db_session, sku="CART-DRAFT", product_status="DRAFT"
    )
    _, _, inactive_var, _ = _seed_variant(db_session, sku="CART-VAROFF", variant_active=False)
    _, _, eur_var, _ = _seed_variant(db_session, sku="CART-EUR", currency="EUR")

    # Guest origin validation on state-changing cart mutations
    assert client.get("/api/v1/cart").status_code == 200
    bad_guest_origin = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(active_var.id), "quantity": 1},
        headers={"Origin": "https://evil.example.com"},
    )
    assert bad_guest_origin.status_code == 403
    assert bad_guest_origin.json()["error"]["code"] == "CSRF_INVALID"

    null_guest_origin = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(active_var.id), "quantity": 1},
        headers={"Origin": "null"},
    )
    assert null_guest_origin.status_code == 403
    assert null_guest_origin.json()["error"]["code"] == "CSRF_INVALID"

    # Guest carrying cookie without Origin/Referer is rejected
    missing_origin_with_cookie = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(active_var.id), "quantity": 1},
    )
    assert missing_origin_with_cookie.status_code == 403
    assert missing_origin_with_cookie.json()["error"]["code"] == "CSRF_INVALID"

    # Authenticated user missing X-CSRF-Token on all 4 cart mutation endpoints -> 403 CSRF_INVALID
    session = login_as(client, role="customer")
    added = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(active_var.id), "quantity": 1},
        headers=_csrf_headers(session),
    )
    assert added.status_code == 200
    item_id = added.json()["items"][0]["id"]

    for method, path, body in (
        ("POST", "/api/v1/cart/items", {"variant_id": str(active_var.id), "quantity": 1}),
        ("PATCH", f"/api/v1/cart/items/{item_id}", {"quantity": 2}),
        ("DELETE", f"/api/v1/cart/items/{item_id}", None),
        ("DELETE", "/api/v1/cart", None),
    ):
        no_csrf = client.request(
            method,
            path,
            json=body,
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert no_csrf.status_code == 403
        assert no_csrf.json()["error"]["code"] == "CSRF_INVALID"

    # Missing variant -> 404 VARIANT_NOT_FOUND
    missing_resp = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(uuid.uuid4()), "quantity": 1},
        headers=_csrf_headers(session),
    )
    assert missing_resp.status_code == 404
    assert missing_resp.json()["error"]["code"] == "VARIANT_NOT_FOUND"

    # Inactive category / non-ACTIVE product / inactive variant / currency mismatch -> 409
    for unavail_var in (inactive_cat_var, draft_prod_var, inactive_var, eur_var):
        unavail_resp = client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(unavail_var.id), "quantity": 1},
            headers=_csrf_headers(session),
        )
        assert unavail_resp.status_code == 409
        assert unavail_resp.json()["error"]["code"] == "VARIANT_UNAVAILABLE"


def test_cart_quantity_validation_stock_bounds_and_idor(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Cart enforces 1..99 bounds, available stock limits, and strict cart-item ownership."""
    _, _, bounded_var, _ = _seed_variant(db_session, sku="STK-LIM", on_hand=5)
    _, _, high_stock_var, _ = _seed_variant(db_session, sku="STK-HIGH", on_hand=150)

    session_a = login_as(client, role="customer", email="cart-a@example.com")

    # Schema validation failures -> 422 VALIDATION_ERROR
    for bad_qty in (0, -2, 100):
        bad_add = client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(bounded_var.id), "quantity": bad_qty},
            headers=_csrf_headers(session_a),
        )
        assert bad_add.status_code == 422
        assert bad_add.json()["error"]["code"] == "VALIDATION_ERROR"

    # Incremental addition exceeding 99 -> 422 VALIDATION_ERROR
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(high_stock_var.id), "quantity": 60},
            headers=_csrf_headers(session_a),
        ).status_code
        == 200
    )
    over_99 = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(high_stock_var.id), "quantity": 40},
        headers=_csrf_headers(session_a),
    )
    assert over_99.status_code == 422
    assert over_99.json()["error"]["code"] == "VALIDATION_ERROR"

    # Exceeding available_quantity on add & incremental add -> 409 INSUFFICIENT_STOCK with details
    too_many = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(bounded_var.id), "quantity": 6},
        headers=_csrf_headers(session_a),
    )
    assert too_many.status_code == 409
    assert too_many.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert too_many.json()["error"]["details"] == {
        "variant_id": str(bounded_var.id),
        "requested": 6,
        "available": 5,
    }

    ok_add = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(bounded_var.id), "quantity": 3},
        headers=_csrf_headers(session_a),
    )
    assert ok_add.status_code == 200
    bounded_item_id = next(
        row["id"] for row in ok_add.json()["items"] if row["variant_id"] == str(bounded_var.id)
    )

    incr_over = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(bounded_var.id), "quantity": 4},
        headers=_csrf_headers(session_a),
    )
    assert incr_over.status_code == 409
    assert incr_over.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert incr_over.json()["error"]["details"] == {
        "variant_id": str(bounded_var.id),
        "requested": 7,
        "available": 5,
    }

    # PATCH bounds & stock check
    patch_over_99 = client.patch(
        f"/api/v1/cart/items/{bounded_item_id}",
        json={"quantity": 100},
        headers=_csrf_headers(session_a),
    )
    assert patch_over_99.status_code == 422
    assert patch_over_99.json()["error"]["code"] == "VALIDATION_ERROR"

    patch_over_stock = client.patch(
        f"/api/v1/cart/items/{bounded_item_id}",
        json={"quantity": 8},
        headers=_csrf_headers(session_a),
    )
    assert patch_over_stock.status_code == 409
    assert patch_over_stock.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert patch_over_stock.json()["error"]["details"] == {
        "variant_id": str(bounded_var.id),
        "requested": 8,
        "available": 5,
    }

    # Authenticated IDOR: User B cannot PATCH or DELETE User A's cart item
    session_b = login_as(client, role="customer", email="cart-b@example.com")
    idor_patch = client.patch(
        f"/api/v1/cart/items/{bounded_item_id}",
        json={"quantity": 1},
        headers=_csrf_headers(session_b),
    )
    assert idor_patch.status_code == 404
    assert idor_patch.json()["error"]["code"] == "CART_ITEM_NOT_FOUND"

    idor_delete = client.delete(
        f"/api/v1/cart/items/{bounded_item_id}",
        headers=_csrf_headers(session_b),
    )
    assert idor_delete.status_code == 404
    assert idor_delete.json()["error"]["code"] == "CART_ITEM_NOT_FOUND"

    # User A deletes whole cart
    _use_auth_cookies(client, session_a)
    cleared = client.delete("/api/v1/cart", headers=_csrf_headers(session_a))
    assert cleared.status_code == 200
    assert cleared.json()["items"] == []
    assert cleared.json()["item_count"] == 0

    # Guest IDOR: Guest 2 cannot PATCH or DELETE Guest 1's cart item
    client.cookies.clear()
    g1_add = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(bounded_var.id), "quantity": 2},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert g1_add.status_code == 200
    g1_item_id = g1_add.json()["items"][0]["id"]
    g1_cookie = client.cookies.get(GUEST_CART_COOKIE_NAME)
    assert g1_cookie is not None

    client.cookies.clear()
    assert client.get("/api/v1/cart").status_code == 200
    g2_patch = client.patch(
        f"/api/v1/cart/items/{g1_item_id}",
        json={"quantity": 1},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert g2_patch.status_code == 404
    assert g2_patch.json()["error"]["code"] == "CART_ITEM_NOT_FOUND"

    g2_del = client.delete(
        f"/api/v1/cart/items/{g1_item_id}",
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert g2_del.status_code == 404
    assert g2_del.json()["error"]["code"] == "CART_ITEM_NOT_FOUND"

    # Guest 1 can delete its own item cleanly
    client.cookies.clear()
    client.cookies.set(GUEST_CART_COOKIE_NAME, g1_cookie)
    g1_del = client.delete(
        f"/api/v1/cart/items/{g1_item_id}",
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert g1_del.status_code == 200
    assert g1_del.json()["items"] == []


def test_cart_live_pricing_shipping_threshold_and_availability_projection(
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /cart reflects live DB price/stock/status changes and shipping threshold rules."""
    _, _, var, _ = _seed_variant(db_session, sku="LIVE-PRC", price_cents=4500, on_hand=10)

    # Subtotal 9000 (< 10000) -> shipping 1000
    add_2 = client.post(
        "/api/v1/cart/items",
        json={"variant_id": str(var.id), "quantity": 2},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert add_2.status_code == 200
    b2 = add_2.json()
    assert b2["subtotal_cents"] == 9000
    assert b2["estimated_shipping_cents"] == 1000
    assert b2["estimated_total_cents"] == 10000
    assert b2["items"][0]["is_available"] is True

    # Live DB price update to 5000 -> subtotal 10000 (>= 10000) -> free shipping (0)
    var.price_cents = 5000
    db_session.commit()

    refreshed = client.get("/api/v1/cart")
    assert refreshed.status_code == 200
    br = refreshed.json()
    assert br["items"][0]["unit_price_cents"] == 5000
    assert br["items"][0]["line_total_cents"] == 10000
    assert br["subtotal_cents"] == 10000
    assert br["estimated_shipping_cents"] == 0
    assert br["estimated_total_cents"] == 10000

    # Disabling variant in DB immediately flags is_available=False in GET /cart
    var.is_active = False
    db_session.commit()
    disabled_view = client.get("/api/v1/cart")
    assert disabled_view.status_code == 200
    assert disabled_view.json()["items"][0]["is_available"] is False


def test_cart_merge_auth_csrf_idempotent_noop_and_stock_clamping(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """POST /cart/merge enforces auth/CSRF, handles no-ops, clamps to min(99, avail), converts."""
    _, _, var_clamp_stock, _ = _seed_variant(db_session, sku="MRG-STK", on_hand=5)
    _, _, var_clamp_99, _ = _seed_variant(db_session, sku="MRG-99", on_hand=200)
    _, _, var_guest_only, _ = _seed_variant(db_session, sku="MRG-ONLY", on_hand=8)
    _, _, var_becomes_off, stock_off = _seed_variant(db_session, sku="MRG-OFF", on_hand=4)

    # Unauthenticated -> 401
    assert (
        client.post("/api/v1/cart/merge", headers={"Origin": DEFAULT_ORIGIN}).status_code == 401
    )

    session = login_as(client, role="customer", email="merge-user@example.com")
    assert (
        client.post("/api/v1/cart/merge", headers={"Origin": DEFAULT_ORIGIN}).status_code == 403
    )

    # Idempotent no-op when no guest cookie is present
    noop_no_cookie = client.post("/api/v1/cart/merge", headers=_csrf_headers(session))
    assert noop_no_cookie.status_code == 200
    assert noop_no_cookie.json()["items"] == []

    # Missing/unknown guest cookie -> 200 OK no-op & clears ecom_guest_cart
    client.cookies.set(GUEST_CART_COOKIE_NAME, "unknown-guest-cart-token")
    noop_unknown = client.post("/api/v1/cart/merge", headers=_csrf_headers(session))
    assert noop_unknown.status_code == 200
    assert GUEST_CART_COOKIE_NAME in _set_cookie_headers_by_name(
        noop_unknown.headers.get_list("set-cookie")
    )

    # Seed user's existing items before guest cart merge
    for vid, qty in ((var_clamp_stock.id, 3), (var_clamp_99.id, 60)):
        assert (
            client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(vid), "quantity": qty},
                headers=_csrf_headers(session),
            ).status_code
            == 200
        )

    # Switch to anonymous guest to create an empty guest cart first
    client.cookies.clear()
    assert client.get("/api/v1/cart").status_code == 200
    empty_guest_token = client.cookies.get(GUEST_CART_COOKIE_NAME)
    assert empty_guest_token is not None

    # Merge empty guest cart -> marks CONVERTED, clears cookie, leaves user's 2 items intact
    _use_auth_cookies(client, session)
    client.cookies.set(GUEST_CART_COOKIE_NAME, empty_guest_token)
    merge_empty = client.post("/api/v1/cart/merge", headers=_csrf_headers(session))
    assert merge_empty.status_code == 200
    assert len(merge_empty.json()["items"]) == 2
    db_session.expire_all()
    converted_empty = db_session.scalar(
        select(Cart).where(Cart.guest_token_hash == hash_token(empty_guest_token))
    )
    assert converted_empty is not None and converted_empty.status == "CONVERTED"

    # Now create an active guest cart with overlapping + new + out-of-stock items
    client.cookies.clear()
    for vid, qty in (
        (var_clamp_stock.id, 4),  # 3 + 4 = 7 -> clamped to available=5
        (var_clamp_99.id, 50),  # 60 + 50 = 110 -> clamped to 99
        (var_guest_only.id, 2),
        (var_becomes_off.id, 2),
    ):
        r = client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(vid), "quantity": qty},
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert r.status_code == 200
    full_guest_token = client.cookies.get(GUEST_CART_COOKIE_NAME)
    assert full_guest_token is not None

    # Drop stock of var_becomes_off to 0 before merge
    stock_off.on_hand = 0
    db_session.commit()

    _use_auth_cookies(client, session)
    client.cookies.set(GUEST_CART_COOKIE_NAME, full_guest_token)
    merged = client.post("/api/v1/cart/merge", headers=_csrf_headers(session))
    assert merged.status_code == 200, merged.text
    merged_items = {row["variant_id"]: row["quantity"] for row in merged.json()["items"]}
    assert merged_items == {
        str(var_clamp_stock.id): 5,
        str(var_clamp_99.id): 99,
        str(var_guest_only.id): 2,
    }
    assert GUEST_CART_COOKIE_NAME in _set_cookie_headers_by_name(
        merged.headers.get_list("set-cookie")
    )

    db_session.expire_all()
    merged_guest_cart = db_session.scalar(
        select(Cart).where(Cart.guest_token_hash == hash_token(full_guest_token))
    )
    assert merged_guest_cart is not None and merged_guest_cart.status == "CONVERTED"

    # Replaying merge with already-CONVERTED guest cookie is an idempotent 200 no-op
    client.cookies.set(GUEST_CART_COOKIE_NAME, full_guest_token)
    replay_merge = client.post("/api/v1/cart/merge", headers=_csrf_headers(session))
    assert replay_merge.status_code == 200
    assert {row["variant_id"]: row["quantity"] for row in replay_merge.json()["items"]} == (
        merged_items
    )


def test_checkout_rejects_forged_totals_invalid_auth_missing_csrf_and_bad_idempotency_key(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Checkout rejects forged totals (422), stale auth (401), CSRF (403), missing key (400)."""
    _, _, var, _ = _seed_variant(db_session, sku="CHK-VAL", price_cents=4000, on_hand=10)
    session = login_as(client, role="customer")
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )

    # Forged monetary fields rejected by extra="forbid" -> 422 VALIDATION_ERROR
    for forged_field in (
        {"subtotal_cents": 1},
        {"discount_cents": 3999},
        {"shipping_cents": 0},
        {"tax_cents": 0},
        {"total_cents": 1},
        {"unit_price_cents": 1},
    ):
        bad_payload = {**_checkout_payload(), **forged_field}
        resp = client.post(
            "/api/v1/checkout/orders",
            json=bad_payload,
            headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # billing_same_as_shipping=False without billing_address -> 422
    no_billing = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(billing_same_as_shipping=False, billing_address=None),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert no_billing.status_code == 422
    assert no_billing.json()["error"]["code"] == "VALIDATION_ERROR"

    # Missing X-CSRF-Token when authenticated -> 403 CSRF_INVALID
    no_csrf = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_INVALID"

    # Missing or blank Idempotency-Key -> 400 IDEMPOTENCY_KEY_REQUIRED
    for bad_headers in (
        _csrf_headers(session),
        _csrf_headers(session, extra={"Idempotency-Key": "   "}),
        _csrf_headers(session, extra={"Idempotency-Key": "k" * 65}),
    ):
        missing_key = client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(),
            headers=bad_headers,
        )
        assert missing_key.status_code == 400
        assert missing_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    # Empty cart -> 409 CART_EMPTY
    assert client.delete("/api/v1/cart", headers=_csrf_headers(session)).status_code == 200
    empty_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert empty_resp.status_code == 409
    assert empty_resp.json()["error"]["code"] == "CART_EMPTY"

    # Stale ecom_session fails closed (401 + cookie clear) even on guest checkout
    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )
    bad_origin = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers={"Origin": "https://evil.example.com", "Idempotency-Key": uuid.uuid4().hex},
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["error"]["code"] == "CSRF_INVALID"

    client.cookies.set(SESSION_COOKIE_NAME, "expired-session-cookie")
    stale = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"
    cleared = _set_cookie_headers_by_name(stale.headers.get_list("set-cookie"))
    assert SESSION_COOKIE_NAME in cleared
    assert CSRF_COOKIE_NAME in cleared


def test_checkout_atomic_rollback_on_unavailable_variant_and_insufficient_stock(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Checkout rolls back cleanly (zero Order/Reservation/Stock change) on failure."""
    _, _, v_ok, _ = _seed_variant(db_session, sku="RB-OK", price_cents=3000, on_hand=5)
    _, _, v_fail, s_fail = _seed_variant(db_session, sku="RB-FAIL", price_cents=2000, on_hand=3)

    session = login_as(client, role="customer")
    for vid, qty in ((v_ok.id, 2), (v_fail.id, 3)):
        assert (
            client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(vid), "quantity": qty},
                headers=_csrf_headers(session),
            ).status_code
            == 200
        )

    # Stock drops below cart quantity on v_fail -> 409 INSUFFICIENT_STOCK
    s_fail.on_hand = 2
    db_session.commit()

    insuff_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert insuff_resp.status_code == 409
    assert insuff_resp.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert insuff_resp.json()["error"]["details"] == {
        "variant_id": str(v_fail.id),
        "requested": 3,
        "available": 2,
    }

    # Restore stock, deactivate v_fail -> 409 VARIANT_UNAVAILABLE
    s_fail.on_hand = 5
    v_fail.is_active = False
    db_session.commit()

    unavail_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert unavail_resp.status_code == 409
    assert unavail_resp.json()["error"]["code"] == "VARIANT_UNAVAILABLE"

    # Zero orders, reservations, payment intents, or reserved stock mutations persisted
    db_session.expire_all()
    assert list(db_session.scalars(select(Order)).all()) == []
    assert list(db_session.scalars(select(InventoryReservation)).all()) == []
    assert list(db_session.scalars(select(PaymentIntent)).all()) == []
    assert list(db_session.scalars(select(InventoryLedgerEntry)).all()) == []
    reloaded_ok = db_session.get(InventoryStock, v_ok.id)
    reloaded_fail = db_session.get(InventoryStock, v_fail.id)
    assert reloaded_ok is not None and reloaded_ok.reserved == 0
    assert reloaded_fail is not None and reloaded_fail.reserved == 0
    cart = db_session.scalar(select(Cart))
    assert cart is not None and cart.status == "ACTIVE"


def test_checkout_server_authoritative_pricing_shipping_and_round_half_up_tax(
    db_session: Session,
    app_factory: Callable[..., FastAPI],
    test_settings: Settings,
) -> None:
    """Checkout computes exact subtotal, shipping threshold ($100), and ROUND_HALF_UP tax."""
    _, _, v_4500, _ = _seed_variant(db_session, sku="MATH-45", price_cents=4500, on_hand=10)
    _, _, v_10000, _ = _seed_variant(db_session, sku="MATH-100", price_cents=10000, on_hand=10)
    _, _, v_50, _ = _seed_variant(db_session, sku="MATH-HALF", price_cents=50, on_hand=10)

    with TestClient(app_factory()) as default_client:
        # 1. Subtotal 9000 (< 10000) -> shipping 1000, 8% tax 720, total 10720
        assert (
            default_client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(v_4500.id), "quantity": 2},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        r1 = default_client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert r1.status_code == 201
        o1 = r1.json()["order"]
        assert (o1["subtotal_cents"], o1["discount_cents"], o1["shipping_cents"]) == (9000, 0, 1000)
        assert (o1["tax_cents"], o1["total_cents"]) == (720, 10720)

        # 2. Exact free shipping boundary: subtotal 10000 -> shipping 0, 8% tax 800, total 10800
        default_client.cookies.delete(GUEST_CART_COOKIE_NAME)
        assert (
            default_client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(v_10000.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        r2 = default_client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert r2.status_code == 201
        o2 = r2.json()["order"]
        assert (
            o2["subtotal_cents"],
            o2["shipping_cents"],
            o2["tax_cents"],
            o2["total_cents"],
        ) == (10000, 0, 800, 10800)

    # 3. Verify ROUND_HALF_UP (2.5 -> 3, NOT banker's 2) using 5% tax rate on 50 cents
    half_up_settings = test_settings.model_copy(update={"tax_rate_bps": 500})
    with TestClient(app_factory(half_up_settings)) as half_client:
        assert (
            half_client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(v_50.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        r3 = half_client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert r3.status_code == 201
        o3 = r3.json()["order"]
        assert o3["subtotal_cents"] == 50
        assert o3["shipping_cents"] == 1000
        assert o3["tax_cents"] == 3
        assert o3["total_cents"] == 1053


def test_checkout_atomic_persistence_guest_cookie_and_authenticated_null_token(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Checkout atomically persists order, snapshots, 15m reservation, ledger, and cookies."""
    _, prod, var, _ = _seed_variant(db_session, sku="ATOM-01", price_cents=4500, on_hand=20)

    # Guest checkout
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 2},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )
    guest_cart_cookie = client.cookies.get(GUEST_CART_COOKIE_NAME)
    assert guest_cart_cookie is not None

    resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(email="  ALICE@Example.COM "),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    order_dto = body["order"]
    intent_dto = body["payment_intent"]
    assert order_dto["status"] == "PENDING_PAYMENT"
    assert order_dto["payment_status"] == "REQUIRES_PAYMENT"
    assert order_dto["fulfillment_status"] == "UNFULFILLED"
    assert order_dto["customer_email"] == "alice@example.com"
    assert order_dto["total_cents"] == 10720
    assert intent_dto["provider"] == "sandbox_simpay"
    assert intent_dto["status"] == "REQUIRES_ACTION"
    assert intent_dto["amount_cents"] == 10720
    assert isinstance(body["guest_access_token"], str) and len(body["guest_access_token"]) == 64

    cookies_by_name = _set_cookie_headers_by_name(resp.headers.get_list("set-cookie"))
    guest_orders_header = cookies_by_name[GUEST_ORDERS_COOKIE_NAME]
    assert "httponly" in guest_orders_header.lower()
    assert "samesite=lax" in guest_orders_header.lower()
    assert "path=/api/v1" in guest_orders_header.lower()
    assert "max-age=2592000" in guest_orders_header.lower()

    db_session.expire_all()
    order_id = uuid.UUID(order_dto["id"])
    db_order = db_session.get(Order, order_id)
    assert db_order is not None and db_order.user_id is None
    assert db_order.guest_access_expires_at is not None

    order_items = list(
        db_session.scalars(select(OrderItem).where(OrderItem.order_id == order_id)).all()
    )
    assert len(order_items) == 1
    assert order_items[0].sku_snapshot == "ATOM-01"
    assert order_items[0].product_title_snapshot == prod.title
    assert order_items[0].unit_price_cents == 4500

    order_events = list(
        db_session.scalars(select(OrderEvent).where(OrderEvent.order_id == order_id)).all()
    )
    assert len(order_events) == 1 and order_events[0].event_type == "ORDER_PLACED"

    res = db_session.scalar(
        select(InventoryReservation).where(InventoryReservation.order_id == order_id)
    )
    assert res is not None and res.status == "ACTIVE" and res.quantity == 2
    delta = ensure_utc_datetime(res.expires_at) - ensure_utc_datetime(db_order.placed_at)
    assert timedelta(minutes=14, seconds=50) <= delta <= timedelta(minutes=15, seconds=10)

    stock = db_session.get(InventoryStock, var.id)
    assert stock is not None and stock.reserved == 2 and stock.version == 2
    ledger = db_session.scalar(
        select(InventoryLedgerEntry).where(InventoryLedgerEntry.order_id == order_id)
    )
    assert ledger is not None and ledger.reason == "RESERVE" and ledger.delta_reserved == 2
    assert ledger.reserved_after == 2

    cart = db_session.scalar(
        select(Cart).where(Cart.guest_token_hash == hash_token(guest_cart_cookie))
    )
    assert cart is not None and cart.status == "CONVERTED"

    # Bounded ecom_guest_orders cookie (max 5 pairs): place 5 more guest orders & cancel
    placed_order_numbers = [order_dto["order_number"]]
    for idx in range(5):
        prev_num = placed_order_numbers[-1]
        assert (
            client.post(
                f"/api/v1/orders/{prev_num}/cancel",
                json={"reason": "Rotating cookie check"},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        client.cookies.delete(GUEST_CART_COOKIE_NAME)
        assert (
            client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(var.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        next_resp = client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(email=f"guest-{idx}@example.com"),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert next_resp.status_code == 201
        placed_order_numbers.append(next_resp.json()["order"]["order_number"])

    cookie_pairs = parse_guest_orders_cookie(client.cookies.get(GUEST_ORDERS_COOKIE_NAME))
    assert [num for num, _ in cookie_pairs] == placed_order_numbers[-5:]
    # Evicted 1st order is no longer accessible via cookie alone (403)
    assert client.get(f"/api/v1/orders/{placed_order_numbers[0]}").status_code == 403
    # Still accessible with its explicit X-Guest-Order-Token header!
    assert (
        client.get(
            f"/api/v1/orders/{placed_order_numbers[0]}",
            headers={"X-Guest-Order-Token": body["guest_access_token"]},
        ).status_code
        == 200
    )

    # Authenticated checkout returns guest_access_token=None and sets no guest orders cookie
    session = login_as(client, role="customer")
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )
    auth_checkout = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert auth_checkout.status_code == 201
    auth_body = auth_checkout.json()
    assert auth_body["guest_access_token"] is None
    assert GUEST_ORDERS_COOKIE_NAME not in _set_cookie_headers_by_name(
        auth_checkout.headers.get_list("set-cookie")
    )
    auth_db_order = db_session.get(Order, uuid.UUID(auth_body["order"]["id"]))
    assert auth_db_order is not None
    assert auth_db_order.user_id == uuid.UUID(session.user_id)


def test_checkout_idempotent_replay_and_conflict_rules(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Checkout replays return 200 OK post-conversion and reject payload/cart mismatches."""
    _, _, var1, _ = _seed_variant(db_session, sku="IDEM-01", price_cents=5000, on_hand=10)
    _, _, var2, _ = _seed_variant(db_session, sku="IDEM-02", price_cents=2500, on_hand=10)

    # 1. Guest idempotent replay after cart CONVERTED + payload mismatch -> 409
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var1.id), "quantity": 1},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )
    guest_key = f"guest-key-{uuid.uuid4().hex[:8]}"
    first_guest = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": guest_key},
    )
    assert first_guest.status_code == 201
    first_guest_body = first_guest.json()

    replay_guest = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": guest_key},
    )
    assert replay_guest.status_code == 200
    assert replay_guest.json() == first_guest_body

    diff_payload_guest = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(city="Oakland"),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": guest_key},
    )
    assert diff_payload_guest.status_code == 409
    assert diff_payload_guest.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    # 2. Authenticated replay works with empty new cart, conflicts if new cart has items
    session = login_as(client, role="customer")
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var1.id), "quantity": 2},
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )
    auth_key = f"user-key-{uuid.uuid4().hex[:8]}"
    auth_first = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": auth_key}),
    )
    assert auth_first.status_code == 201

    # Creating an empty new active cart via GET /cart still permits exact replay
    assert client.get("/api/v1/cart").status_code == 200
    auth_replay = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": auth_key}),
    )
    assert auth_replay.status_code == 200
    assert auth_replay.json()["order"]["id"] == auth_first.json()["order"]["id"]

    # Adding a new item to the new cart and reusing auth_key -> 409 IDEMPOTENCY_KEY_CONFLICT
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var2.id), "quantity": 1},
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )
    modified_cart_conflict = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": auth_key}),
    )
    assert modified_cart_conflict.status_code == 409
    assert modified_cart_conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    db_session.expire_all()
    stock1 = db_session.get(InventoryStock, var1.id)
    assert stock1 is not None and stock1.reserved == 3  # 1 (guest) + 2 (auth), zero duplicates


def test_checkout_lazy_reclaims_expired_pending_reservation_before_stock_check(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Checkout lazily reclaims expired reservations blocking SKU stock before reserving."""
    _, prod, var, stock = _seed_variant(db_session, sku="RECLAIM-CHK", on_hand=2, reserved=0)
    expired_order = _seed_expired_pending_order(
        db_session,
        product=prod,
        variant=var,
        stock=stock,
        quantity=2,
    )

    session = login_as(client, role="customer")
    # Seed CartItem directly after expired order so checkout's own pre-step triggers reclamation.
    user_cart = Cart(
        user_id=uuid.UUID(session.user_id),
        status="ACTIVE",
        currency="USD",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(user_cart)
    db_session.flush()
    db_session.add(CartItem(cart_id=user_cart.id, variant_id=var.id, quantity=2))
    db_session.commit()

    resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert resp.status_code == 201, resp.text
    new_order_id = uuid.UUID(resp.json()["order"]["id"])

    db_session.expire_all()
    reloaded_old = db_session.get(Order, expired_order.id)
    assert reloaded_old is not None and reloaded_old.status == "CANCELLED"
    assert reloaded_old.cancellation_reason == "RESERVATION_EXPIRED"

    reloaded_stock = db_session.get(InventoryStock, var.id)
    assert reloaded_stock is not None and reloaded_stock.reserved == 2

    new_res = db_session.scalar(
        select(InventoryReservation).where(InventoryReservation.order_id == new_order_id)
    )
    assert new_res is not None and new_res.status == "ACTIVE" and new_res.quantity == 2


def test_checkout_guest_max_3_active_pending_orders_and_tracker_isolation(
    db_session: Session,
    app_factory: Callable[..., FastAPI],
) -> None:
    """Guest max-3 active PENDING_PAYMENT cap returns 429; tracker resets per app instance."""
    _, _, var, _ = _seed_variant(db_session, sku="GCAP-01", on_hand=20)

    with TestClient(app_factory()) as guest_client:
        placed_orders: list[tuple[str, str]] = []
        last_guest_cart_token: str | None = None
        last_idem_key = ""

        for idx in range(3):
            guest_client.cookies.delete(GUEST_CART_COOKIE_NAME)
            assert (
                guest_client.post(
                    "/api/v1/cart/items",
                    json={"variant_id": str(var.id), "quantity": 1},
                    headers={"Origin": DEFAULT_ORIGIN},
                ).status_code
                == 200
            )
            last_guest_cart_token = guest_client.cookies.get(GUEST_CART_COOKIE_NAME)
            last_idem_key = f"gcap-key-{idx}-{uuid.uuid4().hex[:6]}"
            resp = guest_client.post(
                "/api/v1/checkout/orders",
                json=_checkout_payload(email=f"cap-{idx}@example.com"),
                headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": last_idem_key},
            )
            assert resp.status_code == 201
            placed_orders.append(
                (resp.json()["order"]["order_number"], resp.json()["guest_access_token"])
            )

        # Replaying the 3rd order's Idempotency-Key still succeeds with 200 OK
        assert last_guest_cart_token is not None
        guest_client.cookies.set(GUEST_CART_COOKIE_NAME, last_guest_cart_token)
        replay_3rd = guest_client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(email="cap-2@example.com"),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": last_idem_key},
        )
        assert replay_3rd.status_code == 200

        # 4th distinct guest checkout on the same app/tracker -> 429 RATE_LIMIT_EXCEEDED
        guest_client.cookies.delete(GUEST_CART_COOKIE_NAME)
        assert (
            guest_client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(var.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        cap_resp = guest_client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(email="cap-4@example.com"),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert cap_resp.status_code == 429
        assert cap_resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert cap_resp.json()["error"]["details"] == {"max_active_pending_orders": 3}

        # Cancelling 1 pending order frees a slot -> 4th checkout now succeeds
        cancel_resp = guest_client.post(
            f"/api/v1/orders/{placed_orders[0][0]}/cancel",
            json={"reason": "Freeing slot"},
            headers={"Origin": DEFAULT_ORIGIN, "X-Guest-Order-Token": placed_orders[0][1]},
        )
        assert cancel_resp.status_code == 200

        retry_4th = guest_client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(email="cap-4@example.com"),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert retry_4th.status_code == 201

    # Separate app_factory() / TestClient instance gets a fresh app.state.guest_pending_tracker
    with TestClient(app_factory()) as separate_client:
        assert (
            separate_client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(var.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        sep_resp = separate_client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(email="separate@example.com"),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert sep_resp.status_code == 201


@pytest.mark.parametrize("bad_city", ["Bad\x00City", "Line1\nLine2", "   "])
def test_checkout_rejects_control_characters_and_blank_address_fields(
    client: TestClient,
    db_session: Session,
    bad_city: str,
) -> None:
    """AddressDTO rejects control characters, NUL bytes, and whitespace-only fields."""
    _, _, var, _ = _seed_variant(db_session, sku="ADDR-CHK", on_hand=5)
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )
    resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(city=bad_city),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_checkout_rejects_int32_overflow_with_clean_rollback(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """LOW-1: Checkout rejects 32-bit signed integer overflow (> INT32_MAX) with clean rollback."""
    _, _, v_line_over, s_line_over = _seed_variant(
        db_session,
        sku="INT32-LINE",
        price_cents=2_000_000_000,
        on_hand=10,
        reserved=0,
    )
    _, _, v_total_over, s_total_over = _seed_variant(
        db_session,
        sku="INT32-TOT",
        price_cents=2_050_000_000,
        on_hand=10,
        reserved=0,
    )

    session = login_as(client, role="customer", email="int32-user@example.com")

    # 1. line_total = 2_000_000_000 * 2 = 4_000_000_000 > INT32_MAX -> 422 VALIDATION_ERROR
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(v_line_over.id), "quantity": 2},
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )
    over_line_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert over_line_resp.status_code == 422
    assert over_line_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # Verify clean rollback: zero orders/reservations/ledger, active cart, reserved == 0
    db_session.expire_all()
    assert list(db_session.scalars(select(Order)).all()) == []
    assert list(db_session.scalars(select(InventoryReservation)).all()) == []
    assert list(db_session.scalars(select(PaymentIntent)).all()) == []
    assert list(db_session.scalars(select(InventoryLedgerEntry)).all()) == []
    db_session.refresh(s_line_over)
    assert s_line_over.reserved == 0
    user_cart = db_session.scalar(
        select(Cart).where(Cart.user_id == uuid.UUID(session.user_id), Cart.status == "ACTIVE")
    )
    assert user_cart is not None and user_cart.status == "ACTIVE"

    # 2. subtotal=2_050_000_000 <= INT32_MAX, but +8% tax -> total=2_214_000_000 > INT32_MAX -> 422
    assert client.delete("/api/v1/cart", headers=_csrf_headers(session)).status_code == 200
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(v_total_over.id), "quantity": 1},
            headers=_csrf_headers(session),
        ).status_code
        == 200
    )
    over_total_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(),
        headers=_csrf_headers(session, extra={"Idempotency-Key": uuid.uuid4().hex}),
    )
    assert over_total_resp.status_code == 422
    assert over_total_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    db_session.expire_all()
    assert list(db_session.scalars(select(Order)).all()) == []
    db_session.refresh(s_total_over)
    assert s_total_over.reserved == 0


def test_checkout_forged_guest_orders_cookie_pair_does_not_count_toward_cap(
    client: TestClient,
    db_session: Session,
    app: FastAPI,
) -> None:
    """LOW-3: Forged ecom_guest_orders cookie entries are ignored by guest pending order cap."""
    _, _, var, _ = _seed_variant(db_session, sku="FORGED-GCAP", on_hand=20)

    # 1. Guest A places one real PENDING_PAYMENT order (ORD-GA)
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )
    ga_resp = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(email="guest-a@example.com"),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert ga_resp.status_code == 201
    ord_ga = str(ga_resp.json()["order"]["order_number"])

    # Clear IP pending-order tracker & Guest A cookies so only forged cookie carries ORD-GA
    tracker = app.state.guest_pending_tracker
    assert isinstance(tracker, GuestPendingOrderTracker)
    tracker.reset()
    client.cookies.clear()

    # 2. Guest B places 2 valid orders, then sends ORD-GA:forged-token alongside its 2 valid pairs
    guest_b_pairs: list[tuple[str, str]] = []
    for idx in range(2):
        client.cookies.delete(GUEST_CART_COOKIE_NAME)
        assert (
            client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(var.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            ).status_code
            == 200
        )
        gb_resp = client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(email=f"guest-b-{idx}@example.com"),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert gb_resp.status_code == 201
        gb_body = gb_resp.json()
        guest_b_pairs.append(
            (str(gb_body["order"]["order_number"]), str(gb_body["guest_access_token"]))
        )

    forged_cookie_value = "|".join(
        [f"{ord_ga}:forged-token", *(f"{num}:{tok}" for num, tok in guest_b_pairs)]
    )
    client.cookies.delete(GUEST_CART_COOKIE_NAME)
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )
    client.cookies.delete(GUEST_ORDERS_COOKIE_NAME)
    client.cookies.set(GUEST_ORDERS_COOKIE_NAME, forged_cookie_value, path="/api/v1")

    # 3rd checkout for Guest B MUST succeed (201) because ORD-GA:forged-token failed HMAC check
    gb_3rd = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(email="guest-b-3rd@example.com"),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert gb_3rd.status_code == 201, gb_3rd.text

    # 4th checkout for Guest B now hits the real 3-order cap (429 RATE_LIMIT_EXCEEDED)
    client.cookies.delete(GUEST_CART_COOKIE_NAME)
    assert (
        client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(var.id), "quantity": 1},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 200
    )
    gb_4th = client.post(
        "/api/v1/checkout/orders",
        json=_checkout_payload(email="guest-b-4th@example.com"),
        headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert gb_4th.status_code == 429
    assert gb_4th.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
