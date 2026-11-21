# SEC-4C-REVIEW — Phase 4C Visual Remediation Security, Commerce Integrity & Accessibility Review

- **Task ID**: `SEC-4C-REVIEW`
- **Auditor**: `security` specialist ([370a6a4b-d62b-4a0a-b603-ea76141bd219](conversation://370a6a4b-d62b-4a0a-b603-ea76141bd219), strictly read-only; recorded by Orchestrator)
- **Target Branch & Baseline**: `feat/phase-4c-visual-remediation` vs `main` (`bb10e88`)
- **Upstream Verification Reviewed**: [FE-4C-verification.md](../qa/FE-4C-verification.md) (`npm.cmd run lint` 0 warnings/errors; `npx.cmd tsc --noEmit` 0 errors; Vitest `6 files / 94 passed` in `8.25s`; `next build` 21 routes / 17 static + 5 dynamic; live Playwright Chromium `5 passed` in `32.3s` against disposable PostgreSQL 18.6)

## Verdict: **GO (`0` Critical / `0` High / `0` Medium / `0` Open Low / `3` Info)**

- **Overall Assessment**: Phase 4C visual remediation changes remain strictly scoped to storefront presentation, layout, and local product studio assets. Zero security vulnerabilities, XSS vectors, external asset dependencies, or fabricated warranties/return windows exist.
- **Scope & Isolation**: Zero changes to `backend/**`, `frontend/src/app/(admin)/**`, `frontend/src/components/admin/**`, or `frontend/next.config.mjs`.
- **Commerce Integrity**: Fabricated `"24-Month Quality Guarantee"` and `"14-Day Easy Returns"` promises were removed and replaced with backend-verified system rules (`Free Shipping Over $100`, `Live Per-SKU Inventory`, `15-Minute Checkout Hold`, `Self-Service Cancellation`, `Sandbox Simulation Mode`). Round 1 finding `LOW-4C-01` (copy precision on unpaid pending-order cancellation) was patched in `home-view.tsx:L317` and `product-detail-view.tsx:L892` and re-verified.
- **Accessibility & Assets**: `MobileBottomNav` route hiding, `visualViewport` listener cleanup, and safe-area insets are sound; `HomeView` / `CatalogView` 2-column mobile grids, `ProductCard` `h2`/`h3` semantics, PDP sticky buy box clearance/contrast/keyboard behavior, and all 9 dark-studio PNG assets pass inspection.

> **Non-Certification Notice**: This scoped read-only audit evaluates the Phase 4C diff (`bb10e88` vs `feat/phase-4c-visual-remediation`) via static code inspection, binary PNG chunk/CRC verification, and WCAG 2.1 luminance calculations. It does not constitute formal security certification or automated assistive-technology certification.

---

## 1. Branch & Scope Isolation Verification

Verified `git diff --name-status bb10e88 -- "backend/" "frontend/src/app/(admin)/" "frontend/src/components/admin/" "frontend/next.config.mjs"` returned empty (exit `0`):

| Area | Status vs `bb10e88` | Verified Files |
| :--- | :--- | :--- |
| `backend/**` | **Untouched** (`0` diffs) | Verified via `git diff --name-status` |
| `frontend/src/app/(admin)/**` & `frontend/src/components/admin/**` | **Untouched** (`0` diffs) | Verified via `git diff --name-status` |
| `frontend/next.config.mjs` & `frontend/src/app/globals.css` | **Untouched** (`0` diffs) | Verified via `git diff --name-status` |
| Storefront & shared UI (`frontend/src/**`) | **8 files modified** | `app/(storefront)/layout.tsx`, `components/shared/product-image.tsx`, `components/storefront/{catalog-view,footer,home-view,mobile-bottom-nav,product-card,product-detail-view}.tsx` |
| Storefront media (`frontend/public/assets/products/*.png`) | **9 PNGs replaced** | `hero-showcase.png` + 8 seeded product PNGs |
| QA & Docs | **5 files** | `tests/{storefront-ssr,ui-and-shells}.test.tsx`, `e2e/storefront-and-admin.spec.ts`, `docs/TASKS.md`, `docs/qa/FE-4C-verification.md` |

---

## 2. Findings & Remediation Status

- **Critical**: `0`
- **High**: `0`
- **Medium**: `0`
- **Low**: `0` open (`1` identified & resolved prior to commit: `LOW-4C-01`)
- **Info**: `3`

### Resolved Finding (`LOW-4C-01`)

- **`LOW-4C-01` [Resolved] — Self-service cancellation trust-strip copy alignment**:
  - **Locations**: `frontend/src/components/storefront/home-view.tsx:L315-L319`, `frontend/src/components/storefront/product-detail-view.tsx:L891-L893`.
  - **Resolution**: Clarified copy to `"Cancel unpaid pending orders during the checkout hold from your order page."` (Home) and `"Self-Service Unpaid Order Cancellation"` (PDP), matching `backend/app/modules/orders/service.py:L1498-L1523` (`PENDING_PAYMENT` orders within the 15-minute `RESERVATION_TTL` hold) and `order-detail-view.tsx:L1109`. Re-verified via `lint`, `tsc`, `94/94` Vitest, `next build`, and `5/5` live Playwright E2E.

### Informational Notes (Non-Blocking)

- **`INFO-4C-01` (Feature Branch Workflow)**: Changes committed on dedicated branch `feat/phase-4c-visual-remediation` (not merged/pushed to `main`), leaving `main` untouched at `bb10e88` and `artifacts/` excluded from Git.
- **`INFO-4C-02` (Mobile Card Density & Decorative Arrow)**: At `<640px`, `ProductCard` (`product-card.tsx:L52-L65`) renders `text-[10px]` category eyebrow and `text-xs` title inside a full-card `<Link>` target; the `h-7 w-7` arrow indicator is `aria-hidden="true"` inside `<Link>` without nested interactive buttons.
- **`INFO-4C-03` (Keyboard-Open Bottom Spacer Behavior)**: In `mobile-bottom-nav.tsx:L48-L50, L89-L92`, when `isKeyboardOpen` becomes `true` (`visualViewport.height < window.innerHeight * 0.78`), both `<nav>` and its bottom spacer unmount cleanly and remount when the virtual keyboard closes.

---

## 3. Commerce Integrity Audit Matrix

| Storefront Copy / Control | Location | Verified Backend Rule | Alignment |
| :--- | :--- | :--- | :---: |
| `Free Shipping Over $100` / `$100.00` | `home-view.tsx:L279-L282`, `product-detail-view.tsx:L884`, `footer.tsx:L103` | `free_shipping_threshold_cents = 10000`, `standard_shipping_cents = 1000` (`config.py`, `cart/service.py`, `orders/service.py`) | **PASS** |
| `Live Per-SKU Inventory` | `home-view.tsx:L257, L291-L294` | Live SKU stock check & row-level lock on checkout (`orders/service.py`) | **PASS** |
| `15-Minute Checkout Hold` | `home-view.tsx:L303-L306`, `product-detail-view.tsx:L888` | `RESERVATION_TTL = timedelta(minutes=15)` (`orders/service.py:L70, L854`) | **PASS** |
| `Self-Service Cancellation` | `home-view.tsx:L315-L318`, `product-detail-view.tsx:L892` | `PENDING_PAYMENT` (`REQUIRES_PAYMENT` / `FAILED`) within hold only (`orders/service.py:L1498-L1523`) | **PASS** |
| `Sandbox Simulation Mode` | `footer.tsx:L113-L131`, `home-view.tsx:L305` | Driven dynamically by `healthApi.getHealth()` (`health?.sandbox_payments`); `SANDBOX_PROVIDER` | **PASS** |
| Fabricated warranties / return windows | `home-view.tsx`, `product-detail-view.tsx`, `footer.tsx` | `"24-Month Quality Guarantee"` and `"14-Day Easy Returns"` completely removed | **PASS** |

---

## 4. Mobile Nav, Responsive Grid, Accessibility & Binary Asset Checks

- **`MobileBottomNav` (`mobile-bottom-nav.tsx`)**: Hidden on `/checkout`, `/cart`, `/login`, `/register`; attaches/cleans `window.visualViewport` `resize`/`scroll` listeners safely; includes `pb-[env(safe-area-inset-bottom)]` and `z-40` stacking below drawers (`z-50`).
- **2-Column Mobile Grids (`home-view.tsx`, `catalog-view.tsx`) & Heading Semantics (`product-card.tsx`)**: Uniform 2-col grids on mobile with single `<Link>` cards (`h2` beneath `h1` on `/products`, `h3` beneath `h2` on `/`).
- **Sticky Buy Box (`product-detail-view.tsx`)**: `lg:sticky lg:top-24 lg:col-span-5 lg:self-start` clears header (`h-16`) cleanly; WCAG 2.1 contrast on dark & light panels remains `>= 5.23:1` for text.
- **Asset Verification (`frontend/public/assets/products/*.png`)**: All `9` PNGs (`797,375`–`1,056,303` bytes; `1200×900` hero + `8` `800×1000` studio products) passed PNG magic-byte, CRC32 checksum, and zero metadata-chunk (`IHDR`, `IDAT`, `IEND` only) checks.
