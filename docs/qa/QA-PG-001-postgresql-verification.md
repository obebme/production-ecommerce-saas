# QA-PG-001 — Live PostgreSQL Integration & Concurrency Verification

- **Task ID**: `QA-PG-001` (`backend/tests/**`, `docs/qa/**`)
- **Author**: `qa` specialist
- **Execution Runner**: Orchestrator (outside Windows sandbox)
- **Overall Result**: **PASSED** (`127 passed, 0 failed, 0 skipped, 1 warning in 28.88s`, `PYTEST_EXIT=0`)
- **Scope & Compatibility Notice**: Executed against an isolated ephemeral **PostgreSQL 18.6** instance (`PostgreSQL 18.6 on x86_64-windows, compiled by msvc-19.44.35229, 64-bit`) because `postgres:16-alpine` via Docker was unavailable (`dockerDesktopLinuxEngine` daemon pipe not running and no local PostgreSQL 16 installation). This serves as **supplementary compatibility verification**; exact target **PostgreSQL 16** container parity remains pending a running Docker Desktop Linux engine or CI PostgreSQL 16 job.

---

## 1. Environment Isolation & Least-Privilege Provisioning

- **Docker Target (`postgres:16-alpine`)**: Unavailable (`dockerDesktopLinuxEngine` daemon pipe not running).
- **Host PostgreSQL Service (`postgresql-x64-18` on `127.0.0.1:5432`, `E:\databasesql\data`)**: Completely untouched (zero queries, roles, databases, or files modified).
- **Ephemeral Cluster**:
  - Initialized in a temporary scratch directory via `E:\databasesql\bin\initdb.exe` and started via `pg_ctl.exe` bound strictly to `127.0.0.1:53983`.
  - Stopped via `pg_ctl stop -m immediate -w` and completely deleted after verification.
- **Least-Privilege Role**:
  - Provisioned `ecom_test_role` owning disposable database `ecom_test_db` with `GRANT ALL ON SCHEMA public TO ecom_test_role`.
  - Verified directly from `pg_roles` (`('ecom_test_role', False, False, False, True, False)`): `rolsuper=False`, `rolcreatedb=False`, `rolcreaterole=False`, `rolcanlogin=True`, `rolbypassrls=False`.
  - `TEST_DATABASE_URL` was supplied exclusively in subprocess environment memory, never written to `.env` or repository files, and never logged.

---

## 2. Pytest Execution Summary

Executed outside the Windows sandbox as a single bounded run (`pytest -v` with `TEST_DATABASE_URL` pointing to the ephemeral cluster):

| Metric / Target | Outcome |
| :--- | :--- |
| Full backend suite (`pytest -v`) | **`127 passed, 0 failed, 0 skipped, 1 warning in 28.88s`** (`PYTEST_EXIT=0`) |
| `tests/test_schema_and_migrations.py::test_alembic_live_postgresql_upgrade_and_downgrade` | **PASSED** (`alembic upgrade head` across all 17 tables/constraints/indexes + `alembic downgrade base`) |
| `tests/test_variants_and_inventory.py::test_live_postgresql_be002_constraints_jsonb_partial_index_and_concurrency` | **PASSED** (PostgreSQL types, 8 CHECK constraints, partial unique index, HTTP NUL/page guards, `FOR UPDATE SKIP LOCKED`, concurrent adjustments & multi-order reclamation) |

---

## 3. PostgreSQL Invariants & Concurrency Cases Verified

Inside `test_live_postgresql_be002_constraints_jsonb_partial_index_and_concurrency`:

1. **Server Version & In-Test Role Verification**:
   - Verified `SELECT current_setting('server_version')` (`PostgreSQL 18.6`) and asserted `rolsuper is False`, `rolcreatedb is False`, `rolcreaterole is False` for `current_user` (`ecom_test_role`).
2. **PostgreSQL Native Types (`JSONB`, `INET`)**:
   - `product_variants.attributes_json`: verified `pg_typeof(attributes_json)::text == 'jsonb'` along with `@>` containment and `->>` extraction queries.
   - `audit_logs.ip_address`: verified via `TestClient(pg_app, client=("203.0.113.42", 50000))` on `PATCH /api/v1/admin/products/{id}` that `pg_typeof(ip_address)::text == 'inet'`, `host(ip_address) == '203.0.113.42'`, and `PRODUCT_UPDATED` audit metadata persisted accurately.
3. **Database `CHECK` Constraints & Partial Unique Index (Savepointed `IntegrityError` Probes)**:
   - `ck_inventory_stocks_on_hand_non_negative` (`on_hand < 0`)
   - `ck_inventory_stocks_reserved_non_negative` (`reserved < 0`)
   - `ck_inventory_stocks_reserved_lte_on_hand` (`reserved > on_hand`)
   - `ck_inventory_stocks_low_stock_threshold_non_negative` (`low_stock_threshold < 0`)
   - `ck_product_variants_price_non_negative` (`price_cents < 0`)
   - `ck_product_variants_compare_price_valid` (`compare_at_price_cents < price_cents`)
   - `ck_categories_parent_not_self` (`parent_id = id`)
   - `ck_inventory_ledger_entries_reason_valid` (disallowed ledger `reason`)
   - `uq_product_primary_image` partial unique index: rejects duplicate `is_primary=True` image per product while permitting multiple `is_primary=False` images and clean `PATCH /api/v1/admin/products/{id}` primary-image replacement.
4. **Live PostgreSQL `MEDIUM-1` & `MEDIUM-2` HTTP Regression Checks**:
   - `GET /api/v1/products/item%00` -> `404 PRODUCT_NOT_FOUND` (confirming pre-query slug validation prevents `psycopg.DataError` `500` on NUL bytes).
   - `GET /api/v1/products?page=1000000` -> `200 OK` (`items == []`, `has_next is False`) and `GET /api/v1/products?page=1000001` -> `422 VALIDATION_ERROR`.
5. **Row-Locking & Multi-Threaded Concurrency**:
   - `FOR UPDATE SKIP LOCKED` probe: while one session holds `FOR UPDATE` on an expired `PENDING_PAYMENT` order, `reclaim_expired_reservations_for_variants` skips it immediately (`0` reclaimed) without blocking or deadlocking.
   - Single-variant barrier-synchronized concurrency (`6` threads): `4` concurrent `POST /api/v1/admin/inventory/{id}/adjust` calls (`+3, +5, -2, +4`) + `2` concurrent `reclaim_expired_reservations_for_variants` workers -> all adjusts returned `200`, `sum(reclaimed) == 1`, final `on_hand=30`, `reserved=0`, `version=6`, `6` ledger entries.
   - Overlapping multi-order concurrency & `PAID` protection (`HIGH-1`, `6` threads on `threading.Barrier(6)`): `3` expired `PENDING_PAYMENT` orders across overlapping pairs `(mv1, mv2)`, `(mv2, mv3)`, `(mv1, mv3)` plus `1` expired-timestamp `payment_status == "PAID"` order on `mv1`, executed concurrently across `3` `reclaim_expired_reservations_for_variants` threads and `3` `POST /api/v1/admin/inventory/{vid}/adjust` (`+5, -3, +4`) threads -> zero deadlocks/500s, `sum(reclaimed) == 3`, all `3` eligible orders transitioned to `CANCELLED` (`EXPIRED` reservations), the `PAID` order + its `ACTIVE` reservation remained untouched, and final `(on_hand, reserved, version, ledger_count)` reconciled exactly to `(45, 2, 4, 4)`, `(37, 0, 4, 4)`, `(44, 0, 4, 4)`.

---

## 4. Remaining Unresolved Limitation

- **Target PostgreSQL 16 Parity**: Exact runtime verification against **`postgres:16-alpine`** (via local `docker compose` or a CI PostgreSQL 16 service container) remains **unverified** until Docker Desktop Linux engine / CI PostgreSQL 16 is available.
