# Security & Accessibility Audit Report — Phase 3B Remediation (`REM-P3B-SEC`)

- **Task ID**: `REM-P3B-SEC`
- **Baseline Commit**: `8ffa133` (working tree diff vs `HEAD` + new files)
- **Scope**: Read-only security, configuration, supply-chain, and accessibility review of `REM-P3B-BE`, `REM-P3B-FE`, `REM-P3B-OPS`, and `REM-P3B-QA`
- **Auditor**: `security` specialist ([a4de1790-4b70-4b85-b71e-7b69642c7e25](conversation://a4de1790-4b70-4b85-b71e-7b69642c7e25), strictly read-only; recorded by Orchestrator)
- **Upstream Evidence**: [REM-P3B-verification.md](../qa/REM-P3B-verification.md) + Orchestrator isolated Docker Compose runtime verification (`ecom_p3b_verify`)
- **Verdict**: **GO** (`0` Critical, `0` High, `0` Medium, `0` Low; `4` Info)

> **Non-Certification Notice**: This scoped read-only audit evaluates the Phase 3B remediation changes and directly executed dependency audits. It does not constitute formal PCI-DSS certification, penetration testing, or production deployment sign-off.

---

## 1. Executive Summary & Remediation Verification Matrix

All targeted security, configuration, supply-chain, and accessibility items from prior reviews (`REM-P2-SEC` and `REM-P2-M09-SEC`) have been verified in the working tree diff and supporting source files:

| Item / Control | Target Files | Verification Result | Status |
| :--- | :--- | :--- | :--- |
| `SEC-REM-P2-01`: Multi-line `X-Forwarded-For` & right-to-left parsing | `backend/app/core/security.py` | Uses `request.headers.getlist("x-forwarded-for")`, joins with `,`, iterates right-to-left, skips only trusted proxy CIDRs, and fails closed to `peer_ip` on empty/malformed hops or when all hops are trusted proxies. | **Verified** |
| `SEC-REM-P2-02`: `/0` CIDR rejection & `> 0` PG timeouts | `backend/app/core/config.py`, `backend/app/core/security.py` | `Settings._parse_trusted_proxy_ips` raises `ValueError` when `network.prefixlen == 0` (`0.0.0.0/0`, `::/0`, or `strict=False` equivalents); `resolve_client_ip` also skips `network.prefixlen == 0` as defense-in-depth. `Settings` rejects `<= 0` for `db_statement_timeout_ms`, `db_lock_timeout_ms`, and `db_idle_in_transaction_timeout_ms` when `environment in ("staging", "production")`. | **Verified** |
| `SEC-M09-01`: OpenGraph image sanitization & slug normalization | `frontend/src/app/(storefront)/products/[slug]/page.tsx`, `frontend/src/lib/format.ts` | `generateMetadata` filters `primaryImage?.url` through `sanitizeMediaUrl` (allowing only `https://` without credentials or clean `/assets/...` paths) before populating `openGraph.images`. `decodeAndValidateSlug` returns `decodedSlug.toLowerCase()` so mixed-case slugs normalize consistently across `fetchServerProductBySlug` and `ProductDetailView`. | **Verified** |
| `SEC-M09-02`: SSR error sanitization, URL hardening & page cap | `frontend/src/lib/api/server-catalog.ts` | `fetchProductBySlugImpl` catches thrown errors and returns `"Product request timed out."` (`AbortError`) or generic `"Unable to load product details."` (preventing `ECONNREFUSED` / host leakage). `resolveServerApiBaseUrl` checks control chars up front and rejects embedded credentials, query strings, and fragments. `parseCatalogSearchParams` caps `page` at `MAX_PAGINATION_PAGE` (`1_000_000`). | **Verified** |
| `SEC-M09-03`: `/cart/:path*` `no-store` caching | `frontend/next.config.mjs` | Adds `Cache-Control: no-store` on `/cart/:path*` alongside `/admin/:path*`, `/account/:path*`, `/orders/:path*`, `/checkout/:path*`, `/login`, and `/register`. | **Verified** |
| `NEXT_PUBLIC_SITE_URL` & `metadataBase` | `frontend/src/lib/site-url.ts`, `frontend/src/app/layout.tsx`, `backend/app/core/config.py`, `.env.example`, `compose.yaml`, `infra/docker/frontend.Dockerfile`, `.github/workflows/ci.yml` | `RootLayout` sets `metadataBase: resolveSiteUrl()`. `isDeployedRuntimeEnv` keys strictly off explicit `NEXT_PUBLIC_APP_ENV`/`APP_ENV`/`ENVIRONMENT` (`staging`/`production`), not `NODE_ENV`, avoiding false failures during CI `next build`. In staging/production, missing, non-`https://`, credentialed, query/fragment-bearing, or loopback/`*.localhost` URLs throw; non-origin paths are stripped via `new URL(parsed.origin)`. Backend `Settings` accepts optional `next_public_site_url` without breaking strict env parsing. | **Verified** |
| `A11Y-M09-01`: Heading hierarchy & single `<main>` landmark | `frontend/src/components/storefront/product-card.tsx`, `catalog-view.tsx`, `home-view.tsx`, `frontend/src/app/not-found.tsx`, `frontend/src/app/(storefront)/not-found.tsx` | `ProductCard` supports `headingAs?: "h2" \| "h3"` (`"h2"` on `/products` under `<h1>`, `"h3"` on `/` under `<h2>`). `HomeView` guarantees strip adds `<h2 id="home-guarantees-heading" className="sr-only">` above `<h3>` cards. Root `not-found.tsx` renders `<main id="main-content">` (`<h1>`), while `(storefront)/not-found.tsx` omits `<main>` because `(storefront)/layout.tsx` already provides `<main id="main-content">`. | **Verified** |
| Supply-chain integrity & vulnerability audits | `backend/requirements.lock`, `infra/docker/backend.Dockerfile`, `.github/workflows/ci.yml`, `docs/ops/local-development-and-ci.md` | `backend/requirements.lock` pins 34 packages with 1,057 SHA-256 hashes (every package hash-pinned). `backend.Dockerfile`, `backend-ci`, and `e2e-ci` use `pip install --require-hashes -r requirements.lock`. `ops-validate` enforces lockfile presence. Both `pip-audit` and `npm audit --audit-level=high` run in CI and passed locally with 0 findings. | **Verified** |

---

## 2. Severity-Classified Findings

- **Critical**: `0`
- **High**: `0`
- **Medium**: `0`
- **Low**: `0`
- **Info**: `4`

### Informational Notes (Non-Blocking)

#### `INFO-01` — Build-Time Evaluation of `NEXT_PUBLIC_SITE_URL` & Loopback Host Set
- **Location**: `frontend/src/lib/site-url.ts` (`L9–L73`), `frontend/src/app/layout.tsx` (`L12–L20`), `infra/docker/frontend.Dockerfile` (`L28–L32`)
- **Observation**: `RootLayout` exports a static `metadata` object (`metadataBase: resolveSiteUrl()`), so `NEXT_PUBLIC_SITE_URL` is evaluated when Next.js builds/bundles static metadata and must be passed via Docker `--build-arg` in staging/production images (documented in `docs/ops/local-development-and-ci.md`). Additionally, `LOOPBACK_HOSTS` checks `localhost`, `127.0.0.1`, `::1`, `[::1]`, `0.0.0.0`, and `*.localhost`, which relies on operators not supplying non-`.1` `127.0.0.0/8` or `[::]` literals.
- **Recommendation**: Continue enforcing `NEXT_PUBLIC_SITE_URL` and deployment environment vars at image build time in staging/production release pipelines.

#### `INFO-02` — SSR Non-OK HTTP Error Envelope Trust Boundary
- **Location**: `frontend/src/lib/api/server-catalog.ts` (`L265–L290`)
- **Observation**: `fetchProductBySlugImpl` sanitizes all thrown network/abort errors (`catch (err)`) to `"Product request timed out."` or `"Unable to load product details."`. For non-404 HTTP error responses (`!res.ok`), it reads `errBody.error.message` from the internal backend API (`INTERNAL_API_BASE_URL`).
- **Recommendation**: No change required; keep backend error handlers free of stack traces or internal hostnames in `error.message` (current FastAPI error handlers already enforce structured envelopes).

#### `INFO-03` — CI Dev Tooling Installation & Dockerfile Lockfile Fallback
- **Location**: `.github/workflows/ci.yml` (`L205–L220`), `infra/docker/backend.Dockerfile` (`L26–L38`)
- **Observation**: Production container builds (`backend.Dockerfile`) and `e2e-ci` install strictly from `requirements.lock` via `--require-hashes`. In `backend-ci`, unpinned test/audit tools (`ruff mypy pytest httpx pip-audit`) are installed after `requirements.lock` in the ephemeral CI runner, and `backend.Dockerfile` retains a fallback to `requirements.txt`/`pyproject.toml` if `requirements.lock` is absent (guarded by `ops-validate` failing if `backend/requirements.lock` is missing).
- **Recommendation**: Acceptable as-is; optionally remove the `requirements.txt` fallback in `backend.Dockerfile` in a future ops cleanup if hash-only builds should always be mandatory even outside CI.

#### `INFO-04` — Local Docker / PG 16 Parity Scope
- **Location**: `docs/qa/REM-P3B-verification.md` (`L26`, `L38`, `L82`)
- **Observation**: QA verified unit, integration, build, and Playwright E2E checks against disposable PostgreSQL `18.6` on Windows (`216/216` backend PG tests, `87/87` Vitest, `5/5` E2E), and Orchestrator verified `postgres:16-alpine` + Docker Compose `--require-hashes` image builds and smoke tests locally via `ecom_p3b_verify`. GitHub-hosted CI execution (`ci.yml`) remains unexecuted remotely without a configured Git remote.

---

## 3. Direct Verification Evidence Executed During Audit

| Check | Command | Outcome |
| :--- | :--- | :--- |
| Working tree & diff scope | `git status --short; git rev-parse --short HEAD; git diff --stat HEAD` | `HEAD` at `8ffa133`; 22 modified files and 4 untracked files inspected. |
| Lockfile hash completeness | `.\.venv\Scripts\python.exe` inspection of `backend/requirements.lock` | `34` pinned packages, `1057` `--hash=sha256:...` entries, `0` unhashed packages. |
| Backend CVE scan | `uvx pip-audit -r requirements.lock --disable-pip` (`backend/`) | `No known vulnerabilities found` (exit `0`). |
| Frontend CVE scan | `npm.cmd audit --audit-level=high` (`frontend/`) | `found 0 vulnerabilities` (exit `0`). |

---

## 4. Final Verdict & Next Action

- **Verdict**: **GO** — All Phase 3B security, configuration, supply-chain, and accessibility controls are properly implemented and verified with zero open Critical, High, Medium, or Low findings.
- **Next Action**: Recorded at `docs/security/REM-P3B-security-review.md`; mark `REM-P3B-SEC` as `verified` in `docs/TASKS.md` and commit Phase 3B on `main`.
