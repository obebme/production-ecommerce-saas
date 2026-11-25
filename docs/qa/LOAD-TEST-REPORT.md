# HARDEN-QA-01 — Reproducible Locust Load Test Report

- **Task ID**: `HARDEN-QA-01`
- **Branch**: `improvement/production-hardening`
- **Author**: `qa` specialist
- **Run Timestamp (UTC)**: `2026-11-25T15:13:21Z`
- **Harness & Workload**:
  - Runner: `backend/tests/load/run_load_suite.py`
  - Workload: `backend/tests/load/locustfile.py` (`Locust 2.47.0`, `--headless`, `15s` per tier, `r == u`)
  - Database: Disposable PostgreSQL `18.6` on `x86_64-windows` (`127.0.0.1:58951`, least-privilege `ecom_load_role`), `alembic upgrade head` (`0003_phase2_indexes_and_cleanup`)
  - API Server: Single-worker `uvicorn app.main:app` (`127.0.0.1:58952`), `DB_POOL_SIZE=15`, `DB_MAX_OVERFLOW=25`, `TRUSTED_PROXY_IPS=127.0.0.1/32`, `RESERVATION_SWEEPER_INTERVAL_SECONDS=0`, in-memory `SlidingWindowRateLimiter` (single-process harness)
  - Client Simulation: Unique synthetic `X-Forwarded-For` (`10.200.<hi>.<lo>`) per VU; `120` pre-seeded load customer accounts (`load.vu.1..120@example.com`) + `admin@atelier.example`; `10` abundant SKUs (`on_hand=100,000`) + `1` scarcity SKU (`ATL-LINEN-SND-L`, `on_hand=5`) to exercise concurrency oversell guards.

---

## 1. Tier Summary (10 / 50 / 100 VUs, 15s each)

| Tier | Total Requests | Locust Failures | RPS | Median (ms) | Avg (ms) | p95 (ms) | p99 (ms) | Max (ms) | `2xx` Count | Expected `409 INSUFFICIENT_STOCK` | `429` / `5xx` |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **10 VUs** | `751` | `0` | `53.39` | `16.0` | `29.29` | `71.0` | `540.0` | `622.5` | `689` | `19` | `0` / `0` |
| **50 VUs** | `1436` | `0` | `102.33` | `270.0` | `340.29` | `700.0` | `2100.0` | `2344.6` | `1177` | `41` | `0` / `0` |
| **100 VUs** | `1542` | `0` | `109.99` | `620.0` | `767.07` | `1800.0` | `4000.0` | `4254.0` | `1034` | `37` | `0` / `0` |

> **Transparency Note**: `status_breakdown` counts requests executed inside `catch_response` task blocks (`StorefrontUser` + `AdminAnalyticsUser`), while `Total Requests` in Locust's CSV also includes initial `_discover_catalog` setup `GET /api/v1/products (init)` and `GET /api/v1/products/{slug} (init)` calls (`54` at `10` VUs, `270` at `50` VUs, `540` at `100` VUs) and post-checkout scarcity payment simulation calls (`5` at `10` VUs). Expected `409 INSUFFICIENT_STOCK` responses on the scarcity SKU (`ATL-LINEN-SND-L`, `on_hand=5`) are recorded explicitly above (`19`, `41`, `37`) and counted as valid oversell-guard rejections rather than failures. Zero unexpected `4xx`, zero `429`, and zero `5xx` occurred in any tier.

---

## 2. Per-Endpoint Latency & Throughput Breakdown

### Tier 1 — 10 VUs (`751` requests, `53.39` RPS, `0` failures)

| Method & Route | Requests | Failures | RPS | Median (ms) | p95 (ms) | p99 (ms) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| `GET /api/v1/products` | `112` | `0` | `7.96` | `11.0` | `20.0` | `36.0` |
| `GET /api/v1/products/{slug}` | `106` | `0` | `7.54` | `8.0` | `15.0` | `34.0` |
| `POST /api/v1/auth/login` | `25` | `0` | `1.78` | `150.0` | `620.0` | `620.0` |
| `POST /api/v1/cart/items` | `99` | `0` | `7.04` | `17.0` | `33.0` | `80.0` |
| `PATCH /api/v1/cart/items/{id}` | `99` | `0` | `7.04` | `15.0` | `31.0` | `38.0` |
| `POST /api/v1/checkout/orders` | `98` | `0` | `6.97` | `21.0` | `48.0` | `62.0` |
| `POST /api/v1/payments/sandbox/{id}/simulate` | `98` | `0` | `6.97` | `20.0` | `42.0` | `61.0` |
| `GET /api/v1/admin/analytics/summary` | `27` | `0` | `1.92` | `10.0` | `20.0` | `160.0` |
| `POST /api/v1/cart/items [scarcity]` | `22` | `0` | `1.56` | `16.0` | `31.0` | `44.0` |
| `POST /api/v1/checkout/orders [scarcity]` | `5` | `0` | `0.36` | `18.0` | `25.0` | `25.0` |
| `POST /api/v1/payments/sandbox/{id}/simulate [scarcity]` | `5` | `0` | `0.36` | `21.0` | `30.0` | `30.0` |

### Tier 2 — 50 VUs (`1436` requests, `102.33` RPS, `0` failures)

| Method & Route | Requests | Failures | RPS | Median (ms) | p95 (ms) | p99 (ms) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| `GET /api/v1/products` | `200` | `0` | `14.25` | `160.0` | `270.0` | `360.0` |
| `GET /api/v1/products/{slug}` | `144` | `0` | `10.26` | `160.0` | `270.0` | `340.0` |
| `POST /api/v1/auth/login` | `82` | `0` | `5.84` | `800.0` | `2300.0` | `2300.0` |
| `POST /api/v1/auth/login [admin]` | `5` | `0` | `0.36` | `780.0` | `790.0` | `790.0` |
| `POST /api/v1/cart/items` | `166` | `0` | `11.83` | `330.0` | `450.0` | `510.0` |
| `PATCH /api/v1/cart/items/{id}` | `161` | `0` | `11.47` | `280.0` | `470.0` | `510.0` |
| `POST /api/v1/checkout/orders` | `149` | `0` | `10.62` | `390.0` | `620.0` | `640.0` |
| `POST /api/v1/payments/sandbox/{id}/simulate` | `144` | `0` | `10.26` | `350.0` | `550.0` | `600.0` |
| `GET /api/v1/admin/analytics/summary` | `77` | `0` | `5.49` | `280.0` | `1500.0` | `1500.0` |
| `POST /api/v1/cart/items [scarcity]` | `38` | `0` | `2.71` | `340.0` | `460.0` | `520.0` |

### Tier 3 — 100 VUs (`1542` requests, `109.99` RPS, `0` failures)

| Method & Route | Requests | Failures | RPS | Median (ms) | p95 (ms) | p99 (ms) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| `GET /api/v1/products` | `193` | `0` | `13.77` | `420.0` | `640.0` | `880.0` |
| `GET /api/v1/products/{slug}` | `138` | `0` | `9.84` | `420.0` | `650.0` | `840.0` |
| `POST /api/v1/auth/login` | `120` | `0` | `8.56` | `2000.0` | `4100.0` | `4200.0` |
| `POST /api/v1/auth/login [admin]` | `10` | `0` | `0.71` | `1000.0` | `1700.0` | `1700.0` |
| `POST /api/v1/cart/items` | `127` | `0` | `9.06` | `820.0` | `1200.0` | `1300.0` |
| `PATCH /api/v1/cart/items/{id}` | `115` | `0` | `8.20` | `720.0` | `1000.0` | `1200.0` |
| `POST /api/v1/checkout/orders` | `96` | `0` | `6.85` | `960.0` | `1300.0` | `1600.0` |
| `POST /api/v1/payments/sandbox/{id}/simulate` | `85` | `0` | `6.06` | `900.0` | `1300.0` | `1600.0` |
| `GET /api/v1/admin/analytics/summary` | `84` | `0` | `5.99` | `820.0` | `2000.0` | `2200.0` |
| `POST /api/v1/cart/items [scarcity]` | `34` | `0` | `2.43` | `710.0` | `1100.0` | `1300.0` |

---

## 3. PostgreSQL Post-Load Business Invariants & Scarcity Verification

Verified directly against the live PostgreSQL `18.6` database immediately after Tier 3 completed:

| Invariant Check | Observed Value | Status |
| :--- | :---: | :---: |
| `inventory_stocks WHERE reserved < 0` | `0` | PASS |
| `inventory_stocks WHERE on_hand < 0` | `0` | PASS |
| `inventory_stocks WHERE reserved > on_hand` | `0` | PASS |
| `inventory_stocks.reserved != SUM(ACTIVE reservations)` | `0` | PASS |
| Orders with `> 1` `SUCCEEDED` `payment_intents` | `0` | PASS |
| Total orders created across all 3 tiers | `385` (`364` paid) | PASS |
| Scarcity SKU (`ATL-LINEN-SND-L`, initial `on_hand=5`) final `on_hand` | `0` | PASS |
| Scarcity SKU final `reserved` / `active_qty` | `0` / `0` | PASS |
| Scarcity SKU total `committed_qty` | `5` (`<= 5`, zero oversell) | PASS |

---

## 4. `/api/v1/metrics` Scrape Excerpt (`Authorization: Bearer`)

Scraped after Tier 3 (`200 OK`, `254` exposition lines):

```text
http_requests_total{method="GET",route="/api/v1/health",status_class="2xx"} 1.0
http_requests_total{method="POST",route="/auth/login",status_class="2xx"} 254.0
http_requests_total{method="GET",route="/admin/analytics/summary",status_class="2xx"} 204.0
http_requests_total{method="GET",route="/products",status_class="2xx"} 675.0
http_requests_total{method="GET",route="/products/{slug}",status_class="2xx"} 1132.0
http_requests_total{method="POST",route="/cart/items",status_class="2xx"} 411.0
http_requests_total{method="PATCH",route="/cart/items/{item_id}",status_class="2xx"} 400.0
http_requests_total{method="POST",route="/checkout/orders",status_class="2xx"} 388.0
http_requests_total{method="POST",route="/payments/sandbox/{provider_intent_id}/simulate",status_class="2xx"} 365.0
http_requests_total{method="POST",route="/cart/items",status_class="4xx"} 99.0
db_pool_connections{state="size"} 15.0
db_pool_connections{state="checkedin"} 12.0
db_pool_connections{state="checkedout"} 28.0
db_pool_connections{state="overflow"} 25.0
checkout_events_total{actor_type="user",outcome="created"} 388.0
payment_webhook_events_total{event_type="payment_intent.succeeded",outcome="processed",provider="sandbox_simpay"} 365.0
rate_limit_decisions_total{backend="memory",decision="allowed",scope="auth:login"} 508.0
rate_limit_decisions_total{backend="memory",decision="allowed",scope="cart:add"} 515.0
rate_limit_decisions_total{backend="memory",decision="allowed",scope="checkout:orders"} 397.0
```

---

## 5. Bottlenecks, Observations & Environmental Scope

1. **Single-Worker CPU Saturation from Argon2id Login at 100 VUs**:
   - At `10` VUs, catalog reads (`GET /products`, `GET /products/{slug}`) stayed at `8–11 ms` median (`15–20 ms` p95), and checkout completed in `21 ms` median (`48 ms` p95).
   - At `50–100` VUs on a single `uvicorn` process (`40`-thread AnyIO worker pool), concurrent `Argon2id` (`64 MiB`, `t=3`) password verifications on `POST /api/v1/auth/login` (p95 `4100 ms` at `100` VUs) saturated CPU and `DB_POOL_SIZE=15` + `DB_MAX_OVERFLOW=25` (`checkedout=28.0`), inflating read/cart/checkout p95 to `640–1300 ms`.
   - **Recommendation**: In multi-worker production deployments behind Nginx/Caddy + Redis (`REDIS_URL`), scale horizontally across multiple Uvicorn/Gunicorn workers (`--workers 4+`) sized to CPU cores so Argon2id verification does not contend in a single process.
2. **Environmental Scope**:
   - Executed on single-host Windows loopback against disposable PostgreSQL `18.6` with single-process `uvicorn` and in-memory rate limiting (`REDIS_URL` unset). Multi-worker Redis Lua rate-limiter throughput under load was not benchmarked against a live Redis server in this harness (`fakeredis` Lua concurrency/multi-instance parity was verified separately in `backend/tests/test_production_hardening.py`).
