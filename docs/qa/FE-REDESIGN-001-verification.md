# QA-REDESIGN-001 — Vellora Store Storefront Redesign Verification

- **Task ID**: `QA-REDESIGN-001` (`frontend/tests/**`, `frontend/e2e/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Dependencies**: `FE-REDESIGN-001` (including post-review accessibility & token patches in `globals.css` and `product-detail-view.tsx`)
- **Production Source Integrity**: Zero edits made to `frontend/src/**` or `backend/**` by QA. Only authorized test and QA documentation files were modified or created:
  - `frontend/tests/storefront-ssr.test.tsx`
  - `frontend/tests/ui-and-shells.test.tsx`
  - `frontend/e2e/storefront-and-admin.spec.ts`
  - `docs/qa/FE-REDESIGN-001-verification.md`

---

## 1. Full Frontend & Live E2E Verification Summary

All commands executed in `e:\fullstack_work\frontend` after the final `FE-REDESIGN-001` patches (`MED-01`, `MED-02`, `LOW-01`, `LOW-02`, `LOW-03`, `INFO-04`) and final QA test edits:

| # | Command | Scope & Runner | Outcome |
| :-: | :--- | :--- | :--- |
| 1 | `npm.cmd run lint` | `next lint` (`frontend/`) | `✔ No ESLint warnings or errors` (exit `0`) |
| 2 | `npx.cmd tsc --noEmit` | TypeScript strict typecheck (`frontend/`) | `0` errors (exit `0`) |
| 3 | `npm.cmd test` | Vitest `v4.1.12` (`jsdom`, `frontend/tests/*.test.ts*`) | **`6 files passed (6), 90 tests passed (90)`** (`0` failed, `0` skipped) in `11.23s` |
| 4 | `npm.cmd run build` (`BypassSandbox: true`) | `next build` (`Next.js 15.5.29`, production bundle) | **`21` routes built** (`17/17` static pages generated, `5` dynamic `ƒ` routes: `/`, `/products`, `/products/[slug]`, `/orders/[orderNumber]`, `/admin/orders/[orderNumber]`, exit `0`) |
| 5 | `..\backend\.venv\Scripts\python.exe e2e\run_e2e_stack.py` (`BypassSandbox: true`) | Live Chromium Playwright E2E + disposable PostgreSQL `18.6` (`127.0.0.1:55059`, `pid=25832`) + `uvicorn` (`pid=53240`) + `next start` (`pid=30820`) | **`5 passed (31.3s)`** (`0` failed, `0` skipped, exit `0`; `Clean teardown verified; zero temporary processes remain.`) |

### Vitest Breakdown (`6` files / `90` passed)

- `tests/api-client.test.ts`: `8` passed
- `tests/format-and-security.test.ts`: `10` passed
- `tests/storefront-ssr.test.tsx`: `17` passed *(updated guarantee heading expectation, RootLayout `Vellora Store` title assertions, `hero-showcase.png` + 8 product PNG disk checks, and added `HomeView` Featured Collection filter pill test)*
- `tests/ui-and-shells.test.tsx`: `21` passed *(added `ProductDetailView` `role="radiogroup"` roving `tabIndex` & `ArrowRight` keyboard navigation assertions, `StorefrontHeader` `"Vellora Store"` link check, `ThemeProvider` dark default + `ThemeToggle variant="pill"` Light/Dark test, and `MobileBottomNav` accessible links test)*
- `tests/checkout-and-orders.test.tsx`: `20` passed
- `tests/admin-console.test.tsx`: `14` passed

### Live Playwright Chromium Breakdown (`5` passed / `31.3s`)

1. Authenticated customer purchase, sandbox payment & order history (`6.9s`) — passed
2. Guest checkout, HttpOnly cookie reload access, zero storage tokens & self-cancel (`6.2s`) — passed
3. Staff RBAC & fulfillment, Admin dashboard analytics, refund+restock & audit diff (`3.9s`) — passed
4. Cross-context concurrent oversell guard prevents second order on last unit (`12.0s`) — passed
5. Raw HTTP SSR HTML, dynamic PDP `<title>` (`Atelier Linen Overshirt | Vellora Store`), dark-default theme toggle/reload persistence (`atelier-theme` only in `localStorage`, restored to `dark`), `hero-showcase.png` + all `8` `<slug>.png` images (`complete && naturalWidth > 0`, HTTP `200` `image/png`), HTTP `404` landmarks, & `390x844` mobile bottom nav + zero horizontal overflow (`1.6s`) — passed

---

## 2. Test Coverage Changes (`QA-REDESIGN-001`)

1. **`frontend/tests/storefront-ssr.test.tsx`**:
   - Updated the `A11Y-M09-01` guarantee heading check to `"Free Shipping Over $100"` to match the redesigned `HomeView` 4-column guarantee strip.
   - Verified `RootLayout` `metadata.title` defaults to `"Vellora Store — Considered Everyday Goods"` with `template: "%s | Vellora Store"`.
   - Expanded PNG disk asset verification to include `hero-showcase.png` alongside all `8` seeded `<slug>.png` files (`> 100` bytes and PNG signature `89 50 4E 47 0D 0A 1A 0A`).
   - Added an interactive `HomeView` Featured Collection filter pill test verifying category (`Apparel`) and stock (`In Stock Only`) pills call `catalogApi.listProducts` with expected parameters while `"All"` restores initial SSR products from cache without a duplicate request.
2. **`frontend/tests/ui-and-shells.test.tsx`**:
   - Added `role="radiogroup"` roving `tabIndex` (`0` / `-1`) and `ArrowRight` keyboard navigation assertions in `ProductDetailView` (`LOW-02`), verifying focus, `tabIndex`, SKU, price, savings, and stock badge update cleanly.
   - Verified `StorefrontHeader` renders the `"Vellora Store"` link to `/`.
   - Added `ThemeProvider` + `ThemeToggle variant="pill"` test verifying default `"dark"` (`html.dark`, `data-theme="dark"` when `atelier-theme` is unset) and clean switching (`"light"` <-> `"dark"`, updating `html.dark`, `data-theme`, `aria-pressed`, and `localStorage["atelier-theme"]`).
   - Added `MobileBottomNav` test verifying accessible `Home`, `Catalog`, `Cart`, and `Account` navigation links and active `aria-current="page"`.
3. **`frontend/e2e/storefront-and-admin.spec.ts`**:
   - Updated Test `5` `<title>` assertion to `<title>Atelier Linen Overshirt | Vellora Store</title>`.
   - Extended Test `5` in live Chromium to verify default dark mode on `/`, `hero-showcase.png` rendering (`complete && naturalWidth > 0`) and HTTP `200` `image/png`, Light/Dark pill switching + `page.reload()` persistence with `localStorage` restricted to `SAFE_LOCAL_STORAGE_KEYS` (`atelier-theme`) + restore to `"dark"`, all `8` product PNGs on `/products`, HTTP `404` handling, and `390x844` mobile viewport rendering of `MobileBottomNav` (`Home`, `Catalog`, `Cart`, `Account`) with `scrollWidth <= innerWidth`.

---

## 3. Defects & Environment Notes

- **Defects found**: None. All `90` Vitest unit/component tests, production build (`next build`), and `5` live Chromium E2E flows passed cleanly after the final patches.
- **Environment scope**: Live Playwright E2E ran against prebuilt Next.js (`next start`), FastAPI (`uvicorn`), and a disposable PostgreSQL `18.6` cluster via `e2e/run_e2e_stack.py` with clean teardown verified (`zero temporary processes remain`).
