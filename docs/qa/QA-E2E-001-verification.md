# QA-E2E-001 — Live Full-Stack Playwright E2E Verification

- **Task ID**: `QA-E2E-001` (`frontend/playwright.config.ts`, `frontend/e2e/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Overall Result**:
  - **Playwright Live Full-Stack E2E (`run_e2e_stack.py`)**: **`4 passed` (`0` failed, `0` skipped) in `29.4s`** (exit `0`)
  - **TypeScript (`tsc --noEmit`)**: **Passed** (`0` errors, exit `0`)
  - **ESLint (`npm.cmd run lint` / `next lint`)**: **Passed** (`✔ No ESLint warnings or errors`, exit `0`)
  - **Vitest (`npm.cmd test` / `vitest run`)**: **`5 passed` files, `60 passed` tests (`0` failed, `0` skipped) in `9.54s`** (exit `0`)
- **Scope & Compatibility Notice**: Executed against an isolated ephemeral **PostgreSQL 18.6** cluster (`PostgreSQL 18.6 on x86_64-windows, compiled by msvc-19.44.35229, 64-bit`) rather than `postgres:16-alpine` in Docker. Exact target **PostgreSQL 16** container parity remains subject to CI / Docker runtime verification (`OPS-002`).

---

## 1. Deliverables & Isolation Safeguards

1. **Playwright Configuration (`frontend/playwright.config.ts`)**:
   - Chromium-only (`Desktop Chrome`), `testDir: "./e2e"`, `fullyParallel: false`, `workers: 1`, `retries: 0`.
   - `timeout: 60_000`, `expect.timeout: 15_000`, `use.baseURL: process.env.E2E_BASE_URL || "http://localhost:3000"`.
   - `trace: "retain-on-failure"`, `screenshot: "only-on-failure"`, `reporter: [["list"]]`, and no `webServer` block so `run_e2e_stack.py` and CI manage lifecycle explicitly.
2. **Windows-Safe Stack Runner (`frontend/e2e/run_e2e_stack.py`)**:
   - **Host DB & Port Isolation**: Verifies `127.0.0.1` and `::1` ports `8000` and `3000` are free without killing unrelated processes, validates prebuilt `frontend/.next/BUILD_ID`, refuses port `5432` and `E:\databasesql\data`, and confines temporary cluster files to `%TEMP%` (`C:\Users\User\AppData\Local\Temp\ecom_e2e_pg18_8rwptrkh`).
   - **No Inherited Pipe Deadlocks**: Redirects `initdb`, `pg_ctl`, `alembic`, `seed-demo`, `uvicorn`, and `next start` output strictly to files inside the `%TEMP%` run directory (`stdin=subprocess.DEVNULL, close_fds=True`).
   - **Least-Privilege Ephemeral Cluster & Seeding**: Started PostgreSQL 18.6 (`pid=16552`) on ephemeral loopback port `56928`, provisioned `ecom_test_role` and `ecom_test_db` via `psql` stdin, applied `alembic upgrade head` (`0001_initial_schema`), and seeded demo catalog & users via `python -m app.cli seed-demo --with-demo-users` (`categories=3, products=8, created_products=8, created_variants=11, created_demo_users=3`) with fresh ephemeral HMAC secrets and `ENVIRONMENT=test`, `COOKIE_SECURE=false`, `ALLOWED_ORIGINS=http://localhost:3000`, `PAYMENT_SANDBOX_MODE=true`.
   - **Live Servers & Verified Teardown**: Started `uvicorn` (`pid=14152`) on `127.0.0.1:8000` (`/api/v1/health` verified) and `next start` (`pid=8400`) on `127.0.0.1:3000` (`200 OK` verified), ran Playwright against `http://localhost:3000`, terminated Next.js, Uvicorn, and PostgreSQL cleanly, verified all temporary PIDs exited (`Clean teardown verified; zero temporary processes remain.`), and deleted the `%TEMP%` directory.

---

## 2. Live Browser E2E Coverage (`frontend/e2e/storefront-and-admin.spec.ts`)

| # | Spec | Duration | Key Verified Assertions |
|---|---|---|---|
| 1 | `1. Authenticated customer purchase, sandbox payment & order history` | `6.7s` | Customer login (`customer@atelier.example`); `/admin` blocked with `403 Access Denied`; catalog → PDP (`ATL-LINEN-SND-M`) → `/cart` → `/checkout` → `Place Order & Reserve Stock`; zero card/PAN/CVV/expiry inputs rendered; `Simulate Payment Success` transitions order to `Confirmed` / `Paid`; order appears in `/orders`. |
| 2 | `2. Guest checkout, HttpOnly cookie reload access, zero storage tokens & self-cancel` | `6.0s` | Fresh context; catalog → PDP (`ATL-TOTE-STN-OS`) → `/cart` → guest checkout (`guest.e2e@example.com`) → `Pending Payment`; `page.reload()` succeeds via `HttpOnly` `ecom_guest_orders` cookie (absent from `document.cookie`); `sessionStorage` empty and `localStorage` contains only `atelier-theme`; UI self-cancel updates order to `Cancelled` and releases reserved stock. |
| 3 | `3. Staff RBAC & fulfillment, Admin dashboard analytics, refund+restock & audit diff` | `3.9s` | Staff login (`staff@atelier.example`): `/admin/users` & `/admin/audit-logs` blocked by `403 Restricted to Administrators`; `Issue Refund` absent on order detail; fulfilled order `Start Processing` → `Mark Shipped` (`UPS`, `1Z999AA10123456784`) → `Mark Completed`. Admin login (`admin@atelier.example`): `/admin` KPIs show non-zero `Net Revenue` and `Paid Orders >= 1`; `/admin/inventory` confirms `ATL-TOTE-STN-OS` released (`on_hand=32`, `reserved=0`) and `ATL-LINEN-SND-M` (`on_hand=23`); `Issue Refund` with `restock` checked transitions to `Refunded` and restores `on_hand` to `24`; `/admin/audit-logs` shows `ORDER_REFUNDED` and Before/After JSON Diff drawer (`COMPLETED` → `REFUNDED`, `RETURNED`). |
| 4 | `4. Cross-context concurrent oversell guard prevents second order on last unit` | `11.9s` | Admin calibrates `ATL-LINEN-SND-L` to `available=1`; isolated guest `Context A` and `Context B` both add 1 unit and open `/checkout`; `Context A` submits order (`Pending Payment`, active reservation); `Context B` submission is blocked on `/checkout` with stock error alert (`409 INSUFFICIENT_STOCK`) and disabled `Place Order`; PDP reflects `Out of stock` for size `L`. |

---

## 3. Command Execution Summary

| Command | Working Dir | Outcome |
|---|---|---|
| `e:\fullstack_work\backend\.venv\Scripts\python.exe e:\fullstack_work\frontend\e2e\run_e2e_stack.py` | `e:\fullstack_work` | **`4 passed (29.4s)`**, exit `0`; clean teardown verified |
| `"C:\Program Files\nodejs\npx.cmd" tsc --noEmit` | `e:\fullstack_work\frontend` | **Passed** (`0` TypeScript errors, exit `0`) |
| `"C:\Program Files\nodejs\npm.cmd" run lint` | `e:\fullstack_work\frontend` | **`✔ No ESLint warnings or errors`**, exit `0` |
| `"C:\Program Files\nodejs\npm.cmd" test` (`vitest run`) | `e:\fullstack_work\frontend` | **`Test Files 5 passed (5)` / `Tests 60 passed (60)` in `9.54s`**, exit `0` |

---

## 4. Production Defects & Observations

- **Production Defects Blocking E2E**: None discovered across storefront, guest cookies, sandbox payments, RBAC, fulfillment, refunds, restocking, analytics, audit diffs, or oversell protection.
- **Operational Observation (Non-Blocking)**: `next start` logged `⚠ "next start" does not work with "output: standalone" configuration. Use "node .next/standalone/server.js" instead.` in `nextjs.log`, while continuing to serve all routes cleanly on `127.0.0.1:3000` under Next.js `15.5.29` (shared for awareness without modifying production config).
- **Target PostgreSQL 16 Parity**: Live E2E ran against disposable PostgreSQL 18.6; `postgres:16-alpine` container verification remains pending `OPS-002` / CI.
