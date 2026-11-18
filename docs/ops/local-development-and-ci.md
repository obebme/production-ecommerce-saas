# Local Development, Container Topology & CI Runbook (`OPS-001`)

> **Scope**: Reproducible Docker Compose setup, multi-stage Dockerfiles, environment templates, database migration workflow, and GitHub Actions CI.  
> **Baseline Constraint**: Minimum 4-service stack (`db`, `migrate`, `api`, `web`) with PostgreSQL 16 as single source of truth. Redis, Celery, Kubernetes, and external SaaS services are intentionally excluded per `ARCH-001`.

---

## 1. Prerequisites & Scope Status

### Verified vs. Unverified Capabilities at `OPS-001`

| Area | Status | Notes |
| :--- | :--- | :--- |
| `compose.yaml` & `.github/workflows/ci.yml` YAML parsing | **Verified** | Validated statically via Python `PyYAML` |
| `.env.example` variable coverage & placeholder check | **Verified** | Verified against `ARCH-001` required settings |
| `docker compose up --build` runtime boot | **Blocked until `BE-001` & `FE-001`** | `backend/` and `frontend/` directories do not exist yet |
| `backend-ci` & `frontend-ci` GitHub Actions execution | **Skipped cleanly until `BE-001` / `FE-001`** | `detect` job activates each pipeline automatically once manifests exist |

Required host tooling for full local container startup (once `BE-001` and `FE-001` are implemented):
- Docker Engine + Docker Compose v2 (`docker compose`)
- Python 3.12+ (for local secret generation or host-side backend tooling)
- Node.js 20 LTS (optional for host-side frontend development)

---

## 2. Quick Start (Local Development)

### Step 1: Create `.env` from `.env.example`

Never commit `.env` (already excluded by `.gitignore`).

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### Step 2: Generate Cryptographic Secrets

Replace all `CHANGE_ME_DEV_ONLY_*` placeholders in `.env` with freshly generated random strings (`>= 32` characters each):

```bash
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24)); print('CSRF_HMAC_SECRET=' + secrets.token_urlsafe(32)); print('GUEST_ORDER_HMAC_SECRET=' + secrets.token_urlsafe(32)); print('PAYMENT_WEBHOOK_SECRET=' + secrets.token_urlsafe(32))"
```

Update `DATABASE_URL` in `.env` so its password matches `POSTGRES_PASSWORD`:

```env
DATABASE_URL=postgresql+psycopg://ecom_dev:<POSTGRES_PASSWORD>@db:5432/ecom_dev
```

> **Host vs. Container `DATABASE_URL`**: Inside Docker Compose (`api` and `migrate`), PostgreSQL is reachable at hostname `db:5432`. If you run `alembic` or `pytest` directly from your host machine against the exposed `127.0.0.1:5432` port, set `DATABASE_URL=postgresql+psycopg://ecom_dev:<POSTGRES_PASSWORD>@localhost:5432/ecom_dev` in your host shell environment.

### Step 3: Build & Start the Baseline Stack

```bash
docker compose up --build
```

Startup dependency order enforced by `compose.yaml`:
1. `db` (`postgres:16-alpine`) starts and persists data in named volume `pgdata`.
2. `db` passes `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`.
3. `migrate` builds from [backend.Dockerfile](../../infra/docker/backend.Dockerfile), runs `alembic upgrade head`, and exits (`restart: "no"`).
4. `api` starts (`uvicorn app.main:app --host 0.0.0.0 --port 8000`) only after `db` is healthy **and** `migrate` completes with exit code `0`.
5. `web` starts (`node server.js` or `next start`) only after `api` passes `/api/v1/health`.

Local endpoints once running:
- **Storefront & Admin UI**: `http://localhost:3000`
- **FastAPI Swagger / API**: `http://localhost:8000/api/v1/health`
- **PostgreSQL (host-bound loopback only)**: `127.0.0.1:5432`

To stop services while preserving database volume `pgdata`:

```bash
docker compose down
```

To reset local database state completely (**destructive — deletes all local DB data**):

```bash
docker compose down -v
```

---

## 3. Container & Health-Check Architecture

### Service Matrix

| Service | Image / Dockerfile | Build Context | Ports | Health Check | Dependency Gate |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `db` | `postgres:16-alpine` | N/A | `127.0.0.1:5432:5432` | `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}` | None |
| `migrate` | [backend.Dockerfile](../../infra/docker/backend.Dockerfile) | `./backend` | None | One-shot exit code `0` | `db` (`service_healthy`) |
| `api` | [backend.Dockerfile](../../infra/docker/backend.Dockerfile) | `./backend` | `8000:8000` | Python `urllib.request` on `http://127.0.0.1:8000/api/v1/health` (`HTTP 200`) | `db` (`service_healthy`), `migrate` (`service_completed_successfully`) |
| `web` | [frontend.Dockerfile](../../infra/docker/frontend.Dockerfile) | `./frontend` | `3000:3000` | Node `http.get` on `http://127.0.0.1:3000/` (`HTTP 2xx/3xx`) | `api` (`service_healthy`) |

### Container Hardening & Build Notes

- **Non-root Execution**:
  - `backend.Dockerfile` runs as `appuser` (`UID 10001`, `nologin`) using an isolated `/opt/venv` copied from a multi-stage `builder` image (no compilers in runtime).
  - `frontend.Dockerfile` runs as `nextjs` (`UID 10001`) on `node:20-alpine`, copying only runtime artifacts (`server.js` standalone output when configured, or `next start` runtime files).
- **No `curl` Required**: Health checks use Python `urllib.request` and Node `http.get` built-ins to keep minimal runtime images free of extra utilities.
- **Browser vs. SSR URL Split**:
  - `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1` is passed as a Docker build arg so browser requests target `localhost:8000` (same-site as `localhost:3000` for `SameSite=Lax` HTTP-only cookies).
  - `INTERNAL_API_BASE_URL=http://api:8000/api/v1` is passed as a runtime env var for Next.js SSR fetches inside the Compose network.
- **Recommended `.dockerignore` in `BE-001` / `FE-001`**: When `backend/` and `frontend/` are scaffolded by their respective owners, include `.dockerignore` files excluding `.venv/`, `node_modules/`, `.next/`, `__pycache__/`, `.pytest_cache/`, and `.env*` to keep build contexts fast and clean.

---

## 4. Database Migration & Rollback Workflow

Per `ARCH-001` (`02-data-model-and-erd.md`), `Base.metadata.create_all()` is forbidden in application runtime. All schema changes go through Alembic.

### Normal Migration Startup

Compose runs `migrate` automatically before `api` starts:

```bash
docker compose run --rm migrate
```

### Verify Single Linear Migration Head

Before opening or merging a PR with schema changes, confirm Alembic has exactly one head revision:

```bash
docker compose run --rm --no-deps api alembic heads
```

Expected output: a single revision line (e.g., `0001_initial_schema (head)`). Multiple heads fail CI in `backend-ci`.

### Rollback Workflow (Development / Recovery)

1. Stop `api` and `web` before rolling back schema versions so running code does not query dropped columns/tables:
   ```bash
   docker compose stop web api
   ```
2. Downgrade one revision (or to a specific target revision):
   ```bash
   docker compose run --rm migrate alembic downgrade -1
   ```
3. Inspect current state:
   ```bash
   docker compose run --rm migrate alembic current
   ```
4. Note that in production/staging, expand-and-contract migrations are preferred over destructive `downgrade` operations on populated tables.

---

## 5. Secret Management & Fail-Closed Configuration Rules

1. **Never Commit Secrets**: `.env` is gitignored. `.env.example` contains only `CHANGE_ME_DEV_ONLY_*` placeholders.
2. **No Hardcoded Fallbacks in `compose.yaml`**: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`, `CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, and `PAYMENT_WEBHOOK_SECRET` use `${VAR:?...}` interpolation so missing variables fail immediately during Compose parsing.
3. **Production Fail-Closed Invariants** (enforced by `backend/app/core/config.py` in `BE-001`):
   When `ENVIRONMENT=production`, backend startup MUST abort if any of the following are true:
   - `PAYMENT_SANDBOX_MODE=true`
   - `COOKIE_SECURE=false`
   - `ALLOWED_ORIGINS` is empty or contains wildcard `*`
   - Any of `CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, or `PAYMENT_WEBHOOK_SECRET` is `< 32` characters or retains placeholder values (`CHANGE_ME_*`).
4. **Browser Token Hygiene**: Authentication (`ecom_session`), CSRF (`ecom_csrf`), guest cart (`ecom_guest_cart`), and guest order (`ecom_guest_orders`) tokens rely on cookies/headers per `ARCH-001`; never store secrets in browser `localStorage` or `sessionStorage`.

---

## 6. CI Pipeline Guide (`.github/workflows/ci.yml`)

The GitHub Actions workflow runs on `push` to `main` and all `pull_request` events:

1. **`detect`**: Checks whether `backend/pyproject.toml` (or `requirements.txt`) and `frontend/package.json` exist.
2. **`ops-validate`** *(always runs)*:
   - Parses `compose.yaml` and `.github/workflows/ci.yml` with `PyYAML`.
   - Verifies `compose.yaml` defines only the 4 baseline services (`db`, `migrate`, `api`, `web`).
   - Verifies `.env` is untracked and `.env.example` defines all 17 required keys with `CHANGE_ME_DEV_ONLY_` secret placeholders.
   - Runs `docker compose --env-file .env.example config --quiet`.
   - Builds `backend.Dockerfile` and `frontend.Dockerfile` conditionally once their respective app manifests exist.
3. **`backend-ci`** *(runs when `backend/` manifests exist; skipped cleanly before `BE-001`)*:
   - Spins up a healthy `postgres:16-alpine` service container.
   - Installs Python 3.12 dependencies, runs `ruff check .`, `mypy app`, verifies `alembic heads` has exactly 1 head, runs `alembic upgrade head`, and runs `pytest` once tests exist under `backend/tests/`.
4. **`frontend-ci`** *(runs when `frontend/package.json` exists; skipped cleanly before `FE-001`)*:
   - Installs Node 20 dependencies (`npm ci` when `package-lock.json` is present), runs `npm run lint`, `npx tsc --noEmit`, `npm run build`, unit tests under `frontend/tests/` (when present), and Playwright E2E specs under `frontend/e2e/` (when present).

### Local Static Validation Command

Verify Compose and CI configuration locally without starting containers:

```bash
docker compose --env-file .env.example config --quiet
```
