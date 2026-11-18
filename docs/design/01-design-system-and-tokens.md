# 01 — Design System, Tokens & Accessibility Standards

> **Task**: `DESIGN-001`  
> **Scope**: Shared visual foundation for Next.js (App Router), Tailwind CSS, and `shadcn/ui` across `(storefront)` and `(admin)` route groups.  
> **Aligned Contracts**: [01-system-overview-and-modules.md](../architecture/01-system-overview-and-modules.md), [openapi-contracts.md](../api/openapi-contracts.md).  
> **Notice**: Specification only; no application code is implemented here.

---

## 1. Aesthetic Direction & Dual-Surface Philosophy

The design system unifies two distinct operational modes under a single CSS variable architecture:

| Surface | Aesthetic Direction | Density & Rhythm | Visual Signature |
| :--- | :--- | :--- | :--- |
| **Storefront (`(storefront)`)** | **Editorial Luxury**: warm alabaster canvas, crisp white product cards, tailored corners, generous whitespace, calm typographic hierarchy. | **Comfortable**: spacious vertical breathing room (`48px–96px` section spacing), 4:5 portrait product imagery, clean image-first hierarchy. | Warm stone neutrals + deep obsidian ink (`#171615`) + Deep Atelier Pine (`#1E4D3B`) accent. |
| **Admin Console (`(admin)`)** | **High-Clarity Utilitarian**: crisp 1px structural borders, high scanability, dense data tables, monospace metadata, strict status separation. | **Compact**: tighter vertical padding (`16px–24px` panel spacing, `40px` table row heights), persistent sidebar navigation. | Identical semantic tokens with tabular numerals (`tabular-nums`) and distinct semantic pills for payment vs. fulfillment. |

---

## 2. Color System & Semantic CSS Variables

All colors are defined as semantic CSS custom properties on `:root` (Light Mode) and `.dark` (Dark Mode) and mapped into Tailwind CSS theme tokens. Components **must never** hardcode raw hex values for surface, text, border, or status colors.

### 2.1 Semantic Token Palette

| CSS Variable | Purpose | Light Mode (`:root`) | Dark Mode (`.dark`) |
| :--- | :--- | :--- | :--- |
| `--background` | Page canvas | `#FAF9F6` *(Warm Alabaster)* | `#111110` *(Deep Obsidian)* |
| `--foreground` | Primary body & heading text | `#171615` *(Obsidian Ink)* | `#F5F4F0` *(Warm Ivory)* |
| `--surface` | Elevated cards, sheets, popovers (`--card` / `--popover`) | `#FFFFFF` *(Pure White)* | `#1A1918` *(Slate Obsidian)* |
| `--surface-foreground` | Text on elevated surfaces | `#171615` | `#F5F4F0` |
| `--muted` | Recessed panels, table headers, skeleton base | `#F1EFEA` *(Warm Linen)* | `#242320` *(Warm Charcoal)* |
| `--muted-foreground` | Secondary labels, helper text, metadata | `#57534E` *(Stone 600)* | `#A8A29E` *(Stone 400)* |
| `--primary` | Primary CTAs (`Add to Cart`, `Place Order`, `Save`) | `#1C1917` *(Espresso Charcoal)* | `#F5F4F0` *(Warm Ivory)* |
| `--primary-foreground` | Text/icons inside `--primary` | `#FAF9F6` | `#171615` |
| `--secondary` | Secondary buttons, inactive filter pills | `#EAE6DF` *(Soft Sand)* | `#2C2A27` *(Soft Charcoal)* |
| `--secondary-foreground` | Text/icons inside `--secondary` | `#171615` | `#F5F4F0` |
| `--accent` | Brand highlights, active nav markers, links, focus accents | `#1E4D3B` *(Atelier Pine)* | `#52B788` *(Sage Emerald)* |
| `--accent-foreground` | Text/icons on filled `--accent` badges/buttons | `#FFFFFF` | `#0B1F17` *(Deep Pine Ink)* |
| `--accent-subtle` | Hover/selected state background for dropdown/menu items | `#EDF4F1` *(Mint Mist)* | `#192C24` *(Dark Pine Tint)* |
| `--destructive` | Errors, out-of-stock, cancel/refund/delete actions | `#B91C1C` *(Crimson 700)* | `#F87171` *(Red 400)* |
| `--destructive-foreground` | Text/icons on filled `--destructive` | `#FFFFFF` | `#2A0A0A` |
| `--destructive-subtle` | Inline error alert backgrounds | `#FEF2F2` | `#2C1212` |
| `--warning` | Low stock, `PENDING_PAYMENT` countdown < 3m, caution alerts | `#B45309` *(Amber 700)* | `#FBBF24` *(Amber 400)* |
| `--warning-foreground` | Text/icons on filled `--warning` | `#FFFFFF` | `#1F1402` |
| `--warning-subtle` | Warning banner backgrounds (`#92400E` text in light) | `#FFFBEB` | `#2A200B` |
| `--success` | `ACTIVE`, `PAID`, `CONFIRMED`, `COMPLETED`, in-stock | `#15803D` *(Emerald 700)* | `#4ADE80` *(Green 400)* |
| `--success-foreground` | Text/icons on filled `--success` | `#FFFFFF` | `#052010` |
| `--success-subtle` | Confirmation banner backgrounds | `#F0FDF4` | `#0E2918` |
| `--border` | Structural hairline dividers (cards, table rows) | `#E2DFD7` | `#2E2C29` |
| `--border-control` | Interactive form/control boundaries (`--input`, checkboxes) | `#857F76` | `#757069` |
| `--ring` | Keyboard `:focus-visible` outline ring | `#1E4D3B` | `#52B788` |

### 2.2 Analytics Chart Series Tokens

| CSS Variable | Usage | Light Mode | Dark Mode |
| :--- | :--- | :--- | :--- |
| `--chart-net-revenue` | Primary revenue bars / area | `#1E4D3B` | `#52B788` |
| `--chart-orders-count` | Paid orders count line | `#1C1917` | `#F5F4F0` |
| `--chart-refunds` | Refunded revenue series | `#B91C1C` | `#F87171` |
| `--chart-grid` | Axis & grid rules | `#E2DFD7` | `#2E2C29` |

### 2.3 WCAG 2.1 AA Contrast Verification Matrix

Every text and interactive control pairing satisfies WCAG 2.1 AA (**`>= 4.5:1`** normal text, **`>= 3.0:1`** large text & UI control boundaries per SC 1.4.3 and 1.4.11):

| Pair (Foreground / Background) | Light Ratio | Dark Ratio | Requirement | Status |
| :--- | :---: | :---: | :---: | :---: |
| `--foreground` on `--background` | `17.16:1` (`#171615`/`#FAF9F6`) | `17.16:1` (`#F5F4F0`/`#111110`) | `>= 4.5:1` | Pass |
| `--foreground` on `--surface` | `18.07:1` (`#171615`/`#FFFFFF`) | `15.95:1` (`#F5F4F0`/`#1A1918`) | `>= 4.5:1` | Pass |
| `--muted-foreground` on `--background` | `7.24:1` (`#57534E`/`#FAF9F6`) | `7.48:1` (`#A8A29E`/`#111110`) | `>= 4.5:1` | Pass |
| `--muted-foreground` on `--muted` | `6.64:1` (`#57534E`/`#F1EFEA`) | `6.23:1` (`#A8A29E`/`#242320`) | `>= 4.5:1` | Pass |
| `--primary-foreground` on `--primary` | `16.62:1` (`#FAF9F6`/`#1C1917`) | `16.42:1` (`#171615`/`#F5F4F0`) | `>= 4.5:1` | Pass |
| `--secondary-foreground` on `--secondary` | `14.53:1` (`#171615`/`#EAE6DF`) | `13.00:1` (`#F5F4F0`/`#2C2A27`) | `>= 4.5:1` | Pass |
| `--accent-foreground` on `--accent` | `9.62:1` (`#FFFFFF`/`#1E4D3B`) | `6.94:1` (`#0B1F17`/`#52B788`) | `>= 4.5:1` | Pass |
| `--accent` text on `--background` | `9.14:1` (`#1E4D3B`/`#FAF9F6`) | `7.63:1` (`#52B788`/`#111110`) | `>= 4.5:1` | Pass |
| `--destructive-foreground` on `--destructive` | `6.47:1` (`#FFFFFF`/`#B91C1C`) | `6.62:1` (`#2A0A0A`/`#F87171`) | `>= 4.5:1` | Pass |
| `--destructive` text on `--background` | `6.15:1` (`#B91C1C`/`#FAF9F6`) | `6.83:1` (`#F87171`/`#111110`) | `>= 4.5:1` | Pass |
| `--warning-foreground` on `--warning` | `5.02:1` (`#FFFFFF`/`#B45309`) | `10.86:1` (`#1F1402`/`#FBBF24`) | `>= 4.5:1` | Pass |
| `--warning` text on `--background` | `4.77:1` (`#B45309`/`#FAF9F6`) | `11.31:1` (`#FBBF24`/`#111110`) | `>= 4.5:1` | Pass |
| `--success-foreground` on `--success` | `5.01:1` (`#FFFFFF`/`#15803D`) | `9.86:1` (`#052010`/`#4ADE80`) | `>= 4.5:1` | Pass |
| `--success` text on `--background` | `4.76:1` (`#15803D`/`#FAF9F6`) | `10.84:1` (`#4ADE80`/`#111110`) | `>= 4.5:1` | Pass |
| `--border-control` on `--surface` | `3.97:1` (`#857F76`/`#FFFFFF`) | `3.58:1` (`#757069`/`#1A1918`) | `>= 3.0:1` | Pass |
| `--ring` focus indicator on `--background` | `9.14:1` (`#1E4D3B`/`#FAF9F6`) | `7.63:1` (`#52B788`/`#111110`) | `>= 3.0:1` | Pass |

*Note on subtle light-mode warning banners*: On `--warning-subtle` (`#FFFBEB`), text uses `#92400E` (`6.89:1`) rather than `#B45309` for extra margin.

---

## 3. Typography Scale & Numeric Formatting

### 3.1 Font Families
* **`--font-sans`**: `Plus Jakarta Sans` (fallback: `Inter`, `system-ui`, `sans-serif`) — primary UI, body copy, admin labels, navigation.
* **`--font-display`**: `Newsreader` (fallback: `Georgia`, `serif`) — optional editorial accent strictly for Storefront hero headlines (`display-xl`, `display-lg`). Admin uses `--font-sans` exclusively.
* **`--font-mono`**: `JetBrains Mono` (fallback: `ui-monospace`, `monospace`) — SKUs, `order_number`, `provider_intent_id`, UUIDs, IP addresses, UTC timestamps, and JSON diffs.

### 3.2 Type Scale

| Token | Size (`rem` / `px`) | Line Height | Weight | Tracking | Usage |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `display-xl` | `3rem` / `48px` (`2.25rem` mobile) | `1.10` | `500` | `-0.02em` | Storefront Hero Headline (`--font-display`) |
| `display-lg` | `2.25rem` / `36px` (`1.875rem` mobile) | `1.15` | `500` | `-0.02em` | Storefront Collection Headers (`--font-display`) |
| `h1` | `1.75rem` / `28px` | `1.20` | `600` | `-0.015em` | Page Titles (PDP, Cart, Checkout, Admin Page Header) |
| `h2` | `1.375rem` / `22px` | `1.25` | `600` | `-0.01em` | Section Headings, Admin Card Titles, Drawer Headers |
| `h3` | `1.125rem` / `18px` | `1.30` | `600` | `0em` | Subsections, Product Card Title (detail), Summary Headings |
| `h4` | `0.9375rem` / `15px` | `1.35` | `600` | `0em` | Form Group Legends, Card Subtitles, Table Group Headers |
| `body-lg` | `1rem` / `16px` | `1.60` | `400` | `0em` | Editorial descriptions, PDP prose, checkout primary labels |
| `body-md` | `0.875rem` / `14px` | `1.50` | `400` | `0em` | Default UI text, form inputs (`>= 14px` / `16px` on mobile inputs to avoid iOS zoom), admin table cells |
| `body-sm` | `0.8125rem` / `13px` | `1.45` | `400`–`500` | `0em` | Helper descriptions, filter labels, secondary metadata |
| `caption` | `0.75rem` / `12px` | `1.40` | `500` | `0.02em` | Status pills, breadcrumbs, column headers (`uppercase` optional at `0.05em`) |
| `mono-code` | `0.8125rem` / `13px` | `1.45` | `400`–`500` | `0em` | SKUs, `ORD-...`, audit JSON diffs, `request_id` |

### 3.3 Monetary & Numeric Rules
* **Tabular Figures**: Apply `font-variant-numeric: tabular-nums` (`tabular-nums`) to **every** price, discount, tax, total, cart quantity, countdown timer (`MM:SS`), stock count (`on_hand`, `reserved`, `available`), and analytics KPI.
* **Currency Formatting**: API values arrive in integer `*_cents` + ISO-4217 `currency` (`"USD"`). Format deterministically using `Intl.NumberFormat('en-US', { style: 'currency', currency })` on `(cents / 100)` **for display only**; never perform client arithmetic in floats and submit totals back to the server.

---

## 4. Spacing, Grid, Breakpoints, Radii & Elevation

### 4.1 Breakpoints & Layout Containers

| Token | Min Width | Storefront Layout Behavior | Admin Layout Behavior |
| :--- | :--- | :--- | :--- |
| *Base* | `0px` | 1-col product grid; hamburger nav drawer; bottom/sheet filters; stacked checkout. | 1-col KPI stack; collapsible nav `Sheet`; card-list fallback or horizontal-scroll table wrapper. |
| `sm` | `640px` | 2-col product grid; inline search bar expansion. | 2-col KPI cards; split toolbar actions. |
| `md` | `768px` | 2–3-col product grid; 2-col footer; side-by-side cart items & summary on `/cart` optional. | 2-col KPI cards; multi-column filter bar; full data tables visible. |
| `lg` | `1024px` | Persistent top nav + category dropdown; 260px filter sidebar + 3-col catalog grid; 2-col PDP (`55% / 45%`) & Checkout (`60% / 40%`). | Persistent 240px left sidebar (`64px` collapsed); 4-col KPI grid; split order workbench (`65% / 35%`). |
| `xl` | `1280px` | 4-col catalog grid; container `max-w-[1360px]` centered with `32px` gutters. | 4-col KPI + full-width analytics charts + side-by-side top products & stock health. |
| `2xl` | `1536px` | Container capped at `max-w-[1440px]` to preserve comfortable reading/browsing line lengths. | Fluid up to `max-w-[1600px]` for wide inventory/audit/order tables. |

### 4.2 Spacing Scale (4px Base Grid)
* `space-1` (`4px`), `space-2` (`8px`), `space-3` (`12px`), `space-4` (`16px`), `space-5` (`20px`), `space-6` (`24px`), `space-8` (`32px`), `space-10` (`40px`), `space-12` (`48px`), `space-16` (`64px`), `space-20` (`80px`), `space-24` (`96px`).
* **Density Contract**:
  * Storefront cards/panels: `p-4` (`16px`) mobile / `p-6` (`24px`) desktop; section gap `space-12` to `space-20`.
  * Admin panels/tables: `p-4` (`16px`) panel padding, `gap-4` (`16px`) grid gutters, table cell padding `px-3 py-2.5`.

### 4.3 Border Radii
* `--radius-none`: `0px` (editorial full-bleed hero banners)
* `--radius-sm`: `4px` (badges, table pills, checkboxes, inline code chips)
* `--radius-md`: `8px` (`--radius` default: buttons, inputs, selects, product image cards, alert boxes)
* `--radius-lg`: `12px` (modals, elevated summary cards, admin chart containers)
* `--radius-full`: `9999px` (cart count pill, status indicator dots, avatar circles)

### 4.4 Elevation Tokens
* `--shadow-none`: `none` (default flat cards with `1px solid var(--border)`)
* `--shadow-xs`: `0 1px 2px 0 rgb(23 22 21 / 0.05)` (buttons, inputs, KPI cards in light mode)
* `--shadow-sm`: `0 2px 8px -2px rgb(23 22 21 / 0.08)` (hover product cards, sticky summary cards)
* `--shadow-md`: `0 8px 24px -4px rgb(23 22 21 / 0.12)` (dropdown menus, popovers, toasts)
* `--shadow-lg`: `0 16px 40px -8px rgb(23 22 21 / 0.18)` (dialogs, slide-over sheets)
* **Dark Mode Elevation Rule**: Shadows are suppressed/barely visible on `#111110`; elevated layers (`--surface` `#1A1918` and overlays) must always retain a crisp `1px solid var(--border)` (`#2E2C29`) boundary.

---

## 5. `shadcn/ui` Component Inventory & Variant Mapping

Primitives live under `frontend/src/components/ui/` and wrap Radix UI + Tailwind CSS tokens:

| Component | Variants / Sizes | Storefront Mapping | Admin Mapping |
| :--- | :--- | :--- | :--- |
| **`Button`** | `default` (`primary`), `secondary`, `outline`, `ghost`, `destructive`, `link`; sizes `sm` (`36px`), `default` (`40px`, `44px` on touch), `lg` (`48px`), `icon` (`40px`/`44px`) | Hero CTAs, `Add to Cart` (`lg`), `Place Order` (`lg`), quantity steppers (`icon`), `Simulate Payment`, `Cancel Order` (`outline`/`destructive`) | Toolbar actions (`New Product`, `Adjust Stock`), fulfillment step CTA (`default`), `Issue Refund` (`destructive`), `Export/Filter` (`outline`) |
| **`Badge`** | `default`, `secondary`, `outline`, `success`, `warning`, `destructive` | `Out of stock`, `Low stock`, discount percentage, order status pills | Separate `payment_status` & `fulfillment_status` pills, `DRAFT`/`ACTIVE`/`ARCHIVED`, user `role` (`admin`/`staff`/`customer`) |
| **`Card`** | `default` (`bg-surface border-border rounded-lg`) | Product card wrapper, Checkout Order Summary, Address cards | KPI summary cards, chart panels, Order detail sections |
| **`Input` / `Textarea`** | `default`, `error` (`border-destructive focus-visible:ring-destructive`) | Search input, login/register fields, address forms (`16px` font on `< md` to avoid mobile auto-zoom) | SKU/title/price inputs, mandatory stock adjustment `note`, carrier/tracking fields |
| **`Select` / `RadioGroup`** | Standard + Card/Pill Radio | Sort select (`newest`, `price_asc`, `price_desc`, `title_asc`), Variant attribute pills (`radiogroup`), Billing address toggle | Status filter selects, Category parent select, User Role selector (`admin` only) |
| **`Checkbox` / `Switch`** | `border-control` (`>= 3:1`) | `in_stock` filter, `billing_same_as_shipping` | `restock` checkbox in Refund modal, `is_active` toggle for variants/categories/users |
| **`Dialog`** | Centered modal (`max-w-md` / `max-w-lg`) | `Cancel Unpaid Order` confirm, `Clear Cart` confirm, optional image lightbox | `Adjust Stock`, `Issue Refund`, `Archive Product`, `Role/Status Change` confirmation (with session revocation notice) |
| **`Sheet`** | Side/bottom drawer (`right`, `left`, `bottom`) | Mobile Nav (`left`), Mobile Catalog Filters (`left`/`bottom`), **Cart Drawer** (`right`) | Mobile Admin Nav (`left`), **Inventory Ledger Drawer** (`right`), **Audit Log JSON Diff Drawer** (`right`) |
| **`DropdownMenu`** | Trigger + keyboard menu | Header Account menu, Category dropdown | Order/Product row action menu (`...`) |
| **`Table`** | Sticky header optional, compact rows | Order items table, `/orders` list (`md+`) | Orders, Products, Inventory, Top Products, Users, Audit Logs |
| **`Tabs`** | Underline or segmented pill | Account sections (`Profile` / `Orders`) optional | Product editor tabs (`Details`, `Media`, `Variants`), Order workbench tabs |
| **`Alert`** | `info`, `warning`, `destructive`, `success` | Cart reconciliation warnings, expired session alert, sandbox test notice | Activation rule (`>= 1` active variant), `CANNOT_DEMOTE_SELF`, date-range errors |
| **`Toast` (`Sonner`)** | `polite` (`success`/`info`), `assertive` (`error`) | `"Added to cart"`, `"Cart merged"`, `"Profile updated"` | `"Stock adjusted"`, `"Order marked shipped"`, `"Role updated & sessions revoked"` |
| **`Skeleton`** | Static pulse (disabled on reduced motion) | Catalog card skeletons, PDP skeleton, Cart line skeletons | KPI card skeletons, chart skeleton, table row skeletons |
| **`Pagination`** | Prev / pages / Next + count | `/products` catalog pagination (`page`, `page_size`, `total`, `has_next`) | `/admin/orders`, `/admin/products`, `/admin/users`, `/admin/audit-logs`, ledger pagination |

---

## 6. Global Accessibility Standards (WCAG 2.1 AA)

1. **Skip Links**: Every layout (`(storefront)` and `(admin)`) includes `<a href="#main-content">Skip to main content</a>` as the first element in DOM order, visually hidden until `:focus-visible`, then rendered at top-left above the header (`z-50`).
2. **Semantic Landmarks & Headings**:
   * Each page uses `<header>`, `<nav aria-label="...">` (distinguishing `"Primary"`, `"Breadcrumb"`, `"Pagination"`, `"Admin Sidebar"`), `<main id="main-content">`, optional `<aside aria-label="Filters">`, and `<footer>`.
   * Strictly **one `<h1>`** per route; heading levels never skip (`h1 -> h2 -> h3`).
3. **Keyboard Order & Focus Management**:
   * All interactive elements are reachable via `Tab` / `Shift+Tab` in logical visual reading order (`no positive tabindex`).
   * Visible focus ring on all focusables: `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background`.
   * `Dialog` and `Sheet` components trap focus while open, close on `Escape`, and restore focus to the triggering element on close.
   * PDP thumbnail lists and variant attribute selectors use `role="radiogroup"` / arrow-key navigation.
4. **ARIA Live Regions**:
   * `aria-live="polite"` (`role="status"`): Cart item count changes, catalog filter result counts (`"Showing 12 products"`), non-blocking success toasts.
   * `aria-live="assertive"` (`role="alert"`): Checkout errors, form validation error summaries, `401 AUTH_SESSION_EXPIRED` notices, `409 INSUFFICIENT_STOCK` conflicts, and `PENDING_PAYMENT` reservation timer milestones (announced **only** at `05:00`, `01:00`, and `00:00` expired — never per-second announcements!).
5. **Reduced Motion (`prefers-reduced-motion: reduce`)**:
   * All drawer slides, dialog scales, gallery transitions, and hover image zooms collapse to instant or `0.01ms` opacity fades.
   * `Skeleton` shimmer animations switch to static `--muted` fill.
   * Free-shipping progress bar transitions become instant.
6. **Images & Iconography**:
   * Storefront product images render `alt={image.alt_text || product.title}`; decorative icons inside labeled buttons carry `aria-hidden="true"`.
   * Icon-only buttons (cart, close drawer, quantity `-`/`+`, delete item, theme toggle) **always** carry descriptive `aria-label` (e.g., `aria-label="Increase quantity for Charcoal Merino Tee"`).
   * Touch controls (`+/-` steppers, swatches, pagination buttons, mobile nav triggers) enforce minimum **`44px × 44px`** hit targets on `< lg` viewports (using invisible padding hit-areas if visual icon is compact).
7. **Form Validation Pattern**:
   * Native `required` paired with explicit inline messages linked via `aria-describedby` and `aria-invalid="true"`.
   * On submit error, focus moves to the first invalid field or the top form error summary (`role="alert"`).
