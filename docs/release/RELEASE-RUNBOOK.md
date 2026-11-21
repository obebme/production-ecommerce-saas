# Release Runbook & Verification Matrix (`REL-001`)

- **Document ID**: `REL-001` (`docs/release/RELEASE-RUNBOOK.md`)
- **Date**: `2026-11-20`
- **Baseline Release Status**: **Code, Unit/Integration Tests, Live E2E, CI Config, and Pre-Release Security Audit Complete (`0` Critical / High / Medium Findings)** — **NOT yet live-deployed, NOT PCI-certified**, with remote GitHub Actions CI & local Docker (`postgres:16-alpine`) container parity pending execution.

---

## 1. Non-PCI / Sandbox-Only Disclaimer

1. **No Live Payment Processing**: Baseline checkout and webhooks use the sandbox SimPay adapter (`PAYMENT_SANDBOX_MODE=true`). No credit card numbers, PAN, CVV, or expiry fields exist in frontend forms or backend schemas/tables.
2. **Production Fail-Closed Enforcement**: As implemented in `backend/app/core/config.py`, setting `ENVIRONMENT=production` aborts application startup unless `PAYMENT_SANDBOX_MODE=false`, `COOKIE_SECURE=true`, `ALLOWED_ORIGINS` specifies explicit `https://` origin(s) without `*`, `DATABASE_URL` uses PostgreSQL (non-SQLite, non-placeholder), and all three HMAC signing secrets (`CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, `PAYMENT_WEBHOOK_SECRET`) are distinct, non-placeholder, trimmed values `>= 32` characters.
3. **Required Before Accepting Real Customer Payments**: Replace the sandbox gateway adapter with a PCI-compliant hosted/tokenized PSP flow (never holding DB `FOR UPDATE` row locks across remote HTTP calls per `BE-004` `INFO-3`), complete `SEC-001` pre-production gates below, and obtain formal security/PCI compliance sign-off.

---

## 2. Pre-Production Deployment & Hardening Checklist

Complete every item before deploying to any shared staging or production environment:

- [ ] **Remote CI Verification on `postgres:16-alpine`**: Push to GitHub and confirm all 5 jobs (`detect`, `ops-validate`, `backend-ci`, `frontend-ci`, `e2e-ci`) pass against `postgres:16-alpine` and Docker multi-stage builds (`backend.Dockerfile`, `frontend.Dockerfile`).
- [ ] **Frontend `.dockerignore` (`SEC-001` `LOW-1`)**: Before running local/CI Docker builds from dirty checkouts, have `frontend` owner add `frontend/.dockerignore` excluding `node_modules/`, `.next/`, `out/`, `test-results/`, `playwright-report/`, `.env*`, `tests/`, `e2e/`, and `*.tsbuildinfo`.
- [ ] **TLS Reverse Proxy & Headers (`SEC-001` `INFO-1`, `LOW-5`)**:
  - Terminate TLS 1.2/1.3 at the edge proxy and inject `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
  - Pass trusted forwarded headers to `uvicorn --proxy-headers --forwarded-allow-ips=<proxy_subnet>` (`backend.Dockerfile` CMD/args) so client IP rate limiting and audit logs reflect real client IPs behind a load balancer.
  - If routing browser API calls through same-origin `/api/v1`, fix `deriveApiConnectOrigin()` in `frontend/next.config.mjs` (`LOW-5`) so CSP `connect-src` stays strictly `'self'`.
  - Optionally harden CSP further with per-request nonces in Next.js `middleware.ts`.
- [ ] **Backend `Cache-Control: no-store` & Supply-Chain Pinning (`SEC-001` `LOW-2`, `LOW-4`)**:
  - Add `Cache-Control: no-store` on PII/admin FastAPI routes (`/api/v1/admin/*`, `/api/v1/orders*`, `/api/v1/checkout/*`, `/api/v1/cart*`).
  - Generate hash-pinned backend lockfile (`pip-compile --generate-hashes`) and enable `pip-audit` + `npm audit --audit-level=high` in CI; remove unused `python-multipart` if multipart uploads remain out of scope.
- [ ] **Backend URL Validation Alignment (`SEC-001` `LOW-3`)**: Check `_has_unsafe_url_chars(decoded_pass)` after `unquote()` on every iteration inside `validate_product_image_url` (`backend/app/modules/catalog/schemas.py`).
- [ ] **Environment & Cryptographic Secrets Provisioning**:
  - Set `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `PAYMENT_SANDBOX_MODE=false`, explicit `https://` `ALLOWED_ORIGINS`, strong `POSTGRES_PASSWORD` / `DATABASE_URL`, and three independent `>= 32`-char secrets (`CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, `PAYMENT_WEBHOOK_SECRET`) stored in platform secret manager (never `.env` in Git).
  - If running across multiple FastAPI worker replicas, replace in-memory `SlidingWindowRateLimiter` and `GuestPendingOrderTracker` (`backend/app/core/security.py`) with shared backing state (per ADR governance / user approval).

---

## 3. Secret Rotation, Migration & Admin Bootstrap Procedures

### 3.1 Secret Rotation Impact
- **`CSRF_HMAC_SECRET`**: Invalidates active CSRF signatures derived from existing sessions; schedule maintenance or prompt user re-authentication if rotating abruptly (`GET /api/v1/auth/me` issues fresh CSRF tokens for valid sessions; HMAC verification uses the active secret).
- **`GUEST_ORDER_HMAC_SECRET`**: Rotating invalidates active 30-day `ecom_guest_orders` cookies and guest lookup tokens (guests recover access via support or authenticated email matching if registered).
- **`PAYMENT_WEBHOOK_SECRET`**: Coordinate dual-step rotation with payment provider webhook signing configuration so verified webhook delivery never drops during cutover.

### 3.2 Alembic Database Migration & Rollback Runbook
All schema changes strictly flow through Alembic (`Base.metadata.create_all()` is forbidden in app startup). Current linear head: `0002_phase1_commerce_integrity`.

1. **Before deploying**:
   ```bash
   cd backend
   alembic heads   # Expect exactly 1 head revision (0002_phase1_commerce_integrity)
   ```
2. **Pre-migration backup**: Take a verified PostgreSQL snapshot / `pg_dump` before upgrading production schemas.
3. **Apply migration**:
   ```bash
   alembic upgrade head
   ```
   *(In Docker Compose, the one-shot `migrate` service completes `alembic upgrade head` before `api` starts.)*
4. **Emergency rollback**:
   Stop `web` and `api` first so live queries never hit missing columns/tables (`docker compose stop web api`), run `alembic downgrade -1` (development/recovery) or restore pre-migration DB snapshot (recommended for populated production tables), then inspect `alembic current`.

### 3.3 Administrator Bootstrap (`app.cli`)
Never use `seed-demo` in production (hard-exits `1` when `ENVIRONMENT=production`). Create initial administrators with `--password-env` (`BE-004` `INFO-2`):

```bash
export ADMIN_BOOTSTRAP_PASSWORD="<strong-unique-password>"
python -m app.cli create-user \
  --email ops-admin@example.com \
  --full-name "Operations Administrator" \
  --role admin \
  --password-env ADMIN_BOOTSTRAP_PASSWORD
unset ADMIN_BOOTSTRAP_PASSWORD
```

For non-production staging environments where demo seed data is desired (`INFO-1`), always override `DEMO_ADMIN_PASSWORD`, `DEMO_STAFF_PASSWORD`, and `DEMO_CUSTOMER_PASSWORD` when passing `--with-demo-users`.

### 3.4 Expired Reservation Sweeper (`app.cli sweep-expired-reservations`) & Phase 1 Remediation Summary
Phase 1 audit remediation (`REM-P1-BE` / `REM-P1-FE` / `REM-P1-QA` / `REM-P1-SEC`) added:
- **Periodic & CLI Sweeper (`H-01`, `M-06`)**: FastAPI `lifespan` runs a periodic expired-reservation sweeper every `RESERVATION_SWEEPER_INTERVAL_SECONDS` (default `60`, disabled in `test` or `0`), and operators/cron can run bounded sweeps via:
  ```bash
  python -m app.cli sweep-expired-reservations --batch-size 100 --max-batches 20
  ```
- **Durable Refund Staging (`H-02`, `M-01`, `M-02`)**: Automated edge-case captures and 3-phase admin refunds track state via `payment_refund_requests` and `REFUND_PENDING` without holding database `FOR UPDATE` row locks across refund gateway calls.
- **Webhook Retry & Read-Only Cart (`H-03`, `H-05`)**: Failed webhook events (`payment_webhook_events.status = 'FAILED'`) are safely reclaimable on retry while `PROCESSED` rows remain idempotent; `GET /api/v1/cart` is read-only and `CartProvider` is mounted only under the storefront layout.
- **Atomic Guest Pending Cap (`H-06`)**: Enforced atomically in PostgreSQL via `orders.guest_client_key_hash` and namespaced `pg_advisory_xact_lock(2, hashtext(...))`. Note: until Phase 2 `H-04` configures `--proxy-headers`, reverse-proxied traffic collapses `request.client.host` to the proxy IP (sharing one guest pending-order bucket), and general IP sliding-window rate limiting (`SlidingWindowRateLimiter`) remains per-process in memory.

---

## 4. Architecture & Dependency Decisions Summary

Recorded on `2026-11-18` in [TASKS.md](../TASKS.md) (`24e4e75`) and executed cleanly through Wave 4:

| Dependency / Decision | Outcome | Scope & Rationale |
| :--- | :--- | :--- |
| **`recharts` (`^3.11.0`)** | **Adopted** | Used in `FE-003` `/admin` daily Net Revenue & Paid Orders chart (`AdminDashboardView`), paired with a keyboard-accessible `Chart \| Data Table` parity toggle. |
| **`@playwright/test` (`^1.65.1`)** | **Adopted** | Used in `QA-E2E-001` (`Chromium`-only live full-stack E2E across Next.js + FastAPI + PostgreSQL). |
| **Step 0 Frontend Remediation (`43fea31`)** | **Executed** | Upgraded `vitest` `^3.0.8` -> `^4.1.12` (eliminated `vitest`/`tinypool` criticals & `@vitest/mocker` moderate) and aligned nested `next -> postcss` via `"postcss": "^8.5.33"` + `"overrides": { "next": { "postcss": "^8.5.33" } }`, bringing `npm.cmd audit --json` to **`0` vulnerabilities across `681` packages** and `npm.cmd ls` exit `0` without breaking Next 15. |
| **TanStack Query / Table, RHF + Zod, `openapi-typescript`** | **Deferred / Rejected** | Retained existing `AuthProvider`/`CartProvider` + typed `apiRequest` client, shadcn `Table`, controlled server-validated forms (`extractValidationErrors`), and manual `types.ts` verified by QA contract checks. |
| **Redis, Celery, Microservices** | **Rejected in Baseline** | Single modular-monolith API + PostgreSQL (`db`, `migrate`, `api`, `web`) with atomic DB locking and lazy reservation reclamation avoids unrequested operational complexity. |

---

## 5. Permanent Windows Subprocess & Disposable PostgreSQL Safeguards

Following recovery of an earlier `pg_ctl` pipe hang, all local multi-process runners ([backend/tests/run_disposable_pg_pytest.py](../../backend/tests/run_disposable_pg_pytest.py) and [frontend/e2e/run_e2e_stack.py](../../frontend/e2e/run_e2e_stack.py)) enforce six mandatory safeguards documented in [local-development-and-ci.md §7](../ops/local-development-and-ci.md#7-windows-subprocess--disposable-postgresql-operational-safeguards):

1. **No Daemon `subprocess.PIPE`**: Redirect `initdb`, `pg_ctl`, `uvicorn`, and `next start` output strictly to temporary log files (`stdin=subprocess.DEVNULL, close_fds=True`) so detached child processes never inherit pipe write handles and deadlock `wait()`.
2. **UTF-8 Replacement Decoding**: Pass `encoding="utf-8", errors="replace"` on short captured tools (`psql`, `tasklist`) so localized Windows output never triggers `_readerthread` `UnicodeDecodeError`.
3. **Bounded Timeouts & Global Watchdog**: Set per-step subprocess timeouts, bounded TCP/HTTP readiness polling with flushed `[pg-runner]`/`[e2e-runner]` output, and an overall `threading.Timer` watchdog.
4. **Host Database Protection**: Refuse `127.0.0.1:5432` and `E:\databasesql\data`; isolate temporary clusters strictly inside `%TEMP%` on ephemeral ports `!= 5432` with least-privilege `ecom_test_role`.
5. **Verified `try/finally` Teardown**: Terminate Next.js/Uvicorn process trees (`taskkill /PID <pid> /T /F`), stop PostgreSQL (`pg_ctl -m fast stop`), assert `_is_pid_running == False` across all temporary PIDs, preserve secret-redacted diagnostic logs on failure, and delete `%TEMP%` directories on success.

---

## 6. Evidence-Linked Verification Matrix

### 6.1 Verified Deliverables & Checks

| Category / Task | Commit | Verified Commands & Observed Results | Primary Evidence Artifact(s) |
| :--- | :--- | :--- | :--- |
| **Architecture (`ARCH-001`)** | `89b17ee` | Architecture, ERD, concurrency/security spec & OpenAPI contract review (`0` Critical/High) | [01-system-overview]( ../architecture/01-system-overview-and-modules.md), [openapi-contracts.md](../api/openapi-contracts.md), [ARCH-001-security-review.md](../security/ARCH-001-security-review.md) |
| **UX Specs (`DESIGN-001`)** | `ac9652a` | Design tokens, Storefront/Checkout UX & Admin/RBAC/State matrix | [01-design-system](../design/01-design-system-and-tokens.md), [02-storefront](../design/02-storefront-and-checkout-ux.md), [03-admin](../design/03-admin-and-states-ux.md) |
| **Ops Baseline (`OPS-001`)** | `6390eed` | Compose YAML & `docker compose config` validated statically | [compose.yaml](../../compose.yaml), [.env.example](../../.env.example), [local-development-and-ci.md](../ops/local-development-and-ci.md) |
| **Backend (`BE-001`..`BE-004`, `QA-PG-001`, `QA-BE-001`..`QA-BE-004`)** | `2739d9f`, `e4af80d`, `4f51907`, `e5d0097`, `c949b93`, `9831d31` | `ruff check .` (`0`); `mypy app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` (`0` issues / `55` files); `alembic heads` (`0001_initial_schema`); SQLite `pytest -v`: **`161 passed, 4 skipped`** (`39.54s`); Disposable PG 18.6 (`run_disposable_pg_pytest.py`, port `65458`, `ecom_test_role`): **`165 passed, 0 failed, 0 skipped`** (`56.60s`); `0` open Crit/High/Med across `BE-001`..`BE-004` security reviews. | [QA-PG-001](../qa/QA-PG-001-postgresql-verification.md), [QA-BE-003](../qa/QA-BE-003-verification.md), [QA-BE-004](../qa/QA-BE-004-verification.md), [BE-004-security-review.md](../security/BE-004-security-review.md) |
| **Frontend & Unit/Component QA (`FE-001`..`FE-003`, `QA-FE-001`..`QA-FE-003`)** | `0705b14`, `97ff9e2`, `591062e` | At Wave 3 `591062e`: `eslint` (`0`); `tsc --noEmit` (`0`); `vitest run` (`jsdom` + mocked `fetch`/`next/navigation`): **`5` files / `60 passed`** (`11.13s`); `next build` (`BypassSandbox`): **`19/19` pages / `21` routes** (exit `0`). | [QA-FE-001](../qa/QA-FE-001-verification.md), [QA-FE-002](../qa/QA-FE-002-verification.md), [QA-FE-003](../qa/QA-FE-003-verification.md), [FE-001-security-review.md](../security/FE-001-security-review.md) |
| **Frontend Dependency Remediation (Step 0)** | `43fea31` | `vitest@^4.1.12` + `next -> postcss@^8.5.33` override via `npm@11.19.0`; `npm.cmd ls postcss vitest` exit `0`; `npm.cmd audit --json`: **`0` vulnerabilities**; `eslint` (`0`), `tsc` (`0`), `vitest@4.1.12` **`60 passed`** (`9.35s`), `next build` (`19/19` pages / `21` routes, exit `0`). | [package.json](../../frontend/package.json), [package-lock.json](../../frontend/package-lock.json) |
| **Full-Stack Live E2E (`QA-E2E-001`)** | `df6c303` | `@playwright/test@1.65.1` + Chromium (`0` audit advisories); Orchestrator run of `run_e2e_stack.py` against disposable PG 18.6 (`port=57513`) + `uvicorn` + `next start`: **`4 passed (29.3s)`** (`0` failed, `0` skipped), verified PID teardown (`zero temporary processes remain`), plus `eslint` (`0`), `tsc` (`0`), `vitest` **`60 passed`** (`9.05s`). | [playwright.config.ts](../../frontend/playwright.config.ts), [run_e2e_stack.py](../../frontend/e2e/run_e2e_stack.py), [storefront-and-admin.spec.ts](../../frontend/e2e/storefront-and-admin.spec.ts), [QA-E2E-001-verification.md](../qa/QA-E2E-001-verification.md) |
| **CI & Ops Finalization (`OPS-002`)** | `4fd3512` | `PyYAML` validation of `compose.yaml` (`api, db, migrate, web`) & `.github/workflows/ci.yml` (`detect, ops-validate, backend-ci, frontend-ci, e2e-ci`); `docker compose --env-file .env.example config --quiet` (exit `0`); `node:22-alpine` aligned; CLI & Windows subprocess safeguards documented. | [.github/workflows/ci.yml](../../.github/workflows/ci.yml), [frontend.Dockerfile](../../infra/docker/frontend.Dockerfile), [local-development-and-ci.md](../ops/local-development-and-ci.md) |
| **Pre-Release Security Audit (`SEC-001`)** | `3a7704a` | Read-only audit across backend, frontend, runners, Dockerfiles & CI: **`0` Critical, `0` High, `0` Medium** (`5` Low, `4` Info tracked); `frontend` `npm audit` **`0/681`** (`43fea31`/`df6c303`; offline inside sandbox); `backend/.venv` distributions inspected. | [SEC-001-pre-release-audit.md](../security/SEC-001-pre-release-audit.md) |
| **Release Docs (`REL-001`)** | *(final commit)* | Root `README.md`, `docs/release/RELEASE-RUNBOOK.md`, and `docs/TASKS.md` registry completed cleanly on `main`. | [README.md](../../README.md), [RELEASE-RUNBOOK.md](./RELEASE-RUNBOOK.md), [TASKS.md](../TASKS.md) |

### 6.2 Unverified Items

| Item | Reason & How to Verify |
| :--- | :--- |
| **Remote GitHub Actions Workflow Execution** | CI definitions ([ci.yml](../../.github/workflows/ci.yml)) were validated syntactically via `PyYAML` locally; actual GitHub-hosted runner execution (`ops-validate`, `backend-ci`, `frontend-ci`, `e2e-ci` on `postgres:16-alpine`) has not executed on a remote runner yet. |
| **Real Payment Gateway & Webhook Settlement** | Baseline supports only the sandbox SimPay adapter (`PAYMENT_SANDBOX_MODE=true`); live PSP SDK/API calls, dispute webhooks, and PCI scope require a future payment-provider integration milestone. |
| **Multi-Replica Distributed Rate Limiting & Load Benchmarks** | Rate limiting (`SlidingWindowRateLimiter`) and pending guest-order tracking (`GuestPendingOrderTracker`) use in-memory per-process structures; horizontal multi-replica behavior and sustained concurrency load benchmarks remain unverified. |

### 6.3 Blocked Items (Environment-Specific)

| Item | Local Blocker Observed | Path to Resolution |
| :--- | :--- | :--- |
| **Docker Image Builds (`backend.Dockerfile`, `frontend.Dockerfile`) & `docker compose up --build`** | `docker info` returned exit `1`: Docker Desktop Linux Engine (`//./pipe/dockerDesktopLinuxEngine`) is not running on the Windows workstation. | Start Docker Desktop on host (after adding `frontend/.dockerignore` per `SEC-001` `LOW-1`) or run GitHub Actions `ops-validate`. |
| **Local Target `postgres:16-alpine` Runtime Execution** | Blocked locally by stopped Docker Desktop engine. All live PostgreSQL integration, migration, concurrency, and full-stack E2E tests instead verified against isolated **PostgreSQL 18.6** binaries (`E:\databasesql\bin`). | Run GitHub Actions `backend-ci` and `e2e-ci` (`postgres:16-alpine` services) or run Docker Compose with Docker Desktop active. |

---

## 7. Known Risks & Release Sign-Off Notes

1. **PostgreSQL Version Delta (PG 18.6 Local vs. PG 16 Target)**: SQLite (`161 passed`), live PG 18.6 (`165 passed`), and full-stack Playwright E2E on PG 18.6 (`4 passed`) passed cleanly; exact `postgres:16-alpine` container parity remains pending remote CI or local Docker Desktop startup.
2. **`SEC-001` Low/Info Tracking**: `LOW-1` (`frontend/.dockerignore` absent before local container builds), `LOW-2` (API `Cache-Control: no-store` on PII/admin routes), `LOW-3` (3rd `unquote()` pass check in `validate_product_image_url`), `LOW-4` (pinned Python hash lockfile + `pip-audit` + unused `python-multipart`), `LOW-5` (relative `/api/v1` CSP `connect-src` fallback), and `INFO-1`..`INFO-4` (TLS edge HSTS, `uvicorn --proxy-headers`, single-process rate limiter, non-prod `DEMO_*_PASSWORD` overrides) are documented for pre-staging/production completion.
3. **Standalone Next.js Local E2E Notice**: Local `run_e2e_stack.py` runs `next start` against `.next` (logging Next's non-blocking `output: "standalone"` notice while serving all routes cleanly); both `frontend.Dockerfile` and GitHub Actions `e2e-ci` execute the production standalone server (`node server.js` / `node .next/standalone/server.js`) with copied `.next/static` and `public/` assets.
