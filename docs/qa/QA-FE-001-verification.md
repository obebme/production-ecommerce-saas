# QA-FE-001 — Frontend Unit, Component & Security Verification

- **Task ID**: `QA-FE-001` (`frontend/tests/**`, `docs/qa/**`)
- **Verified Deliverable**: `FE-001` (Next.js/TypeScript storefront, admin shells, API client, and security fixes)
- **Author**: `qa` specialist
- **Overall Result**:
  - **Vitest (`vitest run`)**: **`3 passed` files, `31 passed` tests (`0` failed) in `6.04s`** (`api-client.test.ts`: `5 passed`, `format-and-security.test.ts`: `10 passed`, `ui-and-shells.test.tsx`: `16 passed`)
  - **Static Checks**: `next lint` (`0` warnings/errors, exit `0`) and `tsc --noEmit` (exit `0`)
  - **Production Build (Orchestrator, outside sandbox)**: `npm run build` (`next build`, `15/15` routes generated, exit `0`)
- **Scope & Compatibility Notice**: Unit and component tests execute in `jsdom` with mocked `globalThis.fetch` and mocked `next/navigation`. Live Next.js + FastAPI integration and Playwright browser E2E are outside `FE-001` scope and remain unverified here.

---

## 1. Test Coverage Summary (`31` Vitest Cases Across `3` Files)

1. **`frontend/tests/format-and-security.test.ts`** (`10` tests):
   - **Formatting & Price Inputs**: `formatCents` (`0`, `4500`, `"USD"`, lowercase/invalid currency fallbacks, non-finite inputs), `formatUtcTimestamp` (UTC rendering and `—` fallback), `computeSavingsPercent`, `parseDollarsToCents` (valid decimals and malformed/overflow rejection), and `formatCentsForInput`.
   - **`sanitizeReturnTo`**: accepts safe internal paths (`/checkout`, `/admin/products?status=ACTIVE`, fragment paths); falls back cleanly on `//evil.com`, `https://evil.com`, `http://localhost:3000/x`, `javascript:alert(1)`, `/\evil.com`, `/%2f/evil.com`, `/%2fevil.com`, `/%252fevil.com`, `/%5cevil.com`, control characters (`\x00`, `\n`, `%0d%0a`), whitespace, malformed encodings, and empty/nullish inputs.
   - **`sanitizeMediaUrl` & `LOW-2` Regression**: allows safe `https://` URLs and `/assets/...` paths; rejects `http://`, `javascript:alert(1)`, `data:text/html,...`, embedded credentials (`https://user:pass@...`), path traversal (`/assets/../secret.png`, `/assets/%2e%2e/secret.png`, `/assets/%252e%252e/secret.png`), `%0d%0a`, backslashes, embedded control bytes (`\x00`, `\n`, `\r`, `\t`), bare `/assets/`, non-`/assets/` relative paths, and **triple-encoded** traversal/control payloads (`https://cdn.example.com/%25252e%25252e/secret.png`, `/assets/img%25250a.png`).
   - **`MEDIUM-1` Regression (`next.config.mjs`)**: imports `next.config.mjs`, asserts `poweredByHeader === false`, evaluates `await nextConfig.headers()`, and verifies `/:path*` sets `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, and `Content-Security-Policy` (`default-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`, `form-action 'self'`), while `/admin/:path*` sets `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.

2. **`frontend/tests/api-client.test.ts`** (`5` tests):
   - **Base URL Normalization, Credentials & `LOW-3` Regression**: verifies `getApiBaseUrl()` avoids `/api/v1/api/v1` duplication, supports safe relative paths (`/backend` -> `/backend/api/v1`), rejects unsafe schemes (`javascript:alert(1)`, `ftp://example.com/api`) and protocol-relative `//evil.com/api`, and passes `credentials: "include"` on all `fetch` calls.
   - **Token Storage Boundary**: spies on `Storage.prototype` (`getItem`, `setItem`, `removeItem`, `clear`) to confirm zero `localStorage`/`sessionStorage` reads or writes across login, CSRF updates, session checks, cart mutations, and logout.
   - **CSRF Attachment & Cookie Fallback**: verifies `setMemoryCsrfToken` attaches `X-CSRF-Token` on `POST`/`PATCH`/`DELETE` (omitted on `GET`), with fallback to non-HttpOnly `ecom_csrf` cookie when in-memory CSRF is unset.
   - **`403 CSRF_INVALID` Refresh & `LOW-3` Regression**: verifies `403 CSRF_INVALID` triggers one `GET /api/v1/auth/me` refresh and retries once with the refreshed token; verifies repeated `403 CSRF_INVALID` and non-CSRF `403` errors do not loop; verifies when `GET /auth/me` during CSRF refresh returns `401 AUTH_SESSION_EXPIRED` or `403 AUTH_ACCOUNT_DISABLED`, `memoryCsrfToken` is cleared (`null`) and `registerAuthInvalidationListener` fires cleanly.
   - **Fail-Closed Auth Invalidation**: verifies direct `401 AUTH_SESSION_EXPIRED` and `403 AUTH_ACCOUNT_DISABLED` clear `memoryCsrfToken`, invoke `registerAuthInvalidationListener` (`"session_expired"` / `"account_disabled"`), and reject with the triggering `ApiError`.

3. **`frontend/tests/ui-and-shells.test.tsx`** (`16` tests):
   - **Shared Feedback & `StatusBadge`**: verifies `LoadingState` (`role="status"`), `EmptyState` (action callback), `ErrorState` (`role="alert"` + retry callback), and `StatusBadge` across `product`, `stock`, `role`, `order`, `payment`, `fulfillment`, and `active` domains.
   - **`ProductCard`**: verifies exact price vs `"From $..."` range, category label, safe `https://` and `/assets/...` image rendering, fallback placeholder on `null` or unsafe (`javascript:alert(1)`) media URLs, and `"Out of Stock"` badge when `in_stock: false`.
   - **`ProductDetailView`**: verifies `role="radiogroup"` (`Color`/`Size`) variant switching updating SKU, price, compare-at savings, `"Low stock"`/`"Out of stock"` badges, disabled `"Out of Stock"` CTA, `"Add to Cart"` calling `cartApi.addItem`, and `409 INSUFFICIENT_STOCK` recovery refreshing `GET /api/v1/variants/{id}/availability` and clamping quantity before retry.
   - **`AdminShell`, `AdminRoleGuard` & `LOW-5` Regressions**:
     - Unauthenticated (`user = null`) redirects to `/login?returnTo=%2Fadmin%2Fproducts` without rendering admin navigation.
     - `customer` renders `"403 Access Denied"` (`role="alert"`) and hides all admin navigation.
     - `staff` renders `Operations` links (`Dashboard`, `Products`, `Categories`, `Inventory`, `Orders`), hides `Administration` (`Users & Roles`, `Audit Logs`), and blocks `<AdminRoleGuard requiredRole="admin">` (`"403 Restricted to Administrators"`).
     - `admin` renders both `Operations` and `Administration` sections and passes `<AdminRoleGuard requiredRole="admin">`.
     - Wrapping real `<AdminDashboardView />` and `<AdminProductsStarterView />` inside `<AdminShell>` issues **zero** `/api/v1/admin/*` or `/health` `fetch` calls when `user` is `null` or `"customer"`.
   - **`LOW-1`, `LOW-5` & `INFO-2` Regressions**:
     - `/products/[slug]/page.tsx` invokes `notFound()` on `%ZZ`, `..`, `%2e%2e`, and `../admin` without crashing `500`, while accepting valid slug `"merino-crewneck"`.
     - `AuthProvider` + `CartProvider` fail closed on initial `401 AUTH_SESSION_EXPIRED` and `403 AUTH_ACCOUNT_DISABLED`, as well as mid-session `403 AUTH_ACCOUNT_DISABLED`, setting `sessionExpired`/`accountDisabled`, clearing `cart` to `null`, and issuing **zero** extra guest `GET /cart` requests.
     - `LoginView` sanitizes `?returnTo=//evil.com` to `/account` upon login and in the registration link.
     - `StorefrontHeader` shows cart count and search navigation, displays `"Admin Console"` for `staff`, and hides `"Admin Console"` for both `customer` and anonymous (`user === null`) users.

---

## 2. Command Execution Summary

| Environment | Command (in `e:\fullstack_work\frontend`) | Outcome |
| :--- | :--- | :--- |
| QA Specialist (Windows sandbox) | `C:\Program Files\nodejs\npm.cmd run lint` | **`✔ No ESLint warnings or errors`** (exit `0`) |
| QA Specialist (Windows sandbox) | `C:\Program Files\nodejs\npx.cmd tsc --noEmit` | **Passed** (`0` TypeScript errors, exit `0`) |
| QA Specialist (Windows sandbox) | `C:\Program Files\nodejs\npm.cmd test` (`vitest run`) | **`Test Files 3 passed (3)` / `Tests 31 passed (31)` in `6.04s`** (exit `0`) |
| Orchestrator (outside Windows sandbox) | `npm run build` (`next build`) | **Passed** (`15/15` routes generated, exit `0`; run outside sandbox due to Windows sandbox `mkdir` `EPERM` on `.next` route-group folders) |

---

## 3. Explicit Scope & Limitations

- **Mocked Runtime Context**: All `31` Vitest cases run inside `jsdom` with mocked `globalThis.fetch` and mocked `next/navigation`.
- **Unverified in `FE-001`**:
  - Live Next.js server response header emission under `next start` (headers in `next.config.mjs` are unit-verified via `nextConfig.headers()` and `poweredByHeader`).
  - Live Next.js + FastAPI HTTP integration across `POST /auth/login`, cookie/CSRF rotation, and `/cart` persistence.
  - Real browser Playwright E2E flows (deferred to integration/E2E phase).
