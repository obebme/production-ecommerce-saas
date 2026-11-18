# OpenAPI & REST API Contracts (`/api/v1`)

> **Version**: `v1` (`1.1.0-arch`)  
> **Base Path**: `/api/v1`  
> **Content-Type**: `application/json` (UTF-8)  
> **Monetary Units**: All money fields are integer minor units (`*_cents`, e.g., `1999` = `$19.99 USD`) paired with ISO-4217 `currency` (`"USD"`).  
> **Timestamps**: ISO-8601 UTC strings (`"2026-11-18T11:30:00Z"`).

---

## 1. Global Conventions

### 1.1 Authentication, Cookies & CSRF
* **`ecom_session`**: `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` (when `COOKIE_SECURE=true`). Set on `POST /auth/login` and `POST /auth/register`; cleared on `POST /auth/logout` or invalid/expired session detection (dual-auth endpoints fail closed with `401 AUTH_SESSION_EXPIRED` if a stale `ecom_session` cookie is present).
* **`ecom_csrf`**: `HttpOnly=false`, `SameSite=Lax`, `Path=/`, `Secure` (when HTTPS). Deterministically derived via `HMAC-SHA256(CSRF_HMAC_SECRET, f"{session.id}:{session.csrf_version}")`. Every state-changing request (`POST`, `PATCH`, `PUT`, `DELETE`) carrying `ecom_session` **must** send header `X-CSRF-Token: <token>` (except `login`, `register`, and HMAC webhooks).
* **Origin Enforcement**: All browser state-changing endpoints (excluding HMAC webhooks) reject `Origin: null` and reject `Origin` / `Referer` values not in `ALLOWED_ORIGINS` (or missing both headers when `Cookie` or `Sec-Fetch-Site` is present).
* **`ecom_guest_cart`**: `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` (when HTTPS), `Max-Age=2592000`. Automatically issued on anonymous `GET /cart` or `POST /cart/items`.
* **`ecom_guest_orders` / `X-Guest-Order-Token`**: Single bounded `HttpOnly`, `SameSite=Lax`, `Path=/api/v1`, `Secure` (when HTTPS), `Max-Age=2592000` cookie holding up to the 5 most recent `order_number:token` pairs (or `X-Guest-Order-Token` request header). Tokens are derived via `HMAC-SHA256(GUEST_ORDER_HMAC_SECRET, f"{order.id}:{order.order_number}:{order.guest_token_version}")` and expire at `guest_access_expires_at` (30 days).

### 1.2 Idempotency (`Idempotency-Key`)
* Required on `POST /api/v1/checkout/orders` and `POST /api/v1/payments/intents` (non-empty string `1..64` chars, recommended UUID v4).
* Server computes `SHA-256(f"{actor_scope}:{client_key}")` (`CHAR(64)`):
  * Checkout `actor_scope`: `user:{user_id}` or `guest:{guest_cart_token_hash}`. `request_fingerprint` hashes canonical `(cart_id, sorted_items[(variant_id, quantity)], customer_email, shipping_address, billing_address)`.
  * Payment intent `actor_scope`: `user:{user_id}` or `guest_order:{order.id}` (auto-created checkout intent uses internal key `SHA-256(f"checkout-init:{order.id}")`). `request_fingerprint` hashes canonical `(order_number, amount_cents, currency)`.
* Protected against concurrent identical-key races via `pg_advisory_xact_lock` on the scoped key / unique constraint re-fetch: exact payload replay returns `200 OK`; payload mismatch returns `409 IDEMPOTENCY_KEY_CONFLICT`.

### 1.3 Server-Side Shipping & Tax Rules (Baseline)
* **Shipping**: Free (`0`) when `subtotal_cents >= FREE_SHIPPING_THRESHOLD_CENTS` (default `10000`, i.e., `$100.00`), otherwise `STANDARD_SHIPPING_CENTS` (default `1000`, i.e., `$10.00`) when cart has items.
* **Tax**: `ROUND_HALF_UP((subtotal_cents - discount_cents) * TAX_RATE_BPS / 10000)` (default `TAX_RATE_BPS = 800`, i.e., `8.00%`).

### 1.4 Standard Pagination & Error Envelopes
Pagination query params: `page` (`int >= 1`, default `1`), `page_size` (`int 1..100`, default `20`). Search parameter `q` (`max_length=100`) escapes `\`, `%`, `_` before `ILIKE`.

```json
{
  "items": [],
  "total": 42,
  "page": 1,
  "page_size": 20,
  "has_next": true
}
```
Error envelope (`4xx` / `5xx`; `422` uses `"code": "VALIDATION_ERROR"`):
```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Requested quantity exceeds available stock for TEE-MRN-M.",
    "details": {
      "variant_id": "550e8400-e29b-41d4-a716-446655440000",
      "requested": 3,
      "available": 1
    },
    "request_id": "req_01JCY..."
  }
}
```

---

## 2. Health Endpoint

### `GET /api/v1/health`
* **Auth**: Public
* **200 OK**: `{ "status": "ok", "database": "ok", "sandbox_payments": true }`
* **503 Service Unavailable**: `DATABASE_UNAVAILABLE` if `SELECT 1` fails.

---

## 3. Module 1 — Identity & Access Management (`/auth`)

### `POST /api/v1/auth/register`
* **Auth**: Public (rate-limited). Revokes any prior `ecom_session`. Does not accept `role`.
* **Request**: `{ "email": "alice@example.com", "password": "StrongPassword!2026", "full_name": "Alice Mercer" }` (`password` `10..128` chars, `full_name` `1..120`).
* **201 Created** (sets `ecom_session` & `ecom_csrf` cookies):
  ```json
  {
    "user": {
      "id": "uuid",
      "email": "alice@example.com",
      "full_name": "Alice Mercer",
      "role": "customer",
      "is_active": true,
      "created_at": "2026-11-18T11:30:00Z"
    },
    "csrf_token": "hmac-derived-csrf-token"
  }
  ```
* **Errors**: `409 AUTH_EMAIL_ALREADY_EXISTS`, `422 VALIDATION_ERROR`, `429 RATE_LIMIT_EXCEEDED`.

### `POST /api/v1/auth/login`
* **Auth**: Public (rate-limited; constant-time dummy Argon2id check when email unknown; revokes prior `ecom_session`).
* **Request**: `{ "email": "alice@example.com", "password": "StrongPassword!2026" }`
* **200 OK** (sets `ecom_session` & `ecom_csrf` cookies): same schema as `register`.
* **Errors**: `401 AUTH_INVALID_CREDENTIALS`, `403 AUTH_ACCOUNT_DISABLED` *(checked only after valid password)*, `429 RATE_LIMIT_EXCEEDED`.

### `POST /api/v1/auth/logout`
* **Auth**: Authenticated + `X-CSRF-Token`. Revokes current `auth_sessions` row and clears cookies.
* **204 No Content**.

### `GET /api/v1/auth/me`
* **Auth**: Authenticated (`customer | staff | admin`).
* **200 OK**: `{ "user": { ...UserDTO }, "csrf_token": "hmac-derived-csrf-token" }`.

### `PATCH /api/v1/auth/me`
* **Auth**: Authenticated + `X-CSRF-Token`.
* **Request**: `{ "full_name": "Alice M." }` (forbids `role` and `is_active`).
* **200 OK**: `{ "user": { ...UserDTO } }`.

---

## 4. Module 2 — Product Catalog (`/categories`, `/products`)

### `GET /api/v1/categories`
* **Auth**: Public. Returns active categories sorted by `sort_order ASC, name ASC`.
* **200 OK**: `{ "items": [{ "id": "uuid", "parent_id": null, "name": "Apparel", "slug": "apparel", "description": "...", "sort_order": 1 }] }`.

### `GET /api/v1/products`
* **Auth**: Public (`status = 'ACTIVE'` products with `categories.is_active = true`).
* **Query Params**: `category_slug`, `q` (`<= 100` chars), `min_price_cents`, `max_price_cents`, `in_stock` (`bool`), `sort` (`newest | price_asc | price_desc | title_asc`), `page`, `page_size`.
* **200 OK**: Paginated envelope of `ProductSummaryDTO` (`id`, `title`, `slug`, `category`, `primary_image`, `min_price_cents`, `max_price_cents`, `currency`, `in_stock`).

### `GET /api/v1/products/{slug}`
* **Auth**: Public (`ACTIVE` product with active category).
* **Query Strategy**: Single-query/joined fetch of `products`, `categories`, `product_images`, `product_variants`, and `inventory_stocks` (zero N+1 queries; availability computed server-side as `available_quantity = GREATEST(0, on_hand - reserved)`, `in_stock = available_quantity > 0`, `low_stock = available_quantity > 0 AND available_quantity <= low_stock_threshold`).
* **200 OK** (`ProductDetailDTO`):
  ```json
  {
    "id": "uuid",
    "title": "Merino Crewneck Tee",
    "slug": "merino-crewneck-tee",
    "description": "...",
    "status": "ACTIVE",
    "category": { "id": "uuid", "name": "Apparel", "slug": "apparel" },
    "images": [
      { "id": "uuid", "url": "https://...", "alt_text": "Merino Tee front", "sort_order": 0, "is_primary": true }
    ],
    "variants": [
      {
        "id": "uuid",
        "sku": "TEE-MRN-M",
        "title": "Charcoal / Medium",
        "attributes": { "color": "Charcoal", "size": "M" },
        "price_cents": 4500,
        "compare_at_price_cents": 5500,
        "currency": "USD",
        "is_active": true,
        "available_quantity": 14,
        "in_stock": true,
        "low_stock": false
      }
    ]
  }
  ```
* **Errors**: `404 PRODUCT_NOT_FOUND`.

---

## 5. Module 3 — Variants & Inventory Availability (`/variants`)

### `GET /api/v1/variants/{variant_id}/availability`
* **Auth**: Public. Runs bounded expired-reservation cleanup before reading `available_quantity = on_hand - reserved`.
* **200 OK**: `{ "variant_id": "uuid", "sku": "TEE-MRN-M", "is_active": true, "available_quantity": 14, "in_stock": true, "low_stock": false }`.
* **Errors**: `404 VARIANT_NOT_FOUND`.

---

## 6. Module 4 — Shopping Cart (`/cart`)

### `GET /api/v1/cart`
* **Auth**: Anonymous (`ecom_guest_cart` cookie) or Authenticated (`ecom_session`). Fails closed (`401`) on invalid `ecom_session`.
* **Query Strategy**: Single joined query projecting `cart_items` with `product_variants`, `products`, `categories`, `inventory_stocks`, and primary `product_images` (zero N+1 queries).
* **200 OK** (`CartDTO`):
  ```json
  {
    "id": "uuid",
    "currency": "USD",
    "items": [
      {
        "id": "uuid",
        "variant_id": "uuid",
        "product_id": "uuid",
        "product_slug": "merino-crewneck-tee",
        "product_title": "Merino Crewneck Tee",
        "variant_title": "Charcoal / Medium",
        "sku": "TEE-MRN-M",
        "image_url": "https://...",
        "unit_price_cents": 4500,
        "quantity": 2,
        "line_total_cents": 9000,
        "available_quantity": 14,
        "in_stock": true,
        "low_stock": false,
        "is_available": true
      }
    ],
    "item_count": 2,
    "subtotal_cents": 9000,
    "estimated_shipping_cents": 1000,
    "estimated_total_cents": 10000
  }
  ```
  *Note*: `is_available` is `true` iff `categories.is_active AND products.status == 'ACTIVE' AND product_variants.is_active AND available_quantity >= quantity`.

### `POST /api/v1/cart/items`
* **Auth**: Anonymous or Authenticated (+ `X-CSRF-Token` if `ecom_session` active; rate-limited).
* **Request**: `{ "variant_id": "uuid", "quantity": 1 }` (`1 <= quantity <= 99`).
* **Checks**: `categories.is_active`, `products.status == 'ACTIVE'`, `variant.is_active`, `variant.currency == cart.currency`, and `quantity <= available_quantity`.
* **200 OK**: Full `CartDTO`.
* **Errors**: `404 VARIANT_NOT_FOUND`, `409 VARIANT_UNAVAILABLE`, `409 INSUFFICIENT_STOCK`, `422 VALIDATION_ERROR`.

### `PATCH /api/v1/cart/items/{item_id}`
* **Auth**: Cart owner (+ `X-CSRF-Token` if authenticated).
* **Request**: `{ "quantity": 3 }` (`1 <= quantity <= 99`).
* **200 OK**: Full `CartDTO`.

### `DELETE /api/v1/cart/items/{item_id}` & `DELETE /api/v1/cart`
* **Auth**: Cart owner (+ `X-CSRF-Token` if authenticated).
* **200 OK**: Updated `CartDTO`.

### `POST /api/v1/cart/merge`
* **Auth**: Authenticated + `X-CSRF-Token`.
* **Idempotent Behavior**: Reads `ecom_guest_cart` cookie if present. If no `ecom_guest_cart` cookie exists, or the guest cart is missing, empty, or already `CONVERTED`, safely returns **`200 OK`** with the authenticated user's current active `CartDTO` (without error). When an active non-empty guest cart exists, merges quantities into the user's `ACTIVE` cart (`min(99, available_quantity)`), marks the guest cart `CONVERTED`, and clears `ecom_guest_cart`.
* **200 OK**: Merged (or current) `CartDTO`.

---

## 7. Module 5 — Checkout & Orders (`/checkout`, `/orders`)

### `POST /api/v1/checkout/orders`
* **Auth**: Anonymous (rate-limited; max 3 active `PENDING_PAYMENT` reservations per guest/IP window) or Authenticated (+ `X-CSRF-Token` if `ecom_session` active). Fails closed (`401`) if `ecom_session` cookie is invalid.
* **Required Header**: `Idempotency-Key: <1..64 chars>`
* **Request** *(no prices, discounts, shipping rates, or totals accepted from client)*:
  ```json
  {
    "customer_email": "alice@example.com",
    "shipping_address": {
      "full_name": "Alice Mercer",
      "line1": "120 Market St",
      "line2": "Apt 4B",
      "city": "San Francisco",
      "state": "CA",
      "postal_code": "94105",
      "country": "US",
      "phone": "+14155550101"
    },
    "billing_same_as_shipping": true,
    "billing_address": null
  }
  ```
* **201 Created** *(or `200 OK` on idempotent replay; sets/updates bounded `ecom_guest_orders` cookie when guest)*:
  ```json
  {
    "order": {
      "id": "uuid",
      "order_number": "ORD-20261118-8F3K2A",
      "status": "PENDING_PAYMENT",
      "payment_status": "REQUIRES_PAYMENT",
      "fulfillment_status": "UNFULFILLED",
      "customer_email": "alice@example.com",
      "currency": "USD",
      "subtotal_cents": 9000,
      "discount_cents": 0,
      "shipping_cents": 1000,
      "tax_cents": 720,
      "total_cents": 10720,
      "items": [
        {
          "id": "uuid",
          "variant_id": "uuid",
          "sku_snapshot": "TEE-MRN-M",
          "product_title_snapshot": "Merino Crewneck Tee",
          "variant_title_snapshot": "Charcoal / Medium",
          "unit_price_cents": 4500,
          "quantity": 2,
          "line_total_cents": 9000
        }
      ],
      "placed_at": "2026-11-18T11:32:00Z",
      "paid_at": null
    },
    "payment_intent": {
      "id": "uuid",
      "provider": "sandbox_simpay",
      "provider_intent_id": "pi_sandbox_...",
      "amount_cents": 10720,
      "currency": "USD",
      "status": "REQUIRES_ACTION"
    },
    "reservation_expires_at": "2026-11-18T11:47:00Z",
    "guest_access_token": "hmac-guest-token-or-null"
  }
  ```
* **Errors**: `400 IDEMPOTENCY_KEY_REQUIRED`, `409 CART_EMPTY`, `409 VARIANT_UNAVAILABLE`, `409 INSUFFICIENT_STOCK`, `409 IDEMPOTENCY_KEY_CONFLICT`, `429 RATE_LIMIT_EXCEEDED`, `422 VALIDATION_ERROR`.

### `GET /api/v1/orders`
* **Auth**: Authenticated user (`customer | staff | admin`); returns only orders where `user_id == current_user.id`, ordered by `placed_at DESC`.

### `GET /api/v1/orders/{order_number}`
* **Auth**: Strictly order owner (`order.user_id == current_user.id`) or unexpired valid guest order token (`ecom_guest_orders` cookie or `X-Guest-Order-Token` header; rate-limited). Staff/admin use `/api/v1/admin/orders/{order_number}`.
* **200 OK**: Detailed `OrderDTO` (`shipping_address`, `billing_address`, `items`, `payment_intent`, `events`).
* **Errors**: `403 FORBIDDEN`, `404 ORDER_NOT_FOUND`.

### `POST /api/v1/orders/{order_number}/cancel`
* **Auth**: Strictly order owner or valid guest order token (+ `X-CSRF-Token` if authenticated).
* **Request**: `{ "reason": "Changed mind" }` (optional).
* **Precondition**: `orders.status == 'PENDING_PAYMENT'` and `orders.payment_status IN ('REQUIRES_PAYMENT', 'FAILED')`. Releases `ACTIVE` reservations (`RELEASED`).
* **200 OK**: Updated `OrderDTO` (`status = 'CANCELLED'`).
* **Errors**: `409 INVALID_ORDER_STATE_TRANSITION`.

---

## 8. Module 6 — Payments & Webhooks (`/payments`, `/webhooks`)

### `POST /api/v1/payments/intents`
* **Auth**: Order owner or valid guest order token (+ `X-CSRF-Token` if authenticated).
* **Required Header**: `Idempotency-Key: <1..64 chars>`
* **Request**: `{ "order_number": "ORD-20261118-8F3K2A" }`
* **Precondition**: Order `PENDING_PAYMENT` and reservation `ACTIVE` (`expires_at > now()`).
* **201 Created / 200 OK**: `{ "payment_intent": { ... } }`.

### `POST /api/v1/payments/sandbox/{provider_intent_id}/simulate`
* **Auth**: Order owner / valid guest order token / `staff` / `admin` (+ `X-CSRF-Token` if authenticated). Only active when `PAYMENT_SANDBOX_MODE=true` (and startup refuses `PAYMENT_SANDBOX_MODE=true` when `ENVIRONMENT=production`).
* **Request**: `{ "outcome": "succeeded" | "failed", "failure_code": null }`
* **Behavior**: Locks `payment_intent FOR UPDATE`, validates `provider == 'sandbox_simpay'` and non-final status, constructs signed payload, and invokes the webhook processor **in-process**.
* **200 OK**: `{ "event_id": "evt_sandbox_...", "webhook_status": "PROCESSED", "order_status": "CONFIRMED", "payment_status": "PAID" }`.
* **Errors**: `403 SANDBOX_MODE_DISABLED`, `404 PAYMENT_INTENT_NOT_FOUND`, `409 PAYMENT_INTENT_ALREADY_FINAL`.

### `POST /api/v1/webhooks/payments/{provider}`
* **Auth**: Public endpoint; **no session or CSRF cookie**. Allowlisted `{provider}` (`sandbox_simpay` in baseline; unknown `{provider}` returns `404 PROVIDER_NOT_FOUND`).
* **Strict Verification Order**:
  1. Verify allowlisted `{provider}` (`404`) and raw request body `<= 65536` bytes (`413 PAYLOAD_TOO_LARGE`).
  2. Parse integer `X-Webhook-Timestamp` header and enforce `abs(server_utc_now - ts) <= 300` seconds (`400 WEBHOOK_TIMESTAMP_EXPIRED`).
  3. Verify `X-Webhook-Signature: v1=<hex>` using constant-time `hmac.compare_digest` over `b"{ts}." + raw_body` (`401 WEBHOOK_SIGNATURE_INVALID`).
  4. Validate JSON/Pydantic schema (`422 VALIDATION_ERROR`).
  5. Inside DB transaction: `INSERT INTO payment_webhook_events (...) ON CONFLICT (provider, provider_event_id) DO NOTHING`. If conflict: return `200 OK { "received": true, "status": "IGNORED_DUPLICATE" }`.
  6. Lock `payment_intent` -> `order` -> `inventory_stocks` (`ORDER BY variant_id ASC FOR UPDATE`). Validate `amount_cents == order.total_cents`, `currency == order.currency`, and matching `order_number`. On mismatch, mark `payment_webhook_events.status = 'FAILED'`, commit the recorded failure event, and return `422 WEBHOOK_PAYLOAD_MISMATCH`. Otherwise execute state transition matrix, mark `PROCESSED`, commit, and return `200 OK { "received": true, "status": "PROCESSED" }`.
* **Supported `event_type` values**: `payment_intent.succeeded`, `payment_intent.failed`, `charge.refunded`.
* **Request**:
  ```json
  {
    "event_id": "evt_sandbox_01JCY...",
    "event_type": "payment_intent.succeeded",
    "data": {
      "provider_intent_id": "pi_sandbox_...",
      "order_number": "ORD-20261118-8F3K2A",
      "amount_cents": 10720,
      "currency": "USD",
      "failure_code": null,
      "failure_message": null
    }
  }
  ```

---

## 9. Modules 2, 3, 5, 6, 7 & 8 — Administrative & Analytics Contracts (`/admin/*`)

All `/api/v1/admin/*` routes require authenticated `ecom_session`, live `users.is_active == true`, `X-CSRF-Token` on all mutations, and `role IN ('staff', 'admin')` (or `role == 'admin'` where specified). **Every** mutating `/api/v1/admin/*` operation records an `audit_logs` row with secret-redacted `before_state` and `after_state`.

### `9.1 Admin Catalog & Variants` (`staff | admin`)
* `POST /api/v1/admin/categories` $\rightarrow$ `201 Created` (`audit_logs` written).
* `PATCH /api/v1/admin/categories/{category_id}` $\rightarrow$ `200 OK` (`422 CATEGORY_CYCLE_DETECTED`, `audit_logs` written).
* `GET /api/v1/admin/products`: lists `DRAFT | ACTIVE | ARCHIVED`, paginated (supports `q`, `status`, `category_id`).
* `GET /api/v1/admin/products/{product_id}`: returns **`AdminProductDetailDTO`** for deep-linked editor views/refreshes across any status (`DRAFT | ACTIVE | ARCHIVED`), including `category`, ordered `images`, and all `variants` with server-derived `on_hand`, `reserved`, `available_quantity`, `low_stock_threshold`, `in_stock`, and `low_stock` (`404 PRODUCT_NOT_FOUND`).
* `POST /api/v1/admin/products`: validates `product_images[].url` (`https://` or `/assets/...`), creates `DRAFT` product $\rightarrow$ `201 Created` (`audit_logs` written).
* `PATCH /api/v1/admin/products/{product_id}`: updates fields or `status` (`DRAFT -> ACTIVE` checks `>= 1` active variant) (`audit_logs` written).
* `DELETE /api/v1/admin/products/{product_id}`: sets `status = 'ARCHIVED'` $\rightarrow$ `200 OK` (`audit_logs` written).
* `POST /api/v1/admin/products/{product_id}/variants`: atomically creates `product_variants`, `inventory_stocks`, and `INITIAL_SEED` ledger row $\rightarrow$ `201 Created` (`audit_logs` written).
* `PATCH /api/v1/admin/variants/{variant_id}`: updates price, attributes, title, `is_active`, `low_stock_threshold` (`audit_logs` written).

### `9.2 Admin Inventory` (`staff | admin`)
* `GET /api/v1/admin/inventory`:
  * **Query Params**: `q` (`<= 100` chars, escaped `ILIKE` on SKU, product title, or variant title), `stock_status` (`all | in_stock | low_stock | out_of_stock`, default `all`), `page`, `page_size`.
  * **200 OK**: Paginated envelope of `AdminInventoryItemDTO` computed in a single joined SQL query:
    ```json
    {
      "items": [
        {
          "variant_id": "uuid",
          "product_id": "uuid",
          "sku": "TEE-MRN-M",
          "product_title": "Merino Crewneck Tee",
          "variant_title": "Charcoal / Medium",
          "product_status": "ACTIVE",
          "is_active": true,
          "on_hand": 16,
          "reserved": 2,
          "available_quantity": 14,
          "low_stock_threshold": 5,
          "in_stock": true,
          "low_stock": false,
          "updated_at": "2026-11-18T11:30:00Z"
        }
      ],
      "total": 42, "page": 1, "page_size": 20, "has_next": true
    }
    ```
* `POST /api/v1/admin/inventory/{variant_id}/adjust`: `{ "delta_on_hand": 10, "note": "Restock PO-1042" }` (`422 INVALID_STOCK_ADJUSTMENT` if `on_hand + delta < reserved` or `< 0`). Records `inventory_ledger_entries` + `audit_logs`.
* `GET /api/v1/admin/inventory/{variant_id}/ledger`: paginated `inventory_ledger_entries`.

### 9.3 Admin Orders, Fulfillment & Refunds
* `GET /api/v1/admin/orders`: filter by `status`, `payment_status`, `fulfillment_status`, `q` (escaped `ILIKE`), `from_ts`, `to_ts`, paginated (`staff | admin`).
* `GET /api/v1/admin/orders/{order_number}`: full order + events + payment intents + reservation status (`staff | admin`).
* `POST /api/v1/admin/orders/{order_number}/fulfill` (`staff | admin`): `{ "action": "START_PROCESSING" | "SHIP" | "COMPLETE", "tracking_number": "...", "carrier": "...", "note": "..." }` $\rightarrow$ `200 OK` (`409 INVALID_ORDER_STATE_TRANSITION` if unpaid or invalid step).
* `POST /api/v1/admin/orders/{order_number}/cancel` (`staff | admin`): strictly restricted to unpaid `PENDING_PAYMENT` orders; releases `ACTIVE` reservations (`RELEASED`) and sets `CANCELLED` (`409 INVALID_ORDER_STATE_TRANSITION` if paid — paid orders must use `/refund`).
* `POST /api/v1/admin/orders/{order_number}/refund` (**`admin` only**): `{ "reason": "Customer return", "restock": true }` (`payment_status == 'PAID'`). Refunds payment, sets `orders.status = 'REFUNDED'`, `payment_status = 'REFUNDED'`, `fulfillment_status = 'RETURNED'` (if `SHIPPED`/`COMPLETED`) else `'CANCELLED'`, optionally restocks `on_hand += qty` (`RETURN_RESTOCK`), and writes `audit_logs`. (`409 REFUND_NOT_ALLOWED`).

### 9.4 Admin Users & Audit Logs (**`admin` only**)
* `GET /api/v1/admin/users`: filter by `role`, `is_active`, `q` (escaped `ILIKE`), paginated.
* `PATCH /api/v1/admin/users/{user_id}/role`: `{ "role": "staff" | "customer" | "admin" }`. Revokes all active `auth_sessions` of `user_id` (`409 CANNOT_DEMOTE_SELF`, `409 CANNOT_REMOVE_LAST_ADMIN`).
* `PATCH /api/v1/admin/users/{user_id}/status`: `{ "is_active": false }`. Revokes all active `auth_sessions` of `user_id` (`409 CANNOT_DEACTIVATE_SELF`, `409 CANNOT_REMOVE_LAST_ADMIN`).
* `GET /api/v1/admin/audit-logs`: filter by `resource_type`, `actor_user_id`, `action`, `from_ts`, `to_ts`, paginated.

### 9.5 Sales Analytics (`staff | admin`)
* `GET /api/v1/admin/analytics/summary?from=...&to=...`: Returns `gross_revenue_cents`, `refunded_revenue_cents`, `net_revenue_cents`, `paid_orders_count`, `average_order_value_cents`, and `orders_by_status` (span `<= 366` days; `400 INVALID_DATE_RANGE`).
* `GET /api/v1/admin/analytics/timeseries?from=...&to=...`: Daily UTC points `{ "date": "YYYY-MM-DD", "net_revenue_cents": 10720, "paid_orders_count": 1 }`.
* `GET /api/v1/admin/analytics/top-products?from=...&to=...&sort_by=revenue&limit=10`: Top SKUs (`limit <= 50`).
* `GET /api/v1/admin/analytics/inventory-health?page=1&page_size=20`: `summary` (`active_skus_count`, `out_of_stock_count`, `low_stock_count`, `total_reserved_units`) + paginated low/out-of-stock SKU alerts.
