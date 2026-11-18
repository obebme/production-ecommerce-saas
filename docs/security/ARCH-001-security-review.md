# ARCH-001 Scoped Security & Consistency Review

- **Task ID**: `ARCH-001`
- **Reviewer**: `security` subagent ([4bd2dd34-2a8b-40f4-be0d-76033b256af6](conversation://4bd2dd34-2a8b-40f4-be0d-76033b256af6))
- **Recorded By**: Orchestrator (per read-only security agent policy)
- **Reviewed Scope**:
  - `docs/architecture/01-system-overview-and-modules.md`
  - `docs/architecture/02-data-model-and-erd.md`
  - `docs/architecture/03-workflows-concurrency-and-security.md`
  - `docs/api/openapi-contracts.md`
- **Notice**: Architectural design review only; not formal security certification or runtime verification.

---

## Resolution Status of Findings

All **5 High**, **6 Medium**, and **1 Low** architectural/consistency findings identified by the `security` subagent were resolved in the architecture and API contract documents prior to marking `ARCH-001` `verified`:

| ID | Severity | Summary | Resolution Applied |
| :--- | :--- | :--- | :--- |
| `HIGH-1` | High | Hash-only `csrf_token_hash` & `guest_access_token_hash` could not be recovered for `GET /auth/me` or guest checkout replay. | Replaced with deterministic HMAC-derived tokens (`csrf_version` on `auth_sessions` with `CSRF_HMAC_SECRET`; `guest_token_version` + `guest_access_expires_at` on `orders` with `GUEST_ORDER_HMAC_SECRET`). |
| `HIGH-2` | High | Lazy expiration on single cart variants could orphan sibling lines on multi-variant orders or invert lock ordering. | Standardized one universal lock hierarchy (`payment_intents` -> `orders` -> `inventory_stocks ORDER BY variant_id ASC FOR UPDATE`) and atomic full-order expiration pre-step (`FOR UPDATE SKIP LOCKED` on `orders` -> sorted lock on all order variants). |
| `HIGH-3` | High | Late payment webhook could resurrect explicitly cancelled orders, collide on `uq_active_succeeded_intent_per_order`, or double-decrement `reserved`. | Separated `RELEASED` (explicit cancel: never re-confirm; auto-refund) from `EXPIRED` (timeout: re-acquire only if `ACTIVE` & in stock, subtracting `on_hand` with `delta_reserved=0`); added `order.payment_status == 'PAID'` guard before setting `payment_intents.status = 'SUCCEEDED'`; defined all columns on out-of-stock refund branch. |
| `HIGH-4` | High | Conflicting paid-order admin cancel state (`CANCELLED` vs `REFUNDED`). | Restricted `/admin/orders/{order_number}/cancel` strictly to unpaid `PENDING_PAYMENT` orders; routed all paid cancellations/returns exclusively through `/admin/orders/{order_number}/refund` (`orders.status = 'REFUNDED'`). |
| `HIGH-5` | High | `VARCHAR(128)` risk on `idempotency_key`, missing `payment_intents.request_fingerprint`, cart omission in checkout fingerprint, and concurrent race. | Changed `orders.idempotency_key` & `payment_intents.idempotency_key` to `CHAR(64)` (`SHA-256(actor_scope:client_key)`), added `payment_intents.request_fingerprint`, included `cart_id` + sorted items in checkout fingerprint, and added `pg_advisory_xact_lock` / conflict handling. |
| `MEDIUM-1` | Medium | Fail-open `Origin` check, Login CSRF, unprotected guest mutations, and dual-auth silent downgrade. | Mandated strict `Origin`/`Referer` check (rejecting `Origin: null` and missing headers on browser/cookie mutations), session rotation on login/register, and fail-closed `401` on invalid `ecom_session` at dual-auth endpoints. |
| `MEDIUM-2` | Medium | Missing production fail-closed guard on `PAYMENT_SANDBOX_MODE` and loopback call risk on `/simulate`. | Added fail-closed startup invariant rejecting `PAYMENT_SANDBOX_MODE=true` or `COOKIE_SECURE=false` in `ENVIRONMENT=production`; specified locked in-process webhook invocation on `/simulate`. |
| `MEDIUM-3` | Medium | Unbounded per-order guest cookies & missing expiry. | Replaced per-order cookies with a single bounded `ecom_guest_orders` cookie (`Path=/api/v1`, `HttpOnly`, `SameSite=Lax`, `Max-Age=2592000`, max 5 pairs) + `orders.guest_access_expires_at` (30 days). |
| `MEDIUM-4` | Medium | Guest checkout inventory-hoarding DoS, auth timing leak, and missing session revalidation / role-change revocation. | Added rate limits + max 3 active `PENDING_PAYMENT` orders per guest/IP window; mandated dummy Argon2id check + post-auth `is_active` check; specified `SESSION_TTL_HOURS=168`, live per-request DB revalidation, and session revocation + `CANNOT_DEMOTE_SELF` on role changes. |
| `MEDIUM-5` | Medium | RBAC contradiction allowing `staff`/`admin` on storefront `/orders/{order_number}` and partial `audit_logs` spec. | Restricted `/orders/{order_number}*` strictly to customer/guest owner; mandated secret-redacted `audit_logs` on all mutating `/admin/*` endpoints. |
| `MEDIUM-6` | Medium | Provider slug mismatch (`sandbox` vs `sandbox_simpay`), verification ordering, and unused `IGNORED_DUPLICATE` DB status. | Unified provider slug to `sandbox_simpay`, enforced strict verification order before DB writes, removed `IGNORED_DUPLICATE` from SQL `CHECK`, and documented `charge.refunded` + `FAILED` handling. |
| `LOW-1` | Low | URL allowlist mismatch, unspecified shipping/tax rules, missing category/currency checks, and `ILIKE` escaping. | Enforced `https://` or `/assets/` URL allowlist, documented baseline shipping/tax rules, added `categories.is_active` & currency parity checks, added DB `CHECK` constraints (`reserved_after <= on_hand_after`, `discount_cents <= subtotal_cents`), and mandated `ILIKE` wildcard escaping. |
