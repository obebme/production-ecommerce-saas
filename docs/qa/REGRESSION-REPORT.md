# HARDEN-QA-01 — Production Hardening Regression & E2E Verification Report

- **Task ID**: `HARDEN-QA-01`
- **Branch**: `improvement/production-hardening`
- **Author**: `qa` specialist
- **Dependencies**: `HARDEN-BE-01`, `HARDEN-FE-CSP`, `HARDEN-BE-SWEEP-FIX`
- **Production Source Integrity**: Zero edits made to `backend/app/**`, `backend/alembic/**`, `frontend/src/**`, `frontend/next.config.mjs`, `compose*.yaml`, `.github/**`, or `docs/TASKS.md` by QA. Only authorized test/QA files were created or updated:
  - `backend/tests/test_config_and_health.py`
  - `backend/tests/test_admin_and_analytics.py`
  - `backend/tests/test_production_hardening.py`
  - `backend/tests/load/locustfile.py`
  - `backend/tests/load/run_load_suite.py`
  - `frontend/e2e/run_e2e_stack.py`
  - `frontend/e2e/storefront-and-admin.spec.ts`
  - `docs/qa/REGRESSION-REPORT.md`
  - `docs/qa/LOAD-TEST-REPORT.md`

---

## 1. Full Verification Suite Summary

### Backend (`e:\fullstack_work\backend`)

| # | Command | Scope & Runner | Outcome |
| :-: | :--- | :--- | :--- |
| 1 | `.\.venv\Scripts\ruff.exe check .` | Ruff lint across `backend/` | `All checks passed!` (exit `0`) |
| 2 | `.\.venv\Scripts\mypy.exe app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` | Mypy strict typecheck (`70` files) | `Success: no issues found in 70 source files` (exit `0`) |
| 3 | `.\.venv\Scripts\python.exe tests\run_disposable_pg_pytest.py` (`BypassSandbox: true`) | Full backend suite against disposable PostgreSQL `18.6` (`127.0.0.1:62272`, `pid=25732`, least-privilege `ecom_test_role`) | **`226 passed, 1 skipped, 1 warning in 84.56s (0:01:24)`** (exit `0`; `1` skipped for optional `REDIS_TEST_URL`, covered by `fakeredis` Lua runtime; clean PG PID teardown verified) |
| 4 | `.\.venv\Scripts\python.exe tests\load\run_load_suite.py` (`BypassSandbox: true`) | Locust `10` / `50` / `100` VUs (`15s`/tier) against disposable PG `18.6` (`127.0.0.1:58951`) + `uvicorn` (`127.0.0.1:58952`) | **`751` / `1436` / `1542` reqs, `0` failures, `invariants_ok=true`, `/api/v1/metrics` 200 OK** (exit `0`; see `docs/qa/LOAD-TEST-REPORT.md`) |

### Frontend (`e:\fullstack_work\frontend`)

| # | Command | Scope & Runner | Outcome |
| :-: | :--- | :--- | :--- |
| 5 | `npm.cmd run lint` | `next lint` (`frontend/`, after final edit) | `✔ No ESLint warnings or errors` (exit `0`) |
| 6 | `npx.cmd tsc --noEmit` | TypeScript strict check (`frontend/`, after final edit) | `0` errors (exit `0`) |
| 7 | `npm.cmd test` | Vitest `v4.1.12` (`jsdom`, `frontend/tests/*.test.ts*`) | **`6 files passed (6), 95 tests passed (95)`** (`0` failed, `0` skipped) in `10.64s` |
| 8 | `npm.cmd run build` (`BypassSandbox: true`) | `next build` (`Next.js 15.5.29`, standalone + middleware) | `21` dynamic routes + `34.6 kB` Middleware built cleanly (exit `0`) |
| 9 | `..\backend\.venv\Scripts\python.exe e2e\run_e2e_stack.py --auto-ports` (`BypassSandbox: true`) | Live Chromium Playwright E2E + disposable PG `18.6` (`54547`, `pid=15228`) + `uvicorn` (`54549`, `pid=26392`) + `node .next/standalone/server.js` (`3000`, `pid=20772`) | **`6 passed (39.9s)`** (`0` failed, `0` skipped, exit `0`; default `.next` build restored & clean teardown verified) |

---

## 2. Hardening Verification Coverage

### A. Production Provider Fail-Closed Settings (`backend/tests/test_config_and_health.py`, `backend/tests/test_admin_and_analytics.py`)
- Updated `_valid_prod_kwargs()` and isolated the 5 positive production-config tests + `test_cli_create_user_and_seed_demo` with temporary `_MockProductionPaymentAdapter` registration/unregistration on `get_default_payment_registry()`, preserving all production security checks.
- Added explicit fail-closed regression tests:
  - `test_harden_qa_01_production_without_registered_provider_fails_closed`: verifies `Settings(environment="production", payment_sandbox_mode=False, ...)` raises `ValidationError` by default when no production-ready PSP adapter is registered, and rejects unregistered `payment_provider`.
  - `test_harden_qa_01_sandbox_simpay_requires_sandbox_mode_true`: verifies `Settings(environment="development", payment_provider="sandbox_simpay", payment_sandbox_mode=False, ...)` raises `ValidationError`.

### B. Distributed Rate Limiter, Sweeper, Provider Architecture & Observability (`backend/tests/test_production_hardening.py`)
1. **Distributed Redis Rate Limiter (`fakeredis` Lua + optional `REDIS_TEST_URL`)**:
   - Verified atomic sliding-window enforcement, `0 < PTTL <= window_ms`, sliding expiration recovery, and prefix-scoped `reset()`.
   - Verified multi-instance consistency across two `create_app()` instances sharing one `fakeredis.FakeRedis` (`POST /api/v1/auth/register` `limit=5` -> alternating calls allow `5` and block the `6th` with `429 RATE_LIMIT_EXCEEDED` + `Retry-After`).
   - Verified `20`-thread concurrent race against `limit=5` allows strictly `5` and denies `15`.
   - Verified `RedisError` outage policy: under `fail_closed_sensitive`, `/api/v1/auth/login` and `/api/v1/checkout/orders` fail closed with `503 RATE_LIMITER_UNAVAILABLE`, whereas non-sensitive `/api/v1/cart/items` falls back to local `SlidingWindowRateLimiter` with `max(1, limit // 2)`; under `fail_closed_all`, non-sensitive paths also raise `503 RATE_LIMITER_UNAVAILABLE`.
2. **Coordinated Reservation Sweeper & Multi-Worker Guest Cap (Live PostgreSQL `18.6`)**:
   - Verified `4` concurrent `run_coordinated_expired_reservation_sweep()` workers against `15` expired `PENDING_PAYMENT` orders sharing overlapping variants: `15` reclaimed, `stock.reserved == 0`, exact `1` `RELEASE` ledger entry per order, zero double-releases, and explicit `pg_try_advisory_lock(4104, 1)` contention causes skipped workers to return `(0, False)`.
   - Verified sweeper racing `mark_order_paid` (webhook) and `cancel_customer_order` concurrently on PostgreSQL with zero inconsistent states or double-releases.
   - Verified abrupt connection invalidation/close releases the session advisory lock so the next sweeper recovers cleanly.
   - Verified cooperative `stop_event` stops mid-batch (`batch_size=2`) cleanly and releases the advisory lock for subsequent sweeps.
   - Verified two separate `create_app()` instances (simulating worker restarts with empty in-memory `GuestPendingOrderTracker`) sharing PostgreSQL enforce `MAX_ACTIVE_GUEST_PENDING_ORDERS = 3` cleanly (`4th` guest checkout returns `429 RATE_LIMIT_EXCEEDED`).
3. **Provider-Independent Payment Architecture**:
   - Registered custom `_CustomMockPaymentAdapter("custom_psp")` on `PaymentProviderRegistry`: verified `create_intent`, `cancel_intent`, `reconcile_payment_intent` (`PROCESSING -> SUCCEEDED` and `PROCESSING -> FAILED`), duplicate webhook `IGNORED_DUPLICATE`, out-of-order late webhook staging `REFUND_PENDING` when stock is depleted, and 3-phase `refund_admin_order` routing through `custom_psp.refund()`.
4. **Observability & Database Query Plans**:
   - Verified `/api/v1/health`, `/api/v1/health/live`, `/api/v1/health/ready` (`disabled`, `ok`, `503 REDIS_UNAVAILABLE`, `degraded`, and `503 DATABASE_UNAVAILABLE`), and `/api/v1/metrics` (`401` unauthenticated/bad bearer, `403` customer cookie, `200` admin cookie or valid Bearer token, `Cache-Control: no-store`, `404` when disabled, zero raw emails or order numbers in metric labels).
   - Verified `StructuredJsonFormatter` propagates `request_id` and redacts passwords, CSRF tokens, cookies, authorization headers, webhook secrets, and database URLs.
   - Verified `alembic check` (`0` drift), `alembic downgrade base && alembic upgrade head`, and `EXPLAIN (FORMAT JSON)` valid query plans on catalog list, guest pending order lookup, expired reservation candidate scan, and payment intent lookup on live PostgreSQL `18.6`.

### C. Strict Per-Request Nonce CSP & Security Headers (`frontend/e2e/storefront-and-admin.spec.ts` Test 6)
- Verified on live `node .next/standalone/server.js` + `uvicorn` + PostgreSQL `18.6`:
  - `Content-Security-Policy` on `/`, `/products/linen-overshirt`, `/login`, and `/admin` contains `script-src 'self' 'nonce-...` without `'unsafe-inline'` or `'unsafe-eval'`.
  - Consecutive requests to `/` return distinct per-request nonces matching inline `<script nonce="...">` in SSR HTML.
  - Preserved headers: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` on `/admin`, `Cache-Control: no-store` on `/admin`, `/cart`, `/checkout`, `/login`.
  - Rendered DOM inline `<script>` elements on `/`, `/products/linen-overshirt`, and `/login` carry nonces, and `securitypolicyviolation` + console CSP error listeners recorded **`0` CSP violations** across storefront navigation AND Admin Analytics (`/admin` with Recharts SVG charts rendered).

---

## 3. Defects Found & Resolved During QA

1. **P1 Defect Caught & Fixed (`HARDEN-BE-SWEEP-FIX` in `backend/app/modules/inventory/service.py`)**:
   - **Finding**: During live PostgreSQL `18.6` sweeper testing, `run_coordinated_expired_reservation_sweep()` acquired `pg_try_advisory_lock(4104, 1)` on a pooled connection via `Session`, released that connection back to `QueuePool` on `db.commit()`/`db.rollback()`, and then ran `pg_advisory_unlock(4104, 1)` in `finally:` on a potentially different pooled connection — logging `WARNING: you don't own a lock of type ExclusiveLock` and leaking the session lock in `QueuePool`.
   - **Resolution**: Escalated to backend owner; `HARDEN-BE-SWEEP-FIX` pinned a dedicated `AUTOCOMMIT` `Connection` for advisory lock acquisition/unlock. Re-verified in full `run_disposable_pg_pytest.py` run (`226 passed`).
2. **Test Helper Race Fixed (`frontend/e2e/storefront-and-admin.spec.ts`)**:
   - **Finding**: Next.js 15 `<React.Suspense>` streaming on `/login` briefly kept a hidden SSR chunk alongside the hydrated form for a few milliseconds after `Sign in` heading became visible, triggering a Playwright strict-mode locator count `2` on `#login-email`.
   - **Resolution**: Added `await expect(page.locator("#login-email")).toHaveCount(1)` and `#login-password` count check inside `signIn()`; re-verified `6/6` Playwright tests passing.

---

## 4. Environmental Limitations (Honest Scope)

- **Live Redis Server**: `REDIS_TEST_URL` was not configured in this local Windows environment, so `test_redis_rate_limiter_live_redis_when_configured` skipped (`1` skip). All Redis Lua sliding-window, `PTTL`, multi-instance, concurrency, and outage behaviors were verified via `fakeredis` Lua runtime.
- **E2E Ephemeral Backend Port**: `127.0.0.1:8000` was occupied by an unrelated local Docker container (`files-pasted-by-the-user-ai-api-1`), so `run_e2e_stack.py --auto-ports` ran uvicorn on ephemeral port `54549` (with Next.js standalone on `3000`) and restored the default `localhost:3000`/`8000` `.next` build upon completion.

---

## 5. Final Release Verification Addendum (`HARDEN-REL-VERIFY`)

All previously deferred Docker, Trivy, live `PostgreSQL 16.16`, live `Redis 7.4.12`, least-privilege DB role, production Compose, failover, and fail-closed startup checks have since been executed against real containers:

1. **Full Backend Suite on Real `postgres:16-alpine` (`16.16`) + `redis:7-alpine` (`7.4.12`)**:
   - Executed `pytest -v` with `TEST_DATABASE_URL` pointing at `postgres:16-alpine` (`127.0.0.1:15432`) and `REDIS_TEST_URL=redis://127.0.0.1:16379/0`: **`227 passed, 0 skipped, 0 failed in 89.83s`** (confirming `test_redis_rate_limiter_live_redis_when_configured` passes against real Redis 7).
2. **DB Role Separation (`init-roles.sql`) & Migrations on `PostgreSQL 16.16`**:
   - Executed `infra/postgres/init-roles.sql` twice idempotently; verified `alembic heads` (`0003_phase2_indexes_and_cleanup`), `alembic upgrade head`, `alembic check` (`No new upgrade operations detected.`), `alembic downgrade base`, and `alembic upgrade head`.
   - Verified `ecom_migrator` & `ecom_app` carry `(rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolinherit=False, rolreplication=False)`; `ecom_app` succeeded on `INSERT/SELECT/UPDATE/DELETE` and was rejected with SQLSTATE `42501` on `CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`, and `alembic downgrade -1`.
3. **Hardened Container Images & Trivy `0.58.1` (`HARDEN-REL-OPS-01`)**:
   - Built `atelier-backend:relverify` (`c7c4b6c920d4`, `343MB`) and `atelier-frontend:relverify` (`dae132a979e3`, `382MB`). After remediation (`apt-get upgrade -y` + uninstalling `pip`/`ensurepip` from runtime in `backend.Dockerfile`; `apk upgrade --no-cache` + stripping global `npm`/`corepack`/`yarn` in `frontend.Dockerfile`), both images exited **`0`** under `--pkg-types os,library --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1` (`0` Python CVEs, `0` Node.js CVEs, `0` Alpine OS CVEs; `53` High / `88` Medium / `96` Low unfixed upstream Debian `13.8` advisories remain tracked).
4. **`compose.prod.yaml` Runtime & Caddy Edge Security (`atelier-prodverify`)**:
   - Executed exact bootstrap (`up -d db` -> `init-roles.sql` -> `up -d`): `migrate` exited `0`, `db`/`redis`/`api`/`web`/`caddy` all reached `Up (healthy)`, zero non-Caddy host ports published, `read_only` rootfs enforced on `api`/`web`/`redis`/`caddy`, HTTPS `GET /` returned `Strict-Transport-Security: max-age=31536000; includeSubDomains`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `script-src 'self' 'nonce-...'`, `/api/v1/metrics` returned **`404`** through Caddy and **`200`** (`116` lines) internally with bearer auth, and `app.*` emitted structured JSON logs with `request_id`.
5. **Redis & PostgreSQL Failure Recovery (Zero API Restarts)**:
   - Stopping `redis-1` yielded `/health/live=200`, `/health/ready=503 REDIS_UNAVAILABLE`, `/auth/login=503 RATE_LIMITER_UNAVAILABLE`, and `/products=200`; restarting `redis-1` automatically restored `200` on `/health/ready` and `/auth/login`.
   - Stopping `db-1` yielded `/health/live=200`, `/health/ready=503 DATABASE_UNAVAILABLE`, and `/products=500` JSON error envelope; restarting `db-1` automatically restored `200` via SQLAlchemy `pool_pre_ping=True` (`RestartCount=0`, unchanged container `StartedAt`).
6. **Production Sandbox Payment Rejection**:
   - `uvicorn app.main:app` inside `atelier-backend:relverify` with `ENVIRONMENT=production` and `PAYMENT_PROVIDER=sandbox_simpay` exited **`3`** fail-closed for both `PAYMENT_SANDBOX_MODE=true` and `PAYMENT_SANDBOX_MODE=false`.
