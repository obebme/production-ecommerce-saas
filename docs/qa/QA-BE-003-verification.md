# QA-BE-003 — Cart, Checkout, Orders, Payments & Live PostgreSQL Verification

- **Task ID**: `QA-BE-003` (`backend/tests/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Execution Runner**: Orchestrator (outside Windows sandbox)
- **Overall Result**:
  - **SQLite `StaticPool` + `TestClient` (`TEST_DATABASE_URL` unset)**: **`152 passed, 3 skipped, 1 warning in 30.43s`** (`0` failed; `test_cart_and_checkout.py`: `18 passed`, `test_orders_and_payments.py`: `9 passed, 1 skipped`)
  - **Isolated Disposable PostgreSQL 18.6 (`TEST_DATABASE_URL` set)**: **`155 passed, 0 failed, 0 skipped, 1 warning in 43.03s`** (`PYTEST_EXIT=0`)
- **Scope & Compatibility Notice**: Live PostgreSQL verification ran against an isolated disposable **PostgreSQL 18.6** instance (`PostgreSQL 18.6 on x86_64-windows`, least-privilege `ecom_test_role`, port `56668`, torn down completely in `finally`). Exact target **PostgreSQL 16 (`postgres:16-alpine`)** Docker container parity remains **pending** a running Docker Desktop Linux engine or CI PostgreSQL 16 runner.

---

## 1. Static Checks & Suite Scope (`28` BE-003 Test Cases)

Static verification executed in `e:\fullstack_work\backend`:

| Check | Outcome |
| :--- | :--- |
| `.\.venv\Scripts\ruff.exe check .` | `All checks passed!` (exit `0`) |
| `.\.venv\Scripts\mypy.exe app` | `Success: no issues found in 43 source files` (exit `0`) |
| `.\.venv\Scripts\mypy.exe tests/test_cart_and_checkout.py tests/test_orders_and_payments.py` | `Success: no issues found in 2 source files` (exit `0`) |

Test suite deliverables across `BE-003` (`28` total pytest cases = `18` in `test_cart_and_checkout.py` + `10` in `test_orders_and_payments.py`):

1. **`backend/tests/test_cart_and_checkout.py`** (`16` test functions / `18` pytest cases):
   - **Cart API (`GET /cart`, `POST /cart/items`, `PATCH /cart/items/{id}`, `DELETE /cart/items/{id}`, `DELETE /cart`, `POST /cart/merge`)**:
     - `ecom_guest_cart` issuance (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=2592000`), SHA-256 hashing in `carts.guest_token_hash`, expired guest cart abandonment/renewal, single `ACTIVE` cart per authenticated user, and fail-closed `401 AUTH_SESSION_EXPIRED` + cookie clearing on invalid `ecom_session`.
     - Bounded SQL query count on `GET /api/v1/cart` (zero N+1 across 1 vs 5 items) and lazy expired-reservation reclamation.
     - CSRF/Origin enforcement for authenticated and guest mutations, `404 VARIANT_NOT_FOUND`, `409 VARIANT_UNAVAILABLE` (inactive category/product/variant, currency mismatch), `1..99` quantity validation + incremental `>99` `422 VALIDATION_ERROR`, `409 INSUFFICIENT_STOCK` details, and user/guest cart-item IDOR (`404 CART_ITEM_NOT_FOUND`).
     - Live DB price reflection, `$100` free-shipping threshold (`1000` vs `0`), `is_available: false` projection, and idempotent `POST /api/v1/cart/merge` (`401`/`403` guards, no-op handling, `min(99, available_quantity)` clamping, guest cart `CONVERTED` transition, cookie clearing).
   - **Checkout (`POST /api/v1/checkout/orders`)**:
     - Rejects forged totals (`422`), invalid billing/control-char inputs (`422`), missing `X-CSRF-Token` (`403`), stale `ecom_session` (`401`), missing/invalid `Idempotency-Key` (`400`), and empty carts (`409 CART_EMPTY`).
     - Clean atomic rollback on `409 INSUFFICIENT_STOCK` and `409 VARIANT_UNAVAILABLE`.
     - Server-authoritative subtotal/shipping/`ROUND_HALF_UP` tax (`8%` default + `5%` `2.5 -> 3` check), atomic creation of `Order`/`OrderItem`/`OrderEvent`/`InventoryReservation`/`InventoryLedgerEntry`/`PaymentIntent`, `Cart.status == 'CONVERTED'`, bounded `ecom_guest_orders` cookie (max 5 pairs), and `guest_access_token=None` for authenticated checkout.
     - Idempotent `200 OK` replay post-conversion, `409 IDEMPOTENCY_KEY_CONFLICT` on payload/new-cart mismatch, lazy pre-step expiration reclamation, and guest max-3 active `PENDING_PAYMENT` cap (`429 RATE_LIMIT_EXCEEDED`) + tracker isolation across `app_factory()` instances.
   - **Security Regressions**:
     - `LOW-1` (`test_checkout_rejects_int32_overflow_with_clean_rollback`): rejects `line_total` and `total_cents` exceeding `2_147_483_647` with `422 VALIDATION_ERROR` and clean rollback (`Cart.status == "ACTIVE"`, `InventoryStock.reserved == 0`).
     - `LOW-3` (`test_checkout_forged_guest_orders_cookie_pair_does_not_count_toward_cap`): verifies unverified/forged `ecom_guest_orders` cookie pairs are ignored by `_enforce_guest_pending_order_cap`.

2. **`backend/tests/test_orders_and_payments.py`** (`10` test functions / `10` pytest cases):
   - **Orders (`GET /orders`, `GET /orders/{order_number}`, `POST /orders/{order_number}/cancel`)**:
     - Auth requirement, `user_id` scoping, `placed_at DESC` ordering, lazy expiry, `page <= 1_000_000` validation (`1_000_001 -> 422`), and offset short-circuit.
     - Malformed/NUL-byte `order_number` pre-validation (`404 ORDER_NOT_FOUND`), strict owner-only storefront access (`403 FORBIDDEN` for foreign customer, non-owner `staff`, and non-owner `admin`), and guest token validation (`ecom_guest_orders` cookie / `X-Guest-Order-Token` header, expiry, `guest_token_version`, fail-closed `401` on stale `ecom_session`).
     - Self-cancel reservation release (`RELEASED`), `InventoryStock.reserved` decrement, `RELEASE` ledger, `ORDER_CANCELLED` event, `PaymentIntent` cancellation, and `409 INVALID_ORDER_STATE_TRANSITION` guards.
   - **Payments & Webhooks (`POST /payments/intents`, `POST /payments/sandbox/{id}/simulate`, `POST /webhooks/payments/{provider}`)**:
     - Idempotent `POST /payments/intents` replay (`200`), conflict (`409`), `FAILED -> CANCELLED` retry flow, and state guards (`409`).
     - Strict 6-step webhook verification (`404 PROVIDER_NOT_FOUND`, `413 PAYLOAD_TOO_LARGE`, `400 WEBHOOK_TIMESTAMP_EXPIRED`, `401 WEBHOOK_SIGNATURE_INVALID`, `422 VALIDATION_ERROR`, `200 IGNORED_DUPLICATE`, and `422 WEBHOOK_PAYLOAD_MISMATCH` with `PaymentWebhookEvent.status == "FAILED"`).
     - Full state transition matrix (`payment_intent.succeeded`, `payment_intent.failed` + retry, duplicate intent `DUPLICATE_PAYMENT_REFUNDED`, late success after `RESERVATION_EXPIRED` in-stock vs out-of-stock, late success after explicit `RELEASED` cancel, and `charge.refunded` across `CONFIRMED` and `SHIPPED`).
     - Sandbox simulation (`403 SANDBOX_MODE_DISABLED`, owner/guest/`staff`/`admin` RBAC, `succeeded`/`failed`, `409 PAYMENT_INTENT_ALREADY_FINAL`).
   - **Security Regressions**:
     - `MEDIUM-1` (`test_webhook_charge_refunded_guards_non_winning_and_unpaid_intents`): confirms `charge.refunded` targeting a non-winning duplicate-refunded intent (`pi_1` when `pi_2` is `SUCCEEDED`) or an unpaid `PENDING_PAYMENT` intent transitions only the targeted intent (`REFUNDED` + `PAYMENT_INTENT_REFUNDED`) without corrupting the order, while `charge.refunded` on the winning `SUCCEEDED` intent refunds the order.
     - `LOW-2` (`test_customer_guest_order_and_simulation_probe_rate_limit`): confirms authenticated `customer` probing hits `429 RATE_LIMIT_EXCEEDED` via `orders:guest_lookup:user:{user.id}` and `payments:guest_lookup:user:{user.id}` with per-user isolation and clean `rate_limiter.reset()`.

---

## 2. Pytest Execution Summary (Outside Sandbox)

| Environment | Command / Scope | Outcome |
| :--- | :--- | :--- |
| SQLite `StaticPool` + `TestClient` (`TEST_DATABASE_URL` unset) | Full backend suite (`pytest`) | **`152 passed, 3 skipped, 1 warning in 30.43s`** (`0` failed; `test_cart_and_checkout.py`: `18 passed`, `test_orders_and_payments.py`: `9 passed, 1 skipped`) |
| Disposable PostgreSQL 18.6 (`x86_64-windows`, `ecom_test_role`, port `56668`, torn down in `finally`) | Full backend suite (`pytest` with `TEST_DATABASE_URL`) | **`155 passed, 0 failed, 0 skipped, 1 warning in 43.03s`** (`PYTEST_EXIT=0`) |

---

## 3. Live PostgreSQL 18.6 Concurrency Verification

`test_live_postgresql_be003_checkout_and_webhook_concurrency` **PASSED** inside the disposable PostgreSQL 18.6 run (`alembic upgrade head` setup and `alembic downgrade base` teardown in `finally`), verifying:

1. **Last-unit overselling race**: `6` concurrent checkouts competing via `threading.Barrier(6)` for `on_hand=1, reserved=0` resulted in **`1` x `201 Created`**, **`5` x `409 INSUFFICIENT_STOCK`**, and final `on_hand=1, reserved=1, version=2` with `1` `ACTIVE` reservation.
2. **Inverse-order multi-SKU deadlock freedom**: `4` concurrent checkouts for `(V_A, V_B)` alongside `4` concurrent checkouts for `(V_B, V_A)` via `threading.Barrier(8)` (`ORDER BY variant_id ASC FOR UPDATE`) completed **`8` x `201 Created`** with zero deadlocks and exact reservation totals (`V_A.reserved == 16`, `V_B.reserved == 8`).
3. **Concurrent identical `Idempotency-Key` checkout race**: `4` simultaneous requests with identical user, `Idempotency-Key`, and payload via `threading.Barrier(4)` (`pg_advisory_xact_lock`) returned **`1` x `201 Created` + `3` x `200 OK`**, all returning identical `order.id`/`order_number`/`payment_intent.provider_intent_id` with stock reserved once (`reserved == 3`, `1` `RESERVE` ledger row).
4. **Concurrent duplicate `payment_intent.succeeded` webhook race**: `4` simultaneous identical signed deliveries for `(provider, provider_event_id)` via `threading.Barrier(4)` (`INSERT ... ON CONFLICT DO NOTHING`) produced **`1` x `PROCESSED` + `3` x `IGNORED_DUPLICATE`**, decrementing `on_hand` once (`10 -> 7`), clearing `reserved=0`, and committing `1` `COMMIT` ledger entry and `1` `PROCESSED` `PaymentWebhookEvent` row.

---

## 4. Remaining Unresolved Limitation

- **Target PostgreSQL 16 Parity**: Exact runtime parity against **`postgres:16-alpine`** (via local Docker Compose or a CI PostgreSQL 16 runner) remains **pending** until a running Docker Desktop Linux engine or CI PostgreSQL 16 environment is available.
