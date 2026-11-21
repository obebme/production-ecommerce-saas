# Local Development, Container Topology & CI Runbook (`OPS-002` / `REM-P2-OPS`)

> **Scope**: Reproducible Docker Compose setup, multi-stage Dockerfiles, environment templates, database migration and CLI administration workflows, local PostgreSQL/E2E runners, Windows subprocess operational safeguards, and GitHub Actions CI.  
> **Baseline Constraint**: Minimum 4-service stack (`db`, `migrate`, `api`, `web`) with PostgreSQL 16 as single source of truth. Redis, Celery, Kubernetes, and external SaaS services are intentionally excluded per `ARCH-001`.

---

## 1. Prerequisites & Verification Status (`OPS-002` / `REM-P2-OPS`)

### Verified vs. Unverified vs. Blocked Capabilities

| Area | Status | Notes / Evidence |
| :--- | :--- | :--- |
| `compose.yaml` & `.github/workflows/ci.yml` YAML parsing | **Verified** | Validated statically via Python `PyYAML` and `ops-validate` service assertions |
| `.env.example` variable coverage & placeholder check | **Verified** | All 18 required keys + optional `TRUSTED_PROXY_IPS` match `Settings` in [config.py](../../backend/app/core/config.py) |
| `docker compose --env-file .env.example config --quiet` | **Verified** | Validated locally via Docker Compose CLI |
| Alembic migration head check (`alembic heads`) | **Verified (Host)** | Single linear head `0003_phase2_indexes_and_cleanup (head)` |
| Phase 1 / Phase 2 full test & E2E suites | **Prior baseline verified (`REM-P1-QA`); `REM-P2-QA` pending** | Phase 1 verified (`173` SQLite / `177` PG 18.6, `64` Vitest, `4` Playwright E2E); Phase 2 (`0003` migration + hardening) awaits `REM-P2-QA` |
| Container image builds (`docker build`) & `docker compose up --build` | **Blocked locally / Unverified** | Local Docker daemon is not running (`npipe:////./pipe/docker_engine` unavailable on host); configured in CI via `ops-validate` |
| Exact PostgreSQL 16 (`postgres:16-alpine`) CI parity (`backend-ci`, `e2e-ci`) | **Configured for CI / Pending remote CI run** | Configured in [ci.yml](../../.github/workflows/ci.yml) using `postgres:16-alpine` service containers + least-privilege `ecom_test` role |

Required host tooling:
- Docker Engine + Docker Compose v2+ (`docker compose`) for containerized local execution
- Python 3.12+ (for host-side backend development, CLI administration, and local verification runners)
- Node.js 22 LTS (aligned with [frontend.Dockerfile](../../infra/docker/frontend.Dockerfile), [ci.yml](../../.github/workflows/ci.yml), `@types/node@^22.13.10`, `vitest@^4.1.12`, `vite@^7.3.8`, `@playwright/test@^1.65.1`, and `next@^15.5.29`)

---

## 2. Quick Start (Docker Compose Local Development)

### Step 1: Create `.env` from `.env.example`

Never commit `.env` (already excluded by `.gitignore` and enforced by `ops-validate`).

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### Step 2: Generate Cryptographic Secrets

Replace all `CHANGE_ME_DEV_ONLY_*` placeholders in `.env` with freshly generated random strings (`>= 32` characters each for HMAC secrets):

```bash
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24)); print('CSRF_HMAC_SECRET=' + secrets.token_urlsafe(32)); print('GUEST_ORDER_HMAC_SECRET=' + secrets.token_urlsafe(32)); print('PAYMENT_WEBHOOK_SECRET=' + secrets.token_urlsafe(32))"
```

Update `DATABASE_URL` in `.env` so its password matches `POSTGRES_PASSWORD`:

```env
DATABASE_URL=postgresql+psycopg://ecom_dev:<POSTGRES_PASSWORD>@db:5432/ecom_dev
```

> **Host vs. Container `DATABASE_URL`**: Inside Docker Compose (`api` and `migrate`), PostgreSQL is reachable at hostname `db:5432`. If you run `alembic`, `app.cli`, or `pytest` directly from your host machine against the exposed `127.0.0.1:5432` port, set `DATABASE_URL=postgresql+psycopg://ecom_dev:<POSTGRES_PASSWORD>@localhost:5432/ecom_dev` in your host shell environment.

### Step 3: Build & Start the Baseline Stack

```bash
docker compose up --build
```

Startup dependency order enforced by [compose.yaml](../../compose.yaml):
1. `db` (`postgres:16-alpine`) starts and persists data in named volume `pgdata`.
2. `db` passes `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`.
3. `migrate` builds from [backend.Dockerfile](../../infra/docker/backend.Dockerfile), runs `alembic upgrade head`, and exits (`restart: "no"`).
4. `api` starts (`uvicorn app.main:app --host 0.0.0.0 --port 8000`) only after `db` is healthy **and** `migrate` completes with exit code `0`.
5. `web` starts (`node server.js` standalone output) only after `api` passes `/api/v1/health`.

Local endpoints once running:
- **Storefront & Admin UI**: `http://localhost:3000`
- **FastAPI Health / API**: `http://localhost:8000/api/v1/health`
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
| `api` | [backend.Dockerfile](../../infra/docker/backend.Dockerfile) | `./backend` | `127.0.0.1:8000:8000` | Python `urllib.request` on `http://127.0.0.1:8000/api/v1/health` (`HTTP 200`) | `db` (`service_healthy`), `migrate` (`service_completed_successfully`) |
| `web` | [frontend.Dockerfile](../../infra/docker/frontend.Dockerfile) | `./frontend` | `127.0.0.1:3000:3000` | Node `http.get` on `http://127.0.0.1:3000/` (`HTTP 2xx/3xx`) | `api` (`service_healthy`) |

### Container Hardening & Build Notes

- **Loopback-Only Dev Port Bindings (`L-05`)**:
  - `db`, `api`, and `web` bind strictly to `127.0.0.1` (`127.0.0.1:5432`, `127.0.0.1:8000`, `127.0.0.1:3000`) in [compose.yaml](../../compose.yaml) so local development/sandbox containers never listen on `0.0.0.0` LAN interfaces.
- **Layer Caching (`L-04`) & Non-root Execution**:
  - [backend.Dockerfile](../../infra/docker/backend.Dockerfile) copies `requirements.txt` and `pyproject.toml` and installs dependencies into `/opt/venv` **before** `COPY . /build`, preserving the Docker dependency layer cache when only application code changes. Runtime runs as `appuser` (`UID 10001`, `nologin`) without compilers.
  - [frontend.Dockerfile](../../infra/docker/frontend.Dockerfile) runs as `nextjs` (`UID 10001`) on `node:22-alpine`, copying the `output: "standalone"` runtime bundle (`server.js`, `.next/static`, `public`, and minimal traced `node_modules`) without shipping dev dependencies such as `vitest` or `@playwright/test`.
- **Trusted Proxy IP & Uvicorn Proxy-Headers (`H-04`)**:
  - App-level `resolve_client_ip` in [security.py](../../backend/app/core/security.py) validates the TCP peer IP (`request.client.host`) against `TRUSTED_PROXY_IPS` before parsing `X-Forwarded-For` right-to-left, regardless of whether Uvicorn `--proxy-headers` is enabled.
  - Keep the default `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]` (and **never** pass `--forwarded-allow-ips=*`, which overwrites `request.client.host` and bypasses peer-IP trust verification). If `--proxy-headers` is required behind a TLS-terminating reverse proxy for scheme/host forwarding, pass `--forwarded-allow-ips=<exact-proxy-ip-or-cidr>` matching `TRUSTED_PROXY_IPS`.
- **No `curl` Required**: Health checks use Python `urllib.request` and Node `http.get` built-ins to keep minimal runtime images free of extra utilities.
- **Browser vs. SSR URL Split**:
  - `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1` is passed as a Docker build arg (and CSP `connect-src` origin) so browser requests target `localhost:8000` (same-site as `localhost:3000` for `SameSite=Lax` HTTP-only cookies).
  - `INTERNAL_API_BASE_URL=http://api:8000/api/v1` is passed as a runtime env var for Next.js SSR fetches inside the Compose network (`http://127.0.0.1:8000/api/v1` when running on a single host outside Docker).
- **Docker Build Context Hygiene (`H-07`)**:
  - [backend/.dockerignore](../../backend/.dockerignore) excludes `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.env*`, and `tests/`.
  - [frontend/.dockerignore](../../frontend/.dockerignore) excludes `node_modules/`, `.next/`, `out/`, `test-results/`, `playwright-report/`, `.env`, `.env*`, `tests/`, `e2e/`, `*.tsbuildinfo`, `.git/`, and `coverage/`.

---

## 4. Database Migration, Bootstrap CLI & Seeding Workflows

Per `ARCH-001`, `Base.metadata.create_all()` is forbidden in application runtime. All schema changes go through Alembic.

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

Expected output: a single revision line (`0003_phase2_indexes_and_cleanup (head)`). Multiple heads fail CI in `backend-ci`.

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
4. In staging/production, prefer expand-and-contract migrations over destructive `downgrade` operations on populated tables.

### Administrative User Bootstrap (`app.cli create-user`)

Implemented in [cli.py](../../backend/app/cli.py). Use `create-user` to bootstrap the initial administrator or update/reactivate an account (password length `10..128` characters; hashes with Argon2id, logs `CLI_USER_CREATED` / `CLI_USER_UPDATED` to `audit_logs`, and revokes active `auth_sessions` on updates):

```bash
# Host execution (from backend/) or via Docker Compose (`docker compose run --rm api ...`)
ADMIN_BOOTSTRAP_PASSWORD='<strong-10-to-128-char-password>' \
  python -m app.cli create-user \
    --email admin@example.com \
    --full-name "Store Administrator" \
    --role admin \
    --password-env ADMIN_BOOTSTRAP_PASSWORD
```

Supported flags:
- `--role`: `customer` | `staff` | `admin` (defaults to `customer`)
- `--password-env <VAR>` *(strongly recommended)*: reads password from the specified environment variable
- `--update-if-exists`: updates `full_name`, `role`, `password_hash`, sets `is_active=true`, and revokes all existing sessions for that email

> **Security Warning (`BE-004` `INFO-2`)**: Always prefer `--password-env` over `--password`. Passing `--password <plaintext>` exposes credentials in OS process argument listings (`/proc/<pid>/cmdline`, `ps`, Windows process tools) and shell command history.

### Non-Production Demo Catalog & Accounts (`app.cli seed-demo`)

Idempotently seeds the 3 Atelier categories and 8 demo products (creating initial `InventoryStock` + `INITIAL_SEED` ledger entries only when variants are newly inserted):

```bash
# Catalog only (safe for non-production preview/dev):
python -m app.cli seed-demo

# Catalog + demo accounts (admin@atelier.example, staff@atelier.example, customer@atelier.example):
DEMO_ADMIN_PASSWORD='<strong-unique-password>' \
DEMO_STAFF_PASSWORD='<strong-unique-password>' \
DEMO_CUSTOMER_PASSWORD='<strong-unique-password>' \
  python -m app.cli seed-demo --with-demo-users
```

> **Fail-Closed Production Guard & Default Password Warning (`BE-004` `INFO-1`)**:
> 1. `seed-demo` **hard-fails with exit code `1`** whenever `ENVIRONMENT=production` (`Error: Refusing to run seed-demo when ENVIRONMENT=production.`).
> 2. When `--with-demo-users` is passed and `DEMO_ADMIN_PASSWORD`, `DEMO_STAFF_PASSWORD`, or `DEMO_CUSTOMER_PASSWORD` are unset, `cli.py` falls back to repository default demo passwords in non-production environments. **Always override all three `DEMO_*_PASSWORD` environment variables with strong unique passwords whenever running `--with-demo-users` outside disposable local development** (such as staging or shared test environments).

### Reservation Sweeper & Expired Record Cleanup (`app.cli`)

- **Reservation Sweeper (`sweep-expired-reservations`)**:
  - By default, FastAPI runs an in-process background sweeper every `RESERVATION_SWEEPER_INTERVAL_SECONDS` (`60` seconds in `.env.example`; set `0` to disable if running externally via cron/job).
  - Manual/scheduled one-shot execution:
    ```bash
    python -m app.cli sweep-expired-reservations --batch-size 100 --max-batches 20
    ```
- **Expired Session & Cart Cleanup (`cleanup-expired-records`, `M-08`)**:
  - Purges expired/revoked `auth_sessions` and unreferenced expired `carts` in bounded batches using the indexes introduced in migration `0003_phase2_indexes_and_cleanup`:
    ```bash
    python -m app.cli cleanup-expired-records --retention-days 30 --batch-size 500 --max-batches 20
    ```

---

## 5. Secret Management & Fail-Closed Configuration Rules

1. **Never Commit Secrets**: `.env` is gitignored. `.env.example` contains only `CHANGE_ME_DEV_ONLY_*` placeholders.
2. **No Hardcoded Fallbacks in `compose.yaml`**: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`, `CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, and `PAYMENT_WEBHOOK_SECRET` use `${VAR:?...}` interpolation so missing variables fail immediately during Compose parsing.
3. **Production Fail-Closed Invariants** (enforced by [config.py](../../backend/app/core/config.py)):
   When `ENVIRONMENT=production`, backend startup **aborts** if any of the following are true:
   - `PAYMENT_SANDBOX_MODE=true`
   - `COOKIE_SECURE=false`
   - `ALLOWED_ORIGINS` is empty, non-HTTPS, or contains wildcard `*`
   - `DATABASE_URL` does not use `postgresql://` / `postgresql+psycopg://` or contains `change_me` / `ci_test_only`
   - Any of `CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, or `PAYMENT_WEBHOOK_SECRET` is `< 32` characters, has leading/trailing whitespace, reuses another secret, or contains `change_me` / `ci_test_only`
4. **Browser Token Hygiene**: Authentication (`ecom_session`), CSRF (`ecom_csrf`), guest cart (`ecom_guest_cart`), and guest order (`ecom_guest_orders`) rely on HTTP-only / signed cookies and `X-CSRF-Token` headers per `ARCH-001`; never store secrets or session tokens in `localStorage` or `sessionStorage`.

---

## 6. Local Verification Runners

### Backend Static Gates & Fast Unit/API Suite

From `backend/`:

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py
.\.venv\Scripts\alembic.exe heads
.\.venv\Scripts\python.exe -m pytest -v
```

When `TEST_DATABASE_URL` is unset, `pytest -v` runs the fast SQLite `StaticPool` + `TestClient` suite (`161 passed, 4 skipped`).

### Isolated Disposable PostgreSQL Pytest Runner

Run the complete `165`-test backend suite (including the 4 live PostgreSQL migration, constraint, concurrency, and analytics/INET parity tests) without touching any host database via [run_disposable_pg_pytest.py](../../backend/tests/run_disposable_pg_pytest.py):

```powershell
# From backend/ (uses %PG_BIN_DIR% or defaults to E:\databasesql\bin):
.\.venv\Scripts\python.exe tests\run_disposable_pg_pytest.py
```

The runner creates a temporary PostgreSQL cluster under `%TEMP%` on a random non-`5432` loopback port, provisions least-privilege `ecom_test_role` + `ecom_test_db`, injects `TEST_DATABASE_URL` into the child `pytest` process only, executes `pytest -v` (`165 passed, 0 skipped` verified on PostgreSQL 18.6), shuts down the cluster, verifies zero lingering PIDs, and deletes the temporary cluster directory.

### Frontend Static, Unit & Live Full-Stack E2E Runners

From `frontend/`:

```powershell
npm run lint
npx tsc --noEmit
npm run build
npm test
```

- `npm test` executes Vitest (`60 passed` across 5 files under `frontend/tests/`).
- `npm run build` outputs the standalone server under `frontend/.next/standalone/`.

To execute the full-stack Playwright Chromium E2E suite ([storefront-and-admin.spec.ts](../../frontend/e2e/storefront-and-admin.spec.ts)) locally against a disposable PostgreSQL cluster, live `uvicorn`, and live `next start` (requires prebuilt `frontend/.next` and free ports `8000` & `3000`):

```powershell
# From frontend/ (using backend venv Python):
..\backend\.venv\Scripts\python.exe e2e\run_e2e_stack.py
```

Verified `4 passed` in Chromium covering customer checkout + sandbox payment + webhook confirmation, guest HttpOnly order cookie reload + self-cancel, staff RBAC + fulfillment + admin analytics/refund/restock/audit diff, and cross-context oversell guard.

---

## 7. Windows Subprocess & Disposable PostgreSQL Operational Safeguards

The local runners ([run_disposable_pg_pytest.py](../../backend/tests/run_disposable_pg_pytest.py) and [run_e2e_stack.py](../../frontend/e2e/run_e2e_stack.py)) encode mandatory operational rules learned from Windows process-management deadlocks and host database isolation hazards. Any future Windows automation or local test harness **must** preserve these safeguards:

1. **Never Use `stdout=subprocess.PIPE` / `stderr=subprocess.PIPE` for Daemon Launchers**:
   - On Windows, detached/background daemons spawned by `pg_ctl start`, `uvicorn`, or Node (`next start`) inherit open write pipe handles from the parent process. Even after `pg_ctl.exe` exits, the background `postgres.exe` daemon keeps the pipe write end open, causing `subprocess.run(..., capture_output=True)` or `proc.communicate()` to deadlock waiting for EOF until timeout.
2. **Mandatory Log-File Redirection with `stdin=subprocess.DEVNULL, close_fds=True`**:
   - Redirect daemon and tool output (`initdb`, `pg_ctl start`, `pg_ctl stop`, `alembic`, `seed-demo`, `uvicorn`, `next start`) to dedicated binary log files (`stdout=log_fh, stderr=subprocess.STDOUT`) with `stdin=subprocess.DEVNULL` and `close_fds=True`, closing parent file handles deterministically in `finally:`.
3. **Explicit `encoding="utf-8", errors="replace"` for Short-Lived Captured CLI Output**:
   - Windows CLI tools (`psql.exe`, `tasklist.exe`) can emit localized system code-page bytes (e.g., CP1251/CP866/CP1252) that crash Python's `_readerthread` with `UnicodeDecodeError` under default locale decoding. Always pass `encoding="utf-8", errors="replace"` whenever capturing stdout/stderr from short-lived utility commands.
4. **Explicit Per-Step Timeouts, Bounded Readiness Polling, Flushed Logs & Overall Watchdogs**:
   - Never issue unbounded `subprocess.run()` or `proc.wait()`. Enforce per-step timeouts on every stage (`initdb`, `pg_ctl`, `alembic`, `seed-demo`, `pytest`, `playwright`).
   - Poll readiness with bounded deadlines: `pg_isready` + `psql -tAc "SELECT version();"` for PostgreSQL, and `urllib.request` with `ProxyHandler({})` (`NO_PROXY=127.0.0.1,localhost`) plus premature `proc.poll()` checks for `/api/v1/health` and `/`.
   - Flush progress messages immediately (`flush=True`) and arm an overall `threading.Timer` watchdog so stalled runs abort cleanly rather than hanging CI/agent sessions.
5. **Strict Host Database & Port Protection**:
   - Never connect to, modify, or stop `127.0.0.1:5432` or `E:\databasesql\data`.
   - Assert every temporary cluster/run directory resolves strictly under `%TEMP%` (`tempfile.gettempdir()`) and outside `E:\databasesql\data`.
   - Strip ambient `PG*`, `DATABASE_URL`, `TEST_DATABASE_URL`, `POSTGRES_*`, and HMAC secret variables before spawning isolated subprocesses.
   - Bind disposable PostgreSQL to an ephemeral loopback port (`127.0.0.1:0` with explicit `!= 5432` check), fail fast (`_assert_port_free`) if `8000` or `3000` are already in use rather than killing unrelated host processes, and connect tests via a least-privilege role (`NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION`).
6. **Guaranteed `try/finally` Process-Tree Teardown, PID Liveness Verification & Redacted Diagnostics**:
   - In `finally:`, cancel the watchdog, terminate `Next.js` and `Uvicorn` process trees (`taskkill /PID <pid> /T /F` on Windows), execute `pg_ctl stop`, and clean up the temporary directory.
   - Explicitly verify every recorded PID (`postmaster.pid`, `uvicorn`, `next start`) has exited (`_is_pid_running` via `OpenProcess`/`GetExitCodeProcess` and `tasklist` fallback), force-killing **only** the verified temporary PID if still alive and failing the exit code if any temporary process lingers.
   - On failure or watchdog expiration, scrub all generated secrets/passwords/DB URLs (`[REDACTED]`) before preserving logs to `%TEMP%\ecom_*_diag_*` and printing log tails.

---

## 8. CI Pipeline Guide (`.github/workflows/ci.yml`)

The GitHub Actions workflow ([ci.yml](../../.github/workflows/ci.yml)) runs on `push` to `main` and all `pull_request` events across five coordinated jobs:

1. **`detect`**: Verifies presence of `backend/` and `frontend/` manifests.
2. **`ops-validate`** (`needs: detect`):
   - Parses `compose.yaml` and `.github/workflows/ci.yml` with `PyYAML`.
   - Verifies `compose.yaml` defines only the 4 baseline services (`db`, `migrate`, `api`, `web`).
   - Verifies `.env` is untracked in Git and `.env.example` defines all 18 required keys (plus optional `TRUSTED_PROXY_IPS`) with `CHANGE_ME_DEV_ONLY_` secret placeholders.
   - Runs `docker compose --env-file .env.example config --quiet`.
   - Builds `infra/docker/backend.Dockerfile` and `infra/docker/frontend.Dockerfile` (Node 22 Alpine).
3. **`backend-ci`** (`needs: detect`, `postgres:16-alpine` service):
   - Provisions least-privilege `ecom_test` role (`NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION`) and `ecom_test` database on PostgreSQL 16.
   - Exports both `DATABASE_URL` and `TEST_DATABASE_URL` (`postgresql+psycopg://ecom_test:ecom_test_password@localhost:5432/ecom_test`) so all live PostgreSQL 16 migration, constraint, concurrency, and analytics/INET tests execute instead of skipping.
   - Runs `python -m pip check`, `ruff check .`, `mypy app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py`, single-head `alembic heads` check (`0003_phase2_indexes_and_cleanup`), `alembic upgrade head`, and `pytest -v`.
4. **`frontend-ci`** (`needs: detect`, Node.js 22):
   - Runs `npm ci`, `npm ls --omit=dev --depth=0`, `npm run lint`, `npx tsc --noEmit`, `npm run build`, and `npm test` (Vitest unit/component suite).
5. **`e2e-ci`** (`needs: [backend-ci, frontend-ci]`, `postgres:16-alpine` service, Python 3.12 + Node.js 22):
   - Generates masked ephemeral CI HMAC secrets and demo passwords (`::add-mask::`), provisions least-privilege `ecom_test` on PostgreSQL 16, runs `alembic upgrade head`, and runs `python -m app.cli seed-demo --with-demo-users`.
   - Installs frontend dependencies (`npm ci`), installs Playwright Chromium (`npx playwright install --with-deps chromium`), and builds the Next.js standalone bundle (`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1 npm run build`).
   - Launches FastAPI (`uvicorn app.main:app --host 127.0.0.1 --port 8000`) and Next.js standalone (`node .next/standalone/server.js` with `.next/static` and `public` copied into `.next/standalone/`, `HOSTNAME=127.0.0.1`, `PORT=3000`, `INTERNAL_API_BASE_URL=http://127.0.0.1:8000/api/v1`) using log-file redirection and `</dev/null`, polls `/api/v1/health` and `/` with bounded `curl --max-time 3` loops, and runs `npx playwright test` (`E2E_BASE_URL=http://localhost:3000`).
   - Uploads `frontend/test-results/` and `/tmp/e2e_*.log` on `failure()`, and stops background server PIDs unconditionally in `if: always()`.

### Local Static Validation Commands

Verify YAML syntax and Compose configuration locally without starting containers:

```powershell
.\backend\.venv\Scripts\python.exe -c "import yaml, pathlib; [yaml.safe_load(pathlib.Path(p).read_text(encoding='utf-8')) for p in ('compose.yaml', '.github/workflows/ci.yml')]; print('YAML OK')"
docker compose --env-file .env.example config --quiet
```
