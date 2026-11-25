"""Headless Locust workload for storefront, auth, cart, checkout, payments, and analytics."""

from __future__ import annotations

import itertools
import json
import os
import threading
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from locust import HttpUser, between, events, task

_VU_COUNTER = itertools.count(1)
_COUNTER_LOCK = threading.Lock()
_STATUS_COUNTS: Counter[str] = Counter()

SCARCITY_SKU = os.environ.get("LOAD_SCARCITY_SKU", "ATL-LINEN-SND-L")
ADMIN_EMAIL = os.environ.get("LOAD_ADMIN_EMAIL", "admin@atelier.example")
ADMIN_PASSWORD = os.environ.get("LOAD_ADMIN_PASSWORD", "AtelierAdmin!2026")
CUSTOMER_PASSWORD = os.environ.get("LOAD_CUSTOMER_PASSWORD", "AtelierLoad!2026")
CUSTOMER_POOL_SIZE = int(os.environ.get("LOAD_CUSTOMER_POOL_SIZE", "120"))
ALLOWED_ORIGIN = os.environ.get("LOAD_ALLOWED_ORIGIN", "http://127.0.0.1:8000")
STATUS_SUMMARY_PATH = os.environ.get("LOCUST_STATUS_SUMMARY_PATH", "")


def _next_vu_id_and_ip() -> tuple[int, str]:
    with _COUNTER_LOCK:
        vu_id = next(_VU_COUNTER)
    octet_hi = ((vu_id // 250) % 250) + 1
    octet_lo = (vu_id % 250) + 1
    return vu_id, f"10.200.{octet_hi}.{octet_lo}"


def _record_outcome(bucket: str) -> None:
    with _COUNTER_LOCK:
        _STATUS_COUNTS[bucket] += 1


@events.quitting.add_listener
def _write_status_summary(environment: Any, **_kwargs: Any) -> None:
    _ = environment
    if not STATUS_SUMMARY_PATH:
        return
    with _COUNTER_LOCK:
        payload = dict(_STATUS_COUNTS)
    Path(STATUS_SUMMARY_PATH).write_text(json.dumps(payload, indent=2), encoding="utf-8")


class StorefrontUser(HttpUser):
    """Simulated shopper browsing catalog, managing cart, checking out, and simulating payment."""

    weight = 9
    wait_time = between(0.15, 0.45)

    def on_start(self) -> None:
        self.vu_id, self.client_ip = _next_vu_id_and_ip()
        pool_idx = ((self.vu_id - 1) % CUSTOMER_POOL_SIZE) + 1
        self.email = f"load.vu.{pool_idx}@example.com"
        self.csrf_token = ""
        self.slugs: list[str] = ["linen-overshirt", "merino-crewneck", "canvas-tote"]
        self.abundant_variant_ids: list[str] = []
        self.scarcity_variant_id: str | None = None

        self._login()
        self._discover_catalog()

    def _headers(
        self, *, csrf: bool = False, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        hdrs = {
            "Origin": ALLOWED_ORIGIN,
            "X-Forwarded-For": self.client_ip,
        }
        if csrf and self.csrf_token:
            hdrs["X-CSRF-Token"] = self.csrf_token
        if extra:
            hdrs.update(extra)
        return hdrs

    def _login(self) -> None:
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": CUSTOMER_PASSWORD},
            headers=self._headers(),
            name="POST /api/v1/auth/login",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                self.csrf_token = str(resp.json().get("csrf_token", ""))
                _record_outcome("2xx")
                resp.success()
            elif resp.status_code == 429:
                _record_outcome("429_rate_limited")
                resp.failure(f"429 on login: {resp.text[:160]}")
            else:
                _record_outcome(f"{resp.status_code // 100}xx")
                resp.failure(f"Login failed ({resp.status_code}): {resp.text[:160]}")

    def _discover_catalog(self) -> None:
        resp = self.client.get(
            "/api/v1/products?page_size=20",
            headers=self._headers(),
            name="GET /api/v1/products (init)",
        )
        if resp.status_code != 200:
            return
        items = resp.json().get("items", [])
        discovered_slugs = [str(item["slug"]) for item in items if item.get("slug")]
        if discovered_slugs:
            self.slugs = discovered_slugs

        for slug in self.slugs[:5]:
            det = self.client.get(
                f"/api/v1/products/{slug}",
                headers=self._headers(),
                name="GET /api/v1/products/{slug} (init)",
            )
            if det.status_code != 200:
                continue
            for variant in det.json().get("variants", []):
                vid = str(variant["id"])
                if variant.get("sku") == SCARCITY_SKU:
                    self.scarcity_variant_id = vid
                elif variant.get("is_active") and int(variant.get("available_quantity", 0)) > 0:
                    self.abundant_variant_ids.append(vid)

    @task(5)
    def browse_catalog_list(self) -> None:
        with self.client.get(
            "/api/v1/products?page=1&page_size=12",
            headers=self._headers(),
            name="GET /api/v1/products",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                _record_outcome("2xx")
                resp.success()
            else:
                _record_outcome(f"{resp.status_code // 100}xx")
                resp.failure(f"Catalog list failed ({resp.status_code})")

    @task(4)
    def view_product_detail(self) -> None:
        slug = self.slugs[self.vu_id % len(self.slugs)]
        with self.client.get(
            f"/api/v1/products/{slug}",
            headers=self._headers(),
            name="GET /api/v1/products/{slug}",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                _record_outcome("2xx")
                resp.success()
            else:
                _record_outcome(f"{resp.status_code // 100}xx")
                resp.failure(f"Product detail failed ({resp.status_code})")

    @task(1)
    def refresh_login(self) -> None:
        self._login()

    @task(4)
    def cart_checkout_and_pay(self) -> None:
        if not self.abundant_variant_ids:
            return
        variant_id = self.abundant_variant_ids[self.vu_id % len(self.abundant_variant_ids)]

        with self.client.post(
            "/api/v1/cart/items",
            json={"variant_id": variant_id, "quantity": 1},
            headers=self._headers(csrf=True),
            name="POST /api/v1/cart/items",
            catch_response=True,
        ) as add_resp:
            if add_resp.status_code != 200:
                _record_outcome(f"{add_resp.status_code // 100}xx")
                add_resp.failure(f"Cart add failed ({add_resp.status_code}): {add_resp.text[:160]}")
                return
            _record_outcome("2xx")
            add_resp.success()
            items = add_resp.json().get("items", [])

        if items:
            target_item_id = items[0]["id"]
            with self.client.patch(
                f"/api/v1/cart/items/{target_item_id}",
                json={"quantity": 2},
                headers=self._headers(csrf=True),
                name="PATCH /api/v1/cart/items/{id}",
                catch_response=True,
            ) as patch_resp:
                if patch_resp.status_code == 200:
                    _record_outcome("2xx")
                    patch_resp.success()
                else:
                    _record_outcome(f"{patch_resp.status_code // 100}xx")
                    patch_resp.failure(f"Cart update failed ({patch_resp.status_code})")
                    return

        with self.client.post(
            "/api/v1/checkout/orders",
            json={
                "customer_email": self.email,
                "shipping_address": {
                    "full_name": f"Load Shopper {self.vu_id}",
                    "line1": "100 Howard Street",
                    "line2": None,
                    "city": "San Francisco",
                    "state": "CA",
                    "postal_code": "94105",
                    "country": "US",
                    "phone": "+14155550199",
                },
                "billing_same_as_shipping": True,
                "billing_address": None,
            },
            headers=self._headers(csrf=True, extra={"Idempotency-Key": uuid.uuid4().hex}),
            name="POST /api/v1/checkout/orders",
            catch_response=True,
        ) as co_resp:
            if co_resp.status_code == 201:
                _record_outcome("2xx")
                co_resp.success()
                pi_id = co_resp.json()["payment_intent"]["provider_intent_id"]
            elif co_resp.status_code == 409:
                _record_outcome("409_contention")
                co_resp.failure(f"Unexpected 409 on abundant SKU: {co_resp.text[:160]}")
                return
            elif co_resp.status_code == 429:
                _record_outcome("429_rate_limited")
                co_resp.failure(f"429 on checkout: {co_resp.text[:160]}")
                return
            else:
                _record_outcome(f"{co_resp.status_code // 100}xx")
                co_resp.failure(f"Checkout failed ({co_resp.status_code}): {co_resp.text[:160]}")
                return

        with self.client.post(
            f"/api/v1/payments/sandbox/{pi_id}/simulate",
            json={"outcome": "succeeded"},
            headers=self._headers(csrf=True),
            name="POST /api/v1/payments/sandbox/{id}/simulate",
            catch_response=True,
        ) as pay_resp:
            if pay_resp.status_code == 200:
                _record_outcome("2xx")
                pay_resp.success()
            else:
                _record_outcome(f"{pay_resp.status_code // 100}xx")
                pay_resp.failure(
                    f"Payment simulate failed ({pay_resp.status_code}): {pay_resp.text[:160]}"
                )

    @task(1)
    def scarcity_oversell_probe(self) -> None:
        """Probe tight-stock scarcity SKU to verify 409 INSUFFICIENT_STOCK oversell protection."""
        if not self.scarcity_variant_id:
            return
        with self.client.post(
            "/api/v1/cart/items",
            json={"variant_id": self.scarcity_variant_id, "quantity": 1},
            headers=self._headers(csrf=True),
            name="POST /api/v1/cart/items [scarcity]",
            catch_response=True,
        ) as add_resp:
            if add_resp.status_code == 200:
                _record_outcome("2xx")
                add_resp.success()
            elif add_resp.status_code == 409:
                _record_outcome("409_insufficient_stock_expected")
                add_resp.success()
                return
            else:
                _record_outcome(f"{add_resp.status_code // 100}xx")
                add_resp.failure(f"Scarcity cart add failed ({add_resp.status_code})")
                return

        with self.client.post(
            "/api/v1/checkout/orders",
            json={
                "customer_email": self.email,
                "shipping_address": {
                    "full_name": f"Scarcity Shopper {self.vu_id}",
                    "line1": "200 Folsom Street",
                    "line2": None,
                    "city": "San Francisco",
                    "state": "CA",
                    "postal_code": "94105",
                    "country": "US",
                    "phone": "+14155550188",
                },
                "billing_same_as_shipping": True,
                "billing_address": None,
            },
            headers=self._headers(csrf=True, extra={"Idempotency-Key": uuid.uuid4().hex}),
            name="POST /api/v1/checkout/orders [scarcity]",
            catch_response=True,
        ) as co_resp:
            if co_resp.status_code == 201:
                _record_outcome("2xx")
                co_resp.success()
                pi_id = co_resp.json()["payment_intent"]["provider_intent_id"]
                self.client.post(
                    f"/api/v1/payments/sandbox/{pi_id}/simulate",
                    json={"outcome": "succeeded"},
                    headers=self._headers(csrf=True),
                    name="POST /api/v1/payments/sandbox/{id}/simulate [scarcity]",
                )
            elif co_resp.status_code == 409:
                _record_outcome("409_insufficient_stock_expected")
                co_resp.success()
                self.client.delete(
                    "/api/v1/cart",
                    headers=self._headers(csrf=True),
                    name="DELETE /api/v1/cart [scarcity-cleanup]",
                )
            elif co_resp.status_code == 429:
                _record_outcome("429_rate_limited")
                co_resp.failure(f"429 on scarcity checkout: {co_resp.text[:160]}")
            else:
                _record_outcome(f"{co_resp.status_code // 100}xx")
                co_resp.failure(
                    f"Scarcity checkout failed ({co_resp.status_code}): {co_resp.text[:160]}"
                )


class AdminAnalyticsUser(HttpUser):
    """Simulated admin operator querying summary analytics under load."""

    weight = 1
    wait_time = between(0.25, 0.75)

    def on_start(self) -> None:
        self.vu_id, self.client_ip = _next_vu_id_and_ip()
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Origin": ALLOWED_ORIGIN, "X-Forwarded-For": self.client_ip},
            name="POST /api/v1/auth/login [admin]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                _record_outcome("2xx")
                resp.success()
            else:
                _record_outcome(f"{resp.status_code // 100}xx")
                resp.failure(f"Admin login failed ({resp.status_code}): {resp.text[:160]}")

    @task
    def get_analytics_summary(self) -> None:
        with self.client.get(
            "/api/v1/admin/analytics/summary",
            headers={"Origin": ALLOWED_ORIGIN, "X-Forwarded-For": self.client_ip},
            name="GET /api/v1/admin/analytics/summary",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                _record_outcome("2xx")
                resp.success()
            else:
                _record_outcome(f"{resp.status_code // 100}xx")
                resp.failure(f"Analytics summary failed ({resp.status_code}): {resp.text[:160]}")
