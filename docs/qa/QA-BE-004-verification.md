# QA-BE-004 — Admin Console, Sales Analytics, CLI & Live PostgreSQL Verification

- **Task ID**: `QA-BE-004` (`backend/tests/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Execution Runner**: Orchestrator (outside Windows sandbox)
- **Overall Result**:
  - **SQLite `StaticPool` + `TestClient` (`TEST_DATABASE_URL` unset)**: **`161 passed, 4 skipped, 1 warning in 39.54s`** (`0` failed; `test_live_postgresql_*` skipped cleanly; `test_admin_and_analytics.py`: `9 passed, 1 skipped`)
  - **Isolated Disposable PostgreSQL 18.6 (`.\.venv\Scripts\python.exe tests\run_disposable_pg_pytest.py`, port `65458`, least-privilege `ecom_test_role`, `pid=15376`)**: **`165 passed, 1 warning in 56.60s`** (`0` failed, `0` skipped, exit `0`)
- **Scope & Compatibility Notice**: Live PostgreSQL verification ran against an isolated disposable **PostgreSQL 18.6** instance (ephemeral loopback port `65458`, least-privilege `ecom_test_role`, `pid=15376`, cleanly stopped and verified exited in `finally`). Exact target **PostgreSQL 16 (`postgres:16-alpine`)** Docker container parity remains **pending** a running Docker Desktop Linux engine or CI PostgreSQL 16 runner.

---

## 1. Static Checks & Suite Scope (`10` BE-004 Test Cases)

Static checks executed in `e:\fullstack_work\backend`:

| Check | Outcome |
| :--- | :--- |
| `.\.venv\Scripts\ruff.exe check .` | `All checks passed!` (exit `0`) |
| `.\.venv\Scripts\mypy.exe app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` | `Success: no issues found in 55 source files` (exit `0`) |
| `.\.venv\Scripts\alembic.exe heads` | `0001_initial_schema (head)` (exit `0`) |
| `.\.venv\Scripts\pytest.exe tests/ --collect-only -q` | `165 tests collected` (`0` accidental tests collected from `tests/run_disposable_pg_pytest.py`) |

Deliverable test coverage in **`backend/tests/test_admin_and_analytics.py`** (`10` pytest test functions):

1. **`test_admin_and_analytics_rbac_and_csrf_matrix`**:
   - Verifies anonymous requests and stale `ecom_session` cookies return `401 AUTH_SESSION_EXPIRED` across all 13 `/api/v1/admin/*` and `/api/v1/admin/analytics/*` routes.
   - Verifies `customer` role receives `403 FORBIDDEN_ROLE` across all 13 routes.
   - Verifies missing/mismatched `X-CSRF-Token` and disallowed `Origin` receive `403 CSRF_INVALID` on every admin `POST`/`PATCH` mutation (`fulfill`, `cancel`, `refund`, `users/{id}/role`, `users/{id}/status`).
   - Verifies `staff` succeeds (`200`) on `GET /admin/orders`, `GET /admin/orders/{order_number}`, `POST .../fulfill`, `POST .../cancel`, and all 4 `/admin/analytics/*` endpoints, while strictly rejecting (`403 FORBIDDEN_ROLE`) `POST .../refund`, `GET /admin/users`, `PATCH /admin/users/{id}/role`, `PATCH /admin/users/{id}/status`, and `GET /admin/audit-logs`.
2. **`test_admin_orders_list_filters_escaped_ilike_date_range_pagination_and_detail`**:
   - Verifies `GET /admin/orders` status/payment/fulfillment filters, escaped `ILIKE` search `q` (`%`, `_`, `\`), inclusive `from_ts`/`to_ts` filtering (`400 INVALID_DATE_RANGE` when `from_ts > to_ts`), guest orders (`user_id=None`), summed `item_count`, `placed_at DESC` ordering, `page <= 1_000_000` (`1_000_001 -> 422`), and bounded SQL query count (`<= 3` statements across `orders`/`order_items`).
   - Verifies `GET /admin/orders/{order_number}` returns full items, chronological `events` (`ORDER_PLACED`, `PAYMENT_FAILED`, `PAYMENT_SUCCEEDED`), `payment_intents` newest-first, `reservations`, and `404 ORDER_NOT_FOUND` on malformed/`%00`/missing order numbers.
3. **`test_admin_order_fulfillment_lifecycle_and_audit_trail`**:
   - Rejects unpaid `PENDING_PAYMENT` (`409 INVALID_ORDER_STATE_TRANSITION`), exercises `CONFIRMED -> PROCESSING -> SHIPPED -> COMPLETED` and direct `CONFIRMED -> SHIPPED`, rejects invalid steps (`CONFIRMED -> COMPLETE`, `PROCESSING -> START_PROCESSING`/`COMPLETE`, `SHIPPED -> START_PROCESSING`/`SHIP`, `COMPLETED -> *`), and verifies `carrier`, `tracking_number`, and `note` in `OrderEvent.metadata_json` and `AuditLog`.
4. **`test_admin_order_cancel_releases_stock_and_rejects_paid_orders`**:
   - Cancels unpaid `PENDING_PAYMENT` orders (with explicit reason or empty body defaulting to `ADMIN_CANCELLED`), cancels non-final `PaymentIntent` rows, marks `ACTIVE` reservations `RELEASED`, decrements `InventoryStock.reserved` with `RELEASE` ledger (`actor_user_id` set) and `AuditLog`, and rejects `PAID` orders (`409 INVALID_ORDER_STATE_TRANSITION`).
5. **`test_admin_order_refund_restock_ledger_and_state_guards`** (incl. **`LOW-3`**):
   - Admin-only refund on `CONFIRMED` and `PROCESSING` orders with `restock=true` (`fulfillment_status -> CANCELLED`, `on_hand += qty`, `RETURN_RESTOCK` ledger, `AuditLog`, `PaymentIntent -> REFUNDED`) and on `SHIPPED`/`COMPLETED` orders with `restock=false` (`fulfillment_status -> RETURNED`, `on_hand` unchanged).
   - Rejects refunds on `PENDING_PAYMENT`, `CANCELLED`, and already `REFUNDED` orders (`409 REFUND_NOT_ALLOWED`).
   - **`LOW-3` regression**: Refunding a `PAID` order (`restock=true`) when `InventoryStock.on_hand + qty > 2_147_483_647` rolls back cleanly with `422 INVALID_STOCK_ADJUSTMENT` (order stays `CONFIRMED`/`PAID`, intent stays `SUCCEEDED`, stock unchanged, no `RETURN_RESTOCK` ledger).
6. **`test_admin_users_management_last_admin_guards_and_session_revocation`**:
   - Verifies `GET /admin/users` (`role`, `is_active`, escaped `q` filters, `password_hash` absent from JSON).
   - Verifies `PATCH /admin/users/{id}/role` and `/status` self-demotion (`409 CANNOT_DEMOTE_SELF`), self-deactivation (`409 CANNOT_DEACTIVATE_SELF`), immediate session revocation (`revoked_sessions_count`, existing sessions immediately receive `401 AUTH_SESSION_EXPIRED`, deactivated login returns `403 AUTH_ACCOUNT_DISABLED`), and `409 CANNOT_REMOVE_LAST_ADMIN` when 1 active admin remains.
7. **`test_admin_audit_logs_filters_redaction_and_actor_join`**:
   - Verifies `GET /admin/audit-logs` filters (`resource_type`, `actor_user_id`, `action`, `from_ts`/`to_ts`, `400 INVALID_DATE_RANGE`), `User` outer join (`actor_email`/`actor_full_name` and `None` for CLI/system rows), and recursive secret redaction (`[REDACTED]`).
8. **`test_analytics_summary_timeseries_top_products_and_inventory_health`** (incl. **`LOW-2`**):
   - Verifies `summary` (`gross_revenue_cents`, `refunded_revenue_cents`, `net_revenue_cents`, `paid_orders_count`, integer `average_order_value_cents` including 0-order `0` AOV, and all 7 `orders_by_status` counts by `placed_at`), `> 366`-day and `from > to` `400 INVALID_DATE_RANGE` bounds, continuous UTC zero-filled `timeseries`, `top-products` (`revenue` vs `units` & `limit` bounds), and `inventory-health` (`active_skus_count`, non-overlapping `out_of_stock_count` vs `low_stock_count`, `total_reserved_units`, paginated alerts ordered by `available ASC` excluding inactive/draft catalog items).
   - **`LOW-2` regression**: Verifies `/admin/analytics/summary`, `/timeseries`, and `/top-products` return `400 INVALID_DATE_RANGE` (never `500`) for `from=9999-12-31&to=9999-12-31`, `to=0001-01-01`, `from=1969-12-31`, and `from=1969-12-31&to=1970-01-01`, and `422 VALIDATION_ERROR` for `len(from) > 64`.
9. **`test_cli_create_user_and_seed_demo`**:
   - Tests `app.cli.main` `create-user` (Argon2id hash, `--password-env`, duplicate rejection without `--update-if-exists`, `--update-if-exists` role promotion + session revocation, password/flag validation) and `seed-demo` (`ENVIRONMENT=production` refusal writing 0 rows, idempotent repeated runs creating strictly `3` categories, `8` products, `11` variants, `8` primary `ProductImage` rows, `11` `InventoryStock` rows, and `11` `INITIAL_SEED` ledger entries + `--with-demo-users`).
10. **`test_live_postgresql_be004_concurrency_and_parity`**:
    - Runs `alembic upgrade head` / `downgrade base` against live PostgreSQL (`TEST_DATABASE_URL`).

---

## 2. Reusable Disposable PostgreSQL Runner (`backend/tests/run_disposable_pg_pytest.py`)

Added permanent Windows-safe disposable PostgreSQL test runner **`backend/tests/run_disposable_pg_pytest.py`** (excluded from pytest test discovery) with the following safeguards:

- **No inherited `PIPE` blocking**: `initdb.exe` (`initdb.log`), `pg_ctl.exe start` (`pg_ctl_start.log` + `-l postgres.log`), and `pg_ctl.exe stop` (`pg_ctl_stop.log`) redirect stdout/stderr exclusively to log files inside the `%TEMP%` cluster directory (`stdin=subprocess.DEVNULL, close_fds=True`), avoiding Windows daemon `PIPE` handle inheritance hangs.
- **Bounded per-step timeouts & overall watchdog**: `initdb.exe` (`30s`), `pg_ctl.exe start` (`30s`), `pg_isready.exe`/`psql.exe -tAc "SELECT version();"` readiness probes (`5s` per probe across a `20s` deadline), `psql.exe` least-privilege provisioning (`15s`), `pytest` (`--pytest-timeout`, default `180s` via `Popen` + poll loop with `PYTHONUNBUFFERED=1`), `pg_ctl.exe stop -m immediate -w` (`20s`), and a `threading.Timer` overall watchdog (`--overall-timeout`, default `240s`, exit `124`).
- **Windows encoding safety**: Explicit `encoding="utf-8", errors="replace"` on every `text=True` `subprocess.run(...)` (`tasklist`, `psql` readiness/setup) to prevent background reader-thread `UnicodeDecodeError` under non-UTF-8 Windows locales.
- **Isolation & least privilege**: Allocates an unused ephemeral `127.0.0.1` loopback port (`!= 5432`), enforces cluster creation strictly under `%TEMP%` (refusing `E:\databasesql\data`), provisions `ecom_test_role` (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION`) with a random `secrets.token_urlsafe(24)` password via `psql` stdin only, and exposes `TEST_DATABASE_URL` exclusively in the child `pytest` environment.
- **Guaranteed `finally` teardown & failure diagnostics**: Captures `<data_dir>/postmaster.pid`, executes `pg_ctl stop -m immediate -w`, verifies that specific temp PID has exited (force-killing only that verified temp PID if needed), preserves secret-scrubbed logs on any non-zero exit (`--diag-dir` or `%TEMP%\ecom_pg_diag_<timestamp>`), and deletes the temporary cluster directory.

---

## 3. Pytest Execution Summary (Outside Sandbox)

| Environment | Command / Scope | Outcome |
| :--- | :--- | :--- |
| SQLite `StaticPool` + `TestClient` (`TEST_DATABASE_URL` unset) | Full backend suite (`pytest -v`) | **`161 passed, 4 skipped, 1 warning in 39.54s`** (`0` failed; `test_live_postgresql_*` skipped) |
| Isolated Disposable PostgreSQL 18.6 (`ecom_test_role`, port `65458`, `pid=15376`) | `.\.venv\Scripts\python.exe tests\run_disposable_pg_pytest.py` | **`165 passed, 1 warning in 56.60s`** (`0` failed, `0` skipped, exit `0`) |

---

## 4. Live PostgreSQL 18.6 Verification & Monotonic Event Finding

- **Initial PG 18.6 Catch & Fix**: The initial PostgreSQL 18.6 run (`164 passed, 1 failed`) surfaced a sub-microsecond `func.now()` tie on Windows PostgreSQL 18 when `payment_intent.failed` and `payment_intent.succeeded` committed inside the same timestamp microsecond (`['ORDER_PLACED', 'PAYMENT_SUCCEEDED', 'PAYMENT_FAILED']` in `test_admin_orders_list_filters_escaped_ilike_date_range_pagination_and_detail`). Backend introduced monotonic per-order event timestamp sequencing via `append_order_event` (`backend/app/core/order_events.py`), after which all `165` tests passed cleanly (`0` failed).
- **`test_live_postgresql_be004_concurrency_and_parity` PASSED** on PostgreSQL 18.6, verifying:
  1. **Concurrent last-admin `FOR UPDATE` race**: Both service-layer (`threading.Barrier(2)` on `update_admin_user_role` vs `update_admin_user_status`) and HTTP-level (`threading.Barrier(2)` on `PATCH /admin/users/{id}/role` vs `/status`) allowed strictly `1` mutation to succeed while rejecting the concurrent counterpart cleanly (`409 CANNOT_REMOVE_LAST_ADMIN` deterministically at the service lock layer; `409 CANNOT_REMOVE_LAST_ADMIN` or `401 AUTH_SESSION_EXPIRED` at the HTTP layer), leaving strictly `1` active admin in DB.
  2. **PostgreSQL parity**: Verified `POST /admin/orders/{num}/refund` (`restock=true`, `RETURN_RESTOCK` ledger, `on_hand` restored), UTC `analytics/summary` + `timeseries` (`AT TIME ZONE 'UTC'`) + `top-products`, and `GET /admin/audit-logs` PostgreSQL `INET` IPv4 (`203.0.113.10`) / IPv6 (`2001:db8::1`) string serialization with `User` join.
  3. **All prior PG suites green**: `test_live_postgresql_be003_checkout_and_webhook_concurrency`, `test_live_postgresql_be002_constraints_jsonb_partial_index_and_concurrency`, and `test_alembic_live_postgresql_upgrade_and_downgrade` all passed, and `run_disposable_pg_pytest.py` confirmed `[pg-runner] Temp PID 15376 exited; zero temporary PostgreSQL processes remain.` with clean temporary directory removal.

---

## 5. Remaining Unresolved Limitation

- **Target PostgreSQL 16 Parity**: Exact runtime parity against **`postgres:16-alpine`** (via local Docker Compose or a CI PostgreSQL 16 runner) remains **pending** until a running Docker Desktop Linux engine or CI PostgreSQL 16 environment is available.
