# REM-P2-QA — Phase 2 Remediation Regression, Live PostgreSQL `0003` Migration, Frontend Vitest & Live E2E Verification

- **Task ID**: `REM-P2-QA` (`backend/tests/**`, `frontend/tests/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Dependencies**: `REM-P2-BE`, `REM-P2-FE`, `REM-P2-OPS`
- **Overall Result**:
  - **Backend Static Checks**: `ruff check .` (`All checks passed!`), `mypy app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` (`Success: no issues found in 55 source files`), `alembic heads` (`0003_phase2_indexes_and_cleanup (head)`).
  - **Backend SQLite `StaticPool` + `TestClient`**: **`192 passed, 4 skipped, 1 warning in 48.76s`** (`0` failed; live PG tests skipped cleanly when `TEST_DATABASE_URL` unset).
  - **Backend Isolated Disposable PostgreSQL 18.6 (`tests/run_disposable_pg_pytest.py`, port `64914`, least-privilege `ecom_test_role`, `pid=23684`)**: **`196 passed, 1 warning in 73.65s`** (`0` failed, `0` skipped, exit `0`, clean cluster teardown verified).
  - **Frontend Checks & Vitest**: `npm run lint` (`✔ No ESLint warnings or errors`), `npx tsc --noEmit` (exit `0`), `npm test` (**`5 files / 69 passed in 9.80s`**), `npm run build` (**`21 routes / 19 static pages`**, exit `0`).
  - **Live Playwright E2E (`frontend/e2e/run_e2e_stack.py`, disposable PG 18.6 port `53272`, `uvicorn` + `next start`)**: **`4 passed (29.0s)`** (exit `0`, clean teardown verified).
- **Scope & Compatibility Notice**: Live PostgreSQL verification executed against an isolated disposable **PostgreSQL 18.6** cluster under `%TEMP%` with least-privilege `ecom_test_role` (zero interaction with `127.0.0.1:5432` or `E:\databasesql\data`). Exact `postgres:16-alpine` Docker container parity remains pending a Docker Desktop / CI PostgreSQL 16 runner.

---

## 1. Command Verification Summary

| Suite / Command | Scope & Runner | Outcome |
| :--- | :--- | :--- |
| `.\.venv\Scripts\ruff.exe check .` | Backend `app` + `tests` (sandboxed) | `All checks passed!` (exit `0`) |
| `.\.venv\Scripts\mypy.exe app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` | 55 source/test files (sandboxed) | `Success: no issues found in 55 source files` (exit `0`) |
| `.\.venv\Scripts\alembic.exe heads` | Alembic migration heads (sandboxed) | `0003_phase2_indexes_and_cleanup (head)` (exit `0`) |
| `.\.venv\Scripts\python.exe -m pytest -v` | SQLite `StaticPool` (`BypassSandbox: true`, 127.0.0.1 loopback) | `192 passed, 4 skipped, 1 warning in 48.76s` (exit `0`) |
| `.\.venv\Scripts\python.exe tests\run_disposable_pg_pytest.py` | Disposable PostgreSQL 18.6 (`BypassSandbox: true`, port `64914`) | `196 passed, 0 failed, 0 skipped, 1 warning in 73.65s` (exit `0`) |
| `npm.cmd run lint` | Frontend ESLint / `next lint` (sandboxed) | `✔ No ESLint warnings or errors` (exit `0`) |
| `npx.cmd tsc --noEmit` | Frontend TypeScript (sandboxed) | `0` errors (exit `0`) |
| `npm.cmd test` | Vitest + React Testing Library (`jsdom`, sandboxed) | `5 files / 69 passed in 9.80s` (exit `0`) |
| `npm.cmd run build` | `next build` (`BypassSandbox: true` after sandbox `EPERM`) | `21 routes / 19 static pages` (exit `0`) |
| `..\backend\.venv\Scripts\python.exe e2e\run_e2e_stack.py` | Live Next.js + FastAPI + PG 18.6 + Chromium (`BypassSandbox: true`) | `4 passed (29.0s)` (exit `0`) |

---

## 2. Phase 2 Regression Coverage

### Backend (`backend/tests/`)

1. **`H-04` Trusted Proxy IP Resolution (`test_config_and_health.py`, `test_csrf_and_rbac.py`, `test_orders_and_payments.py`)**:
   - `test_h04_trusted_proxy_ips_parsing_and_rejection`: verifies `TRUSTED_PROXY_IPS` parses comma-separated/list IPs and CIDRs cleanly and rejects `"*"` and invalid CIDR strings.
   - `test_h04_trusted_proxy_ip_resolution_audit_session_and_rate_limit`: confirms `resolve_client_ip` / `get_client_ip_key` ignore `X-Forwarded-For` from untrusted peer IPs, parse right-to-left across trusted proxy hops (ignoring spoofed leftmost hops), fall back safely to peer IP on malformed `X-Forwarded-For`, and propagate resolved client IP to `auth_sessions.ip_address`, `audit_logs.ip_address`, and per-client rate limiter buckets.
   - Updated `test_live_postgresql_be003_checkout_and_webhook_concurrency` guest-cap workers to pass an explicit IPv4 client peer (`198.51.100.10`) matching `resolve_client_ip` behavior (`"testclient"` is not an IP literal and normalizes to `"unknown"`).

2. **`M-03` `.env`-aware OpenAPI Disable (`test_config_and_health.py`)**:
   - `test_m03_dotenv_production_disables_docs_without_process_env`: confirms `.env`-only `ENVIRONMENT=production` (with `ENVIRONMENT` absent from `os.environ` and even when full `Settings` validation fails) resolves `_resolve_environment_for_docs(None) == "production"` and disables `/api/v1/docs`, `/api/v1/redoc`, and `/api/v1/openapi.json` in `create_app()`.

3. **`M-04` Fail-Closed Staging Security & CLI Demo Seeding (`test_config_and_health.py`, `test_admin_and_analytics.py`)**:
   - `test_m04_staging_settings_fail_closed_and_allow_sandbox`: verifies `ENVIRONMENT=staging` fails closed on `COOKIE_SECURE=false`, `http://` `ALLOWED_ORIGINS`, `sqlite://` `DATABASE_URL`, placeholder DB credentials, short/placeholder/reused HMAC secrets, while allowing `PAYMENT_SANDBOX_MODE=true`.
   - `test_m04_seed_demo_staging_requires_explicit_demo_passwords`: verifies `app.cli.main(["seed-demo", "--with-demo-users"])` exits `1` in `"staging"` when any `DEMO_*_PASSWORD` env var is missing, and exits `0` creating all 3 demo users when `DEMO_ADMIN_PASSWORD`, `DEMO_STAFF_PASSWORD`, and `DEMO_CUSTOMER_PASSWORD` are provided.

4. **`M-05` PostgreSQL Pool, Session Timeouts & Engine Disposal (`test_config_and_health.py`)**:
   - `test_m05_postgres_engine_pool_timeouts_and_lifespan_disposal`: confirms `create_engine_from_url` passes `pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`, and `connect_args["options"]` (`statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`) for PostgreSQL URLs, `dispose_engine()` / `clear_db_caches()` invoke `.dispose()` on tracked engines, and `create_app` lifespan calls `dispose_engine` on shutdown.

5. **`M-07` Sensitive Route `Cache-Control: no-store` (`test_config_and_health.py`)**:
   - `test_m07_sensitive_routes_no_store_headers_and_catalog_not_forced`: verifies `Cache-Control: no-store` is enforced on `/api/v1/auth/me`, `/api/v1/cart`, `/api/v1/checkout/orders`, `/api/v1/orders`, and `/api/v1/admin/orders`, and is not forced on `/api/v1/products` or `/api/v1/health`.

6. **`M-08` Migration `0003_phase2_indexes_and_cleanup` & Cleanup Services/CLI (`test_schema_and_migrations.py`, `test_auth_and_sessions.py`, `test_cart_and_checkout.py`)**:
   - Updated `test_alembic_single_head_and_offline_upgrade_downgrade_sql` and `test_alembic_live_postgresql_upgrade_and_downgrade` to verify `0003_phase2_indexes_and_cleanup` (`upgrade head` -> `downgrade 0002_phase1_commerce_integrity` -> `upgrade head` -> `downgrade 0001_initial_schema` -> `upgrade head` -> `downgrade base`), confirming creation and rollback of all 6 indexes (`idx_product_images_product_sort`, `idx_orders_placed_at`, `idx_audit_logs_actor_created`, `idx_audit_logs_action_created`, `idx_auth_sessions_expires_at`, `idx_carts_status_expires`).
   - `test_m08_purge_expired_auth_sessions_and_retention`: verifies `purge_expired_auth_sessions` deletes expired/revoked sessions older than retention while preserving recent expired and active sessions.
   - `test_m08_purge_expired_carts_and_cli_cleanup_expired_records`: verifies `purge_expired_carts` and `app.cli.main(["cleanup-expired-records", "--retention-days", "30", "--batch-size", "2", "--max-batches", "10"])` delete old unlinked expired carts (+ `CartItem`s) and old `AuthSession`s while preserving `Order`-linked carts, recent expired carts, and active carts.

7. **`M-10`, `L-02`, `L-03` (`test_auth_and_sessions.py`, `test_cart_and_checkout.py`, `test_csrf_and_rbac.py`, `test_catalog_and_categories.py`, `test_admin_and_analytics.py`, `test_orders_and_payments.py`)**:
   - Updated unauthenticated missing-cookie `401` expectations to `"AUTH_REQUIRED"` across `test_admin_and_analytics.py`, `test_catalog_and_categories.py`, `test_csrf_and_rbac.py`, `test_orders_and_payments.py`, plus `test_m10_anonymous_401_auth_required_vs_invalid_cookie_auth_session_expired` (keeping stale/expired/revoked cookie tests asserting `"AUTH_SESSION_EXPIRED"`).
   - Updated `CartDTO` dict expectations for `tax_rate_bps` & `estimated_tax_cents` and added `test_m10_cart_dto_returns_server_tax_rate_bps_and_estimated_tax_cents` (verifying `ROUND_HALF_UP` tax preview from server `TAX_RATE_BPS`).
   - `test_l02_origin_default_ports_accepted_and_referer_userinfo_rejected`: confirms explicit `:443`/`:80` ports match normalized `ALLOWED_ORIGINS` and `Referer` with `user:pass@` userinfo is rejected (`403 CSRF_INVALID`).
   - `test_l03_triple_encoded_image_url_rejected_and_non_slug_integrity_conflict`: confirms `validate_product_image_url` rejects triple-encoded `%25252e%25252e` traversal and non-slug `IntegrityError` raises `ConflictError(code="INTEGRITY_CONFLICT")`.

### Frontend (`frontend/tests/`)

- `frontend/tests/ui-and-shells.test.tsx` & `frontend/tests/checkout-and-orders.test.tsx`: updated anonymous mocked `/auth/me` `401` payloads from `AUTH_SESSION_EXPIRED` to `AUTH_REQUIRED` (preserving `AUTH_SESSION_EXPIRED` in expired-session tests).
- `frontend/tests/checkout-and-orders.test.tsx`: added tests verifying dynamic `tax_rate_bps` / `estimated_tax_cents` + currency-aware free shipping (`€0.00 (Free)`) on `CheckoutView`, and verifying `AuthProvider.logout()` invokes `clearAllGuestOrderTokens()`.
- `frontend/tests/api-client.test.ts`: added tests verifying `401 AUTH_REQUIRED` does not trigger `authInvalidationListener` (unlike `AUTH_SESSION_EXPIRED`), `REQUEST_TIMEOUT` is thrown when fetch stalls past `DEFAULT_REQUEST_TIMEOUT_MS`, and `next.config.mjs` configures strict `connect-src 'self'` for `NEXT_PUBLIC_API_BASE_URL=/api/v1` and `Cache-Control: no-store` on `/account/:path*`, `/orders/:path*`, `/checkout/:path*`, `/login`, `/register`.

---

## 3. Sandbox / Environment Notes

- `pytest` (127.0.0.1 TCP loopback `socketpair`), `run_disposable_pg_pytest.py` (`initdb` timeout), `next build` (`EPERM` creating `.next/server/app/(admin)/admin`), and `e2e/run_e2e_stack.py` (`pg_isready` 127.0.0.1 TCP loopback) required `BypassSandbox: true`; static checks (`ruff`, `mypy`, `alembic heads`, `next lint`, `tsc --noEmit`) and Vitest passed inside the sandbox.
