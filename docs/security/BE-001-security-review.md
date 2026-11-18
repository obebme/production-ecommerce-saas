# BE-001 Scoped Security & Architecture Conformance Review

- **Task ID**: `BE-001` (`backend/**`)
- **Auditor**: `security` subagent ([ed6d61aa-f9e5-46c7-bb52-dcc74bc74bc7](conversation://ed6d61aa-f9e5-46c7-bb52-dcc74bc74bc7))
- **Recorded By**: Orchestrator (per read-only security agent policy)
- **Notice**: Static code and configuration review plus SQLite/TestClient regression checks; live PostgreSQL 16 and Docker runtime verification remain unverified until executed against a running PostgreSQL/Docker host. Not formal security certification.

---

## Findings & Remediation Verification

| ID | Severity | Summary | Resolution & Verification Status |
| :--- | :--- | :--- | :--- |
| `CRITICAL` / `HIGH` | — | None identified | 0 Critical, 0 High findings. |
| `MEDIUM-1` | Medium | `verify_csrf_token` passed raw Starlette `latin-1` header strings directly to `hmac.compare_digest`, raising `TypeError` (`500`) on non-ASCII `X-CSRF-Token`. | **Resolved** in `app/core/security.py` (`isascii()` guard + `.encode("ascii")`). Verified by `test_non_ascii_csrf_token_returns_403_not_500`. |
| `MEDIUM-2` | Medium | Production `ALLOWED_ORIGINS` validation allowed `http://`, `"null"`, scheme-less hosts, and path-bearing URLs. | **Resolved** in `app/core/config.py` (`_validate_and_normalize_origin` enforces `https://` in `production`, valid `hostname`, zero userinfo/paths/queries/fragments, default port normalization). Verified by `test_production_allowed_origins_*`. |
| `MEDIUM-3` | Medium | Production `Settings` lacked DB placeholder/`sqlite` checks and whitespace/distinctness checks across HMAC secrets. | **Resolved** in `app/core/config.py` (enforces `postgresql://`/`postgresql+psycopg://`, rejects `change_me`/`ci_test_only` case-insensitively in DB & secrets, rejects untrimmed secrets, requires 3 distinct HMAC secrets). Verified by `test_production_settings_*`. |
| `LOW-1` | Low | Single-value header lookup on duplicate `Origin`/`Referer`/`Sec-Fetch-Site` and prefix-based webhook skip. | **Resolved** in `app/core/security.py` (rejects duplicate headers via `getlist`; exact `_WEBHOOK_PATH_RE` match). Verified by `test_duplicate_origin_referer_and_sec_fetch_site_headers_rejected`. |
| `LOW-2` | Low | `SlidingWindowRateLimiter` coupled bucket pruning to caller window and lacked active-key eviction at `max_keys`. | **Resolved** in `app/core/security.py` (`OrderedDict` per-bucket `window_seconds` + LRU eviction at `max_keys`). Verified by `test_sliding_window_rate_limiter_per_bucket_windows_and_max_keys_eviction`. |
| `LOW-3` | Low | `\x00` NUL bytes in `full_name` could trigger PostgreSQL `DataError` (`500`); `password` lacked `repr=False`. | **Resolved** in `app/modules/identity/schemas.py` (`_validate_and_strip_full_name` rejects control chars `< 0x20` & `0x7F`; `repr=False` on `password`). Verified by `test_full_name_rejects_control_characters_on_register_and_patch` & `test_auth_request_schemas_hide_password_in_repr`. |
| `LOW-4` | Low | `backend/requirements.txt` uses open-ended `>=` constraints instead of pinned `==` / lockfile. | **Deferred / Open Risk**: Retained `>=` ranges during Python 3.14 local / Python 3.12 CI build phase; add a compiled lockfile or exact `==` pins before staging/production release. |
| `INFO-1..3` | Info | Missing `Cache-Control: no-store` on `/auth/*`, single-session revocation on inactive user detection, docs enabled in prod. | **Resolved** (`router.py` sets `Cache-Control: no-store`; `dependencies.py` calls `revoke_all_user_sessions` on `not user.is_active`; `main.py` disables `/docs`/`/redoc`/`/openapi.json` in `production`). Verified by `pytest` regression suite (`90 passed, 1 skipped`). |
