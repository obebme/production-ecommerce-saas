# Production Hardening & Deployment Runbook (`HARDEN-OPS-01`)

> **Status Notice**: This repository is a **single-tenant modular-monolith** e-commerce platform. Until a production-ready payment adapter is implemented, security-audited, and live-verified against a real PSP sandbox/test account, do **not** treat this stack as PCI-certified or ready to process real card charges.

---

## 1. Local Coexistence & Port Architecture

To avoid collisions with any PostgreSQL instance already running on a developer workstation (`127.0.0.1:5432`), [compose.yaml](../../compose.yaml) parameterizes all host-published loopback bindings with safe defaults documented in [.env.example](../../.env.example):

| Service | Container Port | Default Host Binding | Env Override | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `db` | `5432` | `127.0.0.1:5433` | `POSTGRES_HOST_PORT` | Avoids host PG `5432` collision |
| `api` | `8000` | `127.0.0.1:8000` | `API_HOST_PORT` | `stop_grace_period: 20s` (`--timeout-graceful-shutdown 15`) |
| `web` | `3000` | `127.0.0.1:3000` | `WEB_HOST_PORT` | `stop_grace_period: 20s` |
| `redis` *(optional)* | `6379` | `127.0.0.1:6379` | `REDIS_HOST_PORT` | Enabled only with `--profile redis` |

- **Host tooling against Compose DB**: When running host-side `alembic` or `pytest` against Compose `db`, target `localhost:${POSTGRES_HOST_PORT}` (default `5433`), **never** `5432`.
- **Starting optional Redis locally**:
  ```bash
  # Ensure REDIS_URL=redis://redis:6379/0 in .env when using the redis profile
  docker compose --profile redis up --build
  ```

---

## 2. Production Topology (`compose.prod.yaml` & Caddy)

[compose.prod.yaml](../../compose.prod.yaml) isolates all backend/data services onto an `internal: true` Docker bridge network (`172.28.0.0/24`) with **zero published host ports** on `db`, `redis`, `migrate`, `api`, and `web`.

```text
Internet (80/443)
   │
   ▼
[caddy] (edge + internal networks, TLS/HSTS, blocks /api/v1/metrics)
   ├── /api/* ──▶ [api:8000] (internal only, read_only, appuser UID 10001)
   │                 ├── [db:5432]    (internal only, ecom_app DML role)
   │                 └── [redis:6379] (internal only, read_only, volatile-ttl)
   └── /*     ──▶ [web:3000] (internal only, read_only, nextjs UID 10001)
```

### Key Container Hardening Controls

- **Zero Host DB/Redis Exposure**: Only `caddy` publishes ports (`80:80`, `443:443`, `443:443/udp`).
- **Hash-Verified Backend Builds**: [backend.Dockerfile](../../infra/docker/backend.Dockerfile) strictly requires `backend/requirements.lock` and installs via `pip install --no-cache-dir --require-hashes -r requirements.lock` without unhashed fallback.
- **Non-Root & Read-Only Runtimes**:
  - `api`, `migrate`, `web`, `redis`, and `caddy` run with `security_opt: ["no-new-privileges:true"]` and `read_only: true` with bounded `tmpfs` mounts (`/tmp`, plus `/app/.next/cache` on `web`).
  - `caddy` drops `ALL` Linux capabilities and adds only `NET_BIND_SERVICE`.
- **Health Probes & Graceful Shutdown**:
  - Container image `HEALTHCHECK` in [backend.Dockerfile](../../infra/docker/backend.Dockerfile) hits `GET /api/v1/health/live` (process liveness).
  - [compose.prod.yaml](../../compose.prod.yaml) `api` healthcheck hits `GET /api/v1/health/ready`, which verifies PostgreSQL connectivity and Redis reachability (`fail_closed_*` returns `503` if Redis is down).
  - Uvicorn shuts down with `--timeout-graceful-shutdown 15` inside a `20s` container `stop_grace_period`.

### Caddy TLS, HSTS & `TRUSTED_PROXY_IPS`

[infra/caddy/Caddyfile](../../infra/caddy/Caddyfile) configures:
- Automatic ACME HTTPS certificates for `{$SITE_DOMAIN}` and HTTP->HTTPS redirect.
- `Strict-Transport-Security "max-age=31536000; includeSubDomains"`, `X-Content-Type-Options "nosniff"`, `Referrer-Policy "strict-origin-when-cross-origin"`, and `-Server`.
- Public edge rejection (`404 Not Found`) for `/api/v1/metrics` and `/api/v1/metrics/*`.
- Reverse proxy routing `/api/*` -> `api:8000` and all other paths -> `web:3000`.

**Trusted Proxy Configuration**:
- `compose.prod.yaml` assigns `172.28.0.0/24` to the `internal` network and defaults `TRUSTED_PROXY_IPS` on `api` to `172.28.0.0/24`.
- FastAPI's `resolve_client_ip` (`backend/app/core/security.py`) validates the TCP peer IP (`request.client.host`) against `TRUSTED_PROXY_IPS` before parsing `X-Forwarded-For` right-to-left.
- **If placing an external CDN or cloud load balancer in front of Caddy**:
  1. Add Caddy's `servers { trusted_proxies static <CDN_OR_LB_CIDRS> }` global option so Caddy preserves the upstream client IP instead of overwriting `X-Forwarded-For` with the LB IP.
  2. Never add `0.0.0.0/0` to `TRUSTED_PROXY_IPS` (rejected by backend `Settings` validation).

---

## 3. Least-Privilege PostgreSQL Roles (`ecom_migrator` vs `ecom_app`)

[infra/postgres/init-roles.sql](../../infra/postgres/init-roles.sql) separates schema ownership from runtime application privileges:

| Role | Purpose | Env Variable | Privileges |
| :--- | :--- | :--- | :--- |
| `ecom_migrator` | Alembic schema migrations (`migrate` service) | `MIGRATION_DATABASE_URL` | `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION`; owns target database and `public` schema |
| `ecom_app` | Runtime FastAPI queries (`api` service) | `DATABASE_URL` | `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION`; `CONNECT`, `USAGE` on `public`, DML-only (`SELECT, INSERT, UPDATE, DELETE`) on tables + `USAGE` on sequences via `ALTER DEFAULT PRIVILEGES FOR ROLE ecom_migrator` |

### Bootstrap Sequence (Fresh Production Database)

1. Start `db` only first:
   ```bash
   docker compose -f compose.prod.yaml --env-file .env.prod up -d db
   ```
2. Generate distinct random passwords (`>= 24` chars) for `ecom_migrator` and `ecom_app` (never commit them; store in `.env.prod` or your secrets manager), then run `init-roles.sql`:
   ```bash
   docker compose -f compose.prod.yaml --env-file .env.prod exec -T db \
     psql -U "$POSTGRES_USER" -d postgres \
       -v target_db="$POSTGRES_DB" \
       -v migrator_password="$MIGRATOR_DB_PASSWORD" \
       -v app_password="$APP_DB_PASSWORD" \
     < infra/postgres/init-roles.sql
   ```
3. Verify `.env.prod` sets:
   ```env
   MIGRATION_DATABASE_URL=postgresql+psycopg://ecom_migrator:<MIGRATOR_DB_PASSWORD>@db:5432/<POSTGRES_DB>
   DATABASE_URL=postgresql+psycopg://ecom_app:<APP_DB_PASSWORD>@db:5432/<POSTGRES_DB>
   ```
4. Run migrations and bring up the full stack:
   ```bash
   docker compose -f compose.prod.yaml --env-file .env.prod up -d --build
   ```
5. Bootstrap the initial admin account (using `--password-env` so secrets stay out of process args):
   ```bash
   docker compose -f compose.prod.yaml --env-file .env.prod exec -e ADMIN_BOOTSTRAP_PASSWORD="$ADMIN_BOOTSTRAP_PASSWORD" api \
     python -m app.cli create-user \
       --email admin@example.com \
       --full-name "Store Administrator" \
       --role admin \
       --password-env ADMIN_BOOTSTRAP_PASSWORD
   ```

---

## 4. Distributed Redis Rate Limiter Tuning & Failure Modes

When `REDIS_URL` is configured (default `redis://redis:6379/0` in `compose.prod.yaml`), FastAPI uses `RedisSlidingWindowRateLimiter` (`backend/app/core/rate_limit.py`) backed by an atomic Lua sliding-window script with `0.5s` socket connect/read timeouts and `30s` health-check intervals.

- **Redis Container Configuration**:
  - Ephemeral state only (`--save "" --appendonly no`): rate-limit windows are short-lived and do not require disk persistence.
  - Bounded memory (`--maxmemory 256mb` in prod, `128mb` in dev) with `--maxmemory-policy volatile-ttl` so only TTL-bearing rate-limit keys are evicted under memory pressure.
  - Key namespace controlled by `RATE_LIMIT_KEY_PREFIX` (default `atelier:rl`).

- **Failure Modes (`RATE_LIMIT_FAILURE_MODE`)**:
  - `fail_closed_sensitive` *(recommended default)*:
    - If Redis errors occur, sensitive prefixes (`auth:`, `checkout:`, `payments:`, `orders:guest`) fail closed with `503 SERVICE_UNAVAILABLE` (`RATE_LIMITER_UNAVAILABLE`) to prevent brute-force or checkout abuse bypasses.
    - Non-sensitive routes (e.g., catalog browsing) fall back to the per-process `SlidingWindowRateLimiter`.
    - `GET /api/v1/health/ready` returns `503 REDIS_UNAVAILABLE`.
  - `fail_closed_all`:
    - Fails closed (`503`) on **all** rate-limited routes whenever Redis is unavailable; `GET /api/v1/health/ready` returns `503`.
  - `local_fallback`:
    - Degrades all routes to in-process sliding windows and reports `"redis": "degraded"` (`200 OK`) on `/api/v1/health/ready`. Use only as a deliberate temporary mitigation during single-instance degraded operation; never leave enabled in multi-replica production.

---

## 5. Observability, Structured Logging & Prometheus Metrics

- **Structured Logs (`LOG_FORMAT=json`)**:
  - `StructuredJsonFormatter` (`backend/app/core/observability.py`) emits single-line JSON logs containing `timestamp`, `level`, `logger`, `message`, and `request_id` while recursively redacting keys containing passwords, tokens, secrets, authorization headers, cookies, or database/Redis URLs.
- **Prometheus Metrics (`/api/v1/metrics`)**:
  - Controlled by `METRICS_ENABLED` (`true` by default) and `METRICS_BEARER_TOKEN` (`>= 32` characters, validated on startup in `staging`/`production`, compared with `hmac.compare_digest`).
  - Public requests to `/api/v1/metrics` are blocked (`404`) by [Caddyfile](../../infra/caddy/Caddyfile).
  - Attach your internal Prometheus scraper to the `internal` Docker network (or host-internal monitoring network) and scrape `http://api:8000/api/v1/metrics`:
    ```yaml
    scrape_configs:
      - job_name: atelier-api
        metrics_path: /api/v1/metrics
        scrape_interval: 15s
        authorization:
          type: Bearer
          credentials_file: /etc/prometheus/secrets/metrics_bearer_token
        static_configs:
          - targets: ["api:8000"]
    ```
  - Exported metrics include `http_requests_total`, `http_request_duration_seconds`, `db_pool_connections`, `checkout_events_total`, `payment_webhook_events_total`, `reservation_sweeper_runs_total`, `reservation_sweeper_reclaimed_total`, `reservation_sweeper_duration_seconds`, `rate_limit_decisions_total`, and `rate_limit_redis_errors_total`.

---

## 6. Backup & Verified Clean Restore Procedure

Always back up with `pg_dump -Fc` (PostgreSQL custom archive format) and periodically run clean restore drills against an isolated database.

### Creating a Backup

```bash
BACKUP_TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="backups/atelier_${BACKUP_TS}.dump"
mkdir -p backups

docker compose -f compose.prod.yaml --env-file .env.prod exec -T db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl \
  > "$BACKUP_FILE"

sha256sum "$BACKUP_FILE" > "${BACKUP_FILE}.sha256"
```

### Clean Restore Verification Drill (Isolated Database)

1. Verify archive integrity:
   ```bash
   sha256sum -c "${BACKUP_FILE}.sha256"
   pg_restore --list "$BACKUP_FILE" | head -n 30
   ```
2. Create an isolated verification database owned by `ecom_migrator` and restore into it:
   ```bash
   docker compose -f compose.prod.yaml --env-file .env.prod exec -T db \
     psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
       -c "DROP DATABASE IF EXISTS ecom_restore_verify;" \
       -c "CREATE DATABASE ecom_restore_verify OWNER ecom_migrator ENCODING 'UTF8';"

   docker compose -f compose.prod.yaml --env-file .env.prod exec -T db \
     pg_restore -U ecom_migrator -d ecom_restore_verify \
       --no-owner --no-acl --exit-on-error < "$BACKUP_FILE"
   ```
3. Re-apply `ecom_app` DML grants on the restored database (if restoring to a fresh cluster) and run post-restore verification queries:
   ```bash
   docker compose -f compose.prod.yaml --env-file .env.prod exec -T db \
     psql -U ecom_migrator -d ecom_restore_verify -v ON_ERROR_STOP=1 <<'SQL'
   SELECT version_num FROM alembic_version;
   SELECT
     (SELECT count(*) FROM users) AS users_cnt,
     (SELECT count(*) FROM products) AS products_cnt,
     (SELECT count(*) FROM product_variants) AS variants_cnt,
     (SELECT count(*) FROM inventory_stocks) AS stock_rows_cnt,
     (SELECT count(*) FROM orders) AS orders_cnt,
     (SELECT count(*) FROM payment_intents) AS payment_intents_cnt,
     (SELECT count(*) FROM audit_logs) AS audit_logs_cnt;
   SELECT count(*) AS invalid_stock_rows
   FROM inventory_stocks
   WHERE on_hand < 0 OR reserved < 0 OR reserved > on_hand;
   SQL
   ```
4. Confirm `invalid_stock_rows` is `0`, `alembic_version` matches the expected migration head, and drop `ecom_restore_verify` once verified:
   ```bash
   docker compose -f compose.prod.yaml --env-file .env.prod exec -T db \
     psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
       -c "DROP DATABASE IF EXISTS ecom_restore_verify;"
   ```

> **Production Cutover Restore**: Stop `web` and `api` (`docker compose -f compose.prod.yaml stop web api`) before restoring into the primary database, then run `migrate` (`alembic upgrade head`) and restart `api`, `web`, and `caddy`.

---

## 7. Checklist Before Enabling a Live Payment Provider

In the current codebase (`backend/app/modules/payments/provider.py`), only `sandbox_simpay` (`is_production_ready = False`) is registered. `Settings` (`backend/app/core/config.py`) **aborts startup** whenever `ENVIRONMENT=production` and `PAYMENT_SANDBOX_MODE=true` or `is_production_provider_registered(PAYMENT_PROVIDER)` is `False`.

Do **not** enable `ENVIRONMENT=production` or accept real customer payments until **all** of the following are complete:

1. **Implement & Register Production PSP Adapter**: Implement `PaymentProviderAdapter` (`create_intent`, `retrieve_intent`, `cancel_intent`, `refund`, `verify_and_parse_webhook`) with `is_production_ready = True` and register it in `get_default_payment_registry()`.
2. **Webhook Cryptography & Replay Defense**: Verify official PSP webhook signatures using constant-time HMAC/asymmetric verification, enforce timestamp tolerance, and keep idempotent `payment_webhook_events` deduplication intact.
3. **PCI Scope Minimization**: Use PSP-hosted checkout / tokenized Elements (`SAQ-A`); never accept, store, or log raw PAN/CVV/cardholder data in backend or frontend.
4. **Live Config & Secrets**: Provision production secrets (`CSRF_HMAC_SECRET`, `GUEST_ORDER_HMAC_SECRET`, `PAYMENT_WEBHOOK_SECRET`, `METRICS_BEARER_TOKEN`, DB passwords) in a secret manager, set `PAYMENT_SANDBOX_MODE=false`, `PAYMENT_PROVIDER=<adapter>`, `COOKIE_SECURE=true`, and HTTPS `ALLOWED_ORIGINS` / `NEXT_PUBLIC_SITE_URL`.
5. **Reconciliation & Refund Drills**: Verify payment confirmation, webhook retry idempotency, cancellation/expiry stock release, and admin partial/full refund flows against the PSP's live-equivalent test environment.
6. **Security & QA Sign-Off**: Re-run full CI (`secret-scan`, Trivy image scans, `backend-ci`, `frontend-ci`, `e2e-ci`), conduct final security review on the new payment adapter, and test backup/restore on staging.
