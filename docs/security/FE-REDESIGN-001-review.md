# SEC-REDESIGN-001 — Vellora Store Storefront Redesign Security, WCAG Contrast & Accessibility Review

- **Task ID**: `SEC-REDESIGN-001`
- **Auditor**: `security` specialist ([6d6ce81a-afc5-42e5-96c3-c6f16dcc5dde](conversation://6d6ce81a-afc5-42e5-96c3-c6f16dcc5dde), strictly read-only; recorded by Orchestrator)
- **Target Baseline**: Working tree diff vs `HEAD` `015d403` (`015d40302bb738fab4b5eacc4b334cbdcb913cf0`) after Round 2 remediation patches
- **Upstream Verification Reviewed**: [FE-REDESIGN-001-verification.md](../qa/FE-REDESIGN-001-verification.md) (`npm.cmd run lint` 0 warnings/errors; `npx.cmd tsc --noEmit` 0 errors; Vitest `6 files / 90 passed` in `11.23s`; `next build` 21 routes / 17 static + 5 dynamic; live Playwright Chromium `5 passed` in `31.3s` against disposable PostgreSQL 18.6)

## Verdict: **GO (`0` Critical / `0` High / `0` Medium / `0` Low / `3` Info)**

- **Overall Assessment**: All Round 1 findings (`MED-01`, `MED-02`, `LOW-01`, `LOW-02`, `LOW-03`, and `INFO-04`) have been verified fixed in `frontend/src/app/globals.css` and `frontend/src/components/storefront/product-detail-view.tsx` and regression-tested by `QA-REDESIGN-001`.
- **Security, Fake Data & Isolation**: Admin (`frontend/src/app/(admin)/**`), Backend (`backend/**`), CSP/headers (`frontend/next.config.mjs`), and root `:root` / `.dark` design tokens remain completely untouched. Zero `dangerouslySetInnerHTML`/`innerHTML`, zero unencoded slug interpolation, zero fake ratings/reviews/stock/client totals, and `localStorage` stores strictly `atelier-theme` (`"light" | "dark"`).
- **WCAG 2.1 AA Contrast & Accessibility**: Both `.dark .storefront-theme` and `.storefront-theme` Light Mode meet `>= 4.5:1` for normal text and `>= 3.0:1` for UI control borders, and storefront heading hierarchy, `ProductCard` link semantics, `ThemeToggle`, `HomeView` filter pills, PDP `role="radiogroup"` roving `tabIndex`/arrow navigation, gallery `<ul>`/`<li>` navigation, and `MobileBottomNav` pass inspection.

> **Non-Certification Notice**: This scoped read-only audit evaluates the `FE-REDESIGN-001` storefront redesign diff via static code inspection and mathematical WCAG 2.1 luminance calculations. It does not constitute formal security certification or automated assistive-technology screen-reader certification.

---

## 1. Round 2 Remediation Verification Matrix

| Finding | Target Files & Lines | Fix Verified | Measured Evidence | Status |
| :--- | :--- | :--- | :--- | :--- |
| **`MED-01`** Dark destructive contrast | `frontend/src/app/globals.css` (`L286–L291`) | Updated `.dark .storefront-theme` to `--destructive: #E55C5C` and `--destructive-foreground: #0B0D0F`. | `#0B0D0F` on `#E55C5C`: **`5.58:1`**; `#E55C5C` on `#1A1D21`: **`4.85:1`**; on `#1F0F10`: **`5.31:1`**; on `#0B0D0F`: **`5.58:1`** (all `>= 4.5:1`). | **Verified Fixed** |
| **`MED-02`** Light accent contrast | `frontend/src/app/globals.css` (`L208–L213, L243–L244`) | Darkened `.storefront-theme` `--accent` and `--ring` from `#8B6D43` to `#755932`. | `#755932` on `#F6F4EF`: **`5.91:1`**; on `#EDE6D8`: **`5.23:1`**; on `#FFFFFF`: **`6.50:1`**; on hero `#EFEAE0`: **`5.42:1`**; `#FFFFFF` on `#755932`: **`6.50:1`** (all `>= 4.5:1`). | **Verified Fixed** |
| **`LOW-01`** Dark control border contrast | `frontend/src/app/globals.css` (`L311–L313`) | Raised `.dark .storefront-theme` `--border-control` and `--input` from `#3E444C` to `#6E7681`. | `#6E7681` vs `#1A1D21`: **`3.68:1`**; vs `#14171B`: **`3.91:1`**; vs `#0B0D0F`: **`4.24:1`** (all `>= 3.0:1` WCAG SC 1.4.11). | **Verified Fixed** |
| **`LOW-02`** PDP `role="radiogroup"` keyboard nav | `frontend/src/components/storefront/product-detail-view.tsx` (`L171–L172, L330–L351, L629–L690, L721–L748`) | Added `handleRadioGroupKeyDown` (`ArrowRight`/`ArrowDown`/`ArrowLeft`/`ArrowUp` wrapping, selection update, ref focus) and roving `tabIndex={idx === focusableIdx ? 0 : -1}` on both attribute and fallback variant radiogroups. | Verified in source and `tests/ui-and-shells.test.tsx` (`90/90` Vitest passing). | **Verified Fixed** |
| **`LOW-03`** PDP thumbnail list semantics | `frontend/src/components/storefront/product-detail-view.tsx` (`L507–L542`) | Replaced `<div role="list">` with semantic `<ul aria-label="Product gallery thumbnails">` and `<li key={img.id}>` wrapping each `<button>`. | Valid semantic list structure with `ArrowLeft`/`ArrowRight`/`ArrowUp`/`ArrowDown` thumbnail focus wrapping (`L313–L328`). | **Verified Fixed** |
| **`INFO-04`** Inner slug encoding in `returnTo` | `frontend/src/components/storefront/product-detail-view.tsx` (`L817`) | Updated `returnTo` link to `encodeURIComponent(\`/products/${encodeURIComponent(product.slug)}\`)`. | Consistent defense-in-depth URL encoding across all storefront links. | **Verified Fixed** |

---

## 2. Full WCAG 2.1 Contrast Matrix (Post-Patch)

| Theme | Token / Component Pair | Foreground | Background | Ratio | WCAG AA Target | Result |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: |
| Dark | Body text (`--foreground` / `--background`) | `#F5F3EE` | `#0B0D0F` | `17.56:1` | `>= 4.5:1` | **PASS** |
| Dark | Card/surface text (`--foreground` / `--surface`) | `#F5F3EE` | `#1A1D21` | `15.25:1` | `>= 4.5:1` | **PASS** |
| Dark | Panel/footer text (`--foreground` / `#14171B`) | `#F5F3EE` | `#14171B` | `16.21:1` | `>= 4.5:1` | **PASS** |
| Dark | Muted text on page / surface / panel / muted | `#9DA3AB` | `#0B0D0F` / `#1A1D21` / `#14171B` / `#22262B` | `7.66:1` / `6.65:1` / `7.07:1` / `5.99:1` | `>= 4.5:1` | **PASS** |
| Dark | Champagne CTA (`--accent-foreground` / `--accent`) | `#0B0D0F` | `#D8C3A1` | `11.35:1` | `>= 4.5:1` | **PASS** |
| Dark | Champagne text on page / surface / subtle | `#D8C3A1` | `#0B0D0F` / `#1A1D21` / `#26231D` | `11.35:1` / `9.86:1` / `9.13:1` | `>= 4.5:1` | **PASS** |
| Dark | Emerald stock (`--success` / `--success-subtle` & `#1A1D21`) | `#34D399` | `#0F2920` / `#1A1D21` | `8.04:1` / `8.80:1` | `>= 4.5:1` | **PASS** |
| Dark | Destructive button (`--destructive-foreground` / `--destructive`) | `#0B0D0F` | `#E55C5C` | `5.58:1` | `>= 4.5:1` | **PASS** |
| Dark | Destructive text on surface / subtle / page | `#E55C5C` | `#1A1D21` / `#1F0F10` / `#0B0D0F` | `4.85:1` / `5.31:1` / `5.58:1` | `>= 4.5:1` | **PASS** |
| Dark | Control borders (`--border-control` vs surface / panel / page) | `#6E7681` | `#1A1D21` / `#14171B` / `#0B0D0F` | `3.68:1` / `3.91:1` / `4.24:1` | `>= 3.0:1` | **PASS** |
| Light | Body & card text (`--foreground` / `--background` & `--surface`) | `#121417` | `#F6F4EF` / `#FFFFFF` | `16.79:1` / `18.45:1` | `>= 4.5:1` | **PASS** |
| Light | Muted text on page / surface / muted | `#57534E` | `#F6F4EF` / `#FFFFFF` / `#ECE8DF` | `6.94:1` / `7.63:1` / `6.24:1` | `>= 4.5:1` | **PASS** |
| Light | Accent button (`--accent-foreground` / `--accent`) | `#FFFFFF` | `#755932` | `6.50:1` | `>= 4.5:1` | **PASS** |
| Light | Accent text on page / accent-subtle / surface / hero | `#755932` | `#F6F4EF` / `#EDE6D8` / `#FFFFFF` / `#EFEAE0` | `5.91:1` / `5.23:1` / `6.50:1` / `5.42:1` | `>= 4.5:1` | **PASS** |
| Light | Destructive, success & control borders (`#B91C1C`, `#15803D`, `#8A8377`) | — | `#FFFFFF` / subtle | `>= 3.75:1` (UI) / `>= 4.79:1` (text) | `>= 3.0:1` / `4.5:1` | **PASS** |

---

## 3. Severity-Classified Findings (Current Open State)

- **Critical**: `0`
- **High**: `0`
- **Medium**: `0`
- **Low**: `0`
- **Info**: `3`

### Informational Notes (Non-Blocking)

- **`INFO-01` (Admin Initial Dark Default)**: `RootLayout` (`frontend/src/app/layout.tsx:L28`) and `ThemeProvider` (`frontend/src/components/shared/theme-provider.tsx:L31–L34`) set the initial unstored theme to `"dark"`, so Admin Console also defaults to dark mode on fresh visits while remaining isolated on its untouched `.dark` tokens.
- **`INFO-02` (Portal Overlay Scrims & `ToastProvider`)**: `ToastProvider` (`layout.tsx:L31`, `toast.tsx:L70–L118`) and Radix `<SheetOverlay />` / `<DialogOverlay />` (`sheet.tsx:L52`, `dialog.tsx:L33`) render outside `.storefront-theme`, inheriting `:root`/`.dark` tokens rather than `.storefront-theme` tokens (cosmetic only; no style leak into Admin or CSP impact).
- **`INFO-03` (Light Hero Gradient Surface)**: Light Mode hero gradient `from-[#EFEAE0] via-[#FAF8F3] to-[#E6DFD2]` (`home-view.tsx:L195`) passes WCAG AA contrast for `--foreground`, `--muted-foreground` (`>= 5.76:1`), and patched `--accent` (`#755932` on `#EFEAE0` = `5.42:1`).

---

## 4. Verified Storefront Security, Isolation & Accessibility Controls

- **Admin, Backend & Root Token Isolation**: `git diff HEAD -- frontend/src/app/(admin) backend frontend/next.config.mjs` returned zero changes; `:root` (`L5–L96`) and `.dark` (`L98–L174`) in `globals.css` are byte-for-byte unmodified.
- **Heading Hierarchy & Card Semantics**: `/` (`home-view.tsx`) enforces `h1` hero -> `h2` (`sr-only` guarantees / Categories / Featured Collection / footer) -> `h3` cards; `/products` (`catalog-view.tsx`) enforces `h1` -> `h2` `ProductCard` (`headingAs="h2"`). `ProductCard` (`product-card.tsx:L23–L64`) uses a single `<Link>` wrapper with zero nested `<button>` elements.
- **Interactive Controls & Focus**: Global `:focus-visible` (`globals.css:L343–L346`) plus `focus-visible:ring-2` on interactive primitives; `ThemeToggle` (`variant="pill"`), `HomeView` filter pills, `ProductDetailView` variant radiogroups and gallery thumbnails, and `MobileBottomNav` (`aria-current="page"`, `min-h-[44px]`) all expose valid ARIA roles and keyboard interaction.
- **Fake Data, Storage Hygiene, XSS & Portals**: Zero hardcoded ratings/reviews/stock counts or client-computed totals; `localStorage` stores strictly `atelier-theme` (`"light" | "dark"`); zero `dangerouslySetInnerHTML`/`innerHTML`; all storefront slugs encoded with `encodeURIComponent`; all storefront portal `*Content` wrappers (`header.tsx`, `catalog-view.tsx`, `cart-drawer.tsx`, `cart-helpers.tsx`, `checkout-view.tsx`, `order-detail-view.tsx`) carry `storefront-theme`.
