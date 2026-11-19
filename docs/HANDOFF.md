# Project Handoff & Controlled Pause Checkpoint

- **Checkpoint Date**: `2026-11-20`
- **Status**: **Controlled Development Pause after Wave 3 (`FE-003` + `QA-FE-003`)**
- **Active Background Tasks**: `0`
- **Active / Running Subagents**: `0` (all specialist subagents idle)
- **Wave 4 State**: Not started (`QA-E2E-001` marked `ready`; `OPS-002`, `SEC-001`, `REL-001` remain `proposed`; Playwright not yet installed)
- **Resume Target**: Explicit user instruction to resume with **Orchestrator (Neon)** (no automatic retries, no timers, no Swarm, no model switching, no paid overages)

---

## 1. Current Git State

- **Branch**: `main`
- **Latest Code & Verification Checkpoint**: `591062e` (`feat(frontend): complete FE-003 admin console, analytics dashboard, and QA-FE-003 verification`), followed by this pause-handoff commit adding `docs/HANDOFF.md` and updating `docs/TASKS.md`.
- **Working Tree Expectations**: Clean after committing `docs/TASKS.md` and `docs/HANDOFF.md`.
- **Untouched Legacy Worktree Note**: `git worktree list` retains an unrelated stale empty worktree from an earlier interrupted session (`C:/Users/User/.gemini/antigravity/worktrees/c9ca2e36-f04d-4894-9cb8-91b63eb2db6f/subagent-Backend-BE-004-Implementer-backend-750c92c3` at `24e4e75`); leave untouched or prune safely only if needed later.

### Verified Milestone Commit History on `main`

| Commit | Milestone(s) | Scope |
| :--- | :--- | :--- |
| `591062e` | `FE-003`, `QA-FE-003` | Live Admin Console, Recharts + Data Table analytics, Products/Variants/Categories/Inventory/Orders/Users/Audit UIs & `12` new Vitest specs |
| `9831d31` | `BE-004` hardening, `QA-BE-004` | `OrderEvent` monotonic UTC ordering, `LOW-1`..`LOW-3` fixes, `run_disposable_pg_pytest.py`, `test_admin_and_analytics.py`, QA & Security reports |
| `97ff9e2` | `FE-002`, `QA-FE-002` | Storefront `/cart`, `/checkout` (`Idempotency-Key`), `/orders`, `/orders/[orderNumber]`, SimPay sandbox pay, `HttpOnly` guest cookie + `17` Vitest specs |
| `c949b93` / `f1e5d91` | `BE-004` core | Admin orders/fulfillment/refund, users/RBAC/session revocation, audit logs, UTC sales analytics, `create-user` & `seed-demo` CLI |
| `24e4e75` | Governance | Milestone registration (`BE-004`..`REL-001`) & ADR dependency decisions (`Adopt` Recharts, Playwright; `Defer/Reject` TanStack Query/Table, RHF/Zod, openapi-typescript, Redis, Celery) |
| `0705b14` | `FE-001`, `QA-FE-001` | Next.js 15 / React 19 foundation, design tokens, typed API client, storefront catalog/PDP/auth, RBAC admin shells |
| `e5d0097` | `BE-003`, `QA-BE-003` | Cart, idempotent checkout, atomic stock reservations, orders state machine, HMAC-signed sandbox webhooks |
| `4f51907` | `QA-PG-001` | First live disposable PostgreSQL 18.6 migration, constraint & concurrency verification |
| `e4af80d` | `BE-002`, `QA-BE-002` | Catalog, categories, variants, SKUs, and append-only inventory stock/ledger |
| `2739d9f` | `BE-001`, `QA-BE-001` | FastAPI modular-monolith foundation, `0001_initial_schema`, Identity/Session/CSRF/RBAC |
| `6390eed` | `OPS-001` | `compose.yaml`, Dockerfiles, `.env.example`, `.github/workflows/ci.yml`, `docs/ops/local-development-and-ci.md` |
| `ac9652a` | `DESIGN-001` | Storefront & admin UX specs (`docs/design/*`) |
| `89b17ee` | `ARCH-001` | System architecture, ERD, workflows/security, OpenAPI contracts (`docs/architecture/*`, `docs/api/*`) |

---

## 2. Verified Results (Evidence-Linked)

### Architecture, Design & Baseline Ops
- `ARCH-001` (`89b17ee`): System architecture, ERD, concurrency/security model, and REST contracts (`docs/architecture/*`, `docs/api/openapi-contracts.md`, `docs/security/ARCH-001-security-review.md`).
- `DESIGN-001` (`ac9652a`): Storefront, checkout, and admin UX state matrices (`docs/design/01-design-system-and-tokens.md`, `docs/design/02-storefront-and-checkout-ux.md`, `docs/design/03-admin-and-states-ux.md`).
- `OPS-001` (`6390eed`): Compose topology (`db`, `migrate`, `api`, `web`), Dockerfiles, `.env.example`, and CI skeleton (YAML + `docker compose config` verified; live container runtime remains pending a running Docker daemon).

### Backend (`BE-001`..`BE-004`, `QA-BE-001`..`QA-BE-004`, `QA-PG-001` — Verified at `9831d31`)
Recorded in `docs/qa/QA-BE-004-verification.md` and `docs/security/BE-004-security-review.md` (`e:\fullstack_work\backend`):
- **Lint (`ruff check .`)**: `All checks passed!` (exit `0`).
- **Typecheck (`mypy app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py`)**: `Success: no issues found in 55 source files` (exit `0`).
- **Migrations (`alembic heads`)**: Single linear head `0001_initial_schema (head)` (exit `0`).
- **SQLite Unit/API Suite (`pytest -v`, `TEST_DATABASE_URL` unset, outside sandbox)**: **`161 passed, 4 skipped, 1 warning in 39.54s`** (`0` failed; `4` live-PG tests skipped cleanly).
- **Live Disposable PostgreSQL 18.6 (`tests/run_disposable_pg_pytest.py`, port `65458`, least-privilege `ecom_test_role`, outside sandbox)**: **`165 passed, 0 failed, 0 skipped, 1 warning in 56.60s`** (exit `0`; verified temporary `pid=15376` stopped cleanly and zero temporary PostgreSQL processes remained).
- **Scoped Security Reviews (`docs/security/ARCH-001-security-review.md`, `BE-001`..`BE-004`)**: `0` open Critical/High/Medium findings (`BE-004` `LOW-1`..`LOW-3` resolved & regression-tested; `INFO-1`..`INFO-4` resolved or tracked for `OPS-002`/`SEC-001`).

### Frontend (`FE-001`..`FE-003`, `QA-FE-001`..`QA-FE-003` — Verified at `591062e`)
Directly verified after final `QA-FE-003` edits in `e:\fullstack_work\frontend` (see `docs/qa/QA-FE-003-verification.md`):
- **ESLint (`npm.cmd run lint`)**: `✔ No ESLint warnings or errors` (exit `0`).
- **TypeScript (`npx.cmd tsc --noEmit`)**: `0` errors (exit `0`).
- **Vitest (`npm.cmd test`, `jsdom` + mocked `fetch`/`next/navigation`)**: **`Test Files 5 passed (5)` / `Tests 60 passed (60)` in `11.13s`** (`0` failed, `0` skipped, exit `0`):
  - `tests/format-and-security.test.ts` (`10` tests)
  - `tests/api-client.test.ts` (`5` tests)
  - `tests/ui-and-shells.test.tsx` (`16` tests)
  - `tests/checkout-and-orders.test.tsx` (`17` tests)
  - `tests/admin-console.test.tsx` (`12` tests — dashboard Recharts + keyboard `Data Table` toggle + `<= 366`d UTC range guard, categories hierarchy/cycle/conflict, product editor/activation/media URL/variant cents guards, inventory `?sku=` + delta invariant + ledger drawer, orders 3-column badges + staff vs admin refund gating, admin-only users session-revocation & last-admin guards + audit JSON diff `[REDACTED]`).
- **Production Build (`npm.cmd run build`, `BypassSandbox: true`)**: Compiled cleanly (`3.1s`), **`19/19` static/dynamic pages / `21` total routes** generated with exit `0` (includes `/admin`, `/admin/categories`, `/admin/products`, `/admin/products/new`, `/admin/products/[id]`, `/admin/inventory`, `/admin/orders`, `/admin/orders/[orderNumber]`, `/admin/users`, `/admin/audit-logs`, `/cart`, `/checkout`, `/orders`, `/orders/[orderNumber]`).
- **Frontend Security Coverage**: `docs/security/FE-001-security-review.md` verified `0` open Critical/High (`FE-002` checkout/orders and `FE-003` admin UI audit queued under `SEC-001`).

---

## 3. Remaining Tasks (`Wave 4`)

| ID | Owner | Allowed Paths | Summary of Deliverables | Status |
| :--- | :--- | :--- | :--- | :--- |
| `QA-E2E-001` | `qa` | `frontend/e2e/**`, `frontend/playwright.config.ts` (transferred), `docs/qa/**` (plus Chromium / `@playwright/test` install via Orchestrator) | Windows-safe full-stack orchestrator (`frontend/e2e/run_e2e_stack.py`), `frontend/playwright.config.ts`, `frontend/e2e/storefront-and-admin.spec.ts` covering 4 live browser journeys against Next.js + FastAPI + disposable PostgreSQL 18.6 (`alembic upgrade head` + `seed-demo --with-demo-users`), plus `docs/qa/QA-E2E-001-verification.md`. | `ready` |
| `OPS-002` | `devops` | `infra/**`, `.github/workflows/**`, `compose.yaml`, `.env.example`, `docs/ops/**` | Update `.github/workflows/ci.yml` (`TEST_DATABASE_URL` live PG 16 tests in `backend-ci`, split Playwright into dedicated `e2e-ci` job with `postgres:16-alpine`), verify Compose/Dockerfiles against implemented apps, document CLI (`create-user`, `seed-demo` + `INFO-1` staging password warning), disposable PG runner, E2E stack runner, and permanent Windows subprocess safeguards in `docs/ops/local-development-and-ci.md`. | `proposed` |
| `SEC-001` | `security` | strictly read-only (`docs/security/SEC-001-pre-release-audit.md` recorded by Orchestrator) | Full pre-release security audit covering auth/CSRF, RBAC (incl. `FE-002`/`FE-003`), checkout/payments/webhooks, admin controls, CSP/headers, `npm.cmd audit --json` + Python deps, Dockerfiles/Compose, and CI workflow; remediate any Critical/High via owning agent before closing. | `proposed` |
| `REL-001` | `orchestrator` | `README.md`, `docs/release/**` | Root `README.md` + `docs/release/RELEASE-RUNBOOK.md` (quickstart, env/secret rotation, Alembic runbook, CLI bootstrap, ADR summary, Windows runner safeguards, evidence-linked Verified/Unverified/Blocked matrix, known risks, explicit non-PCI / sandbox-only disclaimer). | `proposed` |

---

## 4. Known Blockers, Risks & Environment Constraints

1. **Docker Desktop Engine Not Running Locally (PG 16 Parity Blocked Locally)**:
   - Docker CLI (`29.8.2`) is installed, but the Docker Desktop Linux engine is not running on the lead Windows host.
   - Consequently, `docker build`, `docker compose up`, and exact `postgres:16-alpine` container runtime execution remain **blocked locally** until Docker Desktop is started or CI executes on GitHub Actions.
   - Local live PostgreSQL testing uses the **PostgreSQL 18.6 binaries at `E:\databasesql\bin`** inside isolated `%TEMP%` disposable clusters.
2. **Host PostgreSQL Safety Boundary (CRITICAL)**:
   - **Never modify, stop, connect tests to, or drop databases/roles in the existing host PostgreSQL instance on `127.0.0.1:5432` or `E:\databasesql\data`.**
   - Always bind disposable clusters to ephemeral `127.0.0.1` ports `!= 5432` under `%TEMP%`, with least-privilege `ecom_test_role`.
3. **Tracked Frontend Dependency Advisories (Must Audit in `SEC-001`)**:
   - `npm.cmd install recharts` installed `recharts@^3.11.0` cleanly with exit `0` and reported `5 vulnerabilities (2 moderate, 1 high, 2 critical)` — consistent with the `FE-001` baseline findings (`next@15.5.29` moderate + bundled `postcss@8.4.31` high, fixed in semver-major `next@16.4.6`; dev-only `vitest@3.2.7` / `tinypool@1.1.1` / `@vitest/mocker@3.2.7`, fixed in `vitest@5.1.1`).
   - Run `C:\Program Files\nodejs\npm.cmd audit --json` in `SEC-001` to verify exact package advisories and confirm `recharts` introduced zero unmitigated runtime risks.
4. **Windows Tooling & Sandbox Constraints**:
   - PowerShell ExecutionPolicy blocks `npm.ps1` / `npx.ps1`: always invoke `"C:\Program Files\nodejs\npm.cmd"` and `"C:\Program Files\nodejs\npx.cmd"`.
   - `rg` is not installed: use PowerShell `Get-ChildItem ... | Select-String`.
   - Commands needing TCP loopback, package registry/browser binaries, parenthesized `.next` route-group `mkdir`, or `.git/index.lock` creation (`pytest`, `run_disposable_pg_pytest.py`, `npm.cmd run build`, `npm.cmd install`, Playwright, `git add`/`commit`) require `BypassSandbox: true` on Windows; lint, `tsc --noEmit`, `vitest run`, `ruff`, `mypy`, `alembic heads`, and read-only `git` run inside the sandbox.
5. **Mandatory Permanent Windows Subprocess Safeguards (`QA-E2E-001` & `OPS-002`)**:
   - **Never pass `stdout=subprocess.PIPE` or `stderr=subprocess.PIPE` when launching daemon processes (`pg_ctl`, `uvicorn`, `next start`) on Windows**: child daemon processes inherit the pipe write handles and deadlock `subprocess.run()` / pipe readers indefinitely. Redirect daemon output strictly to log files inside the temporary run directory (`stdin=subprocess.DEVNULL, close_fds=True`).
   - Decode any short captured diagnostic tools (`psql`, `tasklist`) with `encoding="utf-8", errors="replace"` (avoids localized CP1251 background `_readerthread` `UnicodeDecodeError`).
   - Enforce explicit per-step timeouts, explicit TCP/HTTP readiness loops, an overall `threading.Timer` watchdog, unbuffered (`flush=True`) status logs, and `try/finally` process-tree teardown (`taskkill /PID <pid> /T /F` on Windows) plus verified PID exit (`_is_pid_running == False`).
   - Reuse these exact guarantees from `backend/tests/run_disposable_pg_pytest.py` in `frontend/e2e/run_e2e_stack.py`.
6. **Scope & Compliance Boundary**:
   - Baseline e-commerce application uses SimPay in **sandbox mode only** (`PAYMENT_SANDBOX_MODE=true`), stores zero card numbers, and uses an in-memory single-process rate limiter; it must **not** be represented as PCI-certified or production-ready before `SEC-001` and operational hardening complete.

---

## 5. Exact Continuation Instructions (Resume with Orchestrator Neon)

Follow `AGENTS.md` strictly upon explicit user instruction to resume (no Swarm, no extra custom agents, respect single-owner writable path boundaries):

### Phase 1 — `QA-E2E-001` (Live Full-Stack Playwright Verification)
1. Mark `QA-E2E-001` `in_progress` in `docs/TASKS.md`.
2. In `e:\fullstack_work\frontend`, install Playwright (`BypassSandbox: true`, split commands):
   - `"C:\Program Files\nodejs\npm.cmd" install -D @playwright/test`
   - `"C:\Program Files\nodejs\npx.cmd" playwright install chromium`
   - Optionally ensure Playwright can run cleanly without leaving broken scripts; remember `frontend/package.json` is not in `qa`'s write scope, so Orchestrator updates `package.json` / lockfile during setup and transfers only `frontend/playwright.config.ts`, `frontend/e2e/**`, and `docs/qa/**` to `qa`.
3. Delegate `QA-E2E-001` to `qa` (`Workspace: "inherit"`):
   - Create `frontend/playwright.config.ts` (`chromium`, `baseURL: process.env.E2E_BASE_URL || "http://localhost:3000"`, `timeout: 60_000`, `expect: { timeout: 15_000 }`, `retries: 0`, `workers: 1`, `trace: "retain-on-failure"`, `screenshot: "only-on-failure"`, `reporter: [["list"]]`).
   - Create `frontend/e2e/run_e2e_stack.py` obeying all Windows subprocess safeguards:
     - Fail fast if TCP ports `8000` or `3000` are already occupied (do not kill unrelated host processes).
     - Provision disposable PG 18.6 cluster under `%TEMP%` on an ephemeral non-5432 port (`!= 5432`, rejecting `E:\databasesql\data`), run `alembic upgrade head` (60s timeout) and `python -m app.cli seed-demo --with-demo-users` (60s timeout) with `ENVIRONMENT=test`, `COOKIE_SECURE=false`, `ALLOWED_ORIGINS=http://localhost:3000`, `PAYMENT_SANDBOX_MODE=true`, 32+-char HMAC secrets, and explicit `DEMO_*_PASSWORD` vars.
     - Launch `uvicorn app.main:app --host 127.0.0.1 --port 8000` and `C:\Program Files\nodejs\node.exe .\node_modules\next\dist\bin\next start -H 127.0.0.1 -p 3000` (avoiding standalone missing-static-assets pitfalls unless `.next/static` is copied) with log-file redirection (`stdin=subprocess.DEVNULL, close_fds=True`, zero `PIPE`).
     - Wait on bounded readiness checks (`http://localhost:8000/api/v1/health` and `http://localhost:3000/`), run `npx.cmd playwright test` (`timeout=240`), enforce an overall `threading.Timer` watchdog, tear down Next/Uvicorn/PG in `try/finally`, verify zero temporary PIDs remain, preserve redacted diagnostic logs/traces on failure, and delete `%TEMP%` dir on success.
   - Create `frontend/e2e/storefront-and-admin.spec.ts` covering all 4 journeys:
     1. Authenticated customer purchase -> PDP variant -> `/cart` -> `/checkout` -> Place Order -> zero card-number inputs -> `"Simulate Payment Success"` -> `CONFIRMED`/`PAID` -> `/orders`.
     2. Guest add-to-cart -> guest checkout -> reload `/orders/{orderNumber}` (`HttpOnly` `ecom_guest_orders` cookie) -> assert zero auth/CSRF/guest tokens in `localStorage`/`sessionStorage` -> self-cancel -> `CANCELLED` & stock released.
     3. `staff` vs `admin` RBAC + fulfillment + analytics + refund/restock + audit log (`staff` hits `403 Restricted to Administrators` on `/admin/users` & `/admin/audit-logs`, has no `Issue Refund`, fulfills `CONFIRMED -> PROCESSING -> SHIPPED -> COMPLETED`; `admin` sees non-zero Dashboard KPIs, refunds with `restock=true`, confirms stock restored and audit diff entry visible).
     4. Oversell guard (1-unit variant reserved by Context A blocks Context B with `409 INSUFFICIENT_STOCK` / unavailable UI protection).
4. Orchestrator executes `e:\fullstack_work\backend\.venv\Scripts\python.exe e:\fullstack_work\frontend\e2e\run_e2e_stack.py` (`BypassSandbox: true`), verifies all 4 specs pass and zero lingering PIDs remain, has `qa` write `docs/qa/QA-E2E-001-verification.md`, marks `QA-E2E-001` `verified`, and commits. (If E2E uncovers production bugs, fix via `backend`/`frontend` owner and re-verify before committing.)

### Phase 2 — `OPS-002` (`devops`)
1. Mark `OPS-002` `in_progress` in `docs/TASKS.md` and delegate to `devops` (`Workspace: "inherit"`, writable scope `infra/**`, `.github/workflows/**`, `compose.yaml`, `.env.example`, `docs/ops/**`).
2. Update `.github/workflows/ci.yml` (`backend-ci` sets `TEST_DATABASE_URL` against `postgres:16-alpine` safely alongside `alembic upgrade head`; `frontend-ci` runs `lint`, `tsc`, `build`, `vitest`; move Playwright into dedicated `e2e-ci` job with `postgres:16-alpine`, `uvicorn`, and `next start`).
3. Verify `compose.yaml` + `infra/docker/{backend,frontend}.Dockerfile` match implemented apps; update `docs/ops/local-development-and-ci.md` with `create-user`, non-prod `seed-demo` (`INFO-1` staging password warning), `run_disposable_pg_pytest.py`, `run_e2e_stack.py`, and permanent Windows subprocess safeguards.
4. Orchestrator verifies PyYAML parse + `docker compose --env-file .env.example config --quiet` + `docker info` check (documenting container runtime as verified or blocked honestly), marks `OPS-002` `verified`, and commits.

### Phase 3 — `SEC-001` (`security`, Strictly Read-Only)
1. Mark `SEC-001` `in_progress` in `docs/TASKS.md`.
2. Capture read-only `C:\Program Files\nodejs\npm.cmd audit --json` in `frontend/` and inspect Python dependencies in `backend/`.
3. Invoke `security` (`Workspace: "inherit"`, read-only) for the full pre-release audit (auth/CSRF, RBAC across API + UI, checkout/payments/webhooks, `FE-002`/`FE-003` views, CSP/headers, dependencies, Docker/CI). Remediate any Critical/High findings via owning agent and re-run affected tests.
4. Record `docs/security/SEC-001-pre-release-audit.md`, mark `SEC-001` `verified`, and commit.

### Phase 4 — `REL-001` (`orchestrator`)
1. Mark `REL-001` `in_progress` in `docs/TASKS.md`.
2. Write root `README.md` and `docs/release/RELEASE-RUNBOOK.md` with Quickstart, CLI bootstrap/seed, env & secret rotation checklist, Alembic guidance, ADR summary, Windows runner safeguards, complete evidence-linked **Verified / Unverified / Blocked** matrix, known risks, and explicit non-PCI / sandbox-only disclaimer.
3. Mark `REL-001` `verified` in `docs/TASKS.md`, confirm clean `git status --short`, commit final checkpoint, and issue the final summary report.
