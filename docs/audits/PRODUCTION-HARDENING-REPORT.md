# Atelier Commerce — Production Hardening & Independent Audit Final Report

- **Branch**: `improvement/production-hardening`
- **Baseline Commit (`main` / `origin/main`)**: `4d96474d3700c5847332f724de0e97e4d73be5bc` (`main` untouched)
- **Execution Date**: `2026-11-25`
- **Overall Gate Verdict**: **LOCAL HARDENING VERIFIED (`PASS` across all runnable backend, PG 18.6, frontend, Playwright CSP E2E, Locust load, and independent security gates; Docker image builds & remote GitHub Actions CI `BLOCKED` / `NOT RUN` locally)**

---

## 1. Verified Initial Findings & Execution Status Audit

### 1.1 Baseline Hypothesis Audit
Every finding from prior reviews was independently checked against `main` (`4d96474`) before code edits began:

| ID | Hypothesis / Issue | Verified Baseline Reality | Action Taken |
| :--- | :--- | :--- | :--- |
| `P0-SEC-01` | Secret & dependency exposure | **Partially Open**: `gitleaks v8.24.2` across 28 commits & all branches found `0` real secrets (`1` prose false positive in `.agents/skills/backend/SKILL.md:3`), `pip-audit` `0` CVEs, `npm audit` `0` CVEs; CI lacked Gitleaks and Trivy scans. | Added `.gitleaks.toml`, CI `secret-scan`, and CI Trivy scan. |
| `P1-RL-01` | Process-local rate limit & guest tracking | **Partially Resolved / Open**: `GuestPendingOrderTracker` was already superseded by DB-backed `orders.guest_client_key_hash` + `pg_advisory_xact_lock`, whereas `SlidingWindowRateLimiter` (`core/security.py:363`) remained strictly process-local. | Added atomic Lua-scripted `RedisSlidingWindowRateLimiter` with bounded `PTTL` and fail-closed `503` modes while retaining PostgreSQL as order/cap source of truth. |
| `P1-SWEEP-01` | Uncoordinated reservation sweeper | **Verified Open**: `_run_reservation_sweeper_loop` ran uncoordinated across workers (`main.py:53`) without leader locking or cooperative stop timeout. | Added PostgreSQL advisory-lock leader election + cooperative `stop_event` + bounded `5.0s` shutdown wait. |
| `P1-PAY-01` | Hardcoded SimPay provider & unsafe prod boot | **Verified Open**: `SANDBOX_PROVIDER` hardcoded across `orders`, `payments`, and `admin`; `Settings(environment="production", payment_sandbox_mode=False)` succeeded with zero live PSP registered. | Created `PaymentProviderAdapter` / `PaymentProviderRegistry` / `SandboxSimpayAdapter`; enforced fail-closed `ValidationError` on production without a registered live adapter. |
| `P1-CSP-01` | Production `script-src 'unsafe-inline'` | **Verified Open**: `frontend/next.config.mjs:32` emitted `script-src 'self' 'unsafe-inline'`. | Implemented per-request 128-bit cryptographic nonce CSP in `frontend/src/middleware.ts` + async `RootLayout` (`await headers()`). |
| `P2-MAINT-01` | Monolithic `orders` & `payments` services | **Verified Open**: `orders/service.py` (`1,930`–`2,099` lines across checkpoints) and `payments/service.py` (`871`–`942` lines) mixed domain concerns. | Split cleanly into focused submodules behind backward-compatible `service.py` facades. |
| `P2-CI-01` | Hardcoded Alembic revision in CI | **Verified Open**: `.github/workflows/ci.yml:276` hardcoded `0003_phase2_indexes_and_cleanup`; lacked `alembic check`, downgrade test, Redis service, and job timeouts. | Replaced with single-head check, `alembic upgrade head`, `alembic check`, and `alembic downgrade base && upgrade head` + Redis 7 service. |
| `P2-OPS-01` | Host PG port collision & prod topology | **Verified Open**: `compose.yaml` hardcoded `127.0.0.1:5432:5432`; unhashed fallback in `backend.Dockerfile`; lacked prod TLS & DB role separation. | Parameterized Compose ports (default `5433`), added `redis` profile, enforced `--require-hashes`, added `init-roles.sql`, `compose.prod.yaml` + Caddyfile, and `DEPLOYMENT-RUNBOOK.md`. |
| `P2-OBS-01` | Missing structured observability | **Verified Open**: No JSON log redaction, Prometheus metrics, `/health/live`, `/health/ready`, or protected `/metrics`. | Added `StructuredJsonFormatter`, low-cardinality Prometheus metrics, `/health/live`, `/health/ready`, and bearer/admin-gated `/api/v1/metrics`. |
| `P2-PERF-01` | No reproducible load test suite | **Verified Open**: Zero k6/Locust load harnesses or DB invariant benchmarks existed. | Built & executed `backend/tests/load/` (Locust `10`/`50`/`100` VUs against PG 18.6). |
| `P3-DOCS-01` | Developer-specific paths & missing visuals | **Verified Open**: `README.md` referenced Windows-specific `e:\fullstack_work` / `E:\databasesql\data` paths and lacked real screenshots. | Replaced with portable Linux/macOS/Windows quickstart, Mermaid architecture diagram, and 3 real `1440×900` screenshots. |

### 1.2 Execution Status Audit Summary (During Phase 2/3)
During execution, `HARDEN-BE-01` stalled in an inline `TestClient` smoke script (`PID 16120`) because Starlette `TestClient` / `anyio` `BlockingPortal` uses a loopback `socket.socketpair()` that blocks indefinitely inside the no-network Windows sandbox. Baseline tasks `task-2467` and `task-2469` had already exited `0`. Terminating the orphaned sandbox process (`PID 16120`) without touching host PostgreSQL (`PID 4984`/`6952`) and running `TestClient`, pytest, Playwright, and Locust strictly with `BypassSandbox: true` resolved the hang cleanly.

---

## 2. Resolved Issues (Including Defects Caught & Fixed Mid-Iteration)

1. **`P0-SEC-01` — Automated Secret & Vulnerability Gates**: `.gitleaks.toml`, `.github/workflows/ci.yml` (`secret-scan` + Trivy image scan), and updated hash-pinned `backend/requirements.lock` (`redis`, `prometheus-client`) with `0` CVEs.
2. **`P1-RL-01` — Distributed Atomic Redis Rate Limiter**: `backend/app/core/rate_limit.py` implements atomic Lua `ZREMRANGEBYSCORE`/`ZCARD`/`ZADD`/`PEXPIRE`, short socket timeouts (`0.5s`), bounded key lifetimes, and explicit `503 RATE_LIMITER_UNAVAILABLE` fail-closed handling on sensitive auth/checkout/payment/guest keys under `fail_closed_sensitive`.
3. **`P1-SWEEP-01` & `HARDEN-BE-SWEEP-FIX` — Leader-Elected Sweeper + P1 PG Lock-Leak Fix**:
   - **Defect caught during QA live PostgreSQL testing**: Initial `run_coordinated_expired_reservation_sweep` ran `pg_try_advisory_lock` through `db: Session`, then called `db.commit()`/`db.rollback()` inside batches—returning pooled connection `C1` (still holding the session lock) to `QueuePool` so `finally:` `pg_advisory_unlock` checked out `C2` (`WARNING: you don't own a lock of type ExclusiveLock`).
   - **Fix**: Pinned a dedicated `AUTOCOMMIT` `Connection` (`bind.connect()`) exclusively for `pg_try_advisory_lock` / `pg_advisory_unlock`, invalidating on unlock failure and closing in `finally:`.
4. **`P1-PAY-01` — Provider-Independent Payment Abstraction & Fail-Closed Prod Check**: `PaymentProviderAdapter`, `SandboxSimpayAdapter`, `PaymentProviderRegistry`, `reconcile_payment_intent`, `admin/service.py` gateway routing, and `Settings` fail-closed validation when `ENVIRONMENT=production` lacks a live registered provider.
5. **`P1-CSP-01` — Strict Per-Request Nonce CSP**: `frontend/src/middleware.ts` emits per-request `'nonce-...'` CSP without `script-src 'unsafe-inline'` in production; async `RootLayout` reads `await headers()` so Next.js 15 stamps matching `nonce="..."` on all inline runtime scripts.
6. **`P2-MAINT-01` — Modular Orders & Payments Decomposition**: Split `orders/` into `common`, `authorization`, `queries`, `checkout`, `cancellation`, `transitions`, and `service` facade; split `payments/` into `provider`, `adapters/simpay`, `intents`, `webhooks`, `reconciliation`, `sandbox`, and `service` facade.
7. **`P2-CI-01` & `P2-OPS-01` — CI, Docker, Roles & Deployment Runbook**: Added configurable Compose host ports (`POSTGRES_HOST_PORT=5433`), optional `redis` profile, `compose.prod.yaml` + Caddy TLS/HSTS, `infra/postgres/init-roles.sql` (`ecom_migrator` vs `ecom_app`), hardened `backend.Dockerfile`, `alembic check` + downgrade/re-upgrade CI validation, and `docs/ops/DEPLOYMENT-RUNBOOK.md`.
8. **`P2-OBS-01` — Self-Hosted Observability**: Structured secret-redacting JSON logs (`request_id`), `/api/v1/health/live`, `/api/v1/health/ready`, and admin/bearer-protected low-cardinality `/api/v1/metrics`.
9. **`P2-PERF-01` — Reproducible Locust Benchmark Suite**: Created `backend/tests/load/locustfile.py` + `run_load_suite.py` and executed 10/50/100 VU benchmarks.
10. **Independent Review CI/Lint Fixes (`HARDEN-SEC-FINAL` Round 1 -> Round 2)**: Added `"fakeredis[lua]>=2.26.0"` to `.github/workflows/ci.yml` `backend-ci` and fixed `E501` line length in `backend/tests/load/run_load_suite.py`.

---

## 3. Remaining Issues & Scope Boundaries

- Zero open Critical, High, Medium, or Low security/correctness issues remain within the implemented scope.
- Single-currency (`USD`), flat shipping/tax configuration, and single-tenant data model remain intentional baseline boundaries.

---

## 4. Changed & Added Files (`4d96474..HEAD`)

| Scope | Files |
| :--- | :--- |
| **Root & CI / Secrets** | `README.md`, `.env.example`, `.gitleaks.toml`, `.github/workflows/ci.yml`, `compose.yaml`, `compose.prod.yaml` |
| **Infra** | `infra/caddy/Caddyfile`, `infra/docker/backend.Dockerfile`, `infra/postgres/init-roles.sql` |
| **Backend Core & Migrations** | `backend/pyproject.toml`, `backend/requirements.txt`, `backend/requirements.lock`, `backend/alembic/env.py`, `backend/app/main.py`, `backend/app/core/{config,observability,rate_limit,security}.py` |
| **Backend Domain Modules** | `backend/app/modules/admin/service.py`, `backend/app/modules/inventory/service.py`, `backend/app/modules/orders/{common,authorization,queries,checkout,cancellation,transitions,service}.py`, `backend/app/modules/payments/{provider,intents,webhooks,reconciliation,sandbox,router,service}.py`, `backend/app/modules/payments/adapters/{__init__,simpay}.py` |
| **Frontend CSP** | `frontend/src/middleware.ts`, `frontend/src/app/layout.tsx`, `frontend/next.config.mjs` |
| **Tests & Load Harness** | `backend/tests/{test_config_and_health,test_admin_and_analytics,test_production_hardening}.py`, `backend/tests/load/{locustfile,run_load_suite}.py`, `frontend/e2e/{run_e2e_stack.py,storefront-and-admin.spec.ts}` |
| **Docs & Real Screenshots** | `docs/TASKS.md`, `docs/audits/PRODUCTION-HARDENING-{PLAN,REPORT}.md`, `docs/security/SECRET-SCAN-REPORT.md`, `docs/qa/{REGRESSION-REPORT,LOAD-TEST-REPORT}.md`, `docs/ops/DEPLOYMENT-RUNBOOK.md`, `docs/assets/screenshots/{storefront-home,storefront-pdp,admin-dashboard}.png` |

*(Local `artifacts/` directory remains untracked and excluded from commits; `main` remains untouched at `4d96474`.)*

---

## 5. Architectural Decisions & Trade-Offs

1. **Dynamic SSR (`await headers()`) for Per-Request CSP Nonces**: `RootLayout` opts into dynamic per-request rendering so Next.js 15 stamps a fresh cryptographic nonce on inline scripts. Static HTML caching of routes at the edge is traded off for strict elimination of `script-src 'unsafe-inline'` in production.
2. **Retained `style-src 'self' 'unsafe-inline'`**: Explicitly retained because Recharts (`3.x`) SVG inline styles and Radix UI dynamic portal positioning apply inline `style` attributes. Documented rather than weakening `script-src`.
3. **Redis Rate Limiter (`fail_closed_sensitive`) vs Local Fallback**: Auth, checkout, payment, and guest order routes fail closed (`503 RATE_LIMITER_UNAVAILABLE`) when Redis is unreachable, preventing rate-limit bypass attacks, while non-sensitive read/cart routes degrade to bounded local memory limits (`max(1, limit // 2)`). PostgreSQL remains the sole source of truth for guest pending-order caps, inventory, and orders.
4. **Synchronous SQLAlchemy + Pinned Advisory Lock Connection**: Preserved proven synchronous SQLAlchemy 2.0 `QueuePool` and canonical lock ordering; isolated only session-level sweeper leader locks onto a dedicated `AUTOCOMMIT` connection.

---

## 6. Independent Security Review Results (`HARDEN-SEC-FINAL`)

- **Reviewer**: Read-only `security` specialist auditing full diff `4d96474..HEAD` across all 12 security, authorization, transaction, concurrency, deadlock, idempotency, inventory, API compatibility, error handling, leak, complexity, and test-quality criteria.
- **Round 1 Verdict**: `NO-GO pending 2 CI/lint fixes` (`0` Critical, `0` High, `1` Medium `.github/workflows/ci.yml` missing `fakeredis[lua]`, `1` Low `run_load_suite.py:782` `E501`, `3` Info).
- **Round 2 Verdict**: **`GO`** (**`0` Critical, `0` High, `0` Medium, `0` Low, `3` Info**; zero security, authz, race-condition, deadlock, inventory, or CSP regressions).

---

## 7. Actual Test & Security Verification Matrix

| Check | Command | Exact Measured Outcome | Status |
| :--- | :--- | :--- | :---: |
| Git History & Workspace Secret Scan | `gitleaks git -c .gitleaks.toml --log-opts="--all"` | `28` commits (`3.31 MB`): `no leaks found` (`exit 0`) | **PASS** |
| Python Dependency Audit | `uvx pip-audit -r backend/requirements.lock --disable-pip` | `No known vulnerabilities found` (`exit 0`) | **PASS** |
| Node Dependency Audit | `npm.cmd audit --json` | `0` vulnerabilities across `642` packages | **PASS** |
| Backend Lint & Strict Mypy | `ruff check .` & `mypy app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` | `All checks passed!`; `Success: no issues found in 70 source files` | **PASS** |
| Backend Full Suite + PG 18.6 | `python tests\run_disposable_pg_pytest.py` | **`226 passed, 1 skipped`** in `84.56s` (`1` skip: optional live `REDIS_TEST_URL`, covered via `fakeredis` Lua); PG `EXPLAIN` & `alembic check`/downgrade/upgrade verified | **PASS** |
| Frontend Lint / Types / Unit | `npm.cmd run lint`, `tsc --noEmit`, `npm.cmd test`, `npm.cmd run build` | `0` lint/TS errors; Vitest **`6 files / 95 passed`** (`10.64s`); standalone build **`21` dynamic routes + `34.6 kB` middleware** | **PASS** |
| Browser E2E (Chromium + Nonce CSP) | `python e2e\run_e2e_stack.py --auto-ports` | **`6 passed (39.9s)`**: purchase, guest cookies, RBAC/refund, oversell race, SSR/mobile, strict nonce CSP & Admin Recharts (`0` CSP violations) | **PASS** |
| Ops YAML & Env Validator | `ops-validate` Python validator (`compose.yaml`, `compose.prod.yaml`, `ci.yml`, `.env.example`) | `YAML valid`; `services` & internal-network isolation verified (`exit 0`) | **PASS** |
| Local Docker Image Builds / Compose Up / Trivy | `docker info`, `docker compose` | Docker Desktop Linux Engine daemon not running locally (`//./pipe/dockerDesktopLinuxEngine` absent) | **BLOCKED** |
| Remote GitHub Actions CI (`improvement/production-hardening`) | `.github/workflows/ci.yml` (`secret-scan`, `ops-validate`, `backend-ci`, `frontend-ci`, `e2e-ci`) | Feature branch not pushed to `origin` (`main` untouched at `4d96474`) | **NOT RUN** |

---

## 8. Actual Benchmark Results (Locust `2.47.0` on Disposable PG `18.6`)

Full per-endpoint tables, `/api/v1/metrics` scrape output, and DB pool metrics are in [LOAD-TEST-REPORT.md](../qa/LOAD-TEST-REPORT.md):

| Tier | Reqs | Failures | RPS | Median / p95 / p99 (ms) | Expected `409` Oversell Rejects | `429` / `5xx` | Post-Load PG Invariants |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **10 VUs (15s)** | `751` | `0` | `53.39` | `16` / `71` / `540` | `19` | `0` / `0` | **PASS** |
| **50 VUs (15s)** | `1,436` | `0` | `102.33` | `270` / `700` / `2100` | `41` | `0` / `0` | **PASS** |
| **100 VUs (15s)** | `1,542` | `0` | `109.99` | `620` / `1800` / `4000` | `37` | `0` / `0` | **PASS** (`ATL-LINEN-SND-L`: `5/5` committed, `0` oversell) |

- **Observed Bottleneck**: Single-worker `uvicorn` CPU saturation from concurrent `Argon2id` (`64 MiB`, `t=3`) logins at `50–100` VUs pushed `POST /api/v1/auth/login` p95 to `4,100 ms` and `db_pool_connections` `checkedout=28.0` / `overflow=25.0`. Production should scale horizontally (`--workers 4+`) with Redis rate limiting.

---

## 9. Unverified Assumptions

1. **Live Redis Daemon Parity**: `REDIS_TEST_URL` was unset locally (`test_redis_rate_limiter_live_redis_when_configured` skipped); Lua atomicity, `PTTL`, multi-instance limits, and outage handling were verified against `fakeredis[lua]`. Live `redis:7-alpine` CI execution awaits push/PR.
2. **PostgreSQL 16 Container Parity**: Local backend & load tests ran against disposable PostgreSQL `18.6`; `postgres:16-alpine` execution runs in Docker / CI (`backend-ci`, `e2e-ci`).

---

## 10. Deployment Blockers

1. **Local Host Docker Daemon Stopped**: Docker Desktop Linux Engine was stopped on the workstation during verification, blocking local `docker build` and `docker compose up` execution on the hardened Compose files (`compose.prod.yaml`, Caddy, Redis profile).

---

## 11. Remaining Production Requirements

1. Integrate a concrete production-ready `PaymentProviderAdapter` (Stripe/Adyen/etc.), remove sandbox-only shortcuts for live traffic, verify provider webhooks, and complete PCI compliance.
2. Provision production hosting with Caddy TLS + DNS, real `https://` `NEXT_PUBLIC_SITE_URL` / `ALLOWED_ORIGINS`, explicit `TRUSTED_PROXY_IPS`, strong `METRICS_BEARER_TOKEN`, scheduled `pg_dump -Fc` backups & restore drills, and scheduled `cleanup-expired-records`.
3. Push `improvement/production-hardening` (once approved for remote verification/PR) to execute all GitHub Actions jobs including `secret-scan`, Trivy image scans, live `redis:7-alpine` tests, and `postgres:16-alpine` E2E.
