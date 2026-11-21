# REM-P2-M09-QA — Storefront SSR, Dynamic Metadata, HTTP 404 & Asset Resolution Verification

- **Task ID**: `REM-P2-M09-QA` (`frontend/tests/**`, `frontend/e2e/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Dependency**: `REM-P2-M09`
- **Production Source Integrity**: Zero edits made to `frontend/src/**` or `backend/**` by QA.
- **Overall Result**:
  - **ESLint (`npm.cmd run lint`)**: `✔ No ESLint warnings or errors` (exit `0`).
  - **TypeScript (`npx.cmd tsc --noEmit`)**: `0` errors (exit `0`).
  - **Vitest (`npm.cmd test`)**: **`6 files / 81 passed in 8.48s`** (`0` failed, `0` skipped; includes `12` new SSR/metadata/asset tests in `frontend/tests/storefront-ssr.test.tsx`).
  - **Production Build (`npm.cmd run build`)**: exit `0` (`21` routes; `/`, `/products`, and `/products/[slug]` built as `ƒ (Dynamic)` server-rendered on demand).
  - **Live Playwright E2E (`..\backend\.venv\Scripts\python.exe e2e\run_e2e_stack.py`)**: **`5 passed (30.1s)`** against disposable PostgreSQL 18.6 (`port=51012`), FastAPI (`uvicorn`), and prebuilt Next.js (`next start`), with clean process teardown and tempdir removal verified.

---

## 1. Command Verification Matrix

| Command | Scope & Runner | Outcome |
| :--- | :--- | :--- |
| `npm.cmd run lint` | `next lint` (`frontend/`, `BypassSandbox: true` batch) | `✔ No ESLint warnings or errors` (exit `0`) |
| `npx.cmd tsc --noEmit` | TypeScript strict check (`frontend/`) | `0` errors (exit `0`) |
| `npm.cmd test` | Vitest (`jsdom`, `frontend/tests/*.test.ts*`) | `6 files passed (6)`, `81 tests passed (81)` in `8.48s` (`0` failed, `0` skipped) |
| `npm.cmd run build` | `next build` (`BypassSandbox: true`, `frontend/`) | `21` routes built (`17` static pages + dynamic SSR `/`, `/products`, `/products/[slug]`), exit `0` |
| `..\backend\.venv\Scripts\python.exe e2e\run_e2e_stack.py` | Live Chromium Playwright E2E + disposable PG 18.6 (`port=51012`) + `uvicorn` + `next start` (`BypassSandbox: true`) | `5 passed (30.1s)` (exit `0`, clean teardown verified) |

### Vitest Breakdown (`6` files / `81` passed)

- `tests/api-client.test.ts`: `8` passed
- `tests/format-and-security.test.ts`: `10` passed
- `tests/storefront-ssr.test.tsx`: `12` passed *(new in `REM-P2-M09-QA`)*
- `tests/ui-and-shells.test.tsx`: `17` passed
- `tests/checkout-and-orders.test.tsx`: `20` passed
- `tests/admin-console.test.tsx`: `14` passed

---

## 2. Added Regression & E2E Coverage

### A. Vitest Unit & Component Suite (`frontend/tests/storefront-ssr.test.tsx` — 12 tests)

1. **`server-catalog.ts` URL resolution & error handling (4 tests)**:
   - Verifies `INTERNAL_API_BASE_URL` takes precedence over `NEXT_PUBLIC_API_BASE_URL` and normalizes `/api/v1` without duplication.
   - Verifies relative `/api/v1` falls back cleanly to `http://localhost:8000/api/v1`.
   - Verifies unsafe non-HTTP(S) schemes (`javascript:`, `ftp:`, `file:`), protocol-relative (`//evil.example.com/api/v1`), control characters, and backslashes are rejected with zero `fetch()` calls.
   - Verifies `fetchServerProductBySlug` distinguishes HTTP `404` (`{ status: "not_found" }`) vs `5xx` error envelopes and network failures (`{ status: "error", message }`).
2. **`HomePage` SSR rendering (1 test)**:
   - Invokes async Server Component `HomePage()`, verifies top `4` categories and SSR products render into initial output, and confirms `catalogApi.listProducts` and `catalogApi.listCategories` are not called on mount.
3. **`ProductsPage` SSR & interactive filtering (1 test)**:
   - Invokes `ProductsPage` with `searchParams` (`q`, `category_slug`, `in_stock`, `sort`, `page`), verifies server fetch query parameters and immediate rendering of SSR products without duplicate mount `catalogApi.listProducts` / `catalogApi.listCategories` calls, and confirms interactive filter toggling updates URL via `router.push` and triggers `catalogApi.listProducts` when URL search params change.
4. **`ProductDetailPage` & `generateMetadata` (5 tests)**:
   - Verifies `generateMetadata` returns dynamic `title`, `description`, and primary `openGraph.images` matching the fetched product.
   - Verifies `generateMetadata` handles invalid slugs (`"Product Not Found"`), backend `404` (`"Product Not Found"`), and backend `5xx` (`"Product Details"`).
   - Verifies valid slug renders server-fetched `ProductDetailDTO` inside app providers without calling `catalogApi.getProductBySlug` on mount.
   - Verifies invalid slug syntax (`"%ZZ"`, `"../admin"`, `"invalid slug!"`) and backend `404` invoke Next.js `notFound()`.
   - Verifies backend `5xx` renders `ErrorState` via `initialError` without invoking `notFound()`.
5. **Seeded product image asset integrity on disk (1 test)**:
   - Verifies all `8` seeded product PNGs (`linen-overshirt`, `merino-crewneck`, `leather-weekender`, `canvas-tote`, `stoneware-mug-set`, `walnut-serving-board`, `ceramic-table-lamp`, `wool-throw-blanket`) exist under `frontend/public/assets/products/<slug>.png`, are non-empty, and begin with valid PNG signature bytes (`89 50 4E 47 0D 0A 1A 0A`).

### B. Playwright Live E2E (`frontend/e2e/storefront-and-admin.spec.ts` — 5/5 passed)

- Existing tests `1`–`4` (`QA-E2E-001` authenticated purchase/payment, guest checkout + HttpOnly cookie + self-cancel, staff RBAC/fulfillment + admin analytics/refund/restock/audit diff, and cross-context concurrent last-unit oversell guard) remained intact and all `4` passed.
- Added test `5` (`REM-P2-M09: Storefront SSR, Dynamic Metadata, HTTP 404 & Asset Resolution`):
  - **Raw HTTP SSR checks (`page.request.get`)**: confirmed `/`, `/products`, and `/products/linen-overshirt` return HTTP `200` with server-rendered `"Atelier Linen Overshirt"` in the raw HTML body, `/products/linen-overshirt` includes `<title>Atelier Linen Overshirt | Atelier</title>`, and `/products/nonexistent-product-slug-404` returns a real HTTP `404`.
  - **Browser asset resolution on `/products`**: navigated to `/products`, scrolled each product card image into view under `main`, verified `img.complete === true && img.naturalWidth > 0` for all `8` seeded `<slug>.png` images, and verified HTTP `200` + `Content-Type: image/png` for each `/assets/products/<slug>.png` endpoint.

---

## 3. Observations & Notes

- On `/products` (which wraps `<CatalogView />` inside `<React.Suspense>`), Next.js 15 leaves a streamed `<div hidden id="S:0">` node in `document.body` while the active visible product cards render inside `<main>` with client-resolved `src` attributes ending in `/assets/products/<slug>.png`. Scoping the Playwright image locator to `main img[src$="/assets/products/${slug}.png"]` targets the visible rendered cards cleanly rather than the hidden streaming container.
- All temporary E2E processes (`pg_ctl`/PostgreSQL `pid=10444`, `uvicorn` `pid=23772`, `next start` `pid=26956`) and `%TEMP%\ecom_e2e_pg18_hxrd59i7` were verified cleanly stopped/removed after the E2E run.
