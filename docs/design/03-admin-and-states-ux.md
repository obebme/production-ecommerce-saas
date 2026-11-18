# 03 — Admin Console UX, RBAC Visibility & Global State Matrix

> **Task**: `DESIGN-001`  
> **Scope**: `(admin)` Next.js App Router workspace (`/admin/*`), role-based UI visibility, and unified `loading` / `empty` / `error` / `success` state matrix for all Storefront & Admin views.  
> **Aligned Contracts**: [openapi-contracts.md](../api/openapi-contracts.md), [02-data-model-and-erd.md](../architecture/02-data-model-and-erd.md), [03-workflows-concurrency-and-security.md](../architecture/03-workflows-concurrency-and-security.md).

---

## 1. Admin Shell (`/admin/*`) & Strict RBAC UI Visibility

### 1.1 Workspace Layout
* **Desktop (`>= lg` / `1024px`)**: Persistent collapsible left sidebar (`240px` expanded / `64px` icon rail) + Top Utility Header (`56px` height) + fluid main canvas (`max-w-[1600px]`, `p-6`).
* **Mobile / Tablet (`< lg`)**: Top bar with Sidebar Drawer trigger (`Sheet` from left, `44px × 44px` hit target) + Breadcrumb + User/Role badge.
* **Top Utility Bar**:
  * Breadcrumb trail (`Admin / Orders / ORD-20261118-8F3K2A`).
  * Environment notice badge (when `sandbox_payments: true` -> subtle `--warning` badge `"Sandbox Mode"`).
  * Current actor pill (`full_name` + Role badge: `Staff` or `Admin`).
  * `"View Storefront"` external/same-tab link (`/`) + `Sign Out` button (`POST /api/v1/auth/logout` + `X-CSRF-Token`).

### 1.2 Role-Based Navigation & Action Visibility Matrix
The frontend gates UI entry points using live `GET /api/v1/auth/me` (`user.role`, `user.is_active`), while the backend remains the authoritative RBAC boundary:

| Route / Capability | `anonymous` / `customer` | `staff` | `admin` |
| :--- | :--- | :--- | :--- |
| Entry to `/admin/*` | Unauth -> redirect `/login?returnTo=/admin`; `customer` -> `403 Access Denied` screen (no nav flash) | Allowed | Allowed |
| **Operations Nav**: `Dashboard` (`/admin`), `Products` (`/admin/products`), `Categories` (`/admin/categories`), `Inventory` (`/admin/inventory`), `Orders` (`/admin/orders`) | Hidden | **Visible** | **Visible** |
| **Admin-Only Nav Section** (`Administration` divider): `Users & Roles` (`/admin/users`), `Audit Logs` (`/admin/audit-logs`) | Hidden | **Hidden** (direct URL shows `403 Restricted to Administrators` without issuing failing fetches) | **Visible** |
| **Order Refund Action** (`/admin/orders/[order_number]`) | Hidden | **Hidden** | **Visible** (when `payment_status === 'PAID'`) |

---

## 2. Administrative Sales Analytics Dashboard (`/admin`)

Consumes `/api/v1/admin/analytics/summary`, `/timeseries`, `/top-products`, and `/inventory-health`.

### 2.1 Bounded UTC Date Range Picker
* **Presets**: `Last 7 days`, `Last 30 days` *(default)*, `Last 90 days`, `Last 365 days`, and `Custom UTC Range` (`Popover` with From / To date inputs).
* **Explicit Timezone Cue**: Header displays badge `"UTC Timezone"` so range boundaries match PostgreSQL `paid_at AT TIME ZONE 'UTC'` aggregations.
* **Validation (`<= 366` days)**: Client checks `from <= to` and `(to - from) <= 366 days` before fetching, showing inline validation `"Date range cannot exceed 366 days"` (and maps `400 INVALID_DATE_RANGE` cleanly).

### 2.2 KPI Summary Cards (`summary`)
4-column grid (`xl`), 2-column (`sm`–`lg`), 1-column (`base`):
1. **Net Revenue** (`net_revenue_cents`): Hero figure in `tabular-nums`, subtitle `"Gross minus refunds"`.
2. **Gross Revenue** (`gross_revenue_cents`): Subtitle `"Refunded: -${formatCents(refunded_revenue_cents)}"` (highlighted in `--destructive` if `> 0`).
3. **Paid Orders** (`paid_orders_count`): Integer count of recognized `PAID` orders in window.
4. **Average Order Value** (`average_order_value_cents`): Formatted currency (`$0.00` cleanly when `paid_orders_count === 0`).

### 2.3 Order Status Breakdown Pills
* Horizontal wrap row above/below chart displaying counts from `orders_by_status` (`placed_at` in window): `PENDING_PAYMENT`, `CONFIRMED`, `PROCESSING`, `SHIPPED`, `COMPLETED`, `CANCELLED`, `REFUNDED`.
* Each pill is an interactive `<Link>` to `/admin/orders?status={STATUS}&from_ts=...&to_ts=...` for immediate triage.

### 2.4 Daily Net Revenue & Paid Orders Chart (`timeseries`)
* **Visualization**: Responsive chart container (`min-h-[280px]`) displaying continuous UTC daily points (`date`, `net_revenue_cents`, `paid_orders_count`, including `0`-sale days from `generate_series`):
  * Primary series: `--chart-net-revenue` bar/area (`Net Revenue`).
  * Secondary toggleable overlay: `--chart-orders-count` line (`Paid Orders`).
* **Accessibility**:
  * Interactive tooltip showing exact UTC date, `$XX.XX` revenue, and `N` paid orders.
  * Keyboard-accessible view switch (`Chart` | `Data Table`) rendering an accessible `<table>` of daily points so screen-reader and keyboard users have full numeric parity.

### 2.5 Bottom Split Grid (`xl`: 50% / 50%)
* **Top Products & Variants (`top-products`)**:
  * Header controls: Toggle `Revenue` (`sort_by=revenue`) vs `Units Sold` (`sort_by=units`), limit (`10` / `25` / `50`).
  * Columns: Product & Variant snapshot (`product_title_snapshot` / `variant_title_snapshot`), `sku_snapshot` (`font-mono`), Units Sold, Revenue (`tabular-nums`).
* **Inventory Health Alerts (`inventory-health`)**:
  * Summary header strip: `Active SKUs` (`active_skus_count`), `Out of Stock` (`out_of_stock_count`, `--destructive` badge when `> 0`), `Low Stock` (`low_stock_count`, `--warning` badge when `> 0`), `Reserved Units` (`total_reserved_units`).
  * Paginated SKU alert table ordered by `available = on_hand - reserved ASC` (`Out of Stock` rows at top), each row offering a `"Adjust Stock"` quick link to `/admin/inventory?sku={sku}`.

---

## 3. Catalog & Variant Management (`/admin/products`, `/admin/categories`)

### 3.1 Categories (`/admin/categories`)
* **Display**: Hierarchical/indented table showing `name`, `slug` (`font-mono`), Parent Category, `sort_order`, and `is_active` badge.
* **Create / Edit Dialog** (`POST /api/v1/admin/categories`, `PATCH /api/v1/admin/categories/{category_id}`):
  * Fields: `name` (`1..120`), `slug` (auto-suggested lowercase kebab-case from `name`, manually editable), `parent_id` select (excludes current category when editing to prevent self-parenting), `description`, `sort_order`, `is_active` toggle.
  * Errors: `409 SLUG_CONFLICT` mapped to `slug` field; `422 CATEGORY_CYCLE_DETECTED` mapped to `parent_id` alert `"Selected parent would create a category cycle."`

### 3.2 Products List & Editor (`/admin/products`, `/admin/products/[id]`)
* **List View (`/admin/products`)**:
  * Consumes `GET /api/v1/admin/products` (paginated across `DRAFT`, `ACTIVE`, `ARCHIVED`).
  * Filter bar: Search `q` (`<= 100` chars), `status` (`ALL`, `DRAFT`, `ACTIVE`, `ARCHIVED`), `category_id`, paginated.
  * Status Badges: `DRAFT` (`outline` neutral), `ACTIVE` (`success`), `ARCHIVED` (`secondary` muted).
  * Row actions: `Edit` (`/admin/products/{id}`), `Archive` (`DELETE /api/v1/admin/products/{product_id}` -> confirmation `Dialog`: `"Archiving sets status to ARCHIVED and hides the product from the storefront while preserving historical order snapshots."`).
* **Product Editor (`/admin/products/[id]`)**:
  * **Deep-Linked Loading & Refresh**: Consumes `GET /api/v1/admin/products/{product_id}` (`AdminProductDetailDTO`) on initial navigation, hard refresh, and post-mutation sync across any status (`DRAFT | ACTIVE | ARCHIVED`), providing `category`, ordered `images`, and all `variants` with server-derived `on_hand`, `reserved`, `available_quantity`, `low_stock_threshold`, `in_stock`, and `low_stock`. Maps `404 PRODUCT_NOT_FOUND` to a clean Not Found recovery card linking back to `/admin/products`.
  * **Section 1 — Core Details**: `title` (`1..200`), `slug` (`1..220`, `409 SLUG_CONFLICT` inline error), `category_id`, `description`, `status` selector (`DRAFT | ACTIVE | ARCHIVED`, saved via `PATCH /api/v1/admin/products/{product_id}`).
    * **Activation Rule UX**: Products initialize as `DRAFT`. Before transitioning `DRAFT -> ACTIVE`, UI checks whether `variants.some(v => v.is_active)`. If `0` active variants exist, displays inline `--warning` callout `"Add and activate at least 1 variant before setting status to ACTIVE"` (and handles `422 PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT` cleanly).
  * **Section 2 — Images Manager (`product_images`)**:
    * Ordered list (`sort_order`), `is_primary` single-select radio, `url` input, and `alt_text` input (`1..200`).
    * **URL Validation Hint**: Explicitly displays helper `"Allowed formats: https://... or /assets/..."` and rejects `http://`, `data:`, or `javascript:` client-side before submit.
    * **Alt Text Warning**: Shows gentle inline hint if `alt_text` is empty (`"Describe the image for screen-reader accessibility"`).
  * **Section 3 — Variants & SKUs (`product_variants`)**:
    * Table of existing variants from `AdminProductDetailDTO.variants[]` (`sku`, `title`, `attributes`, `price_cents`, `compare_at_price_cents`, `is_active`, `on_hand`, `reserved`, `available_quantity` with `in_stock`/`low_stock` status badge against `low_stock_threshold`, `Edit`).
    * **Create Variant Modal** (`POST /api/v1/admin/products/{product_id}/variants`):
      * `sku` (`1..64`, unique -> `409 SKU_CONFLICT` field error)
      * `title` (`1..160`, e.g., `"Charcoal / Medium"`)
      * `attributes` key-value builder (e.g., `color`: `Charcoal`, `size`: `M`)
      * `price_cents` (dollar decimal input converted safely to integer cents `>= 0`)
      * `compare_at_price_cents` (optional; validated `>= price_cents` when present)
      * `currency` (`USD`)
      * `initial_on_hand` (`integer >= 0`, seeds `INITIAL_SEED` ledger entry)
      * `low_stock_threshold` (`integer >= 0`, default `5`)
      * `is_active` toggle (default `true`)
    * **Edit Variant Modal** (`PATCH /api/v1/admin/variants/{variant_id}`): Edits price, compare-at price, title, attributes, `low_stock_threshold`, `is_active`. Displays read-only `on_hand`, `reserved`, and `available_quantity` counts with `"Adjust stock in Inventory ->"` link (never mutates `on_hand` directly here so every stock change carries an explicit adjustment `note`). Refreshes `AdminProductDetailDTO` on save.

---

## 4. Inventory Control & Ledger (`/admin/inventory`)

### 4.1 Stock Overview Table
* Consumes `GET /api/v1/admin/inventory` (paginated `AdminInventoryItemDTO`, computed in a single joined SQL query).
* **Query Params / Filter Controls**:
  * Search `q` (`<= 100` chars; matches SKU, `product_title`, or `variant_title`).
  * `stock_status`: `all` (*"All"* — default), `in_stock` (*"In Stock"*), `low_stock` (*"Low Stock"*), `out_of_stock` (*"Out of Stock"*).
  * `page` & `page_size`.
* **Columns** (all counts `tabular-nums`, mapped to `AdminInventoryItemDTO`):
  * `SKU` (`sku`, `font-mono`)
  * `Product / Variant` (`product_title` / `variant_title`, with `product_status` and `is_active` status cues)
  * `On Hand` (`on_hand` — total physical units)
  * `Reserved` (`reserved` — units held in active `PENDING_PAYMENT` reservations)
  * `Available` (`available_quantity` — server-derived sellable units) + stock status badge (`in_stock`, `low_stock`): `--destructive` (`Out of Stock`) when `!in_stock`, `--warning` (`Low Stock`) when `in_stock && low_stock`, `--success` (`In Stock`) when `in_stock && !low_stock`
  * `Threshold` (`low_stock_threshold`)
  * `Actions`: `Adjust Stock` button + `View Ledger` button (`variant_id`).

### 4.2 Manual Stock Adjustment Modal (`POST /api/v1/admin/inventory/{variant_id}/adjust`)
* Header displays immutable current snapshot: `On Hand: {on_hand}`, `Reserved: {reserved}`, `Available: {available_quantity}`.
* **Signed Delta Input (`delta_on_hand`)**: Accepts positive (`+10`) or negative (`-2`) non-zero integers; live preview computes `Projected On Hand = on_hand + delta` and `Projected Available = projected_on_hand - reserved`.
* **Client + Server Invariant Guard**: If `on_hand + delta_on_hand < reserved` (or `< 0`), immediately highlight preview in `--destructive`, disable submit, and show `"Cannot reduce physical on-hand stock below currently reserved units ({reserved})."` (also maps `422 INVALID_STOCK_ADJUSTMENT`).
* **Mandatory Note (`note`)**: Textarea (`1..255` chars, placeholder `"e.g., Restock PO-1042 or Damaged units write-off"`). Submit button remains disabled while `note.trim().length === 0`.

### 4.3 Immutable Inventory Ledger Drawer (`Sheet`)
* Consumes `GET /api/v1/admin/inventory/{variant_id}/ledger` (paginated).
* Explicitly badged `"Append-Only Stock Ledger"` (no edit/delete controls).
* Columns: `UTC Timestamp`, `Reason` badge (`INITIAL_SEED`, `MANUAL_ADJUSTMENT`, `RESERVE`, `COMMIT`, `RELEASE`, `RETURN_RESTOCK`), `Δ On Hand` (`+10` / `-2`), `Δ Reserved` (`+1` / `-1`), `After (On Hand / Reserved)`, `Order` (link to `/admin/orders/{order_number}` if present), `Actor`, and `Note`.

---

## 5. Order Fulfillment & Refund Workbench (`/admin/orders`, `/admin/orders/[order_number]`)

### 5.1 Orders List (`/admin/orders`)
* **Filters**: `q` (order number or customer email, `<= 100` chars), `status`, `payment_status`, `fulfillment_status`, UTC `from_ts` / `to_ts`, paginated.
* **Strict Visual Separation of Status Columns**: Every row displays **distinct** badge columns so staff never confuse unpaid orders with shippable orders:
  * **Order Status** (`orders.status`): `PENDING_PAYMENT` (`warning`/`outline`), `CONFIRMED` (`accent`), `PROCESSING` (`accent`), `SHIPPED` (`default`), `COMPLETED` (`success`), `CANCELLED` (`secondary`), `REFUNDED` (`destructive` outline).
  * **Payment Status** (`orders.payment_status`): `REQUIRES_PAYMENT` (`warning`), `PAID` (`success`), `FAILED` (`destructive`), `REFUNDED` (`destructive` outline), `CANCELLED` (`secondary`).
  * **Fulfillment Status** (`orders.fulfillment_status`): `UNFULFILLED` (`outline`), `ALLOCATED` (`accent`), `SHIPPED` (`default`), `DELIVERED` (`success`), `CANCELLED` (`secondary`), `RETURNED` (`warning`).

### 5.2 Order Detail Workbench (`/admin/orders/[order_number]`)
Consumes `GET /api/v1/admin/orders/{order_number}` (full order, items, events, payment intents, reservation status).

* **Context-Aware Action Bar** (strictly driven by `orders.status`, `payment_status`, and `currentUser.role`):

| Current State | Enabled Actions (`staff` & `admin`) | Enabled Actions (`admin` ONLY) | Guard / Helper Copy |
| :--- | :--- | :--- | :--- |
| `PENDING_PAYMENT` (`REQUIRES_PAYMENT` / `FAILED`) | `Cancel Unpaid Order` (`/cancel`) | — *(no refund on unpaid)* | Fulfillment actions disabled: `"Payment must succeed before fulfillment can begin."` |
| `CONFIRMED` (`PAID`, `ALLOCATED`) | `Start Processing` (`START_PROCESSING`) or `Mark Shipped` (`SHIP`) | `Issue Refund` (`/refund`) | Self/admin cancel disabled: `"Paid orders must be refunded, not cancelled."` |
| `PROCESSING` (`PAID`, `ALLOCATED`) | `Mark Shipped` (`SHIP`) | `Issue Refund` (`/refund`) | — |
| `SHIPPED` (`PAID`, `SHIPPED`) | `Mark Completed / Delivered` (`COMPLETE`) | `Issue Refund` (`/refund`) | — |
| `COMPLETED` (`PAID`, `DELIVERED`) | *(None — terminal fulfillment)* | `Issue Refund` (`/refund`) | `"Order fulfilled and delivered."` |
| `CANCELLED` / `REFUNDED` | *(None — terminal)* | *(None)* | Displays `cancellation_reason` / refund status badge. |

* **Fulfillment Modals (`POST /api/v1/admin/orders/{order_number}/fulfill`)**:
  * `START_PROCESSING` / `COMPLETE`: Confirmation dialog with optional `note`.
  * `SHIP`: Modal with `carrier` (e.g., `"UPS"`, `"FedEx"`, `"USPS"`), `tracking_number`, and optional `note`; displays `"Mark Shipped"` CTA.
* **Unpaid Cancel Modal (`POST /api/v1/admin/orders/{order_number}/cancel`)**:
  * Explains `"Releases ACTIVE inventory reservations back to sellable stock."` Optional `reason` field.
* **Admin-Only Refund Dialog (`POST /api/v1/admin/orders/{order_number}/refund`)**:
  * **Visibility**: Rendered **strictly** when `currentUser.role === 'admin'`.
  * **Controls**:
    * Warning alert showing full order refund amount (`total_cents`).
    * `reason` input (e.g., `"Customer return"` or `"Order cancellation"`).
    * `restock` Checkbox (`"Restock returned items back to on-hand inventory (`RETURN_RESTOCK`)"` — checked by default when `status` is `CONFIRMED`/`PROCESSING`, unchecked or explicit choice when `SHIPPED`/`COMPLETED`).
    * Resulting state preview: `status -> REFUNDED`, `payment_status -> REFUNDED`, `fulfillment_status -> CANCELLED` (if unshipped) or `RETURNED` (if shipped/completed).
* **Sub-Panels**: Line items snapshot table, Payment Intents & Reservation status card (`ACTIVE` / `COMMITTED` / `RELEASED` / `EXPIRED`), Shipping/Billing addresses, and `order_events` audit timeline.

---

## 6. Users & Audit Logs (`/admin/users`, `/admin/audit-logs`, `admin` Only)

### 6.1 User & Role Management (`/admin/users`)
* **Filters**: `role` (`customer`, `staff`, `admin`), `is_active` (`Active` / `Disabled`), `q` (email/name).
* **Row Controls**:
  * `Role` selector (`customer | staff | admin`) -> `PATCH /api/v1/admin/users/{user_id}/role`
  * `Active` switch (`true | false`) -> `PATCH /api/v1/admin/users/{user_id}/status`
* **Self-Protection & Last-Admin Guards**:
  * Row where `row.id === currentUser.id` disables both Role selector and Status toggle with inline tooltip `"You cannot demote or deactivate your own account"` (plus maps `409 CANNOT_DEMOTE_SELF` and `409 CANNOT_DEACTIVATE_SELF`).
  * Maps `409 CANNOT_REMOVE_LAST_ADMIN` to an explicit alert `"Cannot remove or deactivate the last active administrator."`
* **Session Revocation Confirmation Dialog**:
  * Both role and active-status changes open a mandatory confirmation `Dialog` before calling the API:
    * *"Updating this user's role or status immediately revokes all their active sessions across devices. Continue?"*

### 6.2 Audit Logs Viewer (`/admin/audit-logs`)
* **Filters**: `resource_type` (`user`, `category`, `product`, `product_variant`, `inventory_stock`, `order`), `action`, `actor_user_id`, UTC `from_ts` / `to_ts`, paginated.
* **Table Columns**: `Timestamp (UTC)` (`font-mono`), `Actor`, `Action` badge, `Resource` (`resource_type` + `resource_id`), `IP Address` (`font-mono`), `Inspect` button.
* **Before / After JSON Diff Drawer (`Sheet`)**:
  * Opens side drawer showing metadata header (`actor`, `action`, `resource`, `ip_address`, `created_at`).
  * Renders side-by-side (`md+`) or stacked (`< md`) `before_state` and `after_state` formatted JSON blocks (`font-mono`, changed keys highlighted).
  * Handles `null` gracefully (`"—"` for creations where `before_state === null`, or `"Redacted"` markers preserved verbatim).

---

## 7. Comprehensive State Matrix (Storefront & Admin)

### 7.1 View-by-View Lifecycle States

| View | `loading` | `empty` | `error` | `success` |
| :--- | :--- | :--- | :--- | :--- |
| **Storefront Home & Catalog** (`/`, `/products`) | 8–12 card `Skeleton` grid matching 4:5 aspect ratio (zero CLS) | `"No products match your filters."` + `"Reset Filters"` CTA (or `"Catalog coming soon"` if store empty) | Inline retry `Alert` (`"Unable to load products"`) preserving current URL query params | Populated grid + live `aria-live="polite"` result count (`"Showing X of Y products"`) |
| **Product Detail** (`/products/[slug]`) | 2-col image + title/price/variant `Skeleton` | `404 PRODUCT_NOT_FOUND`: Clean `"Product unavailable"` card + `"Back to Catalog"` CTA | Variant stock conflict `409` refreshes `/availability` & clamps stepper; network error shows inline `"Retry"` | `"Added to Cart"` opens Cart Drawer + `aria-live="polite"` announcement |
| **Cart Drawer & Page** (`/cart`) | Line-item `Skeleton` rows; button `aria-busy` on stepper patch | `"Your cart is empty"` illustration + `"Explore Products"` CTA | Line-level `409 INSUFFICIENT_STOCK` / `VARIANT_UNAVAILABLE` warning + `"Adjust"` / `"Remove"` CTA | Updated subtotal, free-shipping progress bar, merge notification toast after login |
| **Login & Register** (`/login`, `/register`) | Submit button disabled + spinner (`"Signing in..."`) | N/A | Top `role="alert"` banner (`401`, `403`, `429`) or field-level inline error (`409 email`, `422`) | Idempotent `POST /cart/merge` -> navigate to sanitized `returnTo` |
| **Checkout** (`/checkout`) | Summary + form guard skeleton; submit locked during `POST /checkout/orders` | Empty cart redirects/shows `"Your cart is empty"` + link to `/products` | Preserves form inputs & `Idempotency-Key` on transient errors; `409 INSUFFICIENT_STOCK` links to `/cart` | `201` / `200` replay -> redirect to `/orders/{order_number}` |
| **Customer Order & Sandbox Pay** (`/orders/[order_number]`) | Header, items, and timeline `Skeleton` blocks | Guest without token / `403`: `"Order Access Required"` sign-in or `X-Guest-Order-Token` prompt | `403 SANDBOX_MODE_DISABLED` hides simulate controls; `409 PAYMENT_INTENT_ALREADY_FINAL` refreshes order | Countdown clears -> `--success-subtle` `"Payment Confirmed"` banner + refreshed timeline |
| **Customer Order History** (`/orders`) | Table / mobile card `Skeleton` rows | `"No orders yet"` + `"Browse Catalog"` CTA | Retryable `Alert` (`401` redirects to `/login?returnTo=/orders`) | Paginated/chronological order rows with separate status badges |
| **Admin Dashboard** (`/admin`) | 4 KPI `Skeleton` cards + `280px` chart `Skeleton` + table `Skeleton`s | Zero-sales period displays `$0.00` KPIs, flat `0` chart baseline, and `"All active SKUs healthy"` | `400 INVALID_DATE_RANGE` highlights date picker (`<= 366` days); `403` renders access error | Live UTC metrics, interactive status pills, chart + accessible data table |
| **Admin Catalog & Inventory** (`/admin/products`, `/categories`, `/inventory`) | Table row `Skeleton`s; modal save button spinner | `"No products/categories found"` + `"Create First ..."` CTA | Inline validation (`422 PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT`, `CATEGORY_CYCLE_DETECTED`, `INVALID_STOCK_ADJUSTMENT`, `409 SLUG/SKU_CONFLICT`) | Toast confirmation + refreshed table & append-only ledger drawer |
| **Admin Orders, Users, Audit** (`/admin/orders`, `/users`, `/audit-logs`) | Filter bar + table `Skeleton`s; drawer `Skeleton` | `"No matching orders / users / audit events"` + `"Clear Filters"` CTA | `409 INVALID_ORDER_STATE_TRANSITION`, `REFUND_NOT_ALLOWED`, `CANNOT_DEMOTE_SELF`, `CANNOT_DEACTIVATE_SELF`, `CANNOT_REMOVE_LAST_ADMIN` inline/dialog alert | Transitioned status badges, toast (`"Session revocation complete"`), updated timeline/diff |

### 7.2 API Error Code UX Mapping (`error.code` -> UI Behavior)

| HTTP | `error.code` | Scope | User-Facing UI Behavior |
| :---: | :--- | :--- | :--- |
| `400` | `IDEMPOTENCY_KEY_REQUIRED` | Checkout / Intents | Internal client bug guard: generate UUID v4 and prompt retry. |
| `400` | `INVALID_DATE_RANGE` | Analytics / Filters | Inline date picker error: `"Start date must be before end date and span at most 366 days (UTC)." ` |
| `401` | `AUTH_INVALID_CREDENTIALS` | `/login` | Inline form alert (`role="alert"`): `"Invalid email or password."` |
| `401` | `AUTH_SESSION_EXPIRED` | Authenticated & Dual-auth (`/cart`, `/checkout`, `/admin/*`) | **Fail-closed**: Clear client user state (never silently fall back to guest cart/checkout), display `"Session expired — please sign in again"`, link to `/login?returnTo=...`. |
| `403` | `AUTH_ACCOUNT_DISABLED` | Auth / Any route | Clear user state, show `"Account disabled. Contact an administrator."` |
| `403` | `CSRF_INVALID` | Any state mutation | Re-fetch `GET /api/v1/auth/me` to refresh `csrf_token`; if still rejected, prompt page reload or re-login. |
| `403` | `FORBIDDEN_ROLE` / `FORBIDDEN` | Admin / Guest Order | On `/admin/*` -> `403 Access Denied` screen; on `/orders/{id}` -> guest token / sign-in recovery card. |
| `403` | `SANDBOX_MODE_DISABLED` | Order payment panel | Hide/disable sandbox simulation buttons; show non-sandbox environment message. |
| `404` | `*_NOT_FOUND` | Product, Category, Variant, Cart Item, Order, Intent, User | Render clean Not Found state with safe recovery link (or refresh cart if `CART_ITEM_NOT_FOUND`). |
| `409` | `AUTH_EMAIL_ALREADY_EXISTS`, `SLUG_CONFLICT`, `SKU_CONFLICT` | Register / Admin Catalog | Highlight offending input (`email`, `slug`, `sku`) with inline uniqueness error. |
| `409` | `INSUFFICIENT_STOCK`, `VARIANT_UNAVAILABLE` | PDP / Cart / Checkout | Display `error.details.available` count; refresh availability/cart; offer `"Adjust"` / `"Return to Cart"`. |
| `409` | `CART_EMPTY`, `IDEMPOTENCY_KEY_CONFLICT` | Checkout | `CART_EMPTY` -> link to `/products`; `IDEMPOTENCY_KEY_CONFLICT` -> mint fresh UUID key & ask user to confirm payload. |
| `409` | `INVALID_ORDER_STATE_TRANSITION`, `ORDER_NOT_PAYABLE`, `PAYMENT_INTENT_ALREADY_FINAL`, `REFUND_NOT_ALLOWED` | Customer Order / Admin Order Workbench | Refresh `OrderDTO` from server immediately and show inline alert explaining current order state. |
| `409` | `CANNOT_DEMOTE_SELF`, `CANNOT_DEACTIVATE_SELF`, `CANNOT_REMOVE_LAST_ADMIN` | `/admin/users` | Keep dialog open or revert optimistic control; show specific admin-protection alert. |
| `422` | `VALIDATION_ERROR`, `PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT`, `CATEGORY_CYCLE_DETECTED`, `INVALID_STOCK_ADJUSTMENT` | Forms across Storefront & Admin | Map field-level details to inputs (`aria-invalid="true"`) or show top form validation alert. |
| `429` | `RATE_LIMIT_EXCEEDED` | Login, Register, Cart, Checkout, Guest Order Lookup | Non-retry-loop alert: `"Too many requests. Please wait a moment before trying again."` (Note 3-order pending cap on guest checkout). |
| `503` / Net | `DATABASE_UNAVAILABLE` / Offline | Global | Banner/card with `"Service temporarily unavailable"` + manual `"Retry"` button (never auto-loops mutations). |
