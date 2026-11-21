# Release Runbook & Verification Matrix (`REL-001`)

- **Document ID**: `REL-001` (`docs/release/RELEASE-RUNBOOK.md`)
- **Date**: `2026-11-21`
- **Baseline Release Status**: **Phase 3A/3B Pre-Staging Security, Supply-Chain, Metadata/A11y Hardening & Local Docker (`postgres:16-alpine`) Runtime Verification Complete (`REM-P3B-SEC`: `GO`, `0` Crit/High/Med/Low)** — **NOT yet live-deployed, NOT PCI-certified**; remote GitHub Actions CI remains unexecuted locally (no Git remote configured).

---

## 1. Non-PCI / Sandbox-Only Disclaimer

1. **No Live Payment Processing**: Baseline checkout and webhooks use the sandbox SimPay adapter (`PAYMENT_SANDBOX_MODE=true`). No credit card numbers, PAN, CVV, or expiry fields exist in frontend forms or backend schemas/tables.
2. **Production Fail-Closed Enforcement**: As implemented in `backend/app/core/config.py`, setting `ENVIRONMENT=production` aborts application startup unless `PAYMENT_SANDBOX_MODE=false`, `COOKIE_SECURE=true`, `ALLOWED_ORIGINS` specifies explicit `https://` origin(s) without `*`, `DATABASE_URL` uses PostgreSQL (non-SQLite, non-placeholder), and all three HMAC signing secrets (`CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, `PAYMENT_WEBHOOK_SECRET`) are distinct, non-placeholder, trimmed values `>= 32` characters.
3. **Required Before Accepting Real Customer Payments**: Replace the sandbox gateway adapter with a PCI-compliant hosted/tokenized PSP flow (never holding DB `FOR UPDATE` row locks across remote HTTP calls per `BE-004` `INFO-3`), complete `SEC-001` pre-production gates below, and obtain formal security/PCI compliance sign-off.

---

## 2. Pre-Production Deployment & Hardening Checklist

Complete every item before deploying to any shared staging or production environment:

- [ ] **Remote CI Verification on `postgres:16-alpine`**: Push to GitHub and confirm all 5 jobs (`detect`, `ops-validate`, `backend-ci`, `frontend-ci`, `e2e-ci`) pass against `postgres:16-alpine` and Docker multi-stage builds (`backend.Dockerfile`, `frontend.Dockerfile`).
- [x] **Frontend `.dockerignore` (`SEC-001` `LOW-1` / `H-07`)**: `frontend/.dockerignore` added to exclude `node_modules/`, `.next/`, `out/`, `test-results/`, `playwright-report/`, `.env*`, `tests/`, `e2e/`, and `*.tsbuildinfo` from Docker build contexts.
- [ ] **TLS Reverse Proxy & Forwarded-IP Deployment Hardening (`SEC-001` `INFO-1`, `LOW-5` / Phase 2 `H-04`, `M-10`)**:
  - [ ] Terminate TLS 1.2/1.3 at the edge proxy and inject `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
  - [x] Application trusted-proxy resolution (`TRUSTED_PROXY_IPS`) implemented in `backend/app/core/security.py` (`resolve_client_ip`) and wired across rate-limit/audit/guest-cap paths (`H-04`); configure `TRUSTED_PROXY_IPS` in deployment `.env` / Compose (`--proxy-headers` optional behind explicit `TRUSTED_PROXY_IPS`).
  - [x] Relative `/api/v1` `deriveApiConnectOrigin()` CSP `connect-src` `'self'` fix completed in `frontend/next.config.mjs` (`M-10` / `LOW-5`).
  - [ ] Optionally harden CSP further with per-request nonces in Next.js `middleware.ts`.
- [x] **Backend `Cache-Control: no-store` & Supply-Chain Pinning (`SEC-001` `LOW-2`, `LOW-4` / Phase 2 `M-07`, `L-04` / Phase 3B)**:
  - [x] `SensitiveRouteNoStoreMiddleware` enforces `Cache-Control: no-store` on sensitive API routes (`/api/v1/{auth,cart,checkout,orders,payments,admin}`), and `frontend/next.config.mjs` sets `Cache-Control: no-store` on `/cart/:path*`, `/account/:path*`, `/orders/:path*`, `/checkout/:path*`, `/login`, `/register` (`M-07`, `SEC-M09-03`).
  - [x] Unused `python-multipart` removed from `backend/requirements.txt` and `backend/pyproject.toml` (`L-04`), and CI enforces `pip check` + `npm ls --omit=dev --depth=0`.
  - [x] Hash-pinned backend lockfile (`backend/requirements.lock`, `34` packages / `1,057` SHA-256 hashes generated via `uv pip compile --universal --generate-hashes --python-version 3.12`) wired with `pip install --require-hashes` in `backend.Dockerfile` & `.github/workflows/ci.yml`, plus `pip-audit -r requirements.lock --disable-pip` (`0` CVEs) and `npm audit --audit-level=high` (`0` CVEs).
- [x] **Backend URL Validation Alignment (`SEC-001` `LOW-3` / Phase 2 `L-03`)**: `validate_product_image_url` (`backend/app/modules/catalog/schemas.py`) checks `_has_unsafe_url_chars` after every `unquote()` pass.
- [ ] **Environment, `NEXT_PUBLIC_SITE_URL` & Cryptographic Secrets Provisioning**:
  - Set `ENVIRONMENT=production` (or `staging`), `COOKIE_SECURE=true`, `PAYMENT_SANDBOX_MODE=false` (in `production`), explicit `https://` `ALLOWED_ORIGINS`, explicit non-localhost `https://` `NEXT_PUBLIC_SITE_URL` (evaluated at Next.js build/bundle time), `TRUSTED_PROXY_IPS` matching edge proxy subnets, `DB_*_TIMEOUT_MS > 0`, strong `POSTGRES_PASSWORD` / `DATABASE_URL`, and three independent `>= 32`-char secrets (`CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, `PAYMENT_WEBHOOK_SECRET`) stored in platform secret manager (never `.env` in Git).
  - If running across multiple FastAPI worker replicas, replace in-memory `SlidingWindowRateLimiter` and `GuestPendingOrderTracker` (`backend/app/core/security.py`) with shared backing state (per ADR governance / user approval).

---

## 3. Secret Rotation, Migration & Admin Bootstrap Procedures

### 3.1 Secret Rotation Impact
- **`CSRF_HMAC_SECRET`**: Invalidates active CSRF signatures derived from existing sessions; schedule maintenance or prompt user re-authentication if rotating abruptly (`GET /api/v1/auth/me` issues fresh CSRF tokens for valid sessions; HMAC verification uses the active secret).
- **`GUEST_ORDER_HMAC_SECRET`**: Rotating invalidates active 30-day `ecom_guest_orders` cookies and guest lookup tokens (guests recover access via support or authenticated email matching if registered).
- **`PAYMENT_WEBHOOK_SECRET`**: Coordinate dual-step rotation with payment provider webhook signing configuration so verified webhook delivery never drops during cutover.

### 3.2 Alembic Database Migration & Rollback Runbook
All schema changes strictly flow through Alembic (`Base.metadata.create_all()` is forbidden in app startup). Current linear head: `0003_phase2_indexes_and_cleanup`.

1. **Before deploying**:
   ```bash
   cd backend
   alembic heads   # Expect exactly 1 head revision (0003_phase2_indexes_and_cleanup)
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
- **Atomic Guest Pending Cap (`H-06`)**: Enforced atomically in PostgreSQL via `orders.guest_client_key_hash` and namespaced `pg_advisory_xact_lock(2, hashtext(...))`. Phase 2 (`H-04`) replaces blind forwarded-header trust with `resolve_client_ip(request)` configured via `TRUSTED_PROXY_IPS` (default empty = peer IP only; general `SlidingWindowRateLimiter` remains per-process in memory).
- **Phase 2 Production Hardening (`M-03`, `M-04`, `M-05`, `M-08`, `L-05`)**:
  - `ENVIRONMENT=staging` and `ENVIRONMENT=production` enforce fail-closed startup guards (`HTTPS` `ALLOWED_ORIGINS`, `COOKIE_SECURE=true`, PostgreSQL URL, non-placeholder credentials/secrets, distinct >=32-char secrets; `seed-demo --with-demo-users` in `staging` requires explicit `DEMO_*_PASSWORD` values) and disable `/api/v1/docs`, `/api/v1/redoc`, and `/api/v1/openapi.json` via resolved settings (including `.env`).
  - PostgreSQL pool bounds (`db_pool_size=5`, `db_max_overflow=10`, `db_pool_timeout_seconds=15`, `db_pool_recycle_seconds=1800`) and connection-level timeouts (`db_statement_timeout_ms=30000`, `db_lock_timeout_ms=10000`, `db_idle_in_transaction_timeout_ms=60000`) are wired in `backend/app/core/database.py` with shutdown disposal in `backend/app/main.py`.
  - Scheduled retention cleanup for expired `auth_sessions` and non-order-linked `carts`: `python -m app.cli cleanup-expired-records` (`backend/app/cli.py`).
  - Local `compose.yaml` binds `api` (`127.0.0.1:8000:8000`) and `web` (`127.0.0.1:3000:3000`) to loopback only (`L-05`).

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
| **Phase 1 Commerce Integrity (`REM-P1`)** | `90816ec` | Atomic reservation expiration/reclamation, sweeper, advisory-locked guest cap, idempotent refunds/events (`0002_phase1_commerce_integrity`). | [TASKS.md](../TASKS.md) |
| **Phase 2 Production Hardening (`REM-P2`)** | `470419a` | Trusted-proxy IP resolution (`H-04`), `frontend/.dockerignore` (`H-07`), fail-closed `staging` & docs (`M-03`, `M-04`), PG pool/timeouts (`M-05`), `no-store` (`M-07`), indexes + cleanup CLI (`0003_phase2_indexes_and_cleanup`, `M-08`), client resilience & tax preview (`M-10`), `L-02`–`L-06`, QA + SEC (`GO`). | [REM-P2-verification.md](../qa/REM-P2-verification.md), [REM-P2-security-review.md](../security/REM-P2-security-review.md) |
| **Storefront SSR, SEO & Product Assets (`REM-P2-M09`)** | `8ffa133` | Server-side `/` `/products` `/products/[slug]` rendering via `server-catalog.ts` (`INTERNAL_API_BASE_URL`), dynamic `generateMetadata`, HTTP `404` `notFound()`, 8 seeded `/assets/products/*.png` images; `lint` (`0`), `tsc` (`0`), Vitest **`6` files / `81 passed`** (`8.48s`), `next build` (`21` routes), live E2E **`5 passed`** (`30.1s`), SEC/a11y review **`GO`**. | [REM-P2-M09-verification.md](../qa/REM-P2-M09-verification.md), [REM-P2-M09-review.md](../security/REM-P2-M09-review.md) |
| **Isolated Docker Runtime (`Phase 3A`)** | `8ffa133` | Docker Engine `29.8.2` (`linux/amd64`): built `backend.Dockerfile` (`350MB`) & `frontend.Dockerfile` (`358MB`); ran `postgres:16-alpine` (`PostgreSQL 16.16`), Alembic `0003` (`Exited 0`), `api` (`healthy`), `web` (`healthy`), `seed-demo`, & `10`/`8` HTTP/asset smoke checks; clean teardown verified (`0` leftover resources). | [compose.yaml](../../compose.yaml), [backend.Dockerfile](../../infra/docker/backend.Dockerfile), [frontend.Dockerfile](../../infra/docker/frontend.Dockerfile) |
| **Pre-Staging Security & Reliability Hardening (`Phase 3B`)** | `482c065` | Resolved `SEC-REM-P2-01..02`, `SEC-M09-01..03`, `A11Y-M09-01`, `NEXT_PUBLIC_SITE_URL`/`metadataBase`, and `requirements.lock` (`34` SHA-256 pkgs, `--require-hashes`); SQLite **`212 passed, 4 skipped`**, PG 18.6 **`216 passed`**, `pip-audit` (`0`), `npm audit` (`0`), Vitest **`87 passed`**, `next build` (`0` warnings), E2E **`5 passed`**, isolated `ecom_p3b_verify` Docker `postgres:16-alpine` build/smoke/teardown, SEC **`GO`** (`0` C/H/M/L). | [REM-P3B-verification.md](../qa/REM-P3B-verification.md), [REM-P3B-security-review.md](../security/REM-P3B-security-review.md) |
| **Private GitHub Repo & Remote CI (`Phase 3C`)** | `015d403` | Private repo `obebme/production-ecommerce-saas` pushed (`25`-commit preflight scan clean); all `5` GitHub Actions jobs (`Detect Workspace Modules`, `Validate Compose, Docker & Env Config`, `Backend Lint, Typecheck, Migrations & Tests` on `postgres:16-alpine`, `Frontend Lint, Typecheck, Build & Unit Tests`, `Full-Stack Playwright E2E (PostgreSQL 16)`) passed on `ubuntu-latest`. | [.github/workflows/ci.yml](../../.github/workflows/ci.yml) |
| **Vellora Store Storefront Redesign (`FE-REDESIGN-001`)** | *(Redesign checkpoint)* | Dark-first `#0B0D0F` + `#D8C3A1` champagne storefront (`Vellora Store` logo, Light/Dark pill toggle, hero/categories/cards/PDP/mobile bottom nav, `9` dark-studio PNGs) isolated via `.storefront-theme` with Admin & Backend untouched; `lint` (`0`), `tsc` (`0`), Vitest **`6` files / `90 passed`** (`11.23s`), `next build` (`21` routes), live E2E **`5 passed`** (`31.3s`), SEC/WCAG **`GO`** (`0` C/H/M/L). | [FE-REDESIGN-001-verification.md](../qa/FE-REDESIGN-001-verification.md), [FE-REDESIGN-001-review.md](../security/FE-REDESIGN-001-review.md) |

### 6.2 Unverified Items

| Item | Reason & How to Verify |
| :--- | :--- |
| **Real Payment Gateway & Webhook Settlement** | Baseline supports only the sandbox SimPay adapter (`PAYMENT_SANDBOX_MODE=true`); live PSP SDK/API calls, dispute webhooks, and PCI scope require a future payment-provider integration milestone. |
| **Multi-Replica Distributed Rate Limiting & Load Benchmarks** | Rate limiting (`SlidingWindowRateLimiter`) and pending guest-order tracking (`GuestPendingOrderTracker`) use in-memory per-process structures; horizontal multi-replica behavior and sustained concurrency load benchmarks remain unverified. |

### 6.3 Blocked Items (Environment-Specific)

None locally or remotely after Phase 3A–3C verification (`0` blocked items).

---

## 7. Known Risks & Release Sign-Off Notes

1. **Full Multi-Environment Verification (`Phase 3B`, `3C` & `FE-REDESIGN-001`)**: Backend SQLite (`212 passed, 4 skipped`), live PG 18.6 (`216 passed, 0 failed, 0 skipped`), `uvx pip-audit` (`0` CVEs), `npm audit` (`0` CVEs), remote GitHub Actions CI (`015d403` `5/5` jobs green on `postgres:16-alpine`), and post-redesign `eslint` (`0`), `tsc` (`0`), Vitest (`6 files / 90 passed`), `next build` (`21` routes), live Playwright E2E (`5 passed`), and SEC/WCAG review (`GO`) have all passed. Push the `FE-REDESIGN-001` checkpoint to `origin/main` to confirm the latest storefront redesign in remote CI.
2. **Staging & Production Deployment Prerequisites**: Before public staging/production, operators must provision edge TLS + HSTS, build frontend images with non-localhost `https://` `NEXT_PUBLIC_SITE_URL`, set explicit `TRUSTED_PROXY_IPS` CIDRs, schedule `cleanup-expired-records`, add optional CSP per-request nonces (`SEC-001` `INFO-1`), and adopt a shared distributed rate limiter before multi-replica scaling.
3. **Standalone Next.js Local E2E Notice**: Local `run_e2e_stack.py` runs `next start` against `.next` (logging Next's non-blocking `output: "standalone"` notice while serving all routes cleanly); both `frontend.Dockerfile` and GitHub Actions `e2e-ci` execute the production standalone server (`node server.js` / `node .next/standalone/server.js`) with copied `.next/static` and `public/` assets.
