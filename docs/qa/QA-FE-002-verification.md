# QA-FE-002 — Cart, Checkout, Sandbox Payment & Customer Orders Verification

- **Task ID**: `QA-FE-002` (`frontend/tests/**`, `docs/qa/**`)
- **Verified Deliverable**: `FE-002` (Cart drawer & page, idempotent checkout, sandbox SimPay order confirmation/retry/self-cancel, guest in-memory token recovery, and customer order history)
- **Author**: `qa` specialist
- **Overall Result**:
  - **Vitest (`vitest run`)**: **`4 passed` files, `48 passed` tests (`0` failed, `0` skipped) in `8.43s`** (`api-client.test.ts`: `5 passed`, `format-and-security.test.ts`: `10 passed`, `ui-and-shells.test.tsx`: `16 passed`, `checkout-and-orders.test.tsx`: `17 passed`)
  - **Static Checks**: `next lint` (`✔ No ESLint warnings or errors`, exit `0`) and `tsc --noEmit` (exit `0`)
  - **Production Build (Orchestrator, outside sandbox)**: `next build` (`18/18` routes generated, exit `0`)
- **Scope & Compatibility Notice**: All `48` Vitest cases run inside `jsdom` with mocked `globalThis.fetch`, mocked `next/navigation`, and a test-scoped `ResizeObserver` stub (required by `@radix-ui/react-checkbox` in `jsdom`). Live Next.js + FastAPI + PostgreSQL HTTP/cookie flows and Playwright browser E2E are outside `QA-FE-002` scope and await `QA-E2E-001`.

---

## 1. New FE-002 Test Coverage (`frontend/tests/checkout-and-orders.test.tsx`, `17` Cases)

1. **Cart Drawer & `/cart` (`CartDrawer`, `CartPageView`)** (`3` tests):
   - **`CartDrawer` navigation & gating**: verifies `"Proceed to Checkout"` (`/checkout`) and `"View Full Cart"` (`/cart`) links on valid carts; verifies empty-bag state (`"Your bag is empty"` -> `/products`) omits checkout actions; verifies out-of-stock/unavailable items disable `"Proceed to Checkout"` button while preserving `"View Full Cart"` (`/cart`).
   - **`CartPageView` stepper, in-flight lock & stock conflict recovery**: verifies `"Adjust to 2"` (`PATCH /api/v1/cart/items/{id}`) reconciles stock conflict and re-enables `/checkout`, verifies per-row stepper (`-`, `+`) and `Remove` buttons disable while `PATCH` is in-flight without blocking sibling rows, and verifies stepper quantity increments.
   - **Free-shipping threshold & Clear Cart confirmation**: verifies `$100.00` (`10000` cents) threshold notice + `role="progressbar"` (`aria-valuemin="0"`, `aria-valuemax="10000"`, `aria-valuenow="4500"`), verifies `"Complimentary standard shipping unlocked ($0.00)"` and `"Free"` shipping at `$135.00`, and verifies `ClearCartDialog` cancels cleanly on `"Keep items"` (`0` `DELETE /cart` calls) and clears bag on `"Clear Bag"` (`DELETE /api/v1/cart`).

2. **Checkout (`/checkout`, `CheckoutView`)** (`5` tests):
   - **Pre-submit & session fail-closed guards**: verifies empty-cart card (`"Your cart is empty"` -> `/products`), unavailable-item alert (`"Stock or availability changed"` -> `"Update Cart"` `/cart` with disabled submit), and `401 AUTH_SESSION_EXPIRED` during `POST /api/v1/checkout/orders` transitioning to the fail-closed `"Session expired"` alert linking to `/login?expired=1&returnTo=%2Fcheckout`.
   - **Billing toggle & zero client-side prices**: verifies `billing_same_as_shipping` defaults checked, unchecking expands required `"Billing recipient and address"` inputs, and inspects captured `POST /api/v1/checkout/orders` JSON payload to assert only `customer_email`, `shipping_address`, `billing_same_as_shipping`, and `billing_address` are sent (zero `subtotal_cents`, `shipping_cents`, `tax_cents`, `total_cents`, `discount_cents`, or `unit_price_cents` keys).
   - **`Idempotency-Key` lifecycle**: verifies RFC 4122 UUID v4 `Idempotency-Key` header on `POST /api/v1/checkout/orders`, exact key reuse when retrying after transient network failure (`TypeError`) or `500` with unchanged fields, regeneration upon `409 IDEMPOTENCY_KEY_CONFLICT`, and regeneration when shipping address fields change; also verifies `generateUuidV4()` uniqueness.
   - **Server error mapping**: verifies `422 VALIDATION_ERROR` maps `error.details.errors` to `customer_email` and `shipping.postal_code` (`aria-invalid="true"`), `409 INSUFFICIENT_STOCK` renders alert with `(requested 2, available 1)` and `"Update Cart"` (`/cart`), and `429 RATE_LIMIT_EXCEEDED` renders the reservation cap alert.
   - **Guest checkout token boundary**: verifies `guest_access_token` is saved strictly in `guest-order-memory`, `Storage.prototype` spies (`getItem`, `setItem`, `removeItem`, `clear`) record `0` calls (`localStorage.length === 0`, `sessionStorage.length === 0`), and router pushes `/orders/ORD-20261119-GUEST1`.

3. **Order Detail & Sandbox Payment (`/orders/[orderNumber]`, `OrderDetailPage`, `OrderDetailView`)** (`6` tests):
   - **Route parameter validation**: verifies `OrderDetailPage` invokes `notFound()` on `%ZZ`, `..`, `%2e%2e`, `../admin`, `ORD/../../etc`, and `33`-char inputs while returning a valid React element for `ORD-20261119-8F3K2A`.
   - **Countdown, zero card inputs & Simulate Success**: verifies `"Stock reservation active"` `MM:SS` countdown, verifies `0` `<input>` elements and zero PAN/CVV/expiry fields in the Sandbox SimPay card, and verifies `"Simulate Payment Success"` (`POST /payments/sandbox/pi_sandbox_123/simulate` `{ outcome: "succeeded", failure_code: null }`) refetches to `CONFIRMED`/`PAID` with `"Payment confirmed! Your order is now queued for fulfillment."` and hides `"Cancel Order"`.
   - **Warning countdown, Simulate Failure & Retry Intent**: verifies `<= 03:00` `"Reservation expiring soon"` warning banner, `"Simulate Payment Failure"` (`{ outcome: "failed", failure_code: "card_declined" }`) alert preserving `"Cancel Order"`, and `"Retry / Create Payment Intent"` (`POST /payments/intents` with `Idempotency-Key` UUID v4) hydrating new `pi_sandbox_retry_2`.
   - **`403 SANDBOX_MODE_DISABLED`**: verifies simulate controls are hidden and `"Sandbox payment simulation is disabled in this environment."` is displayed.
   - **Self-cancel confirmation Dialog**: verifies `"Cancel Order"` opens `"Cancel Order & Release Reserved Stock?"`, submits `POST /orders/{orderNumber}/cancel` with optional `reason`, transitions to `CANCELLED`, and verifies `"Cancel Order"` is absent when `PAID` (replaced by `"Paid orders cannot be self-cancelled online. Contact support for refund assistance."`).
   - **Guest token recovery (`403 FORBIDDEN`)**: verifies unauthenticated `403` renders `"Order Access Required"` with `/login?returnTo=...`, validates empty token submissions locally, and submits `"Enter Guest Order Token"` via `X-Guest-Order-Token` in module memory with `0` `Storage.prototype` (`getItem`/`setItem`/`removeItem`/`clear`) calls.

4. **Order History (`/orders`, `OrderHistoryView`)** (`3` tests):
   - **Auth redirect**: verifies anonymous visits redirect via `router.replace("/login?returnTo=%2Forders")` with `0` `GET /orders` requests.
   - **Order rows, 3 status badges & pagination**: verifies desktop table rows render `order_number` link (`/orders/...`) plus all 3 distinct status domains (`Processing`/`Paid`/`Allocated`, `Pending Payment`/`Requires Payment`/`Unfulfilled`), and verifies `"Next page"` requests `page=2&page_size=20` and renders `"Showing 21–25 of 25 orders"`.
   - **Empty state**: verifies `"No orders yet"` and `"Start Shopping"` link (`/products`) when `items: []`.

---

## 2. Command Execution Summary

| Environment | Command (in `e:\fullstack_work\frontend`) | Outcome |
| :--- | :--- | :--- |
| QA Specialist (Windows sandbox) | `C:\Program Files\nodejs\npm.cmd run lint` | **`✔ No ESLint warnings or errors`** (exit `0`) |
| QA Specialist (Windows sandbox) | `C:\Program Files\nodejs\npx.cmd tsc --noEmit` | **Passed** (`0` TypeScript errors, exit `0`) |
| QA Specialist (Windows sandbox) | `C:\Program Files\nodejs\npm.cmd test` (`vitest run`) | **`Test Files 4 passed (4)` / `Tests 48 passed (48)` in `8.43s`** (`0` failed, `0` skipped, exit `0`) |
| Orchestrator (outside Windows sandbox) | `npm run build` (`next build`) | **Passed** (`18/18` routes generated, exit `0`; verified by Orchestrator outside sandbox) |

---

## 3. Explicit Scope, Observations & Deferred Checks

- **Mocked Runtime Context**: All `48` Vitest tests execute in `jsdom` with mocked `globalThis.fetch`, mocked `next/navigation`, and a local `ResizeObserver` stub in `CheckoutView` tests.
- **Non-blocking Implementation Observation**: In `CheckoutView`, `fetch()` rejections are normalized by `apiRequest` into `ApiError(status=0, code="NETWORK_ERROR")`, so network failures display `"Unable to reach the server. Please check your connection and try again."` via the `ApiError` branch (preserving the in-memory `Idempotency-Key` for safe retry); the generic non-`ApiError` fallback branch is only reachable for non-`ApiError` exceptions outside `apiRequest`.
- **Unverified in `QA-FE-002` (Deferred to `QA-E2E-001`)**:
  - Real browser cookie round-trips (`HttpOnly` `ecom_session`, `ecom_guest_cart`, `ecom_guest_orders`, and `ecom_csrf`), live FastAPI checkout/webhook state transitions, and responsive layout checks under Playwright.
