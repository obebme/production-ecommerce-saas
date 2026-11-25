"""Comprehensive QA tests for P1/P2 production hardening controls (HARDEN-QA-01)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
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
    SESSION_COOKIE_NAME,
    ServiceUnavailableError,
    _request_id_ctx,
)
from app.core.observability import REDACTED_VALUE, StructuredJsonFormatter
from app.core.rate_limit import RedisSlidingWindowRateLimiter
from app.core.security import sign_webhook_payload
from app.main import create_app
from app.models.catalog import Category, Product
from app.models.identity import User
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order
from app.models.payment import PaymentIntent
from app.modules.inventory import service as inventory_service
from app.modules.inventory.service import run_coordinated_expired_reservation_sweep
from app.modules.orders.cancellation import cancel_customer_order
from app.modules.orders.common import MAX_ACTIVE_GUEST_PENDING_ORDERS, SANDBOX_PROVIDER
from app.modules.payments.adapters.simpay import SandboxSimpayAdapter
from app.modules.payments.provider import (
    ProviderIntentResult,
    ProviderStatusResult,
    get_default_payment_registry,
)
from app.modules.payments.reconciliation import reconcile_payment_intent
from app.modules.payments.schemas import WebhookPayload

BACKEND_DIR = Path(__file__).resolve().parent.parent


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


def _sample_address() -> dict[str, Any]:
    return {
        "full_name": "Hardening Tester",
        "line1": "450 Mission Street",
        "line2": None,
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94105",
        "country": "US",
        "phone": "+14155550199",
    }


def _checkout_payload(email: str = "hardening.buyer@example.com") -> dict[str, Any]:
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
    price_cents: int = 4200,
    on_hand: int = 20,
    reserved: int = 0,
) -> tuple[Category, Product, ProductVariant, InventoryStock]:
    suffix = uuid.uuid4().hex[:8]
    category = Category(name=f"HrdCat-{suffix}", slug=f"hrd-cat-{suffix}", is_active=True)
    db_session.add(category)
    db_session.flush()

    product = Product(
        category_id=category.id,
        title=f"Hardening Item {suffix}",
        slug=f"hrd-item-{suffix}",
        description="Production hardening test item",
        status="ACTIVE",
    )
    db_session.add(product)
    db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku=sku or f"HRD-{suffix.upper()}",
        title="Standard",
        attributes_json={"size": "M"},
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
    return category, product, variant, stock


def _signed_webhook(
    *,
    event_id: str,
    event_type: str,
    provider_intent_id: str,
    order_number: str,
    amount_cents: int,
    currency: str = "USD",
    failure_code: str | None = None,
    failure_message: str | None = None,
    secret: str = TEST_WEBHOOK_SECRET,
) -> tuple[bytes, dict[str, str]]:
    ts = int(time.time())
    body = json.dumps(
        {
            "event_id": event_id,
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
    ).encode("utf-8")
    sig = sign_webhook_payload(ts, body, secret)
    return body, {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": str(ts),
        "X-Webhook-Signature": sig,
    }


def _use_auth_cookies(client: TestClient, session: AuthenticatedSession) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, session.session_cookie)
    client.cookies.set(CSRF_COOKIE_NAME, session.csrf_cookie)


def _csrf_headers(
    session: AuthenticatedSession | None = None,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {"Origin": DEFAULT_ORIGIN}
    if session is not None:
        headers["X-CSRF-Token"] = session.csrf_token
    if extra:
        headers.update(extra)
    return headers


# ============================================================================
# 1. Distributed Redis Rate Limiter
# ============================================================================


def test_redis_rate_limiter_sliding_window_pttl_and_reset() -> None:
    """Atomic Lua sliding window enforces limit, sets PTTL <= window_ms, expires, and resets."""
    fake_redis = fakeredis.FakeRedis()
    fake_redis.set("unrelated:keep", b"1")
    now_box = [1_700_000_000.0]

    limiter = RedisSlidingWindowRateLimiter(
        fake_redis,
        key_prefix="qa:rl",
        clock=lambda: now_box[0],
    )
    key = "auth:login:ip:198.51.100.10"
    full_key = f"qa:rl:{key}"

    for idx in range(5):
        decision = limiter.check_and_record(key, limit=5, window_seconds=10)
        assert decision.allowed is True
        assert decision.remaining == 5 - (idx + 1)
        assert decision.retry_after_seconds == 0

    pttl = int(fake_redis.pttl(full_key))
    assert 0 < pttl <= 10_000

    blocked = limiter.check_and_record(key, limit=5, window_seconds=10)
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert 1 <= blocked.retry_after_seconds <= 10

    # Advancing clock past sliding window allows requests again
    now_box[0] += 10.5
    recovered = limiter.check_and_record(key, limit=5, window_seconds=10)
    assert recovered.allowed is True
    assert recovered.remaining == 4

    # reset() removes only prefixed keys
    limiter.reset()
    assert fake_redis.exists(full_key) == 0
    assert fake_redis.get("unrelated:keep") == b"1"


def test_redis_rate_limiter_multi_instance_consistency(
    app_factory: Callable[..., FastAPI],
) -> None:
    """Two separate create_app() instances sharing one FakeRedis enforce a unified limit=5."""
    shared_redis = fakeredis.FakeRedis()
    limiter_a = RedisSlidingWindowRateLimiter(shared_redis, key_prefix="shared:rl")
    limiter_b = RedisSlidingWindowRateLimiter(shared_redis, key_prefix="shared:rl")

    app_a = app_factory(rate_limiter=limiter_a)
    app_b = app_factory(rate_limiter=limiter_b)

    target_email = "shared-rl@example.com"
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        clients = [client_a, client_b]
        # /api/v1/auth/register enforces limit=5 per 60s on auth:register:ip_email:...
        for idx in range(5):
            active_client = clients[idx % 2]
            resp = active_client.post(
                "/api/v1/auth/register",
                json={
                    "email": target_email,
                    "password": DEFAULT_TEST_PASSWORD,
                    "full_name": "Shared RL User",
                },
                headers={"Origin": DEFAULT_ORIGIN},
            )
            expected_status = 201 if idx == 0 else 409
            assert resp.status_code == expected_status, resp.text

        # 6th alternating call on app_b must hit 429 RATE_LIMIT_EXCEEDED + Retry-After
        blocked_resp = client_b.post(
            "/api/v1/auth/register",
            json={
                "email": target_email,
                "password": DEFAULT_TEST_PASSWORD,
                "full_name": "Shared RL User",
            },
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert blocked_resp.status_code == 429, blocked_resp.text
        assert blocked_resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        retry_after = int(blocked_resp.headers.get("Retry-After", "0"))
        assert retry_after >= 1


@pytest.mark.skipif(
    not os.environ.get("REDIS_TEST_URL"),
    reason="REDIS_TEST_URL is not configured; covered via fakeredis Lua runtime.",
)
def test_redis_rate_limiter_live_redis_when_configured() -> None:
    """When REDIS_TEST_URL is provided, validate Lua rate limiting against live Redis."""
    redis_url = os.environ["REDIS_TEST_URL"]
    prefix = f"qa:live:{uuid.uuid4().hex[:8]}"
    limiter_a = RedisSlidingWindowRateLimiter.from_url(redis_url, key_prefix=prefix)
    limiter_b = RedisSlidingWindowRateLimiter.from_url(redis_url, key_prefix=prefix)
    try:
        assert limiter_a.ping() is True
        key = "auth:login:live_check"
        for idx in range(5):
            target = limiter_a if idx % 2 == 0 else limiter_b
            dec = target.check_and_record(key, limit=5, window_seconds=15)
            assert dec.allowed is True
        denied = limiter_b.check_and_record(key, limit=5, window_seconds=15)
        assert denied.allowed is False
        assert denied.retry_after_seconds >= 1
    finally:
        limiter_a.reset()
        limiter_a.close()
        limiter_b.close()


def test_redis_rate_limiter_concurrency_20_threads() -> None:
    """20 concurrent threads against limit=5 yield strictly 5 allowed and 15 denied."""
    shared_redis = fakeredis.FakeRedis()
    limiter = RedisSlidingWindowRateLimiter(shared_redis, key_prefix="conc:rl")
    barrier = threading.Barrier(20)

    def _worker() -> bool:
        barrier.wait(timeout=10)
        return limiter.check_and_record(
            "auth:login:ip:198.51.100.55", limit=5, window_seconds=60
        ).allowed

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = [f.result(timeout=15) for f in [pool.submit(_worker) for _ in range(20)]]

    assert outcomes.count(True) == 5
    assert outcomes.count(False) == 15


def test_redis_rate_limiter_outage_fail_closed_and_fallback(
    app_factory: Callable[..., FastAPI],
    db_session: Session,
) -> None:
    """Under RedisError, sensitive paths return 503 while non-sensitive fall back to half-limit."""

    class _DownRedisClient:
        def register_script(self, _lua: str) -> Callable[..., Any]:
            def _boom(*_args: Any, **_kwargs: Any) -> Any:
                raise RedisConnectionError("simulated redis outage")

            return _boom

        def ping(self) -> bool:
            raise RedisConnectionError("simulated redis outage")

        def close(self) -> None:
            return None

    _cat, _prod, variant, _stock = _seed_variant(db_session, on_hand=100)

    # 1. fail_closed_sensitive: 503 on sensitive keys; max(1, limit // 2) on non-sensitive
    sensitive_limiter = RedisSlidingWindowRateLimiter(
        _DownRedisClient(),  # type: ignore[arg-type]
        key_prefix="down:rl",
        failure_mode="fail_closed_sensitive",
    )
    for sensitive_key in (
        "auth:login:ip:1.2.3.4",
        "checkout:orders:ip:1.2.3.4",
        "payments:guest_lookup:ip:1.2.3.4",
        "orders:guest_lookup:ip:1.2.3.4",
    ):
        with pytest.raises(ServiceUnavailableError) as exc_info:
            sensitive_limiter.check_and_record(sensitive_key, limit=10, window_seconds=60)
        assert exc_info.value.code == "RATE_LIMITER_UNAVAILABLE"

    # Non-sensitive fallback applies max(1, limit // 2)
    for _ in range(3):
        assert (
            sensitive_limiter.check_and_record(
                "cart:add:ip:1.2.3.4", limit=6, window_seconds=60
            ).allowed
            is True
        )
    assert (
        sensitive_limiter.check_and_record(
            "cart:add:ip:1.2.3.4", limit=6, window_seconds=60
        ).allowed
        is False
    )
    assert (
        sensitive_limiter.check_and_record("cart:add:ip:tiny", limit=1, window_seconds=60).allowed
        is True
    )
    assert (
        sensitive_limiter.check_and_record("cart:add:ip:tiny", limit=1, window_seconds=60).allowed
        is False
    )

    # 2. HTTP endpoint verification on FastAPI app
    http_limiter = RedisSlidingWindowRateLimiter(
        _DownRedisClient(),  # type: ignore[arg-type]
        key_prefix="down_http:rl",
        failure_mode="fail_closed_sensitive",
    )
    app = app_factory(rate_limiter=http_limiter)
    with TestClient(app) as client:
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": DEFAULT_TEST_PASSWORD},
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert login_resp.status_code == 503
        assert login_resp.json()["error"]["code"] == "RATE_LIMITER_UNAVAILABLE"

        checkout_resp = client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload(),
            headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert checkout_resp.status_code == 503
        assert checkout_resp.json()["error"]["code"] == "RATE_LIMITER_UNAVAILABLE"

        # Non-sensitive /api/v1/cart/items falls back to local limiter with limit=60 // 2 = 30
        for idx in range(30):
            cart_ok = client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(variant.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            )
            assert cart_ok.status_code == 200, (idx, cart_ok.text)
            client.cookies.clear()

        cart_blocked = client.post(
            "/api/v1/cart/items",
            json={"variant_id": str(variant.id), "quantity": 1},
            headers={"Origin": DEFAULT_ORIGIN},
        )
        assert cart_blocked.status_code == 429
        assert cart_blocked.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    # 3. fail_closed_all denies even non-sensitive keys
    strict_limiter = RedisSlidingWindowRateLimiter(
        _DownRedisClient(),  # type: ignore[arg-type]
        key_prefix="down_all:rl",
        failure_mode="fail_closed_all",
    )
    with pytest.raises(ServiceUnavailableError) as exc_info:
        strict_limiter.check_and_record("cart:add:ip:1.2.3.4", limit=10, window_seconds=60)
    assert exc_info.value.code == "RATE_LIMITER_UNAVAILABLE"


# ============================================================================
# 2. Provider-Independent Payment Architecture
# ============================================================================


class _CustomMockPaymentAdapter:
    """In-memory mock PSP adapter exercising the full PaymentProviderAdapter protocol."""

    def __init__(self, name: str = "custom_psp") -> None:
        self._name = name
        self.created_calls: list[dict[str, Any]] = []
        self.cancelled_ids: list[str] = []
        self.refund_calls: list[dict[str, Any]] = []
        self.status_overrides: dict[str, ProviderStatusResult] = {}

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def is_production_ready(self) -> bool:
        return True

    def create_intent(
        self,
        *,
        order_number: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderIntentResult:
        pid = f"pi_custom_{uuid.uuid4().hex[:16]}"
        self.created_calls.append(
            {
                "order_number": order_number,
                "amount_cents": amount_cents,
                "currency": currency,
                "idempotency_key": idempotency_key,
                "provider_intent_id": pid,
            }
        )
        return ProviderIntentResult(
            provider=self._name,
            provider_intent_id=pid,
            status="REQUIRES_ACTION",
            client_secret=f"cs_{pid}",
        )

    def retrieve_intent(self, *, provider_intent_id: str) -> ProviderStatusResult:
        if provider_intent_id in self.status_overrides:
            return self.status_overrides[provider_intent_id]
        return ProviderStatusResult(
            provider=self._name,
            provider_intent_id=provider_intent_id,
            status="REQUIRES_ACTION",
        )

    def cancel_intent(self, *, provider_intent_id: str) -> None:
        self.cancelled_ids.append(provider_intent_id)

    def refund(
        self,
        *,
        provider_intent_id: str,
        amount_cents: int,
        currency: str,
        reason: str | None,
    ) -> str:
        ref = f"re_custom_{uuid.uuid4().hex[:16]}"
        self.refund_calls.append(
            {
                "provider_intent_id": provider_intent_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "reason": reason,
                "refund_id": ref,
            }
        )
        return ref

    def verify_and_parse_webhook(
        self,
        *,
        raw_body: bytes,
        timestamp_header: str | None,
        signature_header: str | None,
        webhook_secret: str,
        now_ts: int | None = None,
    ) -> WebhookPayload:
        return SandboxSimpayAdapter().verify_and_parse_webhook(
            raw_body=raw_body,
            timestamp_header=timestamp_header,
            signature_header=signature_header,
            webhook_secret=webhook_secret,
            now_ts=now_ts,
        )


@contextmanager
def _registered_custom_psp(
    adapter: _CustomMockPaymentAdapter,
) -> Generator[_CustomMockPaymentAdapter, None, None]:
    registry = get_default_payment_registry()
    prior = registry.get_or_none(adapter.provider_name)
    registry.register(adapter, overwrite=True)
    try:
        yield adapter
    finally:
        registry.unregister(adapter.provider_name)
        if prior is not None:
            registry.register(prior, overwrite=True)


def test_provider_independent_payment_lifecycle_reconciliation_webhooks_and_refund(
    app_factory: Callable[..., FastAPI],
    db_session: Session,
    db_session_factory: sessionmaker[Session],
    create_user: Callable[..., User],
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Custom PaymentProviderAdapter drives intents, reconcile, webhooks, and 3-phase refund."""
    adapter = _CustomMockPaymentAdapter("custom_psp")
    _cat, _prod, variant, _stock = _seed_variant(db_session, on_hand=10, price_cents=6500)

    psp_settings = Settings(
        database_url="sqlite://",
        environment="test",
        cookie_secure=False,
        allowed_origins=[DEFAULT_ORIGIN],
        csrf_hmac_secret=TEST_CSRF_SECRET,
        guest_order_hmac_secret=TEST_GUEST_ORDER_SECRET,
        payment_webhook_secret=TEST_WEBHOOK_SECRET,
        payment_sandbox_mode=False,
        payment_provider="custom_psp",
    )

    with _registered_custom_psp(adapter):
        app = app_factory(settings=psp_settings)
        with TestClient(app) as client:
            # 1. Guest checkout creates intent via custom_psp
            add_resp = client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(variant.id), "quantity": 1},
                headers={"Origin": DEFAULT_ORIGIN},
            )
            assert add_resp.status_code == 200
            co_resp = client.post(
                "/api/v1/checkout/orders",
                json=_checkout_payload("psp.buyer1@example.com"),
                headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
            )
            assert co_resp.status_code == 201, co_resp.text
            order1_data = co_resp.json()["order"]
            intent1_data = co_resp.json()["payment_intent"]
            assert intent1_data["provider"] == "custom_psp"
            assert intent1_data["provider_intent_id"].startswith("pi_custom_")
            assert len(adapter.created_calls) == 1

            # Verify cancel_intent adapter method
            adapter.cancel_intent(provider_intent_id=intent1_data["provider_intent_id"])
            assert adapter.cancelled_ids == [intent1_data["provider_intent_id"]]

            # 2. Reconcile stuck PROCESSING -> SUCCEEDED via retrieve_intent
            intent1_id = uuid.UUID(intent1_data["id"])
            with db_session_factory() as r_db:
                pi = r_db.get(PaymentIntent, intent1_id)
                assert pi is not None
                pi.status = "PROCESSING"
                r_db.commit()

            adapter.status_overrides[intent1_data["provider_intent_id"]] = ProviderStatusResult(
                provider="custom_psp",
                provider_intent_id=intent1_data["provider_intent_id"],
                status="SUCCEEDED",
            )
            with db_session_factory() as r_db:
                reconciled = reconcile_payment_intent(r_db, intent_id=intent1_id)
                assert reconciled.status == "SUCCEEDED"

            with db_session_factory() as check_db:
                ord1 = check_db.get(Order, uuid.UUID(order1_data["id"]))
                st1 = check_db.get(InventoryStock, variant.id)
                assert (
                    ord1 is not None
                    and ord1.status == "CONFIRMED"
                    and ord1.payment_status == "PAID"
                )
                assert st1 is not None and st1.on_hand == 9 and st1.reserved == 0

            # 3. Duplicate webhook returns IGNORED_DUPLICATE
            wh_body, wh_headers = _signed_webhook(
                event_id=f"evt_custom_dup_{uuid.uuid4().hex[:8]}",
                event_type="payment_intent.succeeded",
                provider_intent_id=intent1_data["provider_intent_id"],
                order_number=order1_data["order_number"],
                amount_cents=order1_data["total_cents"],
            )
            wh1 = client.post(
                "/api/v1/webhooks/payments/custom_psp", content=wh_body, headers=wh_headers
            )
            assert wh1.status_code == 200 and wh1.json()["status"] == "PROCESSED"
            wh2 = client.post(
                "/api/v1/webhooks/payments/custom_psp", content=wh_body, headers=wh_headers
            )
            assert wh2.status_code == 200 and wh2.json()["status"] == "IGNORED_DUPLICATE"

            # 4. Admin 3-phase refund routes through custom_psp.refund()
            admin_sess = login_as(client, role="admin", email="psp-admin@example.com")
            _use_auth_cookies(client, admin_sess)
            refund_resp = client.post(
                f"/api/v1/admin/orders/{order1_data['order_number']}/refund",
                json={"restock": True, "reason": "Custom PSP refund check"},
                headers=_csrf_headers(admin_sess),
            )
            assert refund_resp.status_code == 200, refund_resp.text
            assert refund_resp.json()["status"] == "REFUNDED"
            assert refund_resp.json()["payment_status"] == "REFUNDED"
            assert len(adapter.refund_calls) == 1
            assert (
                adapter.refund_calls[0]["provider_intent_id"] == intent1_data["provider_intent_id"]
            )
            assert adapter.refund_calls[0]["amount_cents"] == order1_data["total_cents"]

            # 5. Reconcile stuck PROCESSING -> FAILED on a second order
            client.cookies.clear()
            assert (
                client.post(
                    "/api/v1/cart/items",
                    json={"variant_id": str(variant.id), "quantity": 1},
                    headers={"Origin": DEFAULT_ORIGIN},
                ).status_code
                == 200
            )
            co2 = client.post(
                "/api/v1/checkout/orders",
                json=_checkout_payload("psp.buyer2@example.com"),
                headers={"Origin": DEFAULT_ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
            )
            assert co2.status_code == 201
            pi2_data = co2.json()["payment_intent"]
            pi2_id = uuid.UUID(pi2_data["id"])
            with db_session_factory() as r_db:
                pi2 = r_db.get(PaymentIntent, pi2_id)
                assert pi2 is not None
                pi2.status = "PROCESSING"
                r_db.commit()

            adapter.status_overrides[pi2_data["provider_intent_id"]] = ProviderStatusResult(
                provider="custom_psp",
                provider_intent_id=pi2_data["provider_intent_id"],
                status="FAILED",
                failure_code="card_declined",
                failure_message="Insufficient funds",
            )
            with db_session_factory() as r_db:
                reconciled2 = reconcile_payment_intent(r_db, intent_id=pi2_id)
                assert reconciled2.status == "FAILED"
                ord2 = r_db.get(Order, uuid.UUID(co2.json()["order"]["id"]))
                assert ord2 is not None and ord2.payment_status == "FAILED"

            # 6. Out-of-order late webhook after expiry & stock depletion -> REFUND_PENDING
            with db_session_factory() as exp_db:
                res2 = exp_db.scalar(
                    select(InventoryReservation).where(
                        InventoryReservation.order_id == uuid.UUID(co2.json()["order"]["id"])
                    )
                )
                assert res2 is not None
                res2.expires_at = datetime.now(UTC) - timedelta(minutes=10)
                exp_db.commit()
                reclaimed_cnt, _ = run_coordinated_expired_reservation_sweep(exp_db)
                assert reclaimed_cnt == 1
                st_deplete = exp_db.get(InventoryStock, variant.id)
                assert st_deplete is not None
                st_deplete.on_hand = 0
                st_deplete.reserved = 0
                exp_db.commit()

            late_wh_body, late_wh_headers = _signed_webhook(
                event_id=f"evt_custom_late_{uuid.uuid4().hex[:8]}",
                event_type="payment_intent.succeeded",
                provider_intent_id=pi2_data["provider_intent_id"],
                order_number=co2.json()["order"]["order_number"],
                amount_cents=co2.json()["order"]["total_cents"],
            )
            late_wh = client.post(
                "/api/v1/webhooks/payments/custom_psp",
                content=late_wh_body,
                headers=late_wh_headers,
            )
            assert late_wh.status_code == 200 and late_wh.json()["status"] == "PROCESSED"
            with db_session_factory() as check_db:
                ord2_after = check_db.get(Order, uuid.UUID(co2.json()["order"]["id"]))
                pi2_after = check_db.get(PaymentIntent, pi2_id)
                assert ord2_after is not None and ord2_after.payment_status == "REFUND_PENDING"
                assert pi2_after is not None and pi2_after.status == "REFUND_PENDING"


# ============================================================================
# 3. Observability: Health Probes, /metrics RBAC & Cardinality, JSON Logs
# ============================================================================


def test_health_live_ready_and_metrics_rbac_and_cardinality(
    app_factory: Callable[..., FastAPI],
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Verify /health/live, /health/ready states, /metrics auth/RBAC, and zero PII in labels."""
    bearer_secret = "metrics-scrape-secret-token-32-bytes!!"
    _cat, _prod, variant, _stock = _seed_variant(db_session, on_hand=15)

    class _HealthyPingRedis(fakeredis.FakeRedis):
        pass

    class _DeadRedisClient:
        def register_script(self, _lua: str) -> Callable[..., Any]:
            return lambda *_a, **_kw: [1, 0, 0]

        def ping(self) -> bool:
            raise RedisConnectionError("ping failed")

        def close(self) -> None:
            return None

    # 1. Disabled Redis mode + metrics Bearer + RBAC
    settings_enabled = Settings(
        database_url="sqlite://",
        environment="test",
        cookie_secure=False,
        allowed_origins=[DEFAULT_ORIGIN],
        csrf_hmac_secret=TEST_CSRF_SECRET,
        guest_order_hmac_secret=TEST_GUEST_ORDER_SECRET,
        payment_webhook_secret=TEST_WEBHOOK_SECRET,
        payment_sandbox_mode=True,
        metrics_enabled=True,
        metrics_bearer_token=bearer_secret,
    )
    app = app_factory(settings=settings_enabled)
    with TestClient(app) as client:
        assert client.get("/api/v1/health/live").json() == {"status": "ok"}
        assert client.get("/api/v1/health/ready").json() == {
            "status": "ok",
            "database": "ok",
            "redis": "disabled",
        }

        # Unauthenticated -> 401
        assert client.get("/api/v1/metrics").status_code == 401
        assert (
            client.get(
                "/api/v1/metrics", headers={"Authorization": "Bearer wrong-token"}
            ).status_code
            == 401
        )

        # Customer cookie -> 403
        cust = login_as(client, role="customer", email="pii.secret.customer@example.com")
        _use_auth_cookies(client, cust)
        assert client.get("/api/v1/metrics").status_code == 403

        # Exercise checkout to populate checkout_events_total & http_requests_total
        assert (
            client.post(
                "/api/v1/cart/items",
                json={"variant_id": str(variant.id), "quantity": 1},
                headers=_csrf_headers(cust),
            ).status_code
            == 200
        )
        co = client.post(
            "/api/v1/checkout/orders",
            json=_checkout_payload("pii.secret.customer@example.com"),
            headers=_csrf_headers(cust, extra={"Idempotency-Key": uuid.uuid4().hex}),
        )
        assert co.status_code == 201
        order_number = co.json()["order"]["order_number"]

        # Admin cookie -> 200 no-store
        admin = login_as(client, role="admin", email="metrics-admin@example.com")
        _use_auth_cookies(client, admin)
        admin_metrics = client.get("/api/v1/metrics")
        assert admin_metrics.status_code == 200
        assert admin_metrics.headers.get("Cache-Control") == "no-store"
        assert "http_requests_total" in admin_metrics.text
        assert "checkout_events_total" in admin_metrics.text
        assert "pii.secret.customer@example.com" not in admin_metrics.text
        assert order_number not in admin_metrics.text

        # Bearer token without cookies -> 200
        client.cookies.clear()
        bearer_metrics = client.get(
            "/api/v1/metrics",
            headers={"Authorization": f"Bearer {bearer_secret}"},
        )
        assert bearer_metrics.status_code == 200
        assert "http_request_duration_seconds" in bearer_metrics.text

    # 2. Redis ready vs Redis down fail-closed (503) vs local_fallback (degraded)
    ok_redis_app = app_factory(
        rate_limiter=RedisSlidingWindowRateLimiter(_HealthyPingRedis(), key_prefix="ok:rl")
    )
    with TestClient(ok_redis_app) as ok_client:
        assert ok_client.get("/api/v1/health/ready").json() == {
            "status": "ok",
            "database": "ok",
            "redis": "ok",
        }

    down_closed_app = app_factory(
        rate_limiter=RedisSlidingWindowRateLimiter(
            _DeadRedisClient(),  # type: ignore[arg-type]
            key_prefix="dead:rl",
            failure_mode="fail_closed_sensitive",
        )
    )
    with TestClient(down_closed_app) as closed_client:
        ready_fail = closed_client.get("/api/v1/health/ready")
        assert ready_fail.status_code == 503
        assert ready_fail.json()["error"]["code"] == "REDIS_UNAVAILABLE"

    down_fallback_app = app_factory(
        rate_limiter=RedisSlidingWindowRateLimiter(
            _DeadRedisClient(),  # type: ignore[arg-type]
            key_prefix="deg:rl",
            failure_mode="local_fallback",
        )
    )
    with TestClient(down_fallback_app) as deg_client:
        assert deg_client.get("/api/v1/health/ready").json() == {
            "status": "ok",
            "database": "ok",
            "redis": "degraded",
        }

    # 3. DB down: /health/live stays 200 while /health and /health/ready return 503
    class _DeadSession:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise OperationalError("SELECT 1", {}, Exception("db down"))

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    db_down_app = app_factory(
        settings=Settings(
            database_url="sqlite://",
            environment="test",
            cookie_secure=False,
            allowed_origins=[DEFAULT_ORIGIN],
            csrf_hmac_secret=TEST_CSRF_SECRET,
            guest_order_hmac_secret=TEST_GUEST_ORDER_SECRET,
            payment_webhook_secret=TEST_WEBHOOK_SECRET,
            payment_sandbox_mode=True,
            metrics_enabled=False,
        )
    )
    db_down_app.state.db_session_factory = _DeadSession
    with TestClient(db_down_app) as down_client:
        assert down_client.get("/api/v1/health/live").status_code == 200
        assert down_client.get("/api/v1/health").status_code == 503
        assert down_client.get("/api/v1/health/ready").status_code == 503
        assert down_client.get("/api/v1/metrics").status_code == 404


def test_structured_json_formatter_injects_request_id_and_redacts_secrets() -> None:
    """StructuredJsonFormatter propagates request_id and redacts secrets/cookies/tokens."""
    formatter = StructuredJsonFormatter()
    token = _request_id_ctx.set("req_hardening_json_01")
    try:
        record = logging.LogRecord(
            name="app.test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="User action %s",
            args=("completed",),
            exc_info=None,
        )
        record.password = "SuperSecretPw!1"
        record.csrf_token = "raw-csrf-123"
        record.cookies = {"ecom_session": "sess-secret", "safe": "value"}
        record.authorization = "Bearer top-secret-bearer"
        record.nested = {
            "payment_webhook_secret": "whsec-abc",
            "database_url": "postgresql+psycopg://user:pass@db:5432/app",
            "order_number": "ORD-20261125-ABCD",
        }
        rendered = formatter.format(record)
    finally:
        _request_id_ctx.reset(token)

    parsed = json.loads(rendered)
    assert parsed["request_id"] == "req_hardening_json_01"
    assert parsed["message"] == "User action completed"
    assert parsed["password"] == REDACTED_VALUE
    assert parsed["csrf_token"] == REDACTED_VALUE
    assert parsed["cookies"] == REDACTED_VALUE
    assert parsed["authorization"] == REDACTED_VALUE
    assert parsed["nested"]["payment_webhook_secret"] == REDACTED_VALUE
    assert parsed["nested"]["database_url"] == REDACTED_VALUE
    assert parsed["nested"]["order_number"] == "ORD-20261125-ABCD"
    assert "SuperSecretPw!1" not in rendered
    assert "top-secret-bearer" not in rendered
    assert "whsec-abc" not in rendered


# ============================================================================
# 4. Live PostgreSQL Hardening Verification (Sweeper, Multi-Worker, EXPLAIN, Alembic)
# ============================================================================


def _seed_pg_expired_order(
    db: Session,
    *,
    variant_id: uuid.UUID,
    quantity: int,
    price_cents: int = 3000,
    expires_minutes_ago: int = 15,
) -> tuple[uuid.UUID, str, str, int]:
    """Insert an expired PENDING_PAYMENT order + ACTIVE reservation + PaymentIntent on PG."""
    now = datetime.now(UTC)
    order_number = f"ORD-SWP-{uuid.uuid4().hex[:10].upper()}"
    subtotal = price_cents * quantity
    order = Order(
        order_number=order_number,
        user_id=None,
        customer_email=f"sweeper-{uuid.uuid4().hex[:8]}@example.com",
        status="PENDING_PAYMENT",
        payment_status="REQUIRES_PAYMENT",
        fulfillment_status="UNFULFILLED",
        currency="USD",
        subtotal_cents=subtotal,
        discount_cents=0,
        shipping_cents=0,
        tax_cents=0,
        total_cents=subtotal,
        shipping_address_json=_sample_address(),
        billing_address_json=_sample_address(),
        idempotency_key=uuid.uuid4().hex,
        request_fingerprint=uuid.uuid4().hex,
        guest_token_version=1,
        guest_access_expires_at=now + timedelta(days=7),
        placed_at=now - timedelta(minutes=30),
    )
    db.add(order)
    db.flush()

    stock = db.scalar(
        select(InventoryStock).where(InventoryStock.variant_id == variant_id).with_for_update()
    )
    assert stock is not None
    stock.reserved += quantity
    stock.version += 1

    reservation = InventoryReservation(
        order_id=order.id,
        variant_id=variant_id,
        quantity=quantity,
        status="ACTIVE",
        expires_at=now - timedelta(minutes=expires_minutes_ago),
    )
    db.add(reservation)

    provider_intent_id = f"pi_sandbox_{uuid.uuid4().hex[:16]}"
    intent = PaymentIntent(
        order_id=order.id,
        provider=SANDBOX_PROVIDER,
        provider_intent_id=provider_intent_id,
        idempotency_key=uuid.uuid4().hex,
        request_fingerprint=uuid.uuid4().hex,
        amount_cents=subtotal,
        currency="USD",
        status="REQUIRES_ACTION",
    )
    db.add(intent)
    db.commit()
    return order.id, order_number, provider_intent_id, subtotal


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Live PostgreSQL TEST_DATABASE_URL is not configured in this environment.",
)
def test_pg_sweeper_advisory_lock_races_multi_worker_cap_explain_and_alembic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PG coordinated sweeper, multi-worker guest cap, EXPLAIN plans, and Alembic check."""
    test_db_url = os.environ["TEST_DATABASE_URL"]
    assert _run_alembic("upgrade", "head", database_url=test_db_url).returncode == 0

    check_res = _run_alembic("check", database_url=test_db_url)
    assert check_res.returncode == 0, (
        f"alembic check failed:\n{check_res.stdout}\n{check_res.stderr}"
    )

    pg_settings = Settings(
        database_url=test_db_url,
        environment="test",
        cookie_secure=False,
        allowed_origins=[DEFAULT_ORIGIN],
        trusted_proxy_ips=["127.0.0.1/32"],
        csrf_hmac_secret=TEST_CSRF_SECRET,
        guest_order_hmac_secret=TEST_GUEST_ORDER_SECRET,
        payment_webhook_secret=TEST_WEBHOOK_SECRET,
        payment_sandbox_mode=True,
        reservation_sweeper_interval_seconds=0,
    )
    pg_engine = create_engine_from_url(test_db_url, pg_settings)
    pg_session_factory = sessionmaker(
        bind=pg_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    try:
        # ------------------------------------------------------------------
        # A. 4 concurrent sweeper workers against 15 expired orders
        # ------------------------------------------------------------------
        with pg_session_factory() as setup_db:
            _c, _p, v1, _s1 = _seed_variant(setup_db, on_hand=100, reserved=0)
            _c2, _p2, v2, _s2 = _seed_variant(setup_db, on_hand=100, reserved=0)
            expired_order_ids: list[uuid.UUID] = []
            for idx in range(15):
                target_v = v1.id if idx % 2 == 0 else v2.id
                oid, _onum, _pid, _tot = _seed_pg_expired_order(
                    setup_db, variant_id=target_v, quantity=2
                )
                expired_order_ids.append(oid)

        # First confirm explicit advisory lock contention: 3 workers return (0, False)
        with pg_engine.connect() as lock_conn:
            acquired = bool(lock_conn.scalar(text("SELECT pg_try_advisory_lock(4104, 1)")))
            assert acquired is True
            try:
                contend_barrier = threading.Barrier(3)

                def _contended_sweeper() -> tuple[int, bool]:
                    with pg_session_factory() as s_db:
                        contend_barrier.wait(timeout=10)
                        return run_coordinated_expired_reservation_sweep(s_db)

                with ThreadPoolExecutor(max_workers=3) as pool:
                    contend_res = [
                        f.result(timeout=15)
                        for f in [pool.submit(_contended_sweeper) for _ in range(3)]
                    ]
                assert contend_res == [(0, False), (0, False), (0, False)]
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(4104, 1)"))
                lock_conn.commit()

        # Now run 4 concurrent workers racing to reclaim the 15 expired orders (batch_size=3)
        sweep_barrier = threading.Barrier(4)

        def _sweep_worker() -> tuple[int, bool]:
            with pg_session_factory() as s_db:
                sweep_barrier.wait(timeout=10)
                return run_coordinated_expired_reservation_sweep(s_db, batch_size=3, max_batches=10)

        with ThreadPoolExecutor(max_workers=4) as pool:
            sweep_results = [
                f.result(timeout=20) for f in [pool.submit(_sweep_worker) for _ in range(4)]
            ]

        assert sum(cnt for cnt, _acq in sweep_results) == 15, sweep_results
        assert any(acq for _cnt, acq in sweep_results)
        for cnt, acq in sweep_results:
            if not acq:
                assert cnt == 0

        with pg_session_factory() as verify_db:
            st1 = verify_db.get(InventoryStock, v1.id)
            st2 = verify_db.get(InventoryStock, v2.id)
            assert st1 is not None and st1.reserved == 0 and st1.on_hand == 100
            assert st2 is not None and st2.reserved == 0 and st2.on_hand == 100
            release_entries = list(
                verify_db.scalars(
                    select(InventoryLedgerEntry).where(
                        InventoryLedgerEntry.order_id.in_(expired_order_ids),
                        InventoryLedgerEntry.reason == "RELEASE",
                    )
                ).all()
            )
            assert len(release_entries) == 15
            assert len({entry.order_id for entry in release_entries}) == 15

        # ------------------------------------------------------------------
        # B. Sweeper racing mark_order_paid (webhook) & cancel_customer_order
        # ------------------------------------------------------------------
        pg_app = create_app(pg_settings)
        pg_app.state.db_session_factory = pg_session_factory

        with pg_session_factory() as setup_db:
            oid_pay, onum_pay, pid_pay, tot_pay = _seed_pg_expired_order(
                setup_db, variant_id=v1.id, quantity=1
            )
            oid_can, onum_can, _pid_can, _tot_can = _seed_pg_expired_order(
                setup_db, variant_id=v1.id, quantity=1
            )

        wh_raw, wh_hdrs = _signed_webhook(
            event_id=f"evt_swp_race_{uuid.uuid4().hex[:8]}",
            event_type="payment_intent.succeeded",
            provider_intent_id=pid_pay,
            order_number=onum_pay,
            amount_cents=tot_pay,
        )
        race_pay_barrier = threading.Barrier(2)

        def _race_pay_sweeper() -> tuple[int, bool]:
            with pg_session_factory() as s_db:
                race_pay_barrier.wait(timeout=10)
                return run_coordinated_expired_reservation_sweep(s_db)

        def _race_pay_webhook() -> int:
            with TestClient(pg_app) as w_client:
                race_pay_barrier.wait(timeout=10)
                resp = w_client.post(
                    f"/api/v1/webhooks/payments/{SANDBOX_PROVIDER}",
                    content=wh_raw,
                    headers=wh_hdrs,
                )
                return int(resp.status_code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_sw = pool.submit(_race_pay_sweeper)
            f_wh = pool.submit(_race_pay_webhook)
            f_sw.result(timeout=15)
            assert f_wh.result(timeout=15) == 200

        with pg_session_factory() as verify_db:
            ord_pay = verify_db.get(Order, oid_pay)
            assert ord_pay is not None
            assert ord_pay.status == "CONFIRMED"
            assert ord_pay.payment_status == "PAID"

        # Race sweeper vs cancel_customer_order (using guest order HMAC token)
        from app.core.security import derive_guest_order_token

        guest_tok = derive_guest_order_token(oid_can, onum_can, 1, TEST_GUEST_ORDER_SECRET)
        race_can_barrier = threading.Barrier(2)

        def _race_can_sweeper() -> tuple[int, bool]:
            with pg_session_factory() as s_db:
                race_can_barrier.wait(timeout=10)
                return run_coordinated_expired_reservation_sweep(s_db)

        def _race_can_customer() -> str:
            with pg_session_factory() as c_db:
                race_can_barrier.wait(timeout=10)
                try:
                    cancel_customer_order(
                        c_db,
                        order_number=onum_can,
                        reason="Race cancel",
                        auth=None,
                        guest_token_header=guest_tok,
                        guest_orders_cookie=None,
                        settings=pg_settings,
                        rate_limiter=pg_app.state.rate_limiter,
                        client_ip_key="127.0.0.1",
                    )
                    return "cancelled"
                except Exception as exc:
                    return exc.__class__.__name__

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_sw2 = pool.submit(_race_can_sweeper)
            f_ca2 = pool.submit(_race_can_customer)
            f_sw2.result(timeout=15)
            f_ca2.result(timeout=15)

        with pg_session_factory() as verify_db:
            ord_can = verify_db.get(Order, oid_can)
            st_after = verify_db.get(InventoryStock, v1.id)
            assert ord_can is not None and ord_can.status == "CANCELLED"
            assert st_after is not None and st_after.reserved == 0
            can_releases = list(
                verify_db.scalars(
                    select(InventoryLedgerEntry).where(
                        InventoryLedgerEntry.order_id == oid_can,
                        InventoryLedgerEntry.reason == "RELEASE",
                    )
                ).all()
            )
            assert len(can_releases) == 1

        # ------------------------------------------------------------------
        # C. Abrupt connection close releases advisory lock so next sweeper recovers
        # ------------------------------------------------------------------
        with pg_session_factory() as setup_db:
            oid_abrupt, _, _, _ = _seed_pg_expired_order(setup_db, variant_id=v1.id, quantity=1)

        abrupt_conn = pg_engine.connect()
        try:
            assert bool(abrupt_conn.scalar(text("SELECT pg_try_advisory_lock(4104, 1)"))) is True
            with pg_session_factory() as blocked_db:
                assert run_coordinated_expired_reservation_sweep(blocked_db) == (0, False)
        finally:
            # Close raw DBAPI connection abruptly without pg_advisory_unlock
            abrupt_conn.invalidate()
            abrupt_conn.close()

        with pg_session_factory() as recover_db:
            reclaimed_abrupt, acquired_abrupt = run_coordinated_expired_reservation_sweep(
                recover_db
            )
            assert acquired_abrupt is True
            assert reclaimed_abrupt >= 1
            recovered_order = recover_db.get(Order, oid_abrupt)
            assert recovered_order is not None and recovered_order.status == "CANCELLED"

        # ------------------------------------------------------------------
        # D. Cooperative stop_event halts mid-batch cleanly & unlocks
        # ------------------------------------------------------------------
        with pg_session_factory() as setup_db:
            stop_oids = [
                _seed_pg_expired_order(setup_db, variant_id=v2.id, quantity=1)[0] for _ in range(4)
            ]

        stop_ev = threading.Event()
        orig_batch = inventory_service._reclaim_expired_reservations_batch

        def _batch_then_stop(*args: Any, **kwargs: Any) -> int:
            res = orig_batch(*args, **kwargs)
            stop_ev.set()
            return res

        monkeypatch.setattr(
            inventory_service, "_reclaim_expired_reservations_batch", _batch_then_stop
        )
        try:
            with pg_session_factory() as stop_db:
                reclaimed_partial, acquired_partial = run_coordinated_expired_reservation_sweep(
                    stop_db, batch_size=2, max_batches=5, stop_event=stop_ev
                )
            assert acquired_partial is True
            assert reclaimed_partial == 2
        finally:
            monkeypatch.undo()

        # Remaining 2 orders are reclaimed cleanly on the next sweep
        with pg_session_factory() as resume_db:
            reclaimed_rem, acquired_rem = run_coordinated_expired_reservation_sweep(
                resume_db, batch_size=2, max_batches=5
            )
            assert acquired_rem is True
            assert reclaimed_rem == 2
            remaining_pending = [
                o
                for o in resume_db.scalars(select(Order).where(Order.id.in_(stop_oids))).all()
                if o.status == "PENDING_PAYMENT"
            ]
            assert remaining_pending == []

        # ------------------------------------------------------------------
        # E. Guest pending-order cap across simulated worker restarts (2 create_app instances)
        # ------------------------------------------------------------------
        app_worker_1 = create_app(pg_settings)
        app_worker_1.state.db_session_factory = pg_session_factory
        app_worker_2 = create_app(pg_settings)
        app_worker_2.state.db_session_factory = pg_session_factory

        spoof_xff = {"Origin": DEFAULT_ORIGIN, "X-Forwarded-For": "198.51.100.77"}
        with TestClient(app_worker_1, client=("127.0.0.1", 50001)) as w1:
            for idx in range(2):
                w1.cookies.clear()
                assert (
                    w1.post(
                        "/api/v1/cart/items",
                        json={"variant_id": str(v1.id), "quantity": 1},
                        headers=spoof_xff,
                    ).status_code
                    == 200
                )
                r1 = w1.post(
                    "/api/v1/checkout/orders",
                    json=_checkout_payload(f"worker1-{idx}@example.com"),
                    headers={**spoof_xff, "Idempotency-Key": uuid.uuid4().hex},
                )
                assert r1.status_code == 201, r1.text

        # Worker 2 has a completely empty in-memory GuestPendingOrderTracker
        assert app_worker_2.state.guest_pending_tracker.get_ids("198.51.100.77") == []
        with TestClient(app_worker_2, client=("127.0.0.1", 50002)) as w2:
            # 3rd active guest pending order succeeds
            w2.cookies.clear()
            assert (
                w2.post(
                    "/api/v1/cart/items",
                    json={"variant_id": str(v1.id), "quantity": 1},
                    headers=spoof_xff,
                ).status_code
                == 200
            )
            r3 = w2.post(
                "/api/v1/checkout/orders",
                json=_checkout_payload("worker2-3@example.com"),
                headers={**spoof_xff, "Idempotency-Key": uuid.uuid4().hex},
            )
            assert r3.status_code == 201, r3.text

            # 4th active guest pending order is blocked by DB-backed cap (MAX = 3)
            w2.cookies.clear()
            assert (
                w2.post(
                    "/api/v1/cart/items",
                    json={"variant_id": str(v1.id), "quantity": 1},
                    headers=spoof_xff,
                ).status_code
                == 200
            )
            r4 = w2.post(
                "/api/v1/checkout/orders",
                json=_checkout_payload("worker2-4@example.com"),
                headers={**spoof_xff, "Idempotency-Key": uuid.uuid4().hex},
            )
            assert r4.status_code == 429, r4.text
            assert r4.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
            assert MAX_ACTIVE_GUEST_PENDING_ORDERS == 3

        # ------------------------------------------------------------------
        # F. EXPLAIN (FORMAT JSON) on key PostgreSQL queries + downgrade/upgrade roundtrip
        # ------------------------------------------------------------------
        with pg_session_factory() as plan_db:
            explain_queries = [
                (
                    "catalog_list",
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT p.id, p.title, p.slug
                    FROM products p
                    JOIN categories c ON c.id = p.category_id
                    WHERE p.status = 'ACTIVE' AND c.is_active = true
                    ORDER BY p.created_at DESC, p.id DESC
                    LIMIT 20 OFFSET 0
                    """,
                    {},
                ),
                (
                    "guest_pending_lookup",
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT id, order_number
                    FROM orders
                    WHERE user_id IS NULL
                      AND status = 'PENDING_PAYMENT'
                      AND guest_client_key_hash = :client_hash
                    """,
                    {"client_hash": "0" * 64},
                ),
                (
                    "expired_reservation_scan",
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT DISTINCT ir.order_id
                    FROM inventory_reservations ir
                    JOIN orders o ON o.id = ir.order_id
                    WHERE ir.status = 'ACTIVE'
                      AND ir.expires_at < :cutoff
                      AND o.status = 'PENDING_PAYMENT'
                    ORDER BY ir.order_id ASC
                    LIMIT 100
                    """,
                    {"cutoff": datetime.now(UTC)},
                ),
                (
                    "payment_intent_lookup",
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT id, order_id, status
                    FROM payment_intents
                    WHERE provider = :prov AND provider_intent_id = :pid
                    """,
                    {"prov": SANDBOX_PROVIDER, "pid": pid_pay},
                ),
            ]
            for label, sql, params in explain_queries:
                raw_plan = plan_db.execute(text(sql), params).scalar_one()
                plan_doc = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
                assert isinstance(plan_doc, list) and len(plan_doc) == 1, label
                assert "Plan" in plan_doc[0] and "Node Type" in plan_doc[0]["Plan"], label

        pg_engine.dispose()
        get_engine(pg_settings).dispose()
        clear_db_caches()

        down_res = _run_alembic("downgrade", "base", database_url=test_db_url)
        assert down_res.returncode == 0, down_res.stderr
        up_res = _run_alembic("upgrade", "head", database_url=test_db_url)
        assert up_res.returncode == 0, up_res.stderr
        check_after = _run_alembic("check", database_url=test_db_url)
        assert check_after.returncode == 0, check_after.stderr
    finally:
        pg_engine.dispose()
        get_engine(pg_settings).dispose()
        clear_db_caches()
        _run_alembic("downgrade", "base", database_url=test_db_url)
