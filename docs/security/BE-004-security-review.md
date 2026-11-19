# BE-004 Scoped Security & Architecture Conformance Review

- **Task ID**: `BE-004` (`backend/**`)
- **Auditor**: `security` subagent ([bb81db11-9e6d-427d-92d4-a79f4658400d](conversation://bb81db11-9e6d-427d-92d4-a79f4658400d))
- **Recorded By**: Orchestrator (per read-only security agent policy)
- **Notice**: Static code review, `ruff`/`mypy`/offline Alembic checks, `SQLite StaticPool + TestClient` regression verification (`161 passed, 4 skipped`), and isolated disposable **PostgreSQL 18.6** live verification (`165 passed, 0 skipped in 56.60s` via [run_disposable_pg_pytest.py](../../backend/tests/run_disposable_pg_pytest.py) under least-privilege role `ecom_test_role`, covering concurrent last-admin races, `OrderEvent` monotonic ordering, refund/restock, UTC analytics, and `INET` serialization). Exact target **PostgreSQL 16** (`postgres:16-alpine`) Docker Compose / CI container runtime verification remains **unverified** until Docker Desktop Linux engine or a CI PostgreSQL 16 service runs. Not formal security certification.

---

## Findings & Remediation Verification

| ID | Severity | Summary | Resolution & Verification Status |
| :--- | :--- | :--- | :--- |
| `CRITICAL` / `HIGH` / `MEDIUM` | — | None identified. | 0 Critical, 0 High, 0 Medium findings. |
| `LOW-1` | Low | Under PostgreSQL `READ COMMITTED`, `cancel_admin_order` (and `cancel_customer_order`) locked `PaymentIntent` rows before locking `Order FOR UPDATE`, allowing a concurrent `POST /payments/intents` call committing a new intent before `Order` lock to leave a newly created `PaymentIntent` in `REQUIRES_ACTION` on a `CANCELLED` order. | **Resolved** in `backend/app/modules/orders/service.py` (`lock_order_and_payment_intents_for_update`) and reused across `cancel_customer_order`, `cancel_admin_order`, and `refund_admin_order` (`backend/app/modules/admin/service.py`): after locking `Order FOR UPDATE`, re-queries `payment_intents` IDs for `order_id` and rolls back + retries (up to 2 attempts, then `409 INVALID_ORDER_STATE_TRANSITION`) if any newly committed intent appeared. |
| `LOW-2` | Low | Extreme valid ISO-8601 boundary dates (`?from=9999-12-31&to=9999-12-31`, `?to=0001-01-01`) could trigger Python `OverflowError` (`500`) during window arithmetic or `get_analytics_timeseries` cursor stepping, and `from`/`to` lacked query length bounds. | **Resolved** in `backend/app/modules/analytics/router.py` (`Query(..., max_length=64)` on `from`/`to` across `/summary`, `/timeseries`, `/top-products`) and `backend/app/modules/analytics/service.py` (calendar bounds `1970-01-01`..`9998-12-31`, `OverflowError` handling -> `400 INVALID_DATE_RANGE`, bounded loop exit `if cursor >= window.to_date: break`). Verified in `test_analytics_summary_timeseries_top_products_and_inventory_health` (`SQLite` + `PostgreSQL 18.6`). |
| `LOW-3` | Low | `refund_admin_order` (`restock=True`) incremented `stock.on_hand += qty` without checking `_INT32_MAX` (`2_147_483_647`), risking PostgreSQL `int4` `NumericValueOutOfRange` (`500`) if manual adjustments previously raised `on_hand` near max `int32`. | **Resolved** in `backend/app/modules/admin/service.py`: validates `stock.on_hand + qty > _INT32_MAX` before payment refund or stock mutation, rolling back and raising `422 INVALID_STOCK_ADJUSTMENT`. Verified in `test_admin_order_refund_restock_ledger_and_state_guards` (`SQLite` + `PostgreSQL 18.6`). |
| `INFO-1` | Info | `seed-demo --with-demo-users` in `staging` (`ENVIRONMENT != "production"`) falls back to repo default demo passwords if `DEMO_*_PASSWORD` env vars are unset. | **Acknowledged / Tracked** for `OPS-002` container/CI documentation (`docs/ops/local-development-and-ci.md`). `seed-demo` strictly exits `1` when `ENVIRONMENT=production`; `create-user` has zero hardcoded credentials. |
| `INFO-2` | Info | CLI `--password` exposes secrets in process args (`--password-env` is preferred); under `READ COMMITTED`, a mid-request demoted admin finishes an in-flight call while subsequent calls fail closed `401`/`403` and last-admin locking prevents inconsistency. | **Acknowledged**: Operational CLI guidance included in `OPS-002`/`REL-001`; optional `--password-stdin` can be added later if needed. |
| `INFO-3` | Info | `SandboxSimpayRefundGateway` runs in-process without network calls; future real remote PSP adapters must avoid holding DB `FOR UPDATE` locks across remote HTTP calls; admin PII endpoints may optionally add `Cache-Control: no-store`. | **Acknowledged / Tracked** for future real PSP adapter milestone and optional pre-release hardening (`SEC-001`). |
| `INFO-4` | Info | Additional live PostgreSQL finding during QA verification: `OrderEvent.created_at` previously relied only on DB `func.now()`, which could produce tied timestamps under sub-millisecond Windows PostgreSQL transactions (`PAYMENT_SUCCEEDED` sorting before `PAYMENT_FAILED` via random `uuid4` tiebreaker). | **Resolved** in `backend/app/core/order_events.py` (`append_order_event`) and applied across all 18 `OrderEvent` call sites (`orders/service.py`, `admin/service.py`, `inventory/service.py`): enforces monotonically increasing UTC `created_at` (`>= prior + 1us`) under existing `Order` lock, verified across `SQLite` (`161 passed`) and `PostgreSQL 18.6` (`165 passed`). |

---

## Verified Controls Summary

- **Strict RBAC & CSRF**: All `/api/v1/admin/*` routes enforce live `users.is_active`, live `users.role`, global `Origin`/`Referer` validation, and HMAC `X-CSRF-Token` on all mutations (`require_roles_with_csrf`); `staff` is strictly blocked (`403 FORBIDDEN_ROLE`) from `/refund`, `/admin/users*`, and `/admin/audit-logs`.
- **Refund, Cancel & Fulfillment State Integrity**: Canonical deadlock-free lock hierarchy (`PaymentIntent id ASC -> Order -> InventoryReservation id ASC -> InventoryStock variant_id ASC`) + `lock_order_and_payment_intents_for_update` retry guard; immutable `OrderItem` snapshot quantities for `RETURN_RESTOCK` with `_INT32_MAX` protection; pre-query `normalize_valid_order_number_or_none` guards.
- **Last-Admin Race & Session Revocation**: `_lock_target_and_active_admins` locks target + active admins via `ORDER BY User.id ASC FOR UPDATE`, enforcing `CANNOT_DEMOTE_SELF`, `CANNOT_DEACTIVATE_SELF`, and `CANNOT_REMOVE_LAST_ADMIN` under barrier-synchronized concurrency (`PostgreSQL 18.6`), atomically revoking all active `auth_sessions` and omitting `password_hash` from all DTOs/audits.
- **Analytics & CLI Safety**: `<= 366` day UTC window bounds + calendar bounds (`1970-01-01`..`9998-12-31`), `max_length=64` query bounds, escaped `ILIKE` filters, zero secret echo in `create-user` (`Argon2id`), and fail-closed `ENVIRONMENT=production` refusal in `seed-demo`.

---

## Post-Remediation Verification Controls

From `e:\fullstack_work\backend`:
- `.\.venv\Scripts\ruff.exe check .`: **Passed** (`All checks passed!`)
- `.\.venv\Scripts\mypy.exe app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py`: **Passed** (`Success: no issues found in 55 source files`)
- `.\.venv\Scripts\alembic.exe heads`: **Passed** (`0001_initial_schema (head)`)
- SQLite `StaticPool` + `TestClient` (`pytest -v`, `TEST_DATABASE_URL` unset): **Passed** (`161 passed, 4 skipped, 1 warning in 39.54s`)
- Isolated disposable **PostgreSQL 18.6** (`.\.venv\Scripts\python.exe tests\run_disposable_pg_pytest.py`, least-privilege `ecom_test_role`, ephemeral port `65458`, verified PID exit & temp cleanup): **Passed** (`165 passed, 0 failed, 0 skipped, 1 warning in 56.60s`)
