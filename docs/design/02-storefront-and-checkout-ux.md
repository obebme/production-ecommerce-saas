# 02 — Storefront, Cart, Auth, Checkout & Customer Order UX

> **Task**: `DESIGN-001`  
> **Scope**: `(storefront)` Next.js App Router user journeys, layouts, forms, security UX, and edge states.  
> **Aligned Contracts**: [openapi-contracts.md](../api/openapi-contracts.md), [03-workflows-concurrency-and-security.md](../architecture/03-workflows-concurrency-and-security.md).

---

## 1. Storefront Information Architecture & Global Chrome

### 1.1 Route Map (`(storefront)`)
| Route | Purpose | Auth Requirement | Primary API Contract |
| :--- | :--- | :--- | :--- |
| `/` | Editorial Home & Featured Arrivals | Public | `GET /api/v1/categories`, `GET /api/v1/products` |
| `/products` | Filterable Product Catalog | Public | `GET /api/v1/products`, `GET /api/v1/categories` |
| `/products/[slug]` | Product Detail & Variant Selection | Public | `GET /api/v1/products/{slug}`, `GET /api/v1/variants/{variant_id}/availability` |
| `/cart` | Full Shopping Cart & Reconciliation View | Guest cookie or Session | `GET /api/v1/cart`, `PATCH/DELETE /api/v1/cart/items/{id}`, `DELETE /api/v1/cart` |
| `/checkout` | Shipping/Billing & Idempotent Order Creation | Guest cookie or Session | `POST /api/v1/checkout/orders` |
| `/orders/[order_number]` | Order Confirmation, Sandbox Payment & Timeline | Owner or `ecom_guest_orders` / `X-Guest-Order-Token` | `GET /api/v1/orders/{order_number}`, `POST /api/v1/payments/sandbox/{id}/simulate`, `POST /api/v1/orders/{order_number}/cancel` |
| `/orders` | Authenticated Order History | Authenticated | `GET /api/v1/orders` |
| `/account` | Customer Profile Management | Authenticated | `GET /api/v1/auth/me`, `PATCH /api/v1/auth/me` |
| `/login`, `/register` | Customer Authentication | Anonymous (redirects if authed) | `POST /api/v1/auth/login`, `POST /api/v1/auth/register`, `POST /api/v1/cart/merge` |

### 1.2 Responsive Header & Navigation (`sticky top-0 z-40 h-16`)
* **Desktop (`>= lg` / `1024px`)**:
  * **Left**: Brand wordmark (`/`), `Shop All` link (`/products`), and `Categories` dropdown menu populated dynamically from `GET /api/v1/categories` (ordered by `sort_order ASC, name ASC`; parent categories group child categories cleanly).
  * **Center/Right**:
    * **Search Bar**: Compact input (`maxLength={100}`, placeholder `"Search products..."`). Submitting routes to `/products?q=...` (trimmed; preserves or clears category scope explicitly via chip). Clear (`×`) button appears when populated.
    * **Theme Toggle**: Light/Dark switcher (`aria-label="Toggle color theme"`).
    * **Account Menu** (`DropdownMenu`):
      * *Anonymous*: `Sign In` link (`/login`) + `Create Account` link (`/register`).
      * *Authenticated*: User display (`full_name` truncated cleanly) opening menu with `My Account` (`/account`), `My Orders` (`/orders`), **conditional** `Admin Console` (`/admin`, rendered **only** when `user.role === 'staff' || user.role === 'admin'`), and `Sign Out` (`POST /api/v1/auth/logout` with `X-CSRF-Token`).
    * **Cart Trigger**: Shopping bag icon + tabular `item_count` badge (`aria-label="Open cart, {item_count} items"`). Clicking opens the global **Cart Drawer** (`Sheet` from right); badge updates announce via `aria-live="polite"`.
* **Mobile / Tablet (`< lg`)**:
  * **Left**: Hamburger menu button (`44px × 44px`) opening left navigation `Sheet` containing `Shop All`, collapsible category list, account links (`Sign In` / `My Account` / `My Orders` / `Admin Console` if staff/admin), and theme toggle.
  * **Center**: Centered brand wordmark.
  * **Right**: Search icon toggle (expands full-width inline search row directly beneath header with auto-focus) + Cart Trigger with `item_count` badge.

### 1.3 Storefront Footer
* Minimal 3-column grid (`md+`, stacked on mobile): `Shop` (top categories + `/products`), `Customer` (`My Orders`, `Account`, Free Shipping `$100+` info), `Environment Notice` (explicitly states when `sandbox_payments: true` from `/api/v1/health` that payments run in Sandbox Simulation mode and no real cards are charged or collected).

---

## 2. Home (`/`) & Product Catalog (`/products`)

### 2.1 Home Page (`/`)
1. **Editorial Hero**: Split or full-width warm-toned editorial banner with `display-xl` serif headline, concise value copy, and primary CTA `"Explore Collection"` (`/products`).
2. **Category Strip**: 3–4 curated category cards from `GET /api/v1/categories` (`name`, `description`) linking to `/products?category_slug={slug}`.
3. **New Arrivals Grid**: 4-column (`xl`) / 2-column (`sm`) preview of `GET /api/v1/products?sort=newest&page_size=8` + `"View all products"` link.
4. **Shipping & Sandbox Info Bar**: 3-item icon strip (`Complimentary shipping over $100`, `15-minute checkout stock hold`, `Sandbox-safe simulated checkout`).

### 2.2 Catalog Page (`/products`)
* **URL-Synchronized Filter State**: All filter controls map 1:1 to `GET /api/v1/products` query params so URLs are shareable and browser Back/Forward restores exact state:
  * `category_slug`: Single-select category tree/radio list (`All Categories` clears param).
  * `q`: Search keyword (`<= 100` chars; synced from header search or inline search input).
  * `min_price_cents` / `max_price_cents`: Dollar-formatted inputs (e.g., `"25"` -> `2500` cents) with validation `min <= max` (`Apply` button or debounced `400ms` commit to prevent choppy requests).
  * `in_stock`: Toggle switch `"In stock only"` (`true` / omitted).
  * `sort`: Select dropdown with labels mapped to enum values:
    * `newest` -> `"Newest"`
    * `price_asc` -> `"Price: Low to High"`
    * `price_desc` -> `"Price: High to Low"`
    * `title_asc` -> `"Title: A–Z"`
  * `page` (`>= 1`) & `page_size` (default `20`). Changing any filter resets `page=1`.
* **Responsive Layout**:
  * **Desktop (`>= lg`)**: `260px` sticky left filter sidebar (`<aside aria-label="Product filters">`) + main product grid (`3 cols` at `lg`, `4 cols` at `xl`).
  * **Mobile/Tablet (`< lg`)**: Sticky toolbar with `"Filters"` button (showing active filter count badge, opening left/bottom `Sheet` with `"Apply Filters"` and `"Clear All"`) + `Sort` dropdown. Active filters appear as dismissible chips above the grid.
* **Product Card (`ProductSummaryDTO`)**:
  * **Media**: `aspect-[4/5]` frame on `--muted` background (`object-cover`); displays `primary_image.url` with `alt={primary_image.alt_text || title}`. Shows clean typographic placeholder if `primary_image` is `null`.
  * **Metadata**: Category label (`caption`, `--muted-foreground`), Product Title (`h4`, 2-line clamp), Price display (`tabular-nums`: if `min_price_cents === max_price_cents`, `$45.00`; otherwise `From $45.00`).
  * **Availability**: When `in_stock === false`, display a muted/destructive `"Out of Stock"` badge overlaid cleanly at top-left of card (or inline below price) without hiding the card from browse results.
* **Pagination Bar**: Displays `"Showing {start}–{end} of {total} products"`, `Previous` (`disabled` on `page === 1`), page number buttons, and `Next` (`disabled` when `!has_next`). Moves focus/scroll smoothly (respecting `prefers-reduced-motion`) to top of results grid on page change.

---

## 3. Product Detail Page (`/products/[slug]`)

Consumes `GET /api/v1/products/{slug}` (`ProductDetailDTO`), where `variants[]` already include server-derived `available_quantity`, `in_stock`, and `low_stock` computed in a single joined query without N+1 per-variant calls. `GET /api/v1/variants/{variant_id}/availability` is reserved for post-conflict (`409`) revalidation or optional window-focus refresh.

### 3.1 Two-Column Layout (`lg`: `55%` Gallery / `45%` Buy Box; stacked on `< lg`)
* **Breadcrumb**: `Home / {category.name} / {product.title}` (`<nav aria-label="Breadcrumb">`).
* **Image Gallery**:
  * Primary `4:5` stage displaying active image (`url`, `alt_text || product.title`).
  * Thumbnail rail below (mobile) or vertical left rail (`lg+`) sorted by `sort_order ASC` (`is_primary` selected initially). Arrow keys move between thumbnails (`aria-current="true"`).
  * If `images` is empty, show graceful framed placeholder with product title.
* **Product Header & Pricing**:
  * `<h1>` Product Title + active variant `sku` in subtle `font-mono` (`SKU: TEE-MRN-M`).
  * **Dynamic Pricing** (`tabular-nums`): Current `price_cents` prominently in `h2`. When `compare_at_price_cents != null && compare_at_price_cents > price_cents`, show original price struck through (`line-through text-muted-foreground`) alongside an `--accent` savings badge (`Save 20%`).
* **Variant Selector**:
  * Filters `ProductDetailDTO.variants[]` to `is_active === true` variants (if a product somehow has none, shows `"Currently unavailable"`).
  * Parses `variants[].attributes` (e.g., `color`, `size`) into clean `role="radiogroup"` pill/swatch selectors. If attributes vary inconsistently across variants, falls back gracefully to a single `"Option"` `radiogroup` displaying `variant.title`.
  * Selecting an option immediately updates SKU, price, compare-at price, and stock availability directly from `ProductDetailDTO.variants[]` (`available_quantity`, `in_stock`, `low_stock`) with zero extra network calls. Out-of-stock active variants remain selectable with subtle dashed border / `"(Out of stock)"` screen-reader suffix so shoppers can view pricing and option details.
* **Live Stock Availability Badge**:
  * Driven directly by the selected variant's server-computed `available_quantity`, `in_stock`, and `low_stock` fields:
    * `in_stock && !low_stock`: `--success` dot + `"In stock"`
    * `in_stock && low_stock`: `--warning` badge + `"Low stock — only {available_quantity} left"`
    * `!in_stock || available_quantity <= 0`: `--destructive` badge + `"Out of stock"`
* **Quantity Stepper & Add to Cart**:
  * Stepper bounded `1 .. Math.min(99, available_quantity)`. Disabled when `!in_stock`.
  * Primary CTA: `"Add to Cart"` (`Button size="lg"`). When `!in_stock`, label reads `"Out of Stock"` and button is `disabled`.
  * **Submit Flow (`POST /api/v1/cart/items`)**:
    * Button enters loading state (`disabled`, `aria-busy="true"`, `"Adding..."`).
    * **`200 OK`**: Updates global Cart context, opens **Cart Drawer** (`Sheet`), announces `"Added {qty} × {product.title} to your cart"` via `aria-live="polite"`.
    * **`409 INSUFFICIENT_STOCK` / `409 VARIANT_UNAVAILABLE`**: Re-fetches `GET /api/v1/variants/{variant_id}/availability`, clamps stepper max, and displays inline alert `"Only {available} unit(s) currently available"` (or `"This option is no longer available"`).
    * **`401 AUTH_SESSION_EXPIRED`**: Displays fail-closed re-auth prompt rather than silently adding to a guest cart.
* **Product Description**: Clean editorial prose section below Buy Box.

---

## 4. Cart Drawer (`Sheet`) & Cart Page (`/cart`)

Both surfaces consume the same server-reconciled `CartDTO` (`GET /api/v1/cart`), where every `CartDTO.items[]` entry provides `product_slug`, `image_url`, `available_quantity`, `in_stock`, `low_stock`, and `is_available` derived server-side in a single joined query (zero N+1 product or availability calls), and enforce server pricing authority.

### 4.1 Line Item Reconciliation & Controls
* **Displayed Per Item**: Thumbnail (`4:5`, rendered from `item.image_url` with clean placeholder fallback when `null`), Product & Variant title (`product_title`, `variant_title`, linking directly to `/products/{item.product_slug}`), SKU (`item.sku`, `caption`), Live unit price (`unit_price_cents`) & line total (`line_total_cents`, `tabular-nums`), Low-stock cue when `item.is_available && item.low_stock` (`--warning` caption `"Only {available_quantity} left"`), Quantity Stepper (`1..Math.min(99, available_quantity)`), and Remove button (`DELETE /api/v1/cart/items/{item_id}`).
* **In-Flight Lock**: While `PATCH /api/v1/cart/items/{item_id}` or `DELETE` is pending for a row, disable that row's stepper buttons to prevent out-of-order quantity races.
* **Server-Reconciled Edge Notices** (gate Checkout whenever `items.some(i => !i.is_available)`):
  * **Out of Stock (`!item.in_stock || item.available_quantity === 0`)**: Highlight line item border with `--destructive`, display inline notice `"Out of stock — remove this item to proceed to checkout"`, and disable `"Proceed to Checkout"` button until resolved.
  * **Quantity Exceeds Available (`item.in_stock && item.available_quantity > 0 && item.quantity > item.available_quantity`)**: Highlight line item with `--warning-subtle` alert (`"Only {available_quantity} left in stock"`), provide 1-click `"Adjust to {available_quantity}"` button (`PATCH /api/v1/cart/items/{item_id}`), and block Checkout until adjusted.
  * **Variant / Product Deactivated (`!item.is_available && item.in_stock && item.quantity <= item.available_quantity`)**: Highlight line item border with `--destructive`, display inline notice `"This item is no longer available — remove it to proceed to checkout"`, and block Checkout until removed.
  * **Price Change Notice**: If live unit price differs from the price last observed in the client session state, display subtle informational chip `"Price updated to ${formatted}"`.

### 4.2 Shipping Threshold Indicator & Totals
* **Free Shipping Progress Bar** (matching server rule `FREE_SHIPPING_THRESHOLD_CENTS = 10000`, `STANDARD_SHIPPING_CENTS = 1000`):
  * When `subtotal_cents < 10000` (and `item_count > 0`): `"Add ${((10000 - subtotal_cents) / 100).toFixed(2)} more for free standard shipping"` + accessible `role="progressbar"` (`aria-valuenow={subtotal_cents}`, `aria-valuemax={10000}`).
  * When `subtotal_cents >= 10000`: `--success` text `"Complimentary standard shipping unlocked ($0.00)"`.
* **Totals Summary**:
  * `Subtotal`: `subtotal_cents`
  * `Estimated Shipping`: `estimated_shipping_cents` (`"Free"` when `0`)
  * `Tax`: `"Calculated at checkout"` (explicit note referencing server calculation)
  * `Estimated Total`: `estimated_total_cents`
* **Actions**:
  * Primary CTA: `"Proceed to Checkout"` (`/checkout`, disabled if empty or any item has `is_available === false`).
  * Secondary link (in Drawer): `"View Full Cart"` (`/cart`).
  * On `/cart` page: `"Clear Cart"` (`DELETE /api/v1/cart`) gated by confirmation `Dialog`.

### 4.3 Guest-to-User Cart Merge Flow (`POST /api/v1/cart/merge`)
1. Before login/registration, client may track pre-auth `cart.item_count` in React state (never tokens in `localStorage`) for merge-toast UX; browser JS cannot inspect the `HttpOnly` `ecom_guest_cart` cookie directly.
2. Immediately after `POST /api/v1/auth/login` (`200`) or `POST /api/v1/auth/register` (`201`) succeeds and establishes `ecom_session` + `csrf_token`:
   * Client calls `POST /api/v1/cart/merge` with `X-CSRF-Token` (safe to call unconditionally on auth completion).
   * **Idempotent No-Cookie / Empty Behavior**: `POST /api/v1/cart/merge` is idempotent and returns **`200 OK`** with the authenticated user's active `CartDTO` even if no `ecom_guest_cart` cookie exists, or the guest cart is missing, empty, or already `CONVERTED` (never fails with an error). When an active non-empty guest cart exists, the server merges its items (`min(99, available_quantity)`), marks the guest cart `CONVERTED`, and clears `ecom_guest_cart`.
   * On `200 OK`, replace client cart state with the returned `CartDTO`. If guest items were merged and any item quantity was clamped (`min(99, available_quantity)`), display an informational toast/banner: `"Your guest cart items were merged. Quantities were adjusted to current stock limits."` (or `"Your guest cart items were merged."` if un-clamped; if no guest cart existed, hydrate quietly without a merge toast).

---

## 5. Authentication Screens (`/login`, `/register`)

### 5.1 Security & Session UX Contract
* **Zero `localStorage` / `sessionStorage` Secrets**: `ecom_session` is `HttpOnly`; `csrf_token` (returned in `login`/`register`/`GET /auth/me` JSON and `ecom_csrf` cookie) is held **only in memory** (or read from `document.cookie` for `ecom_csrf` on client mutation) and attached via `X-CSRF-Token` header.
* **Safe `returnTo` Handling**: Support `?returnTo=/checkout` (or `/account`, `/admin`, etc.) **strictly validated** on the client to begin with a single `/` and not `//` or a scheme (`https://`), preventing open redirects. Default post-auth destination: `/account` (or `/admin` if `staff`/`admin` came from `/admin`).
* **Fail-Closed Expired Session Banner**: If redirected due to `401 AUTH_SESSION_EXPIRED` on a dual-auth or protected route, `/login` displays a top info/warning alert: `"Your session has expired. Please sign in to continue."`

### 5.2 Form Specifications & Validation
* **Sign In (`/login`)**:
  * `email`: `type="email"`, `autoComplete="email"`, `required`.
  * `password`: `type="password"` with accessible show/hide toggle (`aria-label="Show password"`, `aria-pressed`), `autoComplete="current-password"`, `required`.
  * Submit CTA: `"Sign In"` (loading spinner while pending).
  * Error mapping:
    * `401 AUTH_INVALID_CREDENTIALS` -> Top form alert (`role="alert"`): `"Incorrect email or password."`
    * `403 AUTH_ACCOUNT_DISABLED` -> Top form alert: `"Your account has been deactivated. Please contact an administrator."`
    * `429 RATE_LIMIT_EXCEEDED` -> Top form alert: `"Too many sign-in attempts. Please wait a moment and try again."`
* **Create Account (`/register`)**:
  * `full_name`: `1..120` chars, `autoComplete="name"`, `required`.
  * `email`: `type="email"`, `autoComplete="email"`, `required`.
  * `password`: `10..128` chars, `autoComplete="new-password"`, show/hide toggle, live requirement indicator (`"10 to 128 characters"` turns `--success` when length >= 10). Never includes any `role` field.
  * Error mapping:
    * `409 AUTH_EMAIL_ALREADY_EXISTS` -> Field error on `email`: `"An account with this email already exists."` + `"Sign in instead"` link.
    * `422 VALIDATION_ERROR` -> Map field errors from `error.details` directly below offending inputs (`aria-invalid="true"`).
    * `429 RATE_LIMIT_EXCEEDED` -> Form alert `"Too many registration attempts. Please try again shortly."`

---

## 6. Checkout & Sandbox Payment Experience (`/checkout`, `/orders/[order_number]`)

### 6.1 Checkout Form (`/checkout`)
* **Pre-Checkout Guard**: On mount, fetches `GET /api/v1/cart`. If `item_count === 0`, renders an empty-cart redirect card (`"Your cart is empty"` -> `"Browse Catalog"`). If any cart item has stock conflict, renders an alert linking back to `/cart`.
* **Layout (`lg`: 60% Form / 40% Sticky Summary; stacked on `< lg` with collapsible `"Order Summary ($XX.XX)"` accordion at top for mobile)**:
  1. **Contact Information**: `customer_email` (pre-filled for authenticated users; editable; required). Guest hint: `"We'll send your order reference and guest receipt access here."`
  2. **Shipping Address** (`shipping_address` matching API schema):
     * `full_name` (required)
     * `line1` (`Address line 1`, required)
     * `line2` (`Apartment, suite, etc. (optional)`)
     * `city` (required), `state` (`State / Province`, required), `postal_code` (required) in responsive 3-col row (`sm+`)
     * `country` (`ISO-2`, default `"US"` select/input)
     * `phone` (required, tel input)
  3. **Billing Address**:
     * Checkbox `billing_same_as_shipping` (default `true`: `"Billing address same as shipping"`).
     * When unchecked, smoothly expands identical required `billing_address` fieldset.
* **Sticky Server-Authoritative Order Summary**:
  * Lists cart items (quantity, title, variant, line price), Subtotal, Shipping (`$0.00` or `$10.00`), Estimated Tax (`8.00%` baseline preview), Estimated Total, and disclaimer: `"Final totals and stock availability are verified by the server when you place your order."`

### 6.2 Idempotent Order Submission (`POST /api/v1/checkout/orders`)
* **Idempotency-Key Lifecycle**:
  * Client generates a UUID v4 (`crypto.randomUUID()`) stored in React component state bound to the current checkout payload fingerprint.
  * If a transient network error occurs and the user retries **without editing fields**, the **same** `Idempotency-Key` header is sent (server safely replays `200 OK` without duplicate orders or double stock reservations).
  * If the user modifies address/email fields or cart contents, generate a fresh UUID v4.
* **Double-Submit Protection**: Clicking `"Place Order & Reserve Stock"` immediately sets `isSubmitting = true` (`disabled`, spinner `"Reserving stock..."`).
* **Response Handling**:
  * **`201 Created` / `200 OK`**: Clears cart badge (`0` items), navigates directly to `/orders/{order.order_number}`.
    * *Guest Receipt Access*: Browser automatically stores the server-set `HttpOnly` `ecom_guest_orders` cookie (`Path=/api/v1`, max 5 orders, 30 days). If `guest_access_token` is present in response JSON, **do NOT write it to `localStorage` or `sessionStorage`**; optionally show a one-time copyable receipt notice on the resulting order page (held in React navigation/memory state only) for opening the order in another browser.
  * **`409 INSUFFICIENT_STOCK`**: Displays `Alert` (`role="alert"`) listing the conflicting SKU/quantity from `error.details` with `"Update Cart"` button (`/cart`).
  * **`409 IDEMPOTENCY_KEY_CONFLICT`**: Regenerates UUID key and prompts `"Checkout details changed since last attempt. Please review and submit again."`
  * **`429 RATE_LIMIT_EXCEEDED`**: Explains `"Too many checkout attempts or active unpaid reservations (max 3 active pending orders per guest session). Please complete or cancel an existing pending order."`
  * **`401 AUTH_SESSION_EXPIRED`**: Halts checkout, shows re-auth banner preserving form draft in memory or prompting login (`/login?returnTo=/checkout`).

### 6.3 Pending Payment, 15-Minute Countdown & Sandbox Payment (`/orders/[order_number]`)
When `order.status === 'PENDING_PAYMENT'`:
1. **15-Minute Stock Reservation Countdown Banner**:
   * Anchored to `reservation_expires_at` (UTC ISO timestamp; fallback `+15m` from `placed_at` if inspecting list). Computes remaining `MM:SS` in `tabular-nums` against server `Date` offset.
   * **Visual States**:
     * `> 03:00`: Neutral/info banner (`"Stock reserved for {MM:SS}. Complete sandbox payment before timer expires."`)
     * `<= 03:00` and `> 00:00`: `--warning-subtle` banner (`"Reservation expires in {MM:SS} — complete payment soon."`)
     * `00:00`: Triggers a single `GET /api/v1/orders/{order_number}` refresh (and disables payment buttons while fetching). If expired -> displays `CANCELLED` (`RESERVATION_EXPIRED`) banner with `"Reorder items"` guidance.
   * **Accessibility**: `aria-live="assertive"` announces only at `05:00`, `01:00`, and `00:00` to avoid overwhelming screen readers.
2. **Sandbox Payment Gateway Card (`sandbox_simpay`)**:
   * **Explicit Zero-Card Rule**: Displays `"Sandbox SimPay · Test Payment Mode"` with `provider_intent_id` (`font-mono`), amount (`total_cents`), currency (`USD`), and intent status (`REQUIRES_ACTION` or `FAILED`). **Must never render card number (PAN), CVV, or card expiration form inputs.**
   * If `payment_intent` is missing or in `FAILED` state and order is still `PENDING_PAYMENT` (unexpired), provide `"Retry / Create Payment Intent"` (`POST /api/v1/payments/intents` with fresh `Idempotency-Key`) or allow simulating on current non-final intent.
   * **Simulation Triggers** (`POST /api/v1/payments/sandbox/{provider_intent_id}/simulate`):
     * Primary Button: `"Simulate Payment Success"` -> `{ "outcome": "succeeded", "failure_code": null }`.
       * On `200 OK` (`order_status: "CONFIRMED"`, `payment_status: "PAID"`): Re-fetches `GET /api/v1/orders/{order_number}`, removes countdown banner, displays `--success-subtle` banner `"Payment confirmed! Your order is now queued for fulfillment."`
     * Secondary Destructive-Outline Button: `"Simulate Payment Failure"` -> `{ "outcome": "failed", "failure_code": "card_declined" }`.
       * On `200 OK` (`payment_status: "FAILED"`): Re-fetches order, displays `--destructive-subtle` alert `"Simulated payment failed (card_declined). Your stock reservation remains active until {MM:SS} — you may retry payment or cancel."`
     * **`403 SANDBOX_MODE_DISABLED`**: Hides/disables simulate triggers and shows `"Sandbox payment simulation is disabled in this environment."`
     * **`409 PAYMENT_INTENT_ALREADY_FINAL`**: Refreshes `GET /api/v1/orders/{order_number}` to sync latest server state.

---

## 7. Customer Account, Order History & Order Detail (`/account`, `/orders`, `/orders/[order_number]`)

### 7.1 Account Profile (`/account`)
* Protected route (`GET /api/v1/auth/me`). Unauthenticated visits redirect to `/login?returnTo=/account`.
* Displays:
  * Read-only `email`, `role` badge (`customer` / `staff` / `admin`), member since (`created_at`).
  * Editable Profile Form: `full_name` (`1..120` chars) + `"Save Changes"` (`PATCH /api/v1/auth/me` with `X-CSRF-Token`). Disables submit when pristine or invalid; shows `--success` toast `"Profile updated"`.
  * Quick links: `"View Order History"` (`/orders`), `"Sign Out"` (`POST /api/v1/auth/logout`).

### 7.2 Order History (`/orders`)
* Authenticated users only (`GET /api/v1/orders`, ordered `placed_at DESC`).
* Displays responsive card list (`< md`) / table (`md+`) with: `order_number` (`font-mono`), `placed_at` (formatted local + UTC tooltip), `items` count, `total_cents` (`tabular-nums`), and **separate status badges** for `status`, `payment_status`, and `fulfillment_status`.
* Empty state: `"You haven't placed any orders yet."` + CTA `"Start Shopping"` (`/products`).

### 7.3 Order Detail & Self-Cancel (`/orders/[order_number]`)
* **Guest Access Recovery**:
  * Normally loads seamlessly via `ecom_guest_orders` `HttpOnly` cookie or `ecom_session`.
  * If unauthenticated and `GET /api/v1/orders/{order_number}` returns `403 FORBIDDEN` or `404 ORDER_NOT_FOUND`, render a clean `"Order Access Required"` card offering:
    1. `"Sign in to your account"` (`/login?returnTo=/orders/{order_number}`), OR
    2. `"Enter Guest Order Token"` input (submitted via `X-Guest-Order-Token` header in memory only—never saved to `localStorage`).
* **Order Detail Sections**:
  1. **Header & Status Pills**: `order_number`, `placed_at`, and clear pills for Order Status, Payment Status, and Fulfillment Status.
  2. **Action Bar**:
     * **Self-Cancel Button**: Displayed **only** when `status === 'PENDING_PAYMENT'` and `payment_status IN ('REQUIRES_PAYMENT', 'FAILED')`. Opens confirmation `Dialog` (`"Cancel Order & Release Reserved Stock?"`) with optional `reason` input (`maxLength={255}`), calling `POST /api/v1/orders/{order_number}/cancel`.
     * Once `PAID` (`CONFIRMED`, `PROCESSING`, `SHIPPED`, `COMPLETED`), the cancel button is absent; a subtle helper note clarifies `"Paid orders cannot be self-cancelled online. Contact support for refund assistance."`
  3. **Line Items Table**: Uses immutable snapshots (`product_title_snapshot`, `variant_title_snapshot`, `sku_snapshot`, `unit_price_cents`, `quantity`, `line_total_cents`).
  4. **Financial Totals**: `subtotal_cents`, `discount_cents` (shown if `> 0`), `shipping_cents`, `tax_cents`, `total_cents`.
  5. **Addresses**: Side-by-side `Shipping Address` and `Billing Address` cards.
  6. **Order Event Timeline (`order_events`)**: Vertical timeline ordered chronologically (`created_at ASC`) rendering human-friendly event titles, `from_status -> to_status` chips, and relevant `metadata_json` details (e.g., `carrier` & `tracking_number` on `ORDER_SHIPPED`, `cancellation_reason` on `ORDER_CANCELLED`).
