# Atelier Commerce — Production Hardening & Final Release Verification Report

- **Branch**: `improvement/production-hardening`
- **Baseline Commit (`main` / `origin/main`)**: `4d96474d3700c5847332f724de0e97e4d73be5bc` (`main` untouched)
- **Execution Date**: `2026-11-25`
- **Overall Gate Verdict**: **ALL 12 LOCAL RELEASE GATES VERIFIED (`PASS` across hardened Docker images, Trivy `0.58.1` CI parity, live `PostgreSQL 16.16` + `Redis 7.4.12` integration & role separation, `compose.prod.yaml` HTTPS Caddy edge, Redis/PG zero-restart failover, production sandbox rejection, multi-worker `1/2/4` scaling, `60s` sustained load test, and `INFO-SEC-01..03` documentation; remote GitHub Actions CI & live production PSP remain external deployment prerequisites)**

### Readiness Verdicts

| Track | Verdict | Rationale |
| :--- | :---: | :--- |
| **Portfolio Readiness** | **READY** | Complete full-stack Next.js 15 + FastAPI + PostgreSQL 16 + Redis 7 architecture with real dark-studio assets, strict nonce CSP, least-privilege DB roles, observability (`/health/*`, `/metrics`, JSON logs), reproducible load/failover suites, and clean security sign-off. |
| **Merge Readiness (`improvement/production-hardening` → `main`)** | **READY** | Zero open Critical/High/Medium/Low issues; `227/227` backend pytest passed on live `PostgreSQL 16.16` + `Redis 7.4.12`; Trivy `0.58.1` CI parity exits `0` on both Docker images; `6/6` Playwright E2E and `95/95` Vitest passed; `gitleaks` `0` secrets. Ready to push for CI confirmation and merge upon user approval. |
| **Production Deployment Readiness (Live Commerce / Real Money)** | **NOT READY (INTENTIONALLY GATED)** | Blocked on live payment integration and production ops provisioning: only `sandbox_simpay` is registered (and `ENVIRONMENT=production` intentionally rejects `sandbox_simpay` at startup with exit code `3`), plus production DNS/Caddy ACME domain, secret management, scheduled off-box backups, and CI workflow verification remain required before serving live card traffic. |

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

### 6.1 Informational Security Findings (`INFO-SEC-01..03`)

| ID | Affected Paths | Risk & Finding | Intentional Constraint / Future Guidance |
| :--- | :--- | :--- | :--- |
| **`INFO-SEC-01`** | `frontend/next.config.mjs:28-62`, `frontend/src/middleware.ts:109-111` | Static asset fallback headers retain `script-src 'self'` (without nonce) for static paths excluded by the middleware matcher (`/_next/static`, `/_next/image`, `/assets/`, `/favicon.ico`), while HTML/RSC routes receive per-request `'nonce-...'` headers; `style-src 'self' 'unsafe-inline'` remains present. | Dynamic document/RSC responses strictly forbid `'unsafe-inline'` scripts via per-request 128-bit CSP nonces; static asset routes serve non-HTML binaries/JS bundles. `style-src 'unsafe-inline'` is retained only because Recharts SVG charts and Radix UI portals inject inline `style` attributes. |
| **`INFO-SEC-02`** | `backend/app/modules/orders/checkout.py:642-664` | `payment_adapter.create_intent(...)` executes inside the checkout database transaction while `InventoryStock` rows are locked with `SELECT ... FOR UPDATE`. | Safe with zero network latency for the in-process `SandboxSimpayAdapter`. Before enabling any network-bound live PSP adapter (e.g., Stripe/Adyen), move external HTTP intent creation outside the `InventoryStock` row-lock scope (mirroring 3-phase `refund_admin_order`) or enforce strict sub-second client timeouts + idempotent outbox retries so remote PSP stalls never hold database row locks. |
| **`INFO-SEC-03`** | `infra/caddy/Caddyfile:7-9`, `compose.prod.yaml:110` | FastAPI `TRUSTED_PROXY_IPS` trusts Caddy's Docker `internal` subnet (`172.28.0.0/24`) so `resolve_client_ip` reads Caddy's appended `X-Forwarded-For` address. | Safe when Caddy directly terminates edge TLS on ports `80`/`443`. If deploying an external CDN or cloud load balancer in front of Caddy, operators must configure Caddy's global `servers { trusted_proxies static <CDN_OR_LB_CIDRS> }` block so Caddy validates upstream proxy hops instead of trusting spoofed client `X-Forwarded-For` headers. |

---

## 7. Actual Test, Container, Failover & Security Verification Matrix

| Check | Command / Environment | Exact Measured Outcome | Status |
| :--- | :--- | :--- | :---: |
| Git History & Workspace Secret Scan | `gitleaks git -c .gitleaks.toml --log-opts="--all"` | `0` real secrets found (`exit 0`) | **PASS** |
| Python & Node Dependency Audits | `uvx pip-audit -r backend/requirements.lock --disable-pip`; `npm.cmd audit --json` | `0` Python CVEs; `0` Node CVEs (`642` pkgs) | **PASS** |
| Backend Lint & Strict Mypy | `ruff check .`; `mypy app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` | `All checks passed!`; `Success: no issues found in 70 source files` | **PASS** |
| Backend Full Suite (Live PG `16.16` + Redis `7.4.12`) | `pytest -v` (`postgres:16-alpine` + `redis:7-alpine`, `REDIS_TEST_URL` set) | **`227 passed, 0 skipped, 0 failed`** in `89.83s` (`test_redis_rate_limiter_live_redis_when_configured` active) | **PASS** |
| DB Role Permissions & Migrations (`PG 16.16`) | `infra/postgres/init-roles.sql` (2x idempotent) + `alembic upgrade/check/downgrade` | Both roles `False x5` flags; `ecom_app` DML OK; `CREATE`/`ALTER`/`DROP`/`alembic downgrade -1` denied (`42501`); `alembic check` clean (`0003`) | **PASS** |
| Production Image Builds & Trivy (`0.58.1`) | `docker build` + `ghcr.io/aquasecurity/trivy:0.58.1` (`HARDEN-REL-OPS-01`) | `atelier-backend:relverify` (`c7c4b6c920d4`, `343MB`) & `atelier-frontend:relverify` (`dae132a979e3`, `382MB`): **both `EXIT=0`** on `--severity CRITICAL,HIGH --ignore-unfixed --exit-code 1` (`0` Python/Node/Alpine CVEs; `53H/88M/96L` unfixed Debian `13.8` OS advisories) | **PASS** |
| Production Config Rejects Sandbox PSP | `docker run --rm -e ENVIRONMENT=production -e PAYMENT_PROVIDER=sandbox_simpay ... uvicorn` | Fails closed on FastAPI lifespan startup with **`EXIT=3`** (`ValidationError`) under both `PAYMENT_SANDBOX_MODE=true` and `false` | **PASS** |
| Production Compose Runtime (`compose.prod.yaml`) | `-p atelier-prodverify` (`db`, `redis`, `migrate`, `api`, `web`, `caddy` via HTTPS `:18443`) | `migrate` `Exited (0)`, 5 services `Up (healthy)`, zero non-Caddy host ports, `read_only` rootfs enforced, HSTS + nonce CSP, `/metrics` **`404`** at Caddy edge & **`200`** (`116` lines) internally, JSON logs verified | **PASS** |
| Redis & PostgreSQL Failure Recovery | Live `docker stop` / `docker start` on `redis-1` & `db-1` under `atelier-prodverify` | **Redis down**: `/health/live=200`, `/health/ready=503 REDIS_UNAVAILABLE`, `/auth/login=503 RATE_LIMITER_UNAVAILABLE`, `/products=200` -> auto-recovers `200`. **PG down**: `/live=200`, `/ready=503 DATABASE_UNAVAILABLE` -> auto-recovers `200` via `pool_pre_ping`; **`0` `api` restarts** | **PASS** |
| Frontend Lint / Types / Unit / E2E | `npm.cmd run lint`, `tsc --noEmit`, `npm.cmd test`, `run_e2e_stack.py` | `0` lint/TS errors; Vitest **`95/95`**; standalone `21` routes; Playwright Chromium **`6/6` (`39.9s`)**, `0` CSP violations | **PASS** |
| Remote GitHub Actions CI (`improvement/production-hardening`) | `.github/workflows/ci.yml` (`secret-scan`, `ops-validate`, `backend-ci`, `frontend-ci`, `e2e-ci`) | Feature branch not yet pushed to `origin` (`main` untouched at `4d96474`) | **NOT RUN (READY)** |

---

## 8. Actual Benchmark Results (Multi-Worker Scaling & `60s` Sustained Load)

Full per-endpoint tables, resource telemetry, and `/api/v1/metrics` excerpts are in [LOAD-TEST-REPORT.md](../qa/LOAD-TEST-REPORT.md):

### 8.1 Worker Count Scaling (`atelier-backend:relverify` + `PostgreSQL 16.16` + `Redis 7.4.12`, `50` VUs, `20s`)

| Workers | Run Profile / Condition | Reqs | Fails | RPS | p50 / p95 / p99 (ms) | Avg / Peak API CPU | Peak API Mem (MiB) | Peak PG Conns | Invariants |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`1`** | Fresh container (`task-2841`) | `1,668` | `0` | `82.66` | `390` / `830` / `1,400` | `274.13%` avg | `939.4` | `15` | **PASS** |
| **`1`** | Telemetry JSON run (`task-2865`) | `1,590` | `0` | `79.26` | `440` / `840` / `1,100` | `150.49%` / `230.07%` | `586.2` | `15` | **PASS** |
| **`2`** | Fresh container (`task-2841`) | `2,536` | `0` | `126.89` | `190` / `450` / `1,100` | `221.41%` avg | `228.3` | `30` | **PASS** |
| **`2`** | Telemetry JSON run (`task-2865`) | `2,562` | `0` | `127.62` | `180` / `460` / `840` | `413.50%` / `1104.83%` | `504.8` | `30` | **PASS** |
| **`4`** | Clean burst (`task-2841`, hit `1200m` cap) | `3,288` | `0` | `165.81` | `61` / `290` / `2,100` | `364.48%` avg | `1,200.1` | `39` | **PASS** |
| **`4`** | Ramp Argon2 contention (`task-2865`) | `1,386` | `0` | `72.69` | `92` / `3,700` / `10,000` | `255.07%` / `864.34%` | `502.3` | `46` | **PASS** |

### 8.2 Sustained `60s` Multi-Worker Load Test (`4` Uvicorn Workers, `50` VUs)

| Scenario | Reqs | Failures / `5xx` | RPS | p50 / p95 / p99 (ms) | Peak API Mem (MiB) | Peak PG Mem / Conns | Scarcity & Invariants |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Realistic Pacing (`0.8–1.4s` think, `PostgreSQL 18.6`)** | `4,640` | `0` (`0.00%`) / `0` | `78.34` | `16.0` / `130.0` / `450.0` | `789.01` (`540.63` avg) | `803.75 MiB` / `25` conns (`3` active peak) | **PASS** (`604/604` paid, `5/5` scarcity committed, `151` expected `409`s, `0` PG lock warnings) |
| **Unthrottled Burst (`0.15–0.45s`, `PG 16.16` + `Redis 7.4.12`, `40` VUs)** | `9,528` | `338` expected `429`s (`3.55%`) / `0` `5xx` | `160.65` | `32.0` / `200.0` / `340.0` | — | — | **PASS** (`0` `5xx`; proved cross-worker Redis Lua `429` enforcement when per-IP traffic exceeded `10` logins/min & `30` checkouts/min) |

### 8.3 Telemetry & Measurement Reconciliation Summary

- **Request Totals (`4,646` vs `4,640` vs `4,371`)**: In Gate 9, Locust's console table recorded `4,646` requests vs `4,640` flushed to `locust_stats.csv` (`6` late in-flight teardown requests), while `@task`-scoped `status_breakdown` counted `4,371` (`4,220` `2xx` + `151` expected `409`s), plus `270` `on_start` `_discover_catalog` GETs and `5` scarcity post-checkout payment calls (`4,371 + 270 + 5 = 4,646`); the same mechanism accounts for the `8–89` delta in Gate 8.
- **Process-Local Metrics (`214`) vs DB Orders (`604`)**: `METRICS_REGISTRY` is per-worker (`4` workers), so scraping one worker reported `214` checkouts while PostgreSQL persisted all `604` paid orders globally.
- **Host `psutil` CPU (`0.0%`) vs Cgroup `docker stats`**: Re-calling `root_proc.children(recursive=True)` each `2.0s` tick instantiated fresh `psutil.Process` handles whose first `cpu_percent(interval=None)` returns `0.0%`; kernel RSS (`789.01 MiB` peak Uvicorn, `803.75 MiB` peak PG) and `pg_stat_activity` (`25` peak) remain exact, and Gate 8 `docker stats` measured true multi-worker CPU (`150.49%`–`413.50%` avg).
- **`4`-Worker Series A (`165.81` RPS) vs Series B (`72.69` RPS)**: Spawning `50` VUs at `r=25/s` triggered `50` simultaneous `Argon2id` (`64 MiB`, `t=3`) logins; Series B experienced hypervisor CPU contention queuing initial logins (`10,000 ms` p95), whereas staggered `r=15/s` + `0.8–1.4s` think time in Gate 9 eliminated queueing (`16.0 ms` p50, `130.0 ms` p95). Full reconciliation is in [LOAD-TEST-REPORT.md §6.3](../qa/LOAD-TEST-REPORT.md#63-telemetry--measurement-reconciliation-notes).

---

## 9. Unverified Assumptions, Remaining Unfixed OS CVEs & Environment Notes

### 9.1 Remaining Unfixed Container OS CVEs (`Trivy 0.58.1`, Scanned `2026-11-25`)
- **`atelier-frontend:relverify` (`node:22-alpine` / Alpine `3.24.4`)**: **`0` CVEs total** (`0` Critical, `0` High, `0` Medium, `0` Low across Alpine OS and Node.js).
- **`atelier-backend:relverify` (`python:3.12-slim` / Debian `13.8`)**: **`0` Python CVEs**, **`0` Critical OS CVEs**, **`0` fixable High OS CVEs** (`--ignore-unfixed --exit-code 1` returns **`0`**). **`237` unfixed upstream Debian `13.8` (`trixie`) OS package findings** remain (`53` High occurrences across **`13` unique High CVE IDs**, `88` Medium, `96` Low):
  - **13 Unique Unfixed High CVE IDs**: `CVE-2025-69720`, `CVE-2026-9538`, `CVE-2026-16742`, `CVE-2026-54369`, `CVE-2026-76642`, `CVE-2026-78408`, `CVE-2026-78409`, `CVE-2026-78410`, `CVE-2026-107494`, `CVE-2026-119373`, `CVE-2026-119374`, `CVE-2026-119379`, `CVE-2026-124709`.
  - **Affected Debian Packages**: `util-linux` suite (`bsdutils`, `libblkid1`, `liblastlog2-2`, `libmount1`, `libsmartcols1`, `libuuid1`, `login`, `mount`, `util-linux` `2.41.5-0+deb13u1`: `4` High each), `libsqlite3-0` (`3.46.1-7+deb13u2`: `3` High, `8` Medium, `3` Low), `openssl`/`libssl3t64`/`openssl-provider-legacy` (`3.5.9-1~deb13u1`: `2` High each), `ncurses` (`libncursesw6`, `libtinfo6`, `ncurses-base`, `ncurses-bin` `6.5+20250216-2`: `1` High each), `systemd` (`libsystemd0`, `libudev1` `257.13-1~deb13u1`: `1` High each), `perl-base` (`5.40.1-6+deb13u1`: `1` High), `libacl1` (`2.3.2-2+b1`: `1` High), plus `libc6`/`libc-bin`, `gcc-14-base`/`libgcc-s1`/`libstdc++6`, `libkrb5*`/`libgssapi-krb5-2`, `libldap2`, and `tar` (`Medium`/`Low` only).
  - **Runtime Risk Mitigations**: Backend containers run unprivileged as `appuser` (UID `10001`, `/usr/sbin/nologin`), `read_only: true` rootfs, `no-new-privileges:true`, isolated on an `internal: true` Docker bridge network with zero direct SQLite (`psycopg` + PostgreSQL 16 only), Perl, or mount/login binary usage; periodic base-image rebuilds pick up upstream Debian security releases as published.

### 9.2 Environment & Local Execution Notes
1. **Docker Desktop Lifecycle**: Docker Desktop Linux Engine was active throughout Gates 1–8 and 10 (and `finally:` blocks tore down all temporary `atelier-relverify*`, `atelier-prodverify*`, and `atelier-bench*` containers/networks/volumes); following a subsequent workstation restart, Docker Desktop was offline during the resumed Gate 9 host run (`4`-worker Uvicorn + isolated disposable `PostgreSQL 18.6`). Local `atelier-backend:relverify`, `atelier-frontend:relverify`, `ghcr.io/aquasecurity/trivy:0.58.1` images and `atelier-relverify-trivy-cache` volume remain cached on Docker's stopped Linux VM disk until pruned when Docker is next started.

---

## 10. Production Deployment Blockers

1. **No Registered Live Payment Provider Adapter**: Only `sandbox_simpay` is registered (`PAYMENT_PROVIDER=sandbox_simpay`), which `Settings` intentionally rejects at startup (`exit 3`) when `ENVIRONMENT=production`. Live commerce requires implementing and auditing a real PSP `PaymentProviderAdapter` (plus refactoring `checkout.py:create_intent` outside DB row locks per `INFO-SEC-02`) and full PCI scope compliance.
2. **Production Infrastructure & External CI Execution Not Yet Provisioned**: Production DNS + TLS domain (`SITE_DOMAIN`), external secrets management, scheduled off-host PostgreSQL backups (`pg_dump -Fc`) with restore verification, scheduled `cleanup-expired-records` cron/timer execution, and pushing `improvement/production-hardening` for GitHub Actions `ci.yml` execution remain external pre-launch steps.

---

## 11. Remaining Actions

1. Push `improvement/production-hardening` (upon user approval) so GitHub Actions executes `secret-scan`, `ops-validate` (Docker + Trivy), `backend-ci`, `frontend-ci`, and `e2e-ci` remotely.
2. Merge into `main` only after CI green + explicit user authorization.
3. Implement a live PSP adapter + external infrastructure prerequisites before any public live-commerce deployment.
