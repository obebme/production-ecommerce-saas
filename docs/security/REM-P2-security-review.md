# REM-P2-SEC — Phase 2 Production Hardening Security Review

- **Task ID**: `REM-P2-SEC`
- **Auditor**: `security` specialist ([55d38d14-2bec-4ef6-b7c8-f7434a1b7d0c](conversation://55d38d14-2bec-4ef6-b7c8-f7434a1b7d0c), strictly read-only; recorded by Orchestrator)
- **Target Baseline**: Phase 2 working tree diff vs `HEAD` `90816ec` across `backend/**`, `frontend/**`, `infra/**`, `compose.yaml`, `.env.example`, and `.github/workflows/ci.yml`
- **Upstream Verification Evidence Reviewed**: [REM-P2-verification.md](../qa/REM-P2-verification.md) (`ruff check .` 0 issues; `mypy` 0 issues / 55 files; `alembic heads` `0003_phase2_indexes_and_cleanup`; SQLite `192 passed, 4 skipped`; disposable PostgreSQL 18.6 `196 passed, 0 failed, 0 skipped`; Vitest `5 files / 69 passed`; `npm run lint` / `tsc --noEmit` / `next build` 21 routes clean; Playwright E2E `4 passed`; Docker image builds blocked locally because Docker daemon is stopped; exact `postgres:16-alpine` container parity pending CI/daemon)
- **Deferred Outside Phase 2 Scope**: `REM-P2-M09` (storefront SSR, dynamic metadata, `/assets/products/*` visual assets), `orders.refunded_at` accounting-period change (`L-06`), multi-replica shared rate limiter (`INFO-1`), and real PCI payment gateway integration (`Phase 3`)

## Verdict: **GO (Phase 2 Checkpoint Approved — 2 Low / 3 Info Non-Blocking Items Tracked)**

- **Findings Summary**: **0 Critical**, **0 High**, **0 Medium**, **2 Low**, **3 Info**.
- **Assessment**: All targeted Phase 2 findings (`H-04`, `H-07`, `M-03`, `M-04`, `M-05`, `M-07`, `M-08`, `M-10`, `L-02`–`L-06`) are cleanly implemented without introducing Critical, High, or Medium regressions. Two Low operational/defense-in-depth items and three Info caveats are noted below.

---

## 1. Findings (Low & Informational)

### [Low] SEC-REM-P2-01: Local `.env` values override `ENVIRONMENT` in `_resolve_environment_for_docs` only after validation failure, while `trusted_proxy_ips` / DB timeouts allow zero/empty config
- **Locations**:
  - `backend/app/main.py` — `_resolve_environment_for_docs` (`L149-L167`)
  - `backend/app/core/config.py` — `Settings.db_statement_timeout_ms` (`>= 0`, `L118`), `Settings.db_lock_timeout_ms` (`>= 0`, `L119`), `Settings.db_idle_in_transaction_timeout_ms` (`>= 0`, `L120`)
- **Description**:
  1. In `_resolve_environment_for_docs` (`main.py:L149-L166`), when `settings is None`, `get_settings()` succeeds cleanly for both env-var and `.env`-backed `ENVIRONMENT=production|staging`, disabling `/api/v1/docs`, `/api/v1/redoc`, and `/api/v1/openapi.json`. When `.env` sets `ENVIRONMENT=production` alongside invalid secrets, `get_settings()` raises `ValidationError`, `L156` checks `os.environ`, and `L160` reads `.env` via `DotEnvSettingsSource` (disabling docs properly, while deferring startup `ValidationError` fail-closed crash until FastAPI `lifespan` runs at `L180`).
  2. Timeout bounds (`ge=0`) permit `0` to disable timeouts in local debugging without warning or rejecting `0` in `production`/`staging`.
- **Impact**: Low — docs remain disabled and `lifespan` still fails closed on startup; setting `0` timeouts in production could remove query/lock bounds if misconfigured.
- **Recommendation**: Keep operational defaults (`30000`/`10000`/`60000`) documented in runbooks and consider enforcing positive timeouts (`> 0`) in `"production"` in a future tightening pass.

---

### [Low] SEC-REM-P2-02: Reverse-proxy IP extraction ignores invalid entries if `trusted_proxy_ips` is passed directly bypassing `Settings`
- **Locations**:
  - `backend/app/core/security.py` — `resolve_client_ip` (`L196-L200`, `L212-L224`)
  - `backend/app/core/config.py` — `_validate_trusted_proxy_ips` (`L142-L165`)
- **Description**:
  `Settings._validate_trusted_proxy_ips` rejects `"*"`, whitespace/control chars, and invalid CIDRs at startup (`config.py:L142-L165`). Inside `resolve_client_ip`, right-to-left parsing (`security.py:L212-L224`) rejects malformed/blank `X-Forwarded-For` hops by falling back safely to `peer_ip`. However:
  1. If any internal caller invokes `resolve_client_ip(req, trusted_proxy_ips=...)` bypassing `Settings`, invalid CIDR entries hit `except ValueError: continue` (`L199-L200`) instead of raising.
  2. Behind an L7 proxy terminating TLS via loopback or bridge IP (`127.0.0.1` / `172.18.0.0/16`), operators **must** populate `TRUSTED_PROXY_IPS`; otherwise (`TRUSTED_PROXY_IPS=` empty default), all clients collapse to `peer_ip` (fail-closed safe default per `H-04`).
- **Impact**: Low — cannot be exploited to spoof IPs from untrusted peers, and falls back to safe `peer_ip` rather than trusting unverified headers.
- **Recommendation**: Document exact `TRUSTED_PROXY_IPS` CIDR configuration per deployment topology in the release runbook and verify rightmost untrusted hop extraction in staging smoke tests.

---

### [Info] SEC-REM-P2-03: `CartDTO` tax preview vs `estimated_total_cents` semantic split & client fallback
- **Locations**:
  - `backend/app/modules/cart/service.py` (`build_cart_dto`, `_empty_cart_dto`)
  - `frontend/src/components/storefront/checkout-view.tsx` (`computePreviewTaxCents`, `resolveCartPreviewTotals`)
- **Verification**: `CartDTO` returns server-computed `tax_rate_bps` and `estimated_tax_cents` while keeping `estimated_total_cents = subtotal_cents + estimated_shipping_cents` (backward-compatible) and displaying `subtotal + shipping + tax` in checkout summary. Order creation (`POST /api/v1/checkout/orders`) remains 100% server-authoritative (`CreateCheckoutOrderRequest` uses `extra="forbid"`). Fallback `BASELINE_TAX_RATE_BPS = 800` in `checkout-view.tsx` is display-only when an external mock omits `tax_rate_bps`.

### [Info] SEC-REM-P2-04: `M-07` & `M-10` Next.js headers vs nonce CSP and backend `no-store` coverage
- **Locations**:
  - `frontend/next.config.mjs` (`L2-L90`)
  - `backend/app/main.py` (`SensitiveRouteNoStoreMiddleware`, `L115-L147`)
- **Verification**:
  - `next.config.mjs` sets `Cache-Control: no-store` on `/admin/:path*`, `/account/:path*`, `/orders/:path*`, `/checkout/:path*`, `/login`, and `/register`, and fixes `deriveApiConnectOrigin()` so relative `NEXT_PUBLIC_API_BASE_URL=/api/v1` produces strict `connect-src 'self'`.
  - Next.js CSP still permits `script-src 'self' 'unsafe-inline'` in production (`next.config.mjs:L28`; deferred future nonce middleware enhancement per `SEC-001` `INFO-1`).
  - `SensitiveRouteNoStoreMiddleware` enforces `Cache-Control: no-store` on `/api/v1/{auth,cart,checkout,orders,payments,admin}` including error responses without touching public catalog caching.

### [Info] SEC-REM-P2-05: Blocked local Docker builds & `postgres:16-alpine` runtime verification
- **Location**: `infra/docker/backend.Dockerfile`, `infra/docker/frontend.Dockerfile`, `compose.yaml`, `.github/workflows/ci.yml`
- **Verification**: Static YAML (`PyYAML`), `docker-compose --env-file .env.example config --quiet`, `127.0.0.1` loopback port bindings, `backend.Dockerfile` layer caching reorder, and `frontend/.dockerignore` exclusion rules are verified by inspection. Actual `docker build`, `docker compose up --build`, and remote `postgres:16-alpine` CI execution remain pending an active Docker daemon / GitHub Actions runner (`BLOCKED` locally).

---

## 2. Verified Phase 2 Security & Hardening Controls

1. **`H-04` Trusted Proxy IP Anti-Spoofing (`config.py`, `security.py`, routers)**: `TRUSTED_PROXY_IPS` rejects `*` and malformed CIDRs; `resolve_client_ip` ignores `X-Forwarded-For` unless `request.client.host` is inside `trusted_proxy_ips`, walking right-to-left and failing closed to `peer_ip`; propagates to rate limits, guest cap, `auth_sessions.ip_address`, and `audit_logs.ip_address`.
2. **`M-03` & `M-04` Fail-Closed Staging & OpenAPI Suppression (`config.py`, `main.py`, `cli.py`)**: `_resolve_environment_for_docs` reads explicit `Settings`, `get_settings()`, process env, and `.env` (`DotEnvSettingsSource`), disabling OpenAPI/Swagger/ReDoc in both `production` and `staging`. `staging` enforces HTTPS origins, `COOKIE_SECURE=true`, PostgreSQL DB, non-placeholder creds/secrets, and distinct `>= 32`-char HMAC secrets; `seed-demo --with-demo-users` refuses `staging` without all three `DEMO_*_PASSWORD` env vars.
3. **`M-05` & `M-08` Resource Bounding, Engine Disposal & Safe Cleanup (`database.py`, `main.py`, `0003_phase2_indexes_and_cleanup.py`, `identity/service.py`, `cart/service.py`, `cli.py`)**: PG pool (`5`/`10`/`15s`/`1800s`) and session timeouts (`30s`/`10s`/`60s`); clean shutdown disposal in `lifespan` and `clear_db_caches()`; migration `0003` adds 6 indexes cleanly; `cleanup-expired-records` deletes in bounded batches excluding active rows and `Order`-linked carts.
4. **`M-07` & `M-10` Cache Hygiene, CSP, Timeout & Auth Error Separation (`main.py`, `identity/dependencies.py`, `orders/service.py`, `next.config.mjs`, `client.ts`, `auth-provider.tsx`, `checkout-view.tsx`)**: Backend/frontend `no-store` headers; consistent `AUTH_REQUIRED` vs `AUTH_SESSION_EXPIRED`; `15s` `REQUEST_TIMEOUT` bound; server-derived `CartDTO` tax preview.
5. **`H-07` & `L-02`–`L-06` Hygiene & Validation (`frontend/.dockerignore`, `backend.Dockerfile`, `compose.yaml`, `security.py`, `catalog/schemas.py`, `catalog/service.py`, `guest-order-memory.ts`)**: Docker build isolation, `127.0.0.1` Compose bindings, default-port & userinfo origin/Referer checks, 3-pass `unquote` traversal check, unused `python-multipart` removed, and `clearAllGuestOrderTokens()` on logout.
