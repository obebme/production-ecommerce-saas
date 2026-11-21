# REM-P2-M09-SEC — Storefront SSR, Dynamic Metadata, HTTP 404 & Asset Review

- **Task ID**: `REM-P2-M09-SEC`
- **Auditor**: `security` specialist ([309b9330-dcb0-412c-bd11-4510ff3965ca](conversation://309b9330-dcb0-412c-bd11-4510ff3965ca), strictly read-only; recorded by Orchestrator)
- **Target Baseline**: Working tree diff vs `HEAD` `470419a` for `REM-P2-M09`
- **Upstream Verification Evidence Reviewed**: [REM-P2-M09-verification.md](../qa/REM-P2-M09-verification.md) (`npm.cmd run lint` 0 warnings/errors; `npx.cmd tsc --noEmit` 0 errors; Vitest `6 files / 81 passed`; `npm.cmd run build` 21 routes with `/`, `/products`, `/products/[slug]` built as `ƒ (Dynamic)`; Playwright live Chromium E2E `5 passed` against disposable PostgreSQL 18.6, FastAPI `uvicorn`, and `next start`)

## Verdict: **GO (Approved — 0 Critical / 0 High / 0 Medium; 4 Low & 5 Info Non-Blocking Items)**

- **Findings Summary**: **0 Critical**, **0 High**, **0 Medium**, **4 Low**, **5 Info**.
- **Overall Assessment**: The `REM-P2-M09` SSR catalog integration cleanly enforces server/client module isolation, safe URL resolution, strict slug regex filtering, `URLSearchParams` query encoding, `cache: "no-store"` + `force-dynamic` freshness, real HTTP `404` status via `notFound()`, and valid PNG visual assets under `/assets/products/*.png`. No blocking security or accessibility defects were found; four Low defense-in-depth / semantic improvements and five Info observations are documented below.

---

## 1. Inspected Files & Verification Commands

### Files Inspected
- `frontend/src/lib/api/server-catalog.ts` (`L1-L282`)
- `frontend/src/app/(storefront)/page.tsx` (`L1-L24`)
- `frontend/src/app/(storefront)/products/page.tsx` (`L1-L62`)
- `frontend/src/app/(storefront)/products/[slug]/page.tsx` (`L1-L92`)
- `frontend/src/app/not-found.tsx` (`L1-L24`)
- `frontend/src/components/storefront/home-view.tsx` (`L1-L316`)
- `frontend/src/components/storefront/catalog-view.tsx` (`L1-L647`)
- `frontend/src/components/storefront/product-detail-view.tsx` (`L1-L785`)
- `frontend/src/components/storefront/product-card.tsx` (`L1-L63`)
- `frontend/src/components/shared/product-image.tsx` (`L1-L60`)
- `frontend/src/lib/format.ts` (`L1-L243`)
- `frontend/next.config.mjs` (`L1-L94`)
- Supporting context inspected: `frontend/src/app/layout.tsx`, `frontend/src/app/(storefront)/layout.tsx`, `frontend/src/app/(storefront)/cart/page.tsx`, `frontend/src/app/globals.css`, `frontend/src/components/shared/skip-link.tsx`, `frontend/src/components/shared/state-feedback.tsx`, `frontend/src/components/storefront/header.tsx` (`L1-L250`), `frontend/src/components/storefront/footer.tsx` (`L1-L120`), `frontend/src/components/ui/alert.tsx`, `frontend/src/lib/api/types.ts` (`L1-L150`), `backend/app/modules/catalog/router.py` (`L1-L120`), `backend/app/modules/catalog/schemas.py` (`L1-L250`), `backend/app/modules/catalog/service.py` (`L1-L520`), `backend/app/core/pagination.py` (`L1-L33`), `compose.yaml`, `.env.example`, `docs/qa/REM-P2-M09-verification.md`.

### Read-Only Commands Executed
- `git status --short` & `git diff --stat HEAD` & `git diff HEAD -- frontend/src/` — confirmed scoped `REM-P2-M09` and `REM-P2-M09-QA` changes.
- `Get-ChildItem -Recurse -File -Include *.ts,*.tsx frontend/src | Select-String -Pattern "server-catalog"` — confirmed `server-catalog.ts` is imported exclusively by `(storefront)/page.tsx`, `(storefront)/products/page.tsx`, and `(storefront)/products/[slug]/page.tsx` (zero `"use client"` imports).
- `Get-ChildItem -Recurse -File -Include *.ts,*.tsx frontend/src | Select-String -Pattern "dangerouslySetInnerHTML"` — 0 matches across `frontend/src`.
- `Get-ChildItem -File "frontend/public/assets/products/*.png"` + PNG header byte check — all 8 PNG assets (`canvas-tote.png`, `ceramic-table-lamp.png`, `leather-weekender.png`, `linen-overshirt.png`, `merino-crewneck.png`, `stoneware-mug-set.png`, `walnut-serving-board.png`, `wool-throw-blanket.png`, sizes `43,950`–`91,426` bytes) exist and start with valid PNG magic bytes (`137 80 78 71 13 10 26 10` / `89 50 4E 47 0D 0A 1A 0A`).

---

## 2. Severity-Classified Findings

### [Low] SEC-M09-01: `generateMetadata` emits `primaryImage.url` without `sanitizeMediaUrl` defense-in-depth
- **Category**: Metadata / SSR HTML Safety
- **Location**: `frontend/src/app/(storefront)/products/[slug]/page.tsx` (`L48-L61`) vs `frontend/src/components/shared/product-image.tsx` (`L27`)
- **Description**: `ProductImage` validates every image URL through `sanitizeMediaUrl(src)` (`https://` without credentials or `/assets/<path>` without traversal/control chars). By contrast, `generateMetadata` assigns `primaryImage.url` directly to `openGraph.images` (`[{ url: primaryImage.url, alt: primaryImage.alt_text || product.title }]`) without calling `sanitizeMediaUrl`.
- **Impact**: Low — Backend catalog write schemas (`backend/app/modules/catalog/schemas.py:L73-L127`) enforce the same allowlist on persistence and Next.js HTML-escapes `<meta content="...">` attributes (preventing script injection). However, bypassing `sanitizeMediaUrl` removes frontend defense-in-depth if legacy/external API data ever returns an unexpected scheme (e.g., `http:` or `data:`) for OpenGraph scrapers.
- **Recommendation**: Resolve `const safeImageUrl = sanitizeMediaUrl(primaryImage?.url);` in `generateMetadata` and include `openGraph.images` only when `safeImageUrl` is non-null.

### [Low] SEC-M09-02: `fetchProductBySlugImpl` forwards raw `err.message` from `fetch()` into SSR `initialError` props
- **Category**: SSR Error Hygiene / Information Disclosure
- **Location**: `frontend/src/lib/api/server-catalog.ts` (`L269-L274`), `frontend/src/app/(storefront)/products/[slug]/page.tsx` (`L91`)
- **Description**: Unlike `fetchServerCategories` (`L223-L225`) and `fetchServerProducts` (`L243-L245`), which swallow exceptions and return `null`, `fetchProductBySlugImpl` returns `err instanceof Error ? err.message : "Unable to load product details."` on network/abort exceptions. `ProductDetailPage` passes that message into `<ProductDetailView slug={decodedSlug} initialError={result.message} />`, serializing it in RSC props and rendering it in `ErrorState`.
- **Impact**: Low — Standard Node `fetch` typically yields generic strings (`"fetch failed"` or `"This operation was aborted"`), and `resolveServerApiBaseUrl` throws generic messages, so secrets are not exposed; however, runtime/proxy-specific exceptions containing internal hostname/port details (e.g., `INTERNAL_API_BASE_URL`) could leak to SSR HTML/RSC payloads.
- **Recommendation**: In `fetchProductBySlugImpl`'s `catch` block, return a fixed user-safe message (e.g., `"Unable to load product details."` or `"Product request timed out."` for `AbortError`) instead of raw `err.message`.

### [Low] SEC-M09-03: `/cart` page route lacks explicit `Cache-Control: no-store` header in `next.config.mjs`
- **Category**: Cache Hygiene
- **Location**: `frontend/next.config.mjs` (`L63-L90`), `frontend/src/app/(storefront)/cart/page.tsx` (`L1-L11`)
- **Description**: `next.config.mjs` sets `Cache-Control: no-store` on `/admin/:path*`, `/account/:path*`, `/orders/:path*`, `/checkout/:path*`, `/login`, and `/register`, but omits `/cart` (or `/cart/:path*`).
- **Impact**: Low — `/cart` (`CartPage`) currently renders a static client shell (`CartPageView`) with no cart contents or PII in the SSR HTML, and backend `SensitiveRouteNoStoreMiddleware` enforces `Cache-Control: no-store` on `/api/v1/cart`. Adding `/cart/:path*` to `next.config.mjs` closes the route-level parity gap and prevents intermediate caching if `/cart` ever renders session-aware content.
- **Recommendation**: Add `{ source: "/cart/:path*", headers: [{ key: "Cache-Control", value: "no-store" }] }` to `headers()` in `frontend/next.config.mjs`.

### [Low] A11Y-M09-01: Skipped heading level (`h1` -> `h3`) on `/products` and missing `<main>` landmark on root unmatched 404 routes
- **Category**: Accessibility & 404 Semantics
- **Location**:
  - `frontend/src/components/storefront/catalog-view.tsx` (`L424`) & `frontend/src/components/storefront/product-card.tsx` (`L40`)
  - `frontend/src/components/storefront/home-view.tsx` (`L272-L313`)
  - `frontend/src/app/not-found.tsx` (`L4-L24`)
- **Description**:
  1. On `/products`, `CatalogView` renders `<h1>` (`L424`) and renders `<ProductCard>` items whose titles are hardcoded as `<h3>` (`product-card.tsx:L40`) with no intervening `<h2>`, skipping heading level `h2`. Similarly, section 4 (`aria-label="Storefront guarantees"`) in `HomeView` (`L272-L313`) uses `<h3>` headings without a section `<h2>`.
  2. `frontend/src/app/not-found.tsx` (`L6`) wraps content in a `<div>`. While `notFound()` inside `(storefront)/products/[slug]/page.tsx` renders inside `(storefront)/layout.tsx`'s `<main id="main-content">` (with skip link, header, and footer), unmatched root routes (e.g., `/unknown-path`) render `app/not-found.tsx` directly inside `RootLayout` (`app/layout.tsx`) with no `<main>` landmark or navigation shell.
- **Impact**: Low — Screen reader heading-level navigation (`h1` -> `h2` -> `h3`) is interrupted on `/products`, and root-level 404 pages lack a `<main>` landmark. Contrast tokens in `not-found.tsx` (`#171615`/`#57534E` on `#FAF9F6` in light mode; `#F5F4F0`/`#A8A29E` on `#111110` in dark mode) and focus styles remain compliant.
- **Recommendation**: Allow `ProductCard` to accept an optional heading level (`headingAs?: "h2" | "h3"`, defaulting to `"h3"` on Home and `"h2"` on `/products`, or add a screen-reader-only `<h2>Catalog Results</h2>` in `CatalogView` and `<h2>Storefront Guarantees</h2>` in `HomeView` section 4); consider adding `(storefront)/not-found.tsx` or wrapping root `app/not-found.tsx` in `<main>` when rendered outside `StorefrontLayout`.

---

### Informational Observations (Info)

1. **[Info] `resolveServerApiBaseUrl` extra URL hygiene (`frontend/src/lib/api/server-catalog.ts:L47-L85`)**: Relative paths starting with `/` reject `//`, `\`, and `CONTROL_CHAR_RE` before falling back to `DEFAULT_API_BASE_URL` (`http://localhost:8000/api/v1`). Absolute URLs check `http:`/`https:`, but do not explicitly reject `CONTROL_CHAR_RE`, userinfo (`parsed.username`/`parsed.password`), or `parsed.search`/`parsed.hash`. Because `INTERNAL_API_BASE_URL` and `NEXT_PUBLIC_API_BASE_URL` are server-side operator env vars (not user input), exploitability is negligible; tightening those checks is simple hardening.
2. **[Info] `parseCatalogSearchParams` bounds parity (`frontend/src/lib/api/server-catalog.ts:L140-L176`)**: `q` is capped at `100` chars, `min_price_cents`/`max_price_cents` are bounded to non-negative `int32` (`<= 2_147_483_647`), `in_stock` is strictly `"true" | undefined`, and `sort` is checked against `VALID_SORTS`. `category_slug` is only `.trim()`-med and `page` allows up to `2_147_483_647` (vs backend `MAX_PAGINATION_PAGE = 1_000_000` in `backend/app/core/pagination.py:L7`). All params are safely serialized via `URLSearchParams` (preventing parameter/path injection) and validated server-side by FastAPI.
3. **[Info] Mixed-case PDP slug triggers redundant client fetch (`products/[slug]/page.tsx:L8` vs `product-detail-view.tsx:L128`)**: `PRODUCT_SLUG_RE` uses the `/i` case-insensitive flag while backend normalizes slugs to lowercase (`_normalize_valid_slug_or_none`). Visiting `/products/Linen-Overshirt` succeeds on SSR, but `initialProduct.slug === slug` (`"linen-overshirt" === "Linen-Overshirt"`) evaluates `false` in `ProductDetailView`, triggering an unnecessary client refetch on mount. Normalizing `decodedSlug.toLowerCase()` resolves this.
4. **[Info] Verified visual assets & CSP alignment (`frontend/public/assets/products/*.png`, `frontend/next.config.mjs:L43`)**: All 8 seeded PNG files exist, are non-empty, and pass PNG signature verification (`89 50 4E 47 0D 0A 1A 0A`); `next.config.mjs` CSP `img-src 'self' https:` aligns cleanly with `sanitizeMediaUrl`.
5. **[Info] Pre-existing gallery thumbnail list ARIA (`product-detail-view.tsx:L473-L507`)**: Unchanged in `REM-P2-M09`, the thumbnail strip uses `role="list"` directly wrapping `<button>` elements rather than `role="listitem"` wrappers; keyboard arrow navigation (`ArrowLeft`/`ArrowRight`/`ArrowUp`/`ArrowDown`) and `aria-label` / `aria-current` work as intended.

---

## 3. Verified Controls Checklist

1. **SSRF & URL Validation (`server-catalog.ts`, `products/[slug]/page.tsx`, `products/page.tsx`)**:
   - `INTERNAL_API_BASE_URL` takes precedence over `NEXT_PUBLIC_API_BASE_URL`; relative `/api/v1` safely resolves to `http://localhost:8000/api/v1` only when free of `//`, `\`, and control chars (`L47-L68`).
   - Non-HTTP(S) schemes (`javascript:`, `ftp:`, `file:`) are rejected (`L77-L79`); `fetch` uses an 8s `AbortController` timeout (`L19`, `L199-L212`) and forwards **no** incoming user cookies, `Authorization`, or `X-Forwarded-*` headers.
   - `decodeAndValidateSlug` (`products/[slug]/page.tsx:L10-L21`) decodes safely inside `try/catch` and enforces `^[a-z0-9]+(?:-[a-z0-9]+)*$/i` (linear-time, ReDoS-safe, blocks `/`, `\`, `..`, `%`, `?`, `#`), and `fetchProductBySlugImpl` applies `encodeURIComponent(slug)` (`L251`).
   - `parseCatalogSearchParams` + `buildProductQueryString` use `URLSearchParams` (`L118-L197`), preventing query parameter smuggling.
2. **Metadata, OpenGraph & SSR/RSC Safety**:
   - Zero `dangerouslySetInnerHTML` usages across `frontend/src`.
   - SSR fetches hit only public unauthenticated catalog endpoints (`/categories`, `/products`, `/products/{slug}`) backed by `Product.status == "ACTIVE"` and `ProductVariantPublicDTO` (zero `on_hand`, `reserved`, `DRAFT`/`ARCHIVED` items, admin fields, or PII in SSR HTML or RSC payloads).
   - `generateMetadata` produces dynamic `title`, `description`, and `openGraph` tags via Next.js escaped Metadata API; `ProductCard`, `HomeView`, and `ProductDetailView` encode slugs in internal links (`encodeURIComponent`).
3. **Client/Server Boundaries & Cache Hygiene**:
   - `server-catalog.ts` is imported only by the 3 Server Component `page.tsx` routes and Vitest tests; zero `"use client"` components import it.
   - All `server-catalog.ts` fetches pass `cache: "no-store"` (`L205`); `fetchServerProductBySlug` uses `React.cache` (`L280`) strictly for per-request deduplication between `generateMetadata` and `ProductDetailPage`.
   - `/`, `/products`, and `/products/[slug]` export `dynamic = "force-dynamic"`, preventing stale build-time catalog snapshots.
   - `next.config.mjs` preserves `Cache-Control: no-store` on `/admin/:path*`, `/account/:path*`, `/orders/:path*`, `/checkout/:path*`, `/login`, `/register` (with `/cart/:path*` recommended under `SEC-M09-03`).
4. **Accessibility & 404 Semantics**:
   - Invalid slugs and backend `404` responses trigger Next.js `notFound()` (`products/[slug]/page.tsx:L78-L85`), returning real HTTP `404` status (verified in Playwright E2E).
   - Backend `5xx`/network errors render `ErrorState` without masking outages as `404` (`L91`).
   - Meaningful `alt` text is provided across `ProductCard` (`L24`), PDP gallery thumbnails & hero (`L500`, `L513`), and OpenGraph metadata (`L59`), with `ProductImage` safe fallback rendering.
   - `SkipLink`, `#main-content` `<main>` landmark in `StorefrontLayout`, global `:focus-visible` ring (`globals.css:L196-L199`), `aria-live="polite"` regions, radiogroup semantics, and keyboard arrow navigation in PDP gallery are intact.

---

## 4. Next Action / Follow-Up Notes

- `REM-P2-M09`, `REM-P2-M09-QA`, and `REM-P2-M09-SEC` approved for verified checkpoint commit on `main`.
- Track Low non-blocking follow-up items (`SEC-M09-01`, `SEC-M09-02`, `SEC-M09-03`, `A11Y-M09-01`) alongside pre-staging backlog.
