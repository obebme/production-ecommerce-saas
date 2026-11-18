# BE-003 Scoped Security & Architecture Conformance Review

- **Task ID**: `BE-003` (`backend/**`)
- **Auditor**: `security` subagent ([d89f67d8-d6c8-4a34-adb9-31980db2056a](conversation://d89f67d8-d6c8-4a34-adb9-31980db2056a))
- **Recorded By**: Orchestrator (per read-only security agent policy)
- **Notice**: Static code review, `ruff`/`mypy`/offline Alembic checks, `SQLite StaticPool + TestClient` regression verification (`152 passed, 3 skipped`), and isolated disposable **PostgreSQL 18.6** live verification (`155 passed, 0 skipped in 43.03s` under least-privilege role `ecom_test_role`, covering `pg_advisory_xact_lock`, `FOR UPDATE [SKIP LOCKED]`, `INSERT ... ON CONFLICT DO NOTHING`, and concurrent race suites). Exact target **PostgreSQL 16** (`postgres:16-alpine`) Docker Compose / CI container runtime verification remains **unverified** until Docker Desktop Linux engine (`//./pipe/dockerDesktopLinuxEngine`) or a CI PostgreSQL 16 service runs. Not formal security certification.

---

## Findings & Remediation Verification

| ID | Severity | Summary | Resolution & Verification Status |
| :--- | :--- | :--- | :--- |
| `CRITICAL` / `HIGH` | — | None identified. | 0 Critical, 0 High findings. |
| `MEDIUM-1` | Medium | `mark_order_refunded` (`charge.refunded` webhook) did not check sibling `PaymentIntent` rows or whether the targeted `PaymentIntent` paid the order, allowing a duplicate-payment or out-of-order unpaid `charge.refunded` webhook to transition an actively `PAID` (via sibling `pi_2`) or `PENDING_PAYMENT` order to `REFUNDED`. | **Resolved** in `app/modules/payments/service.py` & `app/modules/orders/service.py`: for `charge.refunded`, locks all sibling `PaymentIntent` rows for `order_id` (`ORDER BY id ASC FOR UPDATE`, `populate_existing=True`) before locking `Order`; updates only `intent.status = "REFUNDED"` + appends `PAYMENT_INTENT_REFUNDED` when a sibling `SUCCEEDED` intent paid the order or the targeted intent/order is unpaid. Verified by `test_webhook_charge_refunded_guards_non_winning_and_unpaid_intents` across SQLite and PostgreSQL 18.6. |
| `LOW-1` | Low | High server-side `price_cents * quantity` totals (`> 2_147_483_647`) could pass checkout Python math and raise PostgreSQL `NumericValueOutOfRange` (`500`) on `int4` columns. | **Resolved** in `app/modules/orders/service.py`: added `_INT32_MAX = 2_147_483_647` validation on line totals, accumulating subtotals, and final totals in `create_checkout_order`, rolling back cleanly with `422 VALIDATION_ERROR`. Verified by `test_checkout_rejects_int32_overflow_with_clean_rollback`. |
| `LOW-2` | Low | `authorize_order_access` and `simulate_sandbox_payment` only enforced `guest_lookup` rate limits when `auth is None`, allowing authenticated `customer` accounts to probe guest order numbers or intent IDs unthrottled. | **Resolved** in `app/modules/orders/service.py` & `app/modules/payments/service.py`: enforced per-user/IP rate limits (`orders:guest_lookup:user:{id}` and `payments:guest_lookup:user:{id}`) on non-staff/non-admin callers probing missing/guest orders or simulation IDs. Verified by `test_customer_guest_order_and_simulation_probe_rate_limit`. |
| `LOW-3` | Low | `_enforce_guest_pending_order_cap` counted `Order.order_number` entries from `ecom_guest_orders` without verifying their HMAC token, and distinct `Idempotency-Key` checkouts did not serialize cap checks under burst concurrency. | **Resolved (HMAC Verification)** in `app/modules/orders/service.py`: `_enforce_guest_pending_order_cap` now requires valid unexpired `verify_guest_order_token(...)` before counting cookie-sourced `order_number`s (`test_checkout_forged_guest_orders_cookie_pair_does_not_count_toward_cap`). Concurrent burst serialization across distinct keys remains an accepted `Low` operational trade-off mitigated by `GuestPendingOrderTracker` and sliding-window checkout rate limits. |
| `INFO-1..5` | Info | (1) `_WEBHOOK_PATH_RE` matches lowercase `[a-z0-9_-]+` (mixed-case browser-originated requests fail closed `403` before `404`); (2) `get_client_ip_key` requires trusted reverse-proxy `forwarded_allow_ips` behind TLS proxies, and `SlidingWindowRateLimiter` / `GuestPendingOrderTracker` are single-process in-memory; (3) anonymous cookie-less `GET /cart` creates `carts` rows needing periodic abandoned-cart cleanup in ops; (4) late `payment_intent.failed` for a retry-superseded intent sets `FAILED` while `order.status` stays `PENDING_PAYMENT`; (5) `backend/requirements.txt` uses `>=` constraints (`BE-001` `LOW-4`), and exact `postgres:16-alpine` parity awaits Docker/CI. | **Acknowledged / Tracked** for staging/production hardening (`OPS` / deployment config & release lockfile). |

---

## Verified Static & Runtime Controls (Post-Remediation)

From `e:\fullstack_work\backend`:
- `ruff check .`: **Passed** (`All checks passed!`).
- `mypy app`: **Passed** (`Success: no issues found in 43 source files`).
- `alembic heads`: **Passed** (`0001_initial_schema (head)`).
- `pytest -v` (SQLite `StaticPool` + `TestClient`, `TEST_DATABASE_URL` unset, `timeout=150`): **Passed** (`152 passed, 3 skipped, 1 warning in 30.43s`).
- `pytest -v` (Isolated disposable **PostgreSQL 18.6**, least-privilege `ecom_test_role`, port `56668`, torn down after run): **Passed** (`155 passed, 0 failed, 0 skipped, 1 warning in 43.03s`, including `test_live_postgresql_be003_checkout_and_webhook_concurrency`, `test_alembic_live_postgresql_upgrade_and_downgrade`, and `test_live_postgresql_be002_constraints_jsonb_partial_index_and_concurrency`).
