# QA-FE-003 — Admin Console & Analytics Verification

- **Task ID**: `QA-FE-003` (`frontend/tests/**`, `docs/qa/**`)
- **Verified Deliverable**: `FE-003` (Admin Sales & Operations Dashboard with Recharts & Data Table parity, Categories hierarchy, Products & Variants editor, Inventory control & append-only ledger, Orders 3-axis list & staff/admin fulfillment/refund workbench, Admin-only Users & Roles with session revocation, and Admin-only Audit Logs with Before/After JSON diff drawer)
- **Author**: `qa` specialist
- **Overall Result**:
  - **Vitest (`vitest run`)**: **`5 passed` files, `60 passed` tests (`0` failed, `0` skipped) in `10.28s`** (`api-client.test.ts`: `5 passed`, `format-and-security.test.ts`: `10 passed`, `ui-and-shells.test.tsx`: `16 passed`, `checkout-and-orders.test.tsx`: `17 passed`, `admin-console.test.tsx`: `12 passed`)
  - **TypeScript (`tsc --noEmit`)**: **Passed** (`0` errors, exit `0`)
  - **ESLint (`next lint`)**: **Passed** (`✔ No ESLint warnings or errors`, exit `0`)
- **Scope & Compatibility Notice**: All `60` Vitest cases execute in `jsdom` with mocked `globalThis.fetch`, mocked `next/navigation`, and test-scoped JSDOM polyfills (`ResizeObserver` plus `scrollIntoView` / pointer-capture stubs for Radix UI Select/Tabs and Recharts `ResponsiveContainer`). Live Next.js + FastAPI + PostgreSQL integration, real `HttpOnly` cookie sessions, and visual Recharts layout checks await `QA-E2E-001`.

---

## 1. New FE-003 Test Coverage (`frontend/tests/admin-console.test.tsx`, `12` Cases)

1. **Dashboard & Analytics (`AdminDashboardView`)** (`1` comprehensive test):
   - **KPI formatting & refund highlighting**: verifies `net_revenue_cents` (`$200.00`), `gross_revenue_cents` (`$250.00`) with highlighted `text-destructive` subtitle (`Refunded: -$50.00` when `refunded_revenue_cents > 0`), `paid_orders_count` (`4`), and `$0.00` AOV + `Refunded: $0.00` when zero sales occur.
   - **Order status breakdown pills**: verifies interactive `orders_by_status` pills link to `/admin/orders?status=CONFIRMED&from_ts=...T00%3A00%3A00Z&to_ts=...T23%3A59%3A59.999Z`.
   - **Recharts & keyboard-accessible Chart / Data Table toggle**: verifies `.recharts-responsive-container` renders cleanly, verifies `ArrowRight` keyboard navigation switches Radix `Tabs` from `Chart` to `Data Table`, and verifies full daily UTC table parity including zero-sale days (`2026-11-17`, `$0.00`, `0`).
   - **Top Products & Inventory Health**: verifies `Revenue` vs `Units Sold` sort toggle (`sort_by=units`) and Inventory Health `"Adjust Stock"` links to `/admin/inventory?sku=TEE-CHR-L`.
   - **UTC presets & custom range bounds**: verifies `Last 7 days`, `Last 30 days`, `Last 90 days` (`89`-day span), and `Last 365 days` (`364`-day span) presets, and confirms custom ranges `> 366` days (`"Date range cannot exceed 366 days"`) and `from > to` (`"Start date must be before or equal to end date."`) block submit with `0` redundant `/admin/analytics/*` fetches.

2. **Categories (`AdminCategoriesView`)** (`1` test):
   - Verifies hierarchical indentation (`paddingLeft: 20px`) and parent label display.
   - Verifies Create/Edit `Dialog`, auto-slug generation, self-exclusion of the edited category from `Parent Category` options, `X-CSRF-Token` on `POST /admin/categories`, and inline surfacing of `409 SLUG_CONFLICT` and `422 CATEGORY_CYCLE_DETECTED`.

3. **Products, Editor & Variant Modals (`AdminProductsView`, `AdminProductEditorView`, `AdminVariantModal`)** (`3` tests):
   - **Product list & archive**: verifies status/search filters and `"Archive Product"` confirmation dialog invoking `DELETE /admin/products/{id}` with `X-CSRF-Token`, while omitting Archive actions on already-`ARCHIVED` items.
   - **Product editor, guards & variants**:
     - Verifies non-UUID `productId` fails safely to `"Product not found"` without issuing `GET /admin/products/*`, and `404 PRODUCT_NOT_FOUND` renders `"Return to Products"` (`/admin/products`).
     - Verifies client `DRAFT -> ACTIVE` guard with `0` active variants shows `"Add and activate at least 1 variant before setting status to ACTIVE"` and blocks `PATCH`, and separately verifies server `422 PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT` surfacing.
     - Verifies image URL security validation rejects `javascript:alert(1)` and `http://...` (`0` `PATCH` calls) while accepting `https://...` and `/assets/...`.
     - Verifies `AdminVariantModal` dollar-to-cents parsing (`"45.50" -> 4550`, `"60.00" -> 6000`, `"49.95" -> 4995`), `compare_at_price_cents >= price_cents` enforcement, `409 SKU_CONFLICT` surfacing, `X-CSRF-Token` attachment, and edit-mode read-only stock snapshot linking `"Adjust stock in Inventory"` to `/admin/inventory?sku=MERINO-CHR-M`.

4. **Inventory Control & Ledger (`AdminInventoryView`)** (`1` test):
   - Verifies `q` search input prefill and initial fetch from `?sku=TEE-CHR-M`.
   - Verifies client stock adjustment invariants (`delta_on_hand === 0`, `on_hand + delta_on_hand < reserved`, and blank `note.trim().length === 0`) disable submit (`0` `POST` calls), verifies server-side `409 INSUFFICIENT_STOCK` error surfacing in the modal, and verifies valid submission sends `{ delta_on_hand: 5, note: "Restock PO-1042" }` with `X-CSRF-Token`.
   - Verifies `"View Ledger"` opens the `"Append-Only Stock Ledger"` `Sheet`, fetches `/admin/inventory/{id}/ledger`, and renders immutable entries (`MANUAL_ADJUSTMENT`, `RESERVE`, `On Hand / Reserved` snapshots) plus order link (`/admin/orders/ORD-20261119-8F3K2A`).

5. **Orders & Fulfillment/Refund Workbench (`AdminOrdersView`, `AdminOrderDetailView`)** (`3` tests):
   - **3 distinct status columns & query prefill**: verifies `AdminOrdersView` reads `?status=...&from_ts=...&to_ts=...` and renders separate `Order Status`, `Payment Status`, and `Fulfillment Status` columns.
   - **Staff RBAC**: verifies `staff` user sees `"Cancel Unpaid Order"` enabled only on `PENDING_PAYMENT`, sees `"Start Processing"` and `"Mark Shipped"` (`carrier` & `tracking_number` fields sent via `POST /admin/orders/{num}/fulfill` with `X-CSRF-Token`) on paid orders, and **never** sees `"Issue Refund"` button or dialog.
   - **Admin-only Refunds**: verifies `admin` user on a `PAID` order sees `"Issue Refund"`, can toggle the `restock` checkbox and enter `reason`, and submits `POST /admin/orders/{num}/refund` with `X-CSRF-Token`.

6. **Users, Roles & Audit Logs (`AdminUsersView`, `AdminAuditLogsView`)** (`3` tests):
   - **Staff RBAC block**: verifies `user.role === "staff"` renders `"403 Restricted to Administrators"` in both `AdminUsersView` and `AdminAuditLogsView` with **0** fetches to `/admin/users` or `/admin/audit-logs`.
   - **Admin user management**: verifies self-row (`row.id === currentUser.id`) disables role `Select` and active `Switch`, verifies mandatory session-revocation confirmation `Dialog` intercepts mutations before `PATCH /admin/users/{id}/status` or `/role` (with `X-CSRF-Token`), verifies toast renders `revoked_sessions_count`, and verifies `409 CANNOT_REMOVE_LAST_ADMIN` surfaces inside the dialog alert.
   - **Audit log diff drawer**: verifies table rows open the Before/After JSON Diff `Sheet`, handles `before_state: null` gracefully (`—`), renders changed keys across before/after states, and preserves `"[REDACTED]"` verbatim.

---

## 2. Existing Regression Suite (`frontend/tests/ui-and-shells.test.tsx`)

- Verified `frontend/tests/ui-and-shells.test.tsx` (`16` tests) remains completely green (`AdminDashboardView` and `AdminProductsStarterView` alias for `AdminProductsView` pass RBAC and zero-leak checks in `AdminShell`).

---

## 3. Command Execution Summary

| Environment | Command (in `e:\fullstack_work\frontend`) | Outcome |
| :--- | :--- | :--- |
| QA Specialist (Windows) | `C:\Program Files\nodejs\npm.cmd test` (`vitest run`) | **`Test Files 5 passed (5)` / `Tests 60 passed (60)` in `10.28s`** (`0` failed, `0` skipped, exit `0`) |
| QA Specialist (Windows) | `C:\Program Files\nodejs\npx.cmd tsc --noEmit` | **Passed** (`0` TypeScript errors, exit `0`) |
| QA Specialist (Windows) | `C:\Program Files\nodejs\npm.cmd run lint` (`next lint`) | **`✔ No ESLint warnings or errors`** (exit `0`) |

---

## 4. Production Defects & Explicit Scope Notes

- **Production Defects Discovered**: None during `QA-FE-003` component/unit/RBAC verification.
- **Unverified / Deferred to `QA-E2E-001`**:
  - Live Next.js + FastAPI + PostgreSQL round-trips, real `HttpOnly` cookie handling, Next.js production `next build` execution (owned by `FE-003` / Orchestrator), and real-browser Recharts SVG sizing/tooltips under Playwright.
