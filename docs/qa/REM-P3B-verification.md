# REM-P3B-QA — Phase 3B Regression, Live PostgreSQL 18.6, Playwright E2E & Vulnerability Audit Verification

- **Task ID**: `REM-P3B-QA` (`backend/tests/**`, `frontend/tests/**`, `frontend/e2e/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Dependencies**: `REM-P3B-BE`, `REM-P3B-FE`, `REM-P3B-OPS`
- **Production Source Integrity**: Zero edits made to `backend/app/**`, `frontend/src/**`, `frontend/next.config.mjs`, `infra/**`, or `compose.yaml` by QA. Only authorized test and QA files were modified/created:
  - `backend/tests/test_csrf_and_rbac.py`
  - `backend/tests/test_config_and_health.py`
  - `frontend/tests/storefront-ssr.test.tsx`
  - `frontend/tests/ui-and-shells.test.tsx`
  - `frontend/e2e/storefront-and-admin.spec.ts`
  - `docs/qa/REM-P3B-verification.md`

---

## 1. Full Verification Suite Summary

### Backend (`e:\fullstack_work\backend`)

| # | Command | Scope & Runner | Outcome |
| :-: | :--- | :--- | :--- |
| 1 | `.\.venv\Scripts\ruff.exe check .` | Ruff lint across `backend/` | `All checks passed!` (exit `0`) |
| 2 | `.\.venv\Scripts\mypy.exe app tests/run_disposable_pg_pytest.py tests/test_admin_and_analytics.py` | Mypy strict typecheck (`55` source files) | `Success: no issues found in 55 source files` (exit `0`) |
| 3 | `.\.venv\Scripts\alembic.exe heads` | Alembic migration head check | `0003_phase2_indexes_and_cleanup (head)` (exit `0`) |
| 4 | `.\.venv\Scripts\pytest.exe -v` (`BypassSandbox: true`) | SQLite `StaticPool` + FastAPI `TestClient` | **`212 passed, 4 skipped, 1 warning in 53.70s`** (exit `0`; 4 skipped because `TEST_DATABASE_URL` unset) |
| 5 | `.\.venv\Scripts\python.exe tests/run_disposable_pg_pytest.py` (`BypassSandbox: true`) | Disposable PostgreSQL `18.6` cluster (`127.0.0.1:56804`, `pid=47284`, least-privilege `ecom_test_role`) | **`216 passed, 0 failed, 0 skipped, 1 warning in 78.65s (0:01:18)`** (exit `0`; clean PG teardown verified: `Temp PID 47284 exited; zero temporary PostgreSQL processes remain.`) |
| 6 | `uvx pip-audit -r requirements.lock --disable-pip` (`BypassSandbox: true`) | Python pinned supply-chain lockfile (`requirements.lock`) | `No known vulnerabilities found` (exit `0`) |

### Frontend (`e:\fullstack_work\frontend`)

| # | Command | Scope & Runner | Outcome |
| :-: | :--- | :--- | :--- |
| 7 | `npm.cmd run lint` | `next lint` (`frontend/`) | `✔ No ESLint warnings or errors` (exit `0`) |
| 8 | `npx.cmd tsc --noEmit` | TypeScript strict check (`frontend/`) | `0` errors (exit `0`) |
| 9 | `npm.cmd test` | Vitest `v4.1.12` (`jsdom`, `frontend/tests/*.test.ts*`) | **`6 files passed (6), 87 tests passed (87)`** (`0` failed, `0` skipped) in `10.97s` |
| 10 | `npm.cmd audit --audit-level=high` (`BypassSandbox: true`) | npm high/critical dependency audit | `found 0 vulnerabilities` (exit `0`) |
| 11 | `npm.cmd run build` (`BypassSandbox: true`) | `next build` (`Next.js 15.5.29`, production bundle) | `21` routes built (`16` static `○`, `5` dynamic `ƒ`: `/`, `/products`, `/products/[slug]`, `/orders/[orderNumber]`, `/admin/orders/[orderNumber]`; zero `metadataBase` warnings, exit `0`) |
| 12 | `..\backend\.venv\Scripts\python.exe e2e\run_e2e_stack.py` (`BypassSandbox: true`) | Live Chromium Playwright E2E + disposable PG `18.6` (`port=56433`, `pid=48252`) + `uvicorn` (`pid=46880`) + `next start` (`pid=27940`) | **`5 passed (33.7s)`** (`0` failed, `0` skipped, exit `0`; `Clean teardown verified; zero temporary processes remain.`) |

### Vitest Breakdown (`6` files / `87` passed)

- `tests/api-client.test.ts`: `8` passed
- `tests/format-and-security.test.ts`: `10` passed
- `tests/storefront-ssr.test.tsx`: `16` passed *(updated `SEC-M09-02` + added `SEC-M09-01`, `resolveSiteUrl` / `metadataBase`, and `A11Y-M09-01`)*
- `tests/ui-and-shells.test.tsx`: `19` passed *(added `SEC-M09-03` `/cart/:path*` `no-store` header check + root/storefront `not-found` `<main>` landmark checks)*
- `tests/checkout-and-orders.test.tsx`: `20` passed
- `tests/admin-console.test.tsx`: `14` passed

---

## 2. Added & Updated Regression Coverage

### A. Backend Regression Tests

1. **`backend/tests/test_csrf_and_rbac.py` (`SEC-REM-P2-01`)**:
   - `test_sec_rem_p2_01_resolve_client_ip_multiline_xff_and_zero_cidr_rejection`:
     - Verifies `resolve_client_ip` with multiple separate `x-forwarded-for` ASGI header lines (`[(b"x-forwarded-for", b"198.51.100.99"), (b"x-forwarded-for", b"10.0.0.10")]` and spoofed leftmost `203.0.113.77, 198.51.100.99`) from a trusted proxy (`10.0.0.0/8`) joins both lines via `getlist("x-forwarded-for")` right-to-left and returns `"198.51.100.99"`.
     - Verifies `resolve_client_ip` rejects `/0` CIDRs (`["0.0.0.0/0"]`, `["::/0"]`, and mixed `["0.0.0.0/0", "10.0.0.0/8"]`) passed directly in `trusted_proxy_ips`, falling back safely to `peer_ip`.
2. **`backend/tests/test_config_and_health.py` (`SEC-REM-P2-02`)**:
   - `test_sec_rem_p2_02_settings_rejects_unrestricted_zero_cidr_proxies`: verifies `Settings` raises `ValidationError` when `TRUSTED_PROXY_IPS` contains `"0.0.0.0/0"`, `"::/0"`, or comma-separated `10.0.0.0/8, 0.0.0.0/0`.
   - `test_sec_rem_p2_02_deployed_envs_reject_non_positive_db_timeouts`: verifies `Settings` rejects `0` and `-1` for `db_statement_timeout_ms`, `db_lock_timeout_ms`, and `db_idle_in_transaction_timeout_ms` in both `ENVIRONMENT="staging"` and `ENVIRONMENT="production"`.
   - `test_sec_rem_p2_02_non_deployed_envs_allow_zero_db_timeouts_and_accept_site_url`: verifies `0` timeouts remain accepted in `"development"` and `"test"` and `Settings` accepts `NEXT_PUBLIC_SITE_URL`.

### B. Frontend Regression Tests

1. **`frontend/tests/storefront-ssr.test.tsx`**:
   - **`SEC-M09-02` (`fetchServerProductBySlug` error sanitization)**: updated the network/abort error test to assert `"Unable to load product details."` on network failure (`connect ECONNREFUSED 127.0.0.1:8000`) and `"Product request timed out."` on `AbortError`, explicitly asserting internal strings (`"ECONNREFUSED"`, `"127.0.0.1:8000"`) do not leak into `result.message`.
   - **`SEC-M09-01` (`generateMetadata` URL sanitization & slug normalization)**: verified `generateMetadata` emits `openGraph.images` for `https://...` and `/assets/products/...` URLs, omits unsafe schemes (`http://`, `javascript:`, `../`), and normalizes mixed-case slugs (`"Linen-Overshirt"` -> `/products/linen-overshirt`).
   - **`resolveSiteUrl` & `RootLayout` `metadata.metadataBase`**: verified local dev default `http://localhost:3000`, verified `ENVIRONMENT="staging"` and `"production"` reject unset `NEXT_PUBLIC_SITE_URL`, `http://` URLs, and loopback (`localhost` / `127.0.0.1`) URLs, and verified `https://shop.example.com/path` normalizes to origin `https://shop.example.com`.
   - **`A11Y-M09-01` (heading hierarchy)**: verified `ProductsPage` / `CatalogView` renders product card titles as `<h2>` below `<h1>` (`Collection`) with zero `<h3>` elements (no skipped `h1 -> h3`), while `HomePage` / `HomeView` renders `<h2 id="home-guarantees-heading">` above its `<h3>` guarantee headings.
2. **`frontend/tests/ui-and-shells.test.tsx`**:
   - **`SEC-M09-03`**: verified `next.config.mjs` `headers()` sets `Cache-Control: no-store` on `/cart/:path*`.
   - **Not-found landmarks**: verified root `NotFoundPage` (`src/app/not-found.tsx`) renders `<main id="main-content">` (`getByRole("main")`), and storefront `StorefrontNotFoundPage` (`src/app/(storefront)/not-found.tsx`) renders `<h1>` without a duplicate `<main>` landmark.
3. **`frontend/e2e/storefront-and-admin.spec.ts` (Test `5`: `REM-P2-M09`)**:
   - Extended test `5` to verify `GET /cart` returns HTTP `200` with `Cache-Control: no-store`, `/products` renders `<h1>` (`Collection`) + `>= 8` `<h2>` product card headings with `0` `<h3>` headings inside `<main>`, and `/products/nonexistent-product-slug-404` returns HTTP `404` with a single `<main>` landmark and `<h1>Page not found</h1>`.

---

## 3. Defects, Risks & Environment Scope Notes

- **Defects found**: None. All `REM-P3B-BE` and `REM-P3B-FE` fixes passed unit, integration, build, dependency audit, and live E2E verification on the first run.
- **Scope note**: Live PostgreSQL verification used disposable PostgreSQL `18.6` on Windows (`run_disposable_pg_pytest.py` and `run_e2e_stack.py`); Docker Desktop / PG `16` container parity remains covered by CI/Docker configuration outside this local run.
