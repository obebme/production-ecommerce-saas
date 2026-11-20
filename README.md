# Atelier Commerce — Modular-Monolith E-Commerce SaaS

Production-oriented, initially **single-tenant** e-commerce platform built with **Next.js 15 / React 19 / TypeScript / Tailwind CSS / Recharts** (storefront & admin console) and **Python 3.12 / FastAPI / SQLAlchemy 2 / Alembic / PostgreSQL** (modular-monolith API and operational CLI).

> [!WARNING]
> **Sandbox & Pre-Production Status Notice**: This repository is **not live-deployed** and is **not PCI-certified**. Checkout uses the in-process **SimPay sandbox payment adapter** (`PAYMENT_SANDBOX_MODE=true`), stores zero payment card data, and relies on baseline single-process in-memory rate limiting. Do **not** deploy to production traffic until all pre-production gates in [RELEASE-RUNBOOK.md](docs/release/RELEASE-RUNBOOK.md) (including replacing the sandbox payment adapter, TLS reverse-proxy hardening, remote CI `postgres:16-alpine` container verification, and `SEC-001` low-severity items) are completed.

---

## Architecture Summary

- **Storefront (`frontend/src/app/(storefront)`)**: Editorial catalog browsing (`/` & `/products`), product detail variant selector (`/products/[slug]`), persistent server-synchronized cart drawer & `/cart`, server-authoritative checkout (`/checkout`) with `Idempotency-Key`, zero-card SimPay sandbox confirmation (`/orders/[orderNumber]`), authenticated customer order history (`/orders`), `HttpOnly` guest order access (`ecom_guest_orders`), and account/session views (`/login`, `/register`, `/account`).
- **Admin Console (`frontend/src/app/(admin)/admin`)**: Role-gated operations console (`staff` and `admin`) featuring UTC sales analytics (`Recharts` + keyboard-accessible Data Table), category hierarchy management, product/image/variant editor, manual inventory adjustment + append-only stock ledger, order fulfillment & strictly `admin`-only refunds (`restock`), administrator-only user/role management with immediate session revocation and last-admin protection, and before/after JSON audit-log inspection.
- **Modular-Monolith API (`backend/app`)**:
  - `identity` (Argon2id passwords, DB-backed `HttpOnly` sessions, `Origin`/`Referer` + HMAC `X-CSRF-Token` validation, `customer`/`staff`/`admin` RBAC)
  - `catalog` & `inventory` (1:1 variant stock rows, `FOR UPDATE` row locking, append-only `inventory_ledger_entries`)
  - `cart`, `orders`, & `payments` (server-side totals, `pg_advisory_xact_lock` idempotent checkout, 15-minute stock reservations with lazy expiry reclamation, canonical deadlock-free lock hierarchy, bounded HMAC-verified webhook ingestion)
  - `admin`, `analytics`, & `cli` (immutable `audit_logs` with secret redaction, UTC revenue/timeseries/top-products/inventory-health queries, `create-user` & non-production `seed-demo` commands)
- **Baseline Infrastructure (`compose.yaml`, `infra/`)**: Minimal 4-service topology (`db` on `postgres:16-alpine`, one-shot `migrate`, `api`, `web`), intentionally omitting Redis, Celery, microservices, and paid external SaaS dependencies per [04-adrs-and-roadmap.md](docs/architecture/04-adrs-and-roadmap.md).

---

## Quickstart

### Option A — Docker Compose (`postgres:16-alpine` + `api` + `web`)

Requires Docker Engine + Docker Compose v2.

1. Copy the environment template (never commit `.env`):
   ```bash
   cp .env.example .env
   ```
   On Windows PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```
2. Generate strong secrets (`>= 32` characters each) and replace every `CHANGE_ME_DEV_ONLY_*` placeholder in `.env` (`POSTGRES_PASSWORD`, `CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, `PAYMENT_WEBHOOK_SECRET`), ensuring `DATABASE_URL` matches `POSTGRES_PASSWORD`:
   ```bash
   python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24)); print('CSRF_HMAC_SECRET=' + secrets.token_urlsafe(32)); print('GUEST_ORDER_HMAC_SECRET=' + secrets.token_urlsafe(32)); print('PAYMENT_WEBHOOK_SECRET=' + secrets.token_urlsafe(32))"
   ```
   *(If running `docker compose` after host frontend builds on Windows, consider creating `frontend/.dockerignore` first per `SEC-001` `LOW-1` so host `node_modules/` and `.next/` are excluded from Docker build context.)*
3. Build and start the 4 baseline services:
   ```bash
   docker compose up --build
   ```
4. Access:
   - Storefront & Admin Console: `http://localhost:3000`
   - FastAPI Health / OpenAPI: `http://localhost:8000/api/v1/health`

*(Note: Container builds and `postgres:16-alpine` runtime were blocked locally on the lead Windows workstation because Docker Desktop Linux Engine is not running; `docker compose --env-file .env.example config --quiet` passed locally and CI targets `postgres:16-alpine`.)*

### Option B — Native Local Development (Python 3.12 + Node.js 22/24 + PostgreSQL)

> [!CAUTION]
> On Windows with existing local PostgreSQL on `127.0.0.1:5432`, **never** point test suites or disposable clusters at host `127.0.0.1:5432` or `E:\databasesql\data`. Use dedicated test databases or the isolated runners below.

1. **Backend (`e:\fullstack_work\backend`)**:
   - Create/activate `.venv` and install `requirements-dev.txt`.
   - Export `DATABASE_URL`, `ENVIRONMENT=development`, `COOKIE_SECURE=false`, `ALLOWED_ORIGINS=http://localhost:3000`, `PAYMENT_SANDBOX_MODE=true`, and 32+-char signing secrets.
   - Apply migrations and start FastAPI:
     ```powershell
     .\.venv\Scripts\alembic.exe upgrade head
     .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
     ```
2. **Frontend (`e:\fullstack_work\frontend`)**:
   - On Windows PowerShell where `npm.ps1` is blocked by ExecutionPolicy, invoke `C:\Program Files\nodejs\npm.cmd` and `npx.cmd` directly (for mutating installs on older global npm, see [local-development-and-ci.md](docs/ops/local-development-and-ci.md)).
   ```powershell
   npm.cmd ci
   $env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1"
   npm.cmd run dev
   ```

---

## Operational CLI (`create-user` & `seed-demo`)

Execute from `e:\fullstack_work\backend`:

- **Bootstrap Administrator (`create-user`)** — preferred `--password-env` pattern (avoids shell argument exposure per `BE-004` `INFO-2`):
  ```powershell
  $env:ADMIN_BOOTSTRAP_PASSWORD = "<strong-password-10-to-128-chars>"
  .\.venv\Scripts\python.exe -m app.cli create-user `
    --email admin@example.com `
    --full-name "Primary Admin" `
    --role admin `
    --password-env ADMIN_BOOTSTRAP_PASSWORD
  ```
- **Non-Production Demo Catalog & Demo Users (`seed-demo`)**:
  ```powershell
  # Set unique passwords before running outside disposable local dev (BE-004 INFO-1)
  $env:DEMO_ADMIN_PASSWORD = "<unique-admin-password>"
  $env:DEMO_STAFF_PASSWORD = "<unique-staff-password>"
  $env:DEMO_CUSTOMER_PASSWORD = "<unique-customer-password>"
  .\.venv\Scripts\python.exe -m app.cli seed-demo --with-demo-users
  ```
  `seed-demo` idempotently creates 3 categories (`apparel`, `accessories`, `home`), 8 products, and 11 variants, and hard-fails (`exit 1`) if `ENVIRONMENT=production`.

---

## Automated Verification Commands

### Backend (`e:\fullstack_work\backend`)
```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py
.\.venv\Scripts\alembic.exe heads
.\.venv\Scripts\pytest.exe -v
.\.venv\Scripts\python.exe tests\run_disposable_pg_pytest.py
```

### Frontend (`e:\fullstack_work\frontend`)
```powershell
npm.cmd run lint
npx.cmd tsc --noEmit
npm.cmd test
npm.cmd run build
npm.cmd audit --json
```

### Full-Stack Live E2E (`e:\fullstack_work`)
```powershell
.\backend\.venv\Scripts\python.exe .\frontend\e2e\run_e2e_stack.py
```
Both [run_disposable_pg_pytest.py](backend/tests/run_disposable_pg_pytest.py) and [run_e2e_stack.py](frontend/e2e/run_e2e_stack.py) enforce the mandatory Windows subprocess safeguards documented in [local-development-and-ci.md §7](docs/ops/local-development-and-ci.md#7-windows-subprocess--disposable-postgresql-operational-safeguards) (no `PIPE` on daemon starters, log-file redirection, UTF-8 replacement decoding, bounded readiness checks, `threading.Timer` watchdogs, `%TEMP%` isolation off port `5432`, and verified `try/finally` PID teardown).

---

## Documentation Map

- **Task Board**: [docs/TASKS.md](docs/TASKS.md)
- **Architecture & Contracts**: [System Overview](docs/architecture/01-system-overview-and-modules.md), [Data Model & ERD](docs/architecture/02-data-model-and-erd.md), [Workflows, Concurrency & Security](docs/architecture/03-workflows-concurrency-and-security.md), [ADRs & Roadmap](docs/architecture/04-adrs-and-roadmap.md), [OpenAPI Contracts](docs/api/openapi-contracts.md)
- **Design System & UX Specs**: [Tokens](docs/design/01-design-system-and-tokens.md), [Storefront & Checkout UX](docs/design/02-storefront-and-checkout-ux.md), [Admin & States UX](docs/design/03-admin-and-states-ux.md)
- **Operations & CI**: [Local Development & CI Runbook](docs/ops/local-development-and-ci.md), [CI Workflow](.github/workflows/ci.yml), [Compose Topology](compose.yaml)
- **QA Verification Reports**: [QA-PG-001](docs/qa/QA-PG-001-postgresql-verification.md), [QA-BE-003](docs/qa/QA-BE-003-verification.md), [QA-BE-004](docs/qa/QA-BE-004-verification.md), [QA-FE-001](docs/qa/QA-FE-001-verification.md), [QA-FE-002](docs/qa/QA-FE-002-verification.md), [QA-FE-003](docs/qa/QA-FE-003-verification.md), [QA-E2E-001](docs/qa/QA-E2E-001-verification.md)
- **Security Reviews**: [ARCH-001](docs/security/ARCH-001-security-review.md), [BE-001](docs/security/BE-001-security-review.md), [BE-002](docs/security/BE-002-security-review.md), [BE-003](docs/security/BE-003-security-review.md), [FE-001](docs/security/FE-001-security-review.md), [BE-004](docs/security/BE-004-security-review.md), [SEC-001 Pre-Release Audit](docs/security/SEC-001-pre-release-audit.md)
- **Release Runbook & Matrix**: [docs/release/RELEASE-RUNBOOK.md](docs/release/RELEASE-RUNBOOK.md)
