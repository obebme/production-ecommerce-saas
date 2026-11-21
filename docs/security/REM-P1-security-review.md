# REM-P1-SEC — Phase 1 Audit Remediation Security Review

- **Task ID**: `REM-P1-SEC`
- **Auditor**: `security` specialist ([55d38d14-2bec-4ef6-b7c8-f7434a1b7d0c](conversation://55d38d14-2bec-4ef6-b7c8-f7434a1b7d0c), strictly read-only; recorded by Orchestrator)
- **Target Baseline**: Phase 1 remediation diff vs `9c9c0e7` across `backend/**` and `frontend/**` (post-remediation re-inspection)
- **Upstream Verification Evidence Reviewed**: [REM-P1-verification.md](../qa/REM-P1-verification.md) (`ruff check .` 0 issues; `mypy` 0 issues / 55 files; `alembic heads` `0002_phase1_commerce_integrity`; SQLite `173 passed, 4 skipped`; disposable PostgreSQL 18.6 `177 passed, 0 failed, 0 skipped`; Vitest `5 files / 64 passed`; `npm run lint` / `tsc --noEmit` / `next build` 21 routes clean; Playwright E2E `4 passed`; exact `postgres:16-alpine` Docker container parity pending CI/daemon)
- **Out of Scope (Explicitly Deferred to Phase 2/3)**: `H-04` (trusted proxy IP headers), `H-07` (`.dockerignore`), `M-03`–`M-05`, `M-07`–`M-10`, `L-01`–`L-06`

## Verdict: **GO (Phase 1 Checkpoint Approved)**

- **Open Findings Summary**: **0 Critical**, **0 High**, **0 Medium**, **0 Low**, **3 Info** (all 3 prior remediation findings — `SEC-REM-P1-01`, `SEC-REM-P1-02`, `SEC-REM-P1-03` — verified resolved).

---

## 1. Resolved Findings (Verified Fixed)

### [Resolved — was Medium] SEC-REM-P1-01: Concurrent webhook rollback race overwriting terminal `PROCESSED` status
- **Location**: `backend/app/modules/payments/service.py` — `_persist_webhook_unexpected_failure` (`L469-L521`, fix at `L497-L500`)
- **Verification**:
  `_persist_webhook_unexpected_failure` now checks `if event_row.status in ("PROCESSED", "PROCESSING"): db.rollback(); return` before mutating `event_row.status = "FAILED"`. A rolled-back worker can no longer downgrade a concurrently committed `PROCESSED` (or active `PROCESSING`) `PaymentWebhookEvent` row.
- **Regression Evidence**: `backend/tests/test_orders_and_payments.py::test_sec_rem_p1_01_persist_webhook_unexpected_failure_preserves_terminal_or_active`.

### [Resolved — was Medium] SEC-REM-P1-02: Multi-intent `REFUND_PENDING` desynchronization in `_mark_admin_refund_failed` and `mark_order_refunded`
- **Locations**:
  - `backend/app/modules/admin/service.py` — `refund_admin_order` (`L894-L911`, `L1030-L1038`) and `_mark_admin_refund_failed` (`L689-L761`)
  - `backend/app/modules/orders/service.py` — `mark_order_refunded` (`L2027-L2035`)
- **Verification**:
  1. `refund_admin_order` (`L898-L904`) skips pre-existing automated `REFUND_PENDING` intents whose `PaymentRefundRequest.trigger_source != "ADMIN_REFUND"` (`DUPLICATE_PAYMENT`, `STALE_INTENT_CAPTURE`, etc.) instead of pulling them into the admin refund batch, and records `revert_to_succeeded_ids` (`L910-L911`) only for intents that started Phase A in `"SUCCEEDED"`.
  2. `_mark_admin_refund_failed` (`L735-L760`) reverts only intents in `revert_to_succeeded_ids` back to `"SUCCEEDED"` and computes `locked_order.payment_status` conservatively (`"REFUND_PENDING"` if any intent is `"REFUNDED"` or an unreverted admin-pending intent remains; `"PAID"` if any `"SUCCEEDED"` intent remains).
  3. `mark_order_refunded` (`orders/service.py:L2027-L2035`) now checks `other.status in ("SUCCEEDED", "REFUND_PENDING")` (consistent with `CANCELLED` branch `L2003`) before transitioning a paid order to `REFUNDED`, emitting `PAYMENT_INTENT_REFUNDED` instead when another intent is still `SUCCEEDED` or `REFUND_PENDING`.
- **Regression Evidence**:
  - `backend/tests/test_admin_and_analytics.py::test_sec_rem_p1_02_admin_refund_failure_preserves_duplicate_pending_sibling`
  - `backend/tests/test_orders_and_payments.py::test_sec_rem_p1_02_charge_refunded_does_not_close_order_while_sibling_refund_pending`

### [Resolved — was Low] SEC-REM-P1-03: Concurrent `refund_admin_order` Phase B duplicate calls and unaudited restock
- **Location**: `backend/app/modules/admin/service.py` — `refund_admin_order` (`L938-L953`, `L1108-L1146`)
- **Verification**:
  1. Phase A (`L938-L947`) rejects overlapping `ADMIN_REFUND` calls while a `PENDING` `PaymentRefundRequest` is within its 60-second active window (`(now_phase_a - ensure_utc_datetime(req.updated_at)) < timedelta(seconds=60)`) with `409 ConflictError(code="REFUND_IN_PROGRESS")`, while allowing stale `PENDING` requests (`>= 60s`) to recover cleanly.
  2. Phase C (`L1108-L1146`) now gates `_apply_refund_restock_if_needed(...)` inside `if not was_already_refunded:` alongside `append_order_event(...)` and `record_audit_log(...)`, eliminating unaudited `InventoryStock.on_hand` mutations.
  *(Note: When integrating a real external refund gateway in future work, also pass `str(req.id)` as an idempotency key to the provider API.)*
- **Regression Evidence**: `backend/tests/test_admin_and_analytics.py::test_sec_rem_p1_03_admin_refund_rejects_concurrent_pending_within_60s`.

---

## 2. Informational Notes (Non-Blocking)

- **[Info] SEC-REM-P1-04 (`H-04` Deferred Proxy Dependency)**: `guest_client_key_hash = hash_token(f"guest_ip:{client_ip_key}")` (`backend/app/modules/orders/service.py:L609`) uses `request.client.host` until Phase 2 `H-04` trusted-proxy IP resolution is implemented. Behind an L7 reverse proxy without `H-04`, all guests share the proxy's IP hash bucket.
- **[Info] SEC-REM-P1-05 (Sweeper Candidate Filtering)**: `_reclaim_expired_reservations_batch` (`backend/app/modules/inventory/service.py:L100-L158`) applies `limit(batch_size)` before filtering `SUCCEEDED` `PaymentIntent` rows; bounded to `100` rows with `FOR UPDATE SKIP LOCKED` so non-blocking and deadlock-free.
- **[Info] SEC-REM-P1-06 (Fallback Dialect Branch & Corner Case)**: `_claim_webhook_event_for_processing` (`backend/app/modules/payments/service.py:L395-L406`) retains a non-atomic fallback for unused dialects outside PostgreSQL/SQLite; and if an order has an already-`REFUNDED` primary intent (via out-of-band webhook) alongside an automated `DUPLICATE_PAYMENT` `REFUND_PENDING` intent (`L901` skipped) and an admin invokes `refund_admin_order`, `pending_targets` is empty (`L965`). Acceptable within Phase 1 scope.

---

## 3. Verified Phase 1 Security Controls

1. **Webhook Verification & Retry State Machine (`backend/app/modules/payments/service.py`, `payments/router.py`)**: Strict 6-step pre-DB verification (allowed provider, `65_536`-byte streamed cap, `±300s` replay window, constant-time HMAC-SHA256, non-finite JSON rejection, Pydantic validation); atomic `ON CONFLICT DO NOTHING` claiming; terminal `PROCESSED` preservation; `409 WEBHOOK_IN_PROGRESS` on `PROCESSING`; authenticated retry of `FAILED` events.
2. **Durable Refund Outbox & State Transitions (`backend/app/modules/orders/service.py`, `admin/service.py`, `0002_phase1_commerce_integrity.py`)**: `PaymentRefundRequest` unique per `payment_intent_id` with DB CHECK constraints; `REFUND_PENDING` staging for `DUPLICATE_PAYMENT`, `STALE_INTENT_CAPTURE`, `LATE_PAYMENT_OUT_OF_STOCK`, `LATE_PAYMENT_AFTER_CANCEL`, and `ADMIN_REFUND`; lock-free Phase B gateway execution; consistent cancellation of open `REQUIRES_ACTION` and `PROCESSING` intents; fulfillment/cancel/new-intent guards while `REFUND_PENDING`.
3. **Atomic DB-Backed Guest Pending-Order Cap (`backend/app/modules/orders/service.py`, `0002_phase1_commerce_integrity.py`)**: `guest_client_key_hash` with partial index `idx_orders_guest_pending_client`, namespaced `pg_advisory_xact_lock(2, hashtext(...))` acquired before checkout/row locks, expired reservation pre-reclaim, and HMAC-verified cookie + IP cap of `3` (`429 RATE_LIMIT_EXCEEDED`).
4. **Read-Only `GET /api/v1/cart` & `CartProvider` Scoping (`backend/app/modules/cart/service.py`, `frontend/src/app/layout.tsx`, `frontend/src/app/(storefront)/layout.tsx`)**: Zero `Cart` row creation and zero `Set-Cookie` emission on `GET /api/v1/cart` without an active cart; `CartProvider` scoped strictly to `(storefront)` layout (zero `/cart` fetches on `/admin/**` or root 404s).
5. **Periodic Reservation Sweeper (`backend/app/main.py`, `backend/app/cli.py`, `backend/app/modules/inventory/service.py`, `catalog/service.py`, `analytics/service.py`, `admin/service.py`)**: Bounded batches (`100` x `20`), `asyncio.to_thread` offload, `test`-env disablement, exception isolation, and strict canonical lock hierarchy with `FOR UPDATE SKIP LOCKED` + step-3b intent re-verification.
6. **Frontend UI & Auth/CSRF/XSS Safety (`frontend/src/**`)**: `REFUND_PENDING` status badges, filters, event labels, and disabled action controls align with server-side RBAC/state guards; React JSX text escaping throughout (`0` `dangerouslySetInnerHTML`, `0` `localStorage` auth/guest tokens).
