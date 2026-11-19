import "@testing-library/jest-dom/vitest";
import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  registerAuthInvalidationListener,
  setMemoryCsrfToken,
} from "@/lib/api/client";
import type {
  AdminCategoryDTO,
  AdminInventoryItemDTO,
  AdminOrderDetailDTO,
  AdminOrderListItemDTO,
  AdminProductDetailDTO,
  AdminProductSummaryDTO,
  AdminUserDTO,
  AdminVariantDTO,
  AnalyticsSummaryResponse,
  AnalyticsTimeseriesResponse,
  AuditLogEntryDTO,
  CartDTO,
  InventoryHealthResponse,
  InventoryLedgerEntryDTO,
  TopProductsResponse,
  UserDTO,
  UserRole,
} from "@/lib/api/types";
import { AdminAuditLogsView } from "@/components/admin/admin-audit-logs-view";
import { AdminCategoriesView } from "@/components/admin/admin-categories-view";
import { AdminDashboardView } from "@/components/admin/admin-dashboard-view";
import { AdminInventoryView } from "@/components/admin/admin-inventory-view";
import { AdminOrderDetailView } from "@/components/admin/admin-order-detail-view";
import { AdminOrdersView } from "@/components/admin/admin-orders-view";
import { AdminProductEditorView } from "@/components/admin/admin-product-editor-view";
import { AdminProductsView } from "@/components/admin/admin-products-view";
import { AdminUsersView } from "@/components/admin/admin-users-view";
import { ToastProvider } from "@/components/ui/toast";
import { AuthProvider } from "@/providers/auth-provider";
import { CartProvider } from "@/providers/cart-provider";

class MockResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

class NextNotFoundError extends Error {
  constructor() {
    super("NEXT_NOT_FOUND");
  }
}

const mockNotFound = vi.fn(() => {
  throw new NextNotFoundError();
});

const mockRouter = {
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  prefetch: vi.fn(),
};

let mockPathname = "/admin";
let mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => mockRouter,
  useSearchParams: () => mockSearchParams,
  notFound: () => mockNotFound(),
}));

function clearCsrfCookie(): void {
  document.cookie = "ecom_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  details: Record<string, unknown> = {}
): Response {
  return jsonResponse(
    {
      error: {
        code,
        message,
        details,
        request_id: "req-qa-fe-003",
      },
    },
    status
  );
}

function makeUser(role: UserRole = "admin", overrides: Partial<UserDTO> = {}): UserDTO {
  return {
    id: `user-${role}`,
    email: `${role}@atelier.test`,
    full_name: `Test ${role.toUpperCase()}`,
    role,
    is_active: true,
    created_at: "2026-11-18T10:00:00Z",
    ...overrides,
  };
}

function makeEmptyCart(): CartDTO {
  return {
    id: "cart-admin-test",
    currency: "USD",
    items: [],
    item_count: 0,
    subtotal_cents: 0,
    estimated_shipping_cents: 0,
    estimated_total_cents: 0,
  };
}

function makeCategory(overrides: Partial<AdminCategoryDTO> = {}): AdminCategoryDTO {
  return {
    id: "cat-1",
    parent_id: null,
    name: "Knitwear",
    slug: "knitwear",
    description: "Merino and cashmere knits",
    is_active: true,
    sort_order: 1,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-01T10:00:00Z",
    ...overrides,
  };
}

function makeVariant(overrides: Partial<AdminVariantDTO> = {}): AdminVariantDTO {
  return {
    id: "var-1",
    product_id: "11111111-1111-4111-8111-111111111111",
    sku: "MERINO-CHR-M",
    title: "Charcoal / M",
    attributes: { color: "Charcoal", size: "M" },
    price_cents: 4500,
    compare_at_price_cents: 6000,
    currency: "USD",
    is_active: true,
    on_hand: 12,
    reserved: 3,
    available_quantity: 9,
    low_stock_threshold: 5,
    in_stock: true,
    low_stock: false,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-18T10:00:00Z",
    ...overrides,
  };
}

function makeAdminProductDetail(
  overrides: Partial<AdminProductDetailDTO> = {}
): AdminProductDetailDTO {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    title: "Merino Crewneck",
    slug: "merino-crewneck",
    description: "Fine-gauge Italian merino wool.",
    status: "ACTIVE",
    category: { id: "cat-1", name: "Knitwear", slug: "knitwear" },
    images: [
      {
        id: "img-1",
        url: "https://cdn.example.com/merino-front.webp",
        alt_text: "Merino Crewneck front",
        sort_order: 0,
        is_primary: true,
      },
    ],
    variants: [makeVariant()],
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-18T10:00:00Z",
    ...overrides,
  };
}

function makeAdminProductSummary(
  overrides: Partial<AdminProductSummaryDTO> = {}
): AdminProductSummaryDTO {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    title: "Merino Crewneck",
    slug: "merino-crewneck",
    status: "ACTIVE",
    category: { id: "cat-1", name: "Knitwear", slug: "knitwear" },
    primary_image: {
      id: "img-1",
      url: "https://cdn.example.com/merino-front.webp",
      alt_text: "Merino Crewneck front",
      sort_order: 0,
      is_primary: true,
    },
    min_price_cents: 4500,
    max_price_cents: 4500,
    currency: "USD",
    variants_count: 1,
    active_variants_count: 1,
    total_available_quantity: 9,
    in_stock: true,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-18T10:00:00Z",
    ...overrides,
  };
}

function makeAdminOrderDetail(
  overrides: Partial<AdminOrderDetailDTO> = {}
): AdminOrderDetailDTO {
  return {
    id: "order-uuid-1",
    order_number: "ORD-20261119-8F3K2A",
    user_id: "user-cust-1",
    status: "CONFIRMED",
    payment_status: "PAID",
    fulfillment_status: "ALLOCATED",
    customer_email: "alice@example.com",
    currency: "USD",
    subtotal_cents: 9000,
    discount_cents: 0,
    shipping_cents: 1000,
    tax_cents: 720,
    total_cents: 10720,
    shipping_address: {
      full_name: "Alice Mercer",
      line1: "120 Market St",
      line2: "Apt 4B",
      city: "San Francisco",
      state: "CA",
      postal_code: "94105",
      country: "US",
      phone: "+14155550101",
    },
    billing_address: {
      full_name: "Alice Mercer",
      line1: "120 Market St",
      line2: "Apt 4B",
      city: "San Francisco",
      state: "CA",
      postal_code: "94105",
      country: "US",
      phone: "+14155550101",
    },
    items: [
      {
        id: "oi-1",
        product_id: "11111111-1111-4111-8111-111111111111",
        variant_id: "var-1",
        sku_snapshot: "MERINO-CHR-M",
        product_title_snapshot: "Merino Crewneck",
        variant_title_snapshot: "Charcoal / M",
        attributes_snapshot: { color: "Charcoal", size: "M" },
        unit_price_cents: 4500,
        quantity: 2,
        line_total_cents: 9000,
      },
    ],
    payment_intent: {
      id: "pi-1",
      provider: "sandbox_simpay",
      provider_intent_id: "pi_sandbox_123",
      amount_cents: 10720,
      currency: "USD",
      status: "SUCCEEDED",
      failure_code: null,
      failure_message: null,
      created_at: "2026-11-19T12:00:00Z",
    },
    payment_intents: [
      {
        id: "pi-1",
        provider: "sandbox_simpay",
        provider_intent_id: "pi_sandbox_123",
        amount_cents: 10720,
        currency: "USD",
        status: "SUCCEEDED",
        failure_code: null,
        failure_message: null,
        created_at: "2026-11-19T12:00:00Z",
      },
    ],
    reservations: [
      {
        id: "res-1",
        order_id: "order-uuid-1",
        variant_id: "var-1",
        quantity: 2,
        status: "COMMITTED",
        expires_at: "2026-11-19T12:15:00Z",
        created_at: "2026-11-19T12:00:00Z",
        updated_at: "2026-11-19T12:02:00Z",
      },
    ],
    reservation_expires_at: null,
    events: [
      {
        id: "evt-1",
        actor_user_id: null,
        event_type: "PAYMENT_SUCCEEDED",
        from_status: "PENDING_PAYMENT",
        to_status: "CONFIRMED",
        metadata: { provider: "sandbox_simpay" },
        created_at: "2026-11-19T12:02:00Z",
      },
    ],
    placed_at: "2026-11-19T12:00:00Z",
    paid_at: "2026-11-19T12:02:00Z",
    shipped_at: null,
    completed_at: null,
    cancelled_at: null,
    cancellation_reason: null,
    updated_at: "2026-11-19T12:02:00Z",
    ...overrides,
  };
}

function renderWithAppProviders(ui: React.ReactNode) {
  return render(
    <ToastProvider>
      <AuthProvider>
        <CartProvider>{ui}</CartProvider>
      </AuthProvider>
    </ToastProvider>
  );
}

/** Open a Radix Select dropdown in JSDOM and click an option matching optionText. */
async function selectRadixOption(trigger: HTMLElement, optionText: string | RegExp) {
  fireEvent.keyDown(trigger, { key: "Enter", code: "Enter" });
  const listbox = await screen.findByRole("listbox");
  const option = within(listbox).getByRole("option", { name: optionText });
  fireEvent.click(option);
}

function installDomPolyfills() {
  vi.stubGlobal("ResizeObserver", MockResizeObserver);
  if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = vi.fn();
  }
  if (!HTMLElement.prototype.hasPointerCapture) {
    HTMLElement.prototype.hasPointerCapture = () => false;
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    HTMLElement.prototype.releasePointerCapture = vi.fn();
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    HTMLElement.prototype.setPointerCapture = vi.fn();
  }
}

describe("AdminDashboardView (Analytics, UTC Presets, Chart/Table & Inventory Health)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/admin";
    mockSearchParams = new URLSearchParams();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    installDomPolyfills();
  });

  afterEach(() => {
    cleanup();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("formats KPIs (including refund highlight & $0.00 AOV), links status pills, validates UTC presets/custom bounds without redundant fetches, toggles Chart/Data Table via keyboard, sorts Top Products, and links Inventory Health SKUs", async () => {
    const summaryWithRefund: AnalyticsSummaryResponse = {
      from_date: "2026-10-21",
      to_date: "2026-11-19",
      gross_revenue_cents: 25_000,
      refunded_revenue_cents: 5_000,
      net_revenue_cents: 20_000,
      paid_orders_count: 4,
      average_order_value_cents: 5_000,
      orders_by_status: {
        PENDING_PAYMENT: 2,
        CONFIRMED: 3,
        PROCESSING: 1,
        SHIPPED: 2,
        COMPLETED: 1,
        CANCELLED: 1,
        REFUNDED: 1,
      },
    };

    const zeroSummary: AnalyticsSummaryResponse = {
      from_date: "2026-11-13",
      to_date: "2026-11-19",
      gross_revenue_cents: 0,
      refunded_revenue_cents: 0,
      net_revenue_cents: 0,
      paid_orders_count: 0,
      average_order_value_cents: 0,
      orders_by_status: {
        PENDING_PAYMENT: 0,
        CONFIRMED: 0,
        PROCESSING: 0,
        SHIPPED: 0,
        COMPLETED: 0,
        CANCELLED: 0,
        REFUNDED: 0,
      },
    };

    const timeseriesData: AnalyticsTimeseriesResponse = {
      from_date: "2026-11-17",
      to_date: "2026-11-19",
      points: [
        { date: "2026-11-17", net_revenue_cents: 0, paid_orders_count: 0 },
        { date: "2026-11-18", net_revenue_cents: 12_500, paid_orders_count: 2 },
        { date: "2026-11-19", net_revenue_cents: 7_500, paid_orders_count: 2 },
      ],
    };

    const topProductsRevenue: TopProductsResponse = {
      from_date: "2026-10-21",
      to_date: "2026-11-19",
      items: [
        {
          variant_id: "var-1",
          product_id: "11111111-1111-4111-8111-111111111111",
          sku_snapshot: "MERINO-CHR-M",
          product_title_snapshot: "Merino Crewneck",
          variant_title_snapshot: "Charcoal / M",
          units_sold: 3,
          revenue_cents: 13_500,
        },
      ],
    };

    const topProductsUnits: TopProductsResponse = {
      from_date: "2026-10-21",
      to_date: "2026-11-19",
      items: [
        {
          variant_id: "var-2",
          product_id: "22222222-2222-4222-8222-222222222222",
          sku_snapshot: "TEE-IVR-S",
          product_title_snapshot: "Heavyweight Tee",
          variant_title_snapshot: "Ivory / S",
          units_sold: 8,
          revenue_cents: 12_000,
        },
      ],
    };

    const healthData: InventoryHealthResponse = {
      summary: {
        active_skus_count: 14,
        out_of_stock_count: 1,
        low_stock_count: 2,
        total_reserved_units: 6,
      },
      alerts: {
        items: [
          {
            variant_id: "var-low",
            product_id: "11111111-1111-4111-8111-111111111111",
            sku: "TEE-CHR-L",
            product_title: "Merino Crewneck",
            variant_title: "Charcoal / L",
            on_hand: 3,
            reserved: 1,
            available_quantity: 2,
            low_stock_threshold: 5,
            in_stock: true,
            low_stock: true,
          },
        ],
        total: 1,
        page: 1,
        page_size: 10,
        has_next: false,
      },
    };

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-staff" });
      }
      if (url.pathname.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.pathname.endsWith("/admin/analytics/summary")) {
        const from = url.searchParams.get("from") ?? "";
        const to = url.searchParams.get("to") ?? "";
        // When Last 7 days preset is active (6-day span), return zeroSummary to verify $0.00 AOV
        if (from && to && (Date.parse(to) - Date.parse(from)) / 86_400_000 === 6) {
          return jsonResponse(zeroSummary);
        }
        return jsonResponse(summaryWithRefund);
      }
      if (url.pathname.endsWith("/admin/analytics/timeseries")) {
        return jsonResponse(timeseriesData);
      }
      if (url.pathname.endsWith("/admin/analytics/top-products")) {
        if (url.searchParams.get("sort_by") === "units") {
          return jsonResponse(topProductsUnits);
        }
        return jsonResponse(topProductsRevenue);
      }
      if (url.pathname.endsWith("/admin/analytics/inventory-health")) {
        return jsonResponse(healthData);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url.pathname}`);
    });

    renderWithAppProviders(<AdminDashboardView />);

    // Exact KPI values & highlighted refund subtitle
    expect(await screen.findByText("$200.00")).toBeInTheDocument();
    expect(screen.getByText("$250.00")).toBeInTheDocument();
    const refundSubtitle = screen.getByText("Refunded: -$50.00");
    expect(refundSubtitle).toHaveClass("text-destructive");
    const paidOrdersCard = screen.getByText("Paid Orders").closest("div")?.parentElement as HTMLElement;
    expect(within(paidOrdersCard).getByText("4")).toBeInTheDocument();
    expect(screen.getByText("$50.00")).toBeInTheDocument();

    // Status pills link to /admin/orders?status=...&from_ts=...&to_ts=...
    const confirmedPill = screen.getByRole("link", { name: /Confirmed/i });
    const confirmedHref = confirmedPill.getAttribute("href") ?? "";
    expect(confirmedHref).toMatch(
      /^\/admin\/orders\?status=CONFIRMED&from_ts=\d{4}-\d{2}-\d{2}T00%3A00%3A00Z&to_ts=\d{4}-\d{2}-\d{2}T23%3A59%3A59\.999Z$/
    );

    // Recharts chart container rendered
    const chartContainer = screen.getByLabelText("Daily Net Revenue and Paid Orders chart");
    expect(chartContainer).toBeInTheDocument();
    expect(chartContainer.querySelector(".recharts-responsive-container")).toBeInTheDocument();

    // Keyboard-accessible Chart | Data Table toggle via ArrowRight on TabsTrigger
    const chartTab = screen.getByRole("tab", { name: "Chart" });
    const tableTab = screen.getByRole("tab", { name: "Data Table" });
    expect(chartTab).toHaveAttribute("aria-selected", "true");
    act(() => {
      chartTab.focus();
    });
    fireEvent.keyDown(chartTab, { key: "ArrowRight", code: "ArrowRight" });
    await waitFor(() => {
      expect(tableTab).toHaveAttribute("aria-selected", "true");
    });

    // Data Table shows full daily UTC rows including zero-sale days
    expect(screen.getByText("2026-11-17")).toBeInTheDocument();
    expect(screen.getByText("$125.00")).toBeInTheDocument();
    expect(screen.getByText("$75.00")).toBeInTheDocument();
    const zeroDayRow = screen.getByText("2026-11-17").closest("tr") as HTMLTableRowElement;
    expect(within(zeroDayRow).getByText("$0.00")).toBeInTheDocument();
    expect(within(zeroDayRow).getByText("0")).toBeInTheDocument();

    // Top Products Revenue vs Units Sold toggle
    expect(screen.getByRole("link", { name: "Merino Crewneck" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Units Sold" }));
    expect(await screen.findByRole("link", { name: "Heavyweight Tee" })).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/admin/analytics/top-products") && String(url).includes("sort_by=units")
      )
    ).toBe(true);

    // Inventory Health alert row links Adjust Stock to /admin/inventory?sku=...
    const adjustLink = screen.getByRole("link", { name: "Adjust Stock" });
    expect(adjustLink).toHaveAttribute("href", "/admin/inventory?sku=TEE-CHR-L");

    // UTC Presets: Last 7 days (returns $0.00 AOV when zero), Last 90 days, Last 365 days
    fireEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    await waitFor(() => {
      const zeroAovMatches = screen.getAllByText("$0.00");
      expect(zeroAovMatches.length).toBeGreaterThanOrEqual(3);
    });
    expect(within(paidOrdersCard).getByText("0")).toBeInTheDocument();
    expect(screen.getByText("Refunded: $0.00")).toBeInTheDocument();

    for (const [presetLabel, expectedSpanDays] of [
      ["Last 90 days", 89],
      ["Last 365 days", 364],
    ] as const) {
      const btn = screen.getByRole("button", { name: presetLabel });
      fireEvent.click(btn);
      expect(btn).toHaveAttribute("aria-pressed", "true");
      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some(([url]) => {
            const parsed = new URL(String(url));
            if (!parsed.pathname.endsWith("/admin/analytics/summary")) return false;
            const from = parsed.searchParams.get("from");
            const to = parsed.searchParams.get("to");
            return Boolean(
              from && to && Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) === expectedSpanDays
            );
          })
        ).toBe(true);
      });
    }

    // Custom UTC range validation (> 366 days and from > to) blocks API calls
    fireEvent.click(screen.getByRole("button", { name: "Custom UTC Range" }));

    const analyticsCallsBeforeInvalid = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/admin/analytics/")
    ).length;

    fireEvent.change(screen.getByLabelText("From (UTC)"), {
      target: { value: "2025-01-01" },
    });
    fireEvent.change(screen.getByLabelText("To (UTC)"), {
      target: { value: "2026-01-03" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply Range" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Date range cannot exceed 366 days");

    fireEvent.change(screen.getByLabelText("From (UTC)"), {
      target: { value: "2026-06-15" },
    });
    fireEvent.change(screen.getByLabelText("To (UTC)"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply Range" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Start date must be before or equal to end date."
    );

    const analyticsCallsAfterInvalid = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/admin/analytics/")
    ).length;
    expect(analyticsCallsAfterInvalid).toBe(analyticsCallsBeforeInvalid);

    // Valid custom range submits cleanly
    fireEvent.change(screen.getByLabelText("From (UTC)"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("To (UTC)"), {
      target: { value: "2026-10-15" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply Range" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("/admin/analytics/summary?from=2026-10-01&to=2026-10-15")
        )
      ).toBe(true);
    });
  });
});

describe("AdminCategoriesView (Hierarchy, Create/Edit Dialog, Self-Exclusion, 409/422 Errors)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/admin/categories";
    mockSearchParams = new URLSearchParams();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    installDomPolyfills();
  });

  afterEach(() => {
    cleanup();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("displays hierarchy, excludes current category from parent options on edit, and surfaces 409 SLUG_CONFLICT & 422 CATEGORY_CYCLE_DETECTED", async () => {
    const rootCat = makeCategory({
      id: "cat-root",
      parent_id: null,
      name: "Apparel",
      slug: "apparel",
      sort_order: 1,
    });
    const childCat = makeCategory({
      id: "cat-child",
      parent_id: "cat-root",
      name: "Knitwear",
      slug: "knitwear",
      sort_order: 1,
    });
    const otherCat = makeCategory({
      id: "cat-other",
      parent_id: null,
      name: "Accessories",
      slug: "accessories",
      sort_order: 2,
    });

    let categoriesList = [rootCat, childCat, otherCat];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("admin"), csrf_token: "csrf-admin" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/admin/categories") && method === "GET") {
        return jsonResponse({ items: categoriesList });
      }
      if (url.endsWith("/admin/categories") && method === "POST") {
        const body = JSON.parse(String(init?.body)) as { slug: string; name: string };
        if (body.slug === "knitwear") {
          return errorResponse(409, "SLUG_CONFLICT", "Category slug already exists.");
        }
        const created = makeCategory({
          id: "cat-new",
          name: body.name,
          slug: body.slug,
          sort_order: 3,
        });
        categoriesList = [...categoriesList, created];
        return jsonResponse(created, 201);
      }
      if (url.endsWith("/admin/categories/cat-root") && method === "PATCH") {
        return errorResponse(
          422,
          "CATEGORY_CYCLE_DETECTED",
          "Cannot set a descendant category as parent."
        );
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderWithAppProviders(<AdminCategoriesView />);

    expect(await screen.findByText("Knitwear")).toBeInTheDocument();
    const knitwearRow = screen.getByText("Knitwear").closest("tr") as HTMLTableRowElement;
    expect(within(knitwearRow).getByText("Apparel")).toBeInTheDocument();
    const indentedLabel = within(knitwearRow).getByText("Knitwear").closest("div");
    expect(indentedLabel).toHaveStyle({ paddingLeft: "20px" });

    // Create category: surface 409 SLUG_CONFLICT then succeed
    fireEvent.click(screen.getByRole("button", { name: "New Category" }));
    const createDialog = await screen.findByRole("dialog");

    fireEvent.change(within(createDialog).getByLabelText("Name"), {
      target: { value: "Knitwear" },
    });
    expect(within(createDialog).getByLabelText("Slug")).toHaveValue("knitwear");
    fireEvent.click(within(createDialog).getByRole("button", { name: "Create Category" }));

    expect(await within(createDialog).findByText("Category slug already exists.")).toBeInTheDocument();

    fireEvent.change(within(createDialog).getByLabelText("Slug"), {
      target: { value: "outerwear" },
    });
    fireEvent.change(within(createDialog).getByLabelText("Name"), {
      target: { value: "Outerwear" },
    });
    fireEvent.click(within(createDialog).getByRole("button", { name: "Create Category" }));

    expect(await screen.findByText("Category created")).toBeInTheDocument();
    expect(await screen.findByText("Outerwear")).toBeInTheDocument();

    // Verify CSRF header on POST /admin/categories
    const postCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith("/admin/categories") && init?.method === "POST"
    );
    expect((postCall?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-admin");

    // Edit root category Apparel: self excluded from parent options; cycle returns 422 CATEGORY_CYCLE_DETECTED
    fireEvent.click(screen.getByRole("button", { name: "Edit category Apparel" }));
    const editDialog = await screen.findByRole("dialog");

    const parentTrigger = within(editDialog).getByRole("combobox", { name: "Parent Category" });
    fireEvent.keyDown(parentTrigger, { key: "Enter", code: "Enter" });
    const listbox = await screen.findByRole("listbox");

    expect(within(listbox).queryByRole("option", { name: "Apparel" })).not.toBeInTheDocument();
    const childOption = within(listbox).getByRole("option", { name: /Knitwear/ });
    fireEvent.click(childOption);

    fireEvent.click(within(editDialog).getByRole("button", { name: "Save Changes" }));

    expect(
      await within(editDialog).findByText("Selected parent would create a category cycle.")
    ).toBeInTheDocument();
  });
});

describe("AdminProductsView, AdminProductEditorView & AdminVariantModal", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/admin/products";
    mockSearchParams = new URLSearchParams();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    installDomPolyfills();
  });

  afterEach(() => {
    cleanup();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("filters products and archives via confirmation dialog (DELETE /admin/products/{id})", async () => {
    const activeProd = makeAdminProductSummary({
      id: "11111111-1111-4111-8111-111111111111",
      title: "Merino Crewneck",
      slug: "merino-crewneck",
      status: "ACTIVE",
    });
    const archivedProd = makeAdminProductSummary({
      id: "22222222-2222-4222-8222-222222222222",
      title: "Legacy Scarf",
      slug: "legacy-scarf",
      status: "ARCHIVED",
    });

    let currentItems = [activeProd, archivedProd];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";

      if (url.pathname.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-staff" });
      }
      if (url.pathname.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.pathname.endsWith("/admin/categories") && method === "GET") {
        return jsonResponse({ items: [makeCategory()] });
      }
      if (url.pathname.endsWith("/admin/products") && method === "GET") {
        const statusParam = url.searchParams.get("status");
        const qParam = url.searchParams.get("q")?.toLowerCase();
        const filtered = currentItems.filter((item) => {
          if (statusParam && item.status !== statusParam) return false;
          if (qParam && !item.title.toLowerCase().includes(qParam)) return false;
          return true;
        });
        return jsonResponse({
          items: filtered,
          total: filtered.length,
          page: 1,
          page_size: 20,
          has_next: false,
        });
      }
      if (
        url.pathname.endsWith("/admin/products/11111111-1111-4111-8111-111111111111") &&
        method === "DELETE"
      ) {
        currentItems = currentItems.map((item) =>
          item.id === activeProd.id ? { ...item, status: "ARCHIVED" } : item
        );
        return jsonResponse(makeAdminProductDetail({ status: "ARCHIVED" }));
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url.pathname}`);
    });

    renderWithAppProviders(<AdminProductsView />);

    expect(await screen.findByText("Merino Crewneck")).toBeInTheDocument();
    expect(screen.getByText("Legacy Scarf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive Legacy Scarf" })).not.toBeInTheDocument();

    // Filter by status ACTIVE and search
    await selectRadixOption(screen.getByRole("combobox", { name: "Status" }), "Active");
    await waitFor(() => {
      expect(screen.queryByText("Legacy Scarf")).not.toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText("Search title, slug, or SKU"), {
      target: { value: "Merino" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url]) =>
            String(url).includes("/admin/products") &&
            String(url).includes("q=Merino") &&
            String(url).includes("status=ACTIVE")
        )
      ).toBe(true);
    });

    // Archive confirmation modal -> DELETE /admin/products/{id} with X-CSRF-Token
    fireEvent.click(screen.getByRole("button", { name: "Archive Merino Crewneck" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Archive Product", { selector: "h2" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Archive Product" }));

    expect(await screen.findByText("Product archived")).toBeInTheDocument();
    const deleteCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).includes("/admin/products/11111111-1111-4111-8111-111111111111") &&
        init?.method === "DELETE"
    );
    expect(deleteCall).toBeDefined();
    expect((deleteCall?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-staff");
  });

  it("handles 404 PRODUCT_NOT_FOUND, enforces DRAFT -> ACTIVE 0-active-variant guard, validates image URLs, and handles variant cents, compare_at >= price, 409 SKU_CONFLICT, and inventory link", async () => {
    const missingId = "99999999-9999-4999-8999-999999999999";
    const prodId = "11111111-1111-4111-8111-111111111111";

    let productState = makeAdminProductDetail({
      id: prodId,
      status: "DRAFT",
      images: [],
      variants: [],
    });

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("admin"), csrf_token: "csrf-admin" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/admin/categories") && method === "GET") {
        return jsonResponse({ items: [makeCategory()] });
      }
      if (url.endsWith(`/admin/products/${missingId}`) && method === "GET") {
        return errorResponse(404, "PRODUCT_NOT_FOUND", "Product not found.");
      }
      if (url.endsWith(`/admin/products/${prodId}`) && method === "GET") {
        return jsonResponse(productState);
      }
      if (url.endsWith(`/admin/products/${prodId}`) && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as Partial<AdminProductDetailDTO>;
        productState = {
          ...productState,
          title: body.title ?? productState.title,
          slug: body.slug ?? productState.slug,
          status: body.status ?? productState.status,
          images: (body.images ?? []).map((img, i) => ({
            id: `img-${i + 1}`,
            url: img.url,
            alt_text: img.alt_text ?? "",
            sort_order: img.sort_order ?? i,
            is_primary: Boolean(img.is_primary),
          })),
        };
        return jsonResponse(productState);
      }
      if (url.endsWith(`/admin/products/${prodId}/variants`) && method === "POST") {
        const body = JSON.parse(String(init?.body)) as {
          sku: string;
          title: string;
          price_cents: number;
          compare_at_price_cents: number | null;
          currency: string;
          initial_on_hand: number;
          low_stock_threshold: number;
          is_active: boolean;
          attributes: Record<string, unknown>;
        };
        if (body.sku === "EXISTING-SKU") {
          return errorResponse(409, "SKU_CONFLICT", "A variant with this SKU already exists.");
        }
        const created = makeVariant({
          id: "var-created",
          sku: body.sku,
          title: body.title,
          price_cents: body.price_cents,
          compare_at_price_cents: body.compare_at_price_cents,
          currency: body.currency,
          on_hand: body.initial_on_hand,
          reserved: 2,
          available_quantity: body.initial_on_hand - 2,
          low_stock_threshold: body.low_stock_threshold,
          is_active: body.is_active,
          attributes: body.attributes,
        });
        productState = { ...productState, variants: [...productState.variants, created] };
        return jsonResponse(created, 201);
      }
      if (url.endsWith("/admin/variants/var-created") && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as Partial<AdminVariantDTO>;
        const updated = makeVariant({
          ...productState.variants[0],
          ...body,
          id: "var-created",
        });
        productState = { ...productState, variants: [updated] };
        return jsonResponse(updated);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    // 1. Invalid non-UUID and 404 PRODUCT_NOT_FOUND recovery
    renderWithAppProviders(<AdminProductEditorView mode="edit" productId="not-a-uuid" />);
    expect(await screen.findByText("Product not found")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/admin/products/not-a-uuid"))
    ).toBe(false);
    cleanup();

    renderWithAppProviders(<AdminProductEditorView mode="edit" productId={missingId} />);
    expect(await screen.findByText("Product not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Return to Products" })).toHaveAttribute(
      "href",
      "/admin/products"
    );

    cleanup();

    // 2. Draft product with 0 active variants: DRAFT -> ACTIVE guard & Image URL security validation
    renderWithAppProviders(<AdminProductEditorView mode="edit" productId={prodId} />);
    expect(await screen.findByRole("heading", { name: "Merino Crewneck" })).toBeInTheDocument();

    await selectRadixOption(screen.getByRole("combobox", { name: "Status" }), "Active");
    expect(
      screen.getByText("Add and activate at least 1 variant before setting status to ACTIVE")
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save Product Changes" }));
    expect(
      await screen.findByText(
        "Add and activate at least 1 variant before setting status to ACTIVE."
      )
    ).toBeInTheDocument();

    // Revert status to DRAFT so image validation can be checked cleanly
    await selectRadixOption(screen.getByRole("combobox", { name: "Status" }), "Draft");

    // Image URL security validation: reject javascript: and http://, accept https:// and /assets/
    fireEvent.click(screen.getByRole("button", { name: "Add Image" }));
    const urlInput = await screen.findByLabelText("Image URL");
    fireEvent.change(urlInput, { target: { value: "javascript:alert(1)" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Product Changes" }));

    expect(
      await screen.findByText(
        "Invalid URL. Only https://... or /assets/... URLs without whitespace or traversal are allowed."
      )
    ).toBeInTheDocument();

    fireEvent.change(urlInput, { target: { value: "http://cdn.example.com/merino.png" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Product Changes" }));
    expect(
      await screen.findByText(
        "Invalid URL. Only https://... or /assets/... URLs without whitespace or traversal are allowed."
      )
    ).toBeInTheDocument();

    const patchBeforeValid = fetchMock.mock.calls.filter(
      ([url, init]) => String(url).endsWith(`/admin/products/${prodId}`) && init?.method === "PATCH"
    );
    expect(patchBeforeValid).toHaveLength(0);

    fireEvent.change(urlInput, { target: { value: "https://cdn.example.com/merino.webp" } });
    fireEvent.click(screen.getByRole("button", { name: "Add Image" }));
    const allUrlInputs = screen.getAllByLabelText("Image URL");
    fireEvent.change(allUrlInputs[1], { target: { value: "/assets/merino-back.png" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Product Changes" }));

    expect(await screen.findByText("Product updated")).toBeInTheDocument();
    const patchProductCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith(`/admin/products/${prodId}`) && init?.method === "PATCH"
    );
    expect(patchProductCall).toBeDefined();
    expect(JSON.parse(String(patchProductCall?.[1]?.body)).images).toHaveLength(2);

    // 3. Variant Create Modal: parseDollarsToCents, compare_at >= price, 409 SKU_CONFLICT
    fireEvent.click(screen.getByRole("button", { name: "Add Variant" }));
    const createVarDialog = await screen.findByRole("dialog");

    fireEvent.change(within(createVarDialog).getByLabelText("SKU"), {
      target: { value: "EXISTING-SKU" },
    });
    fireEvent.change(within(createVarDialog).getByLabelText("Variant Title"), {
      target: { value: "Charcoal / M" },
    });
    fireEvent.change(within(createVarDialog).getByLabelText("Price ($)"), {
      target: { value: "45.50" },
    });
    fireEvent.change(within(createVarDialog).getByLabelText("Compare-at Price ($)"), {
      target: { value: "40.00" },
    });
    fireEvent.change(within(createVarDialog).getByLabelText("Initial On-Hand Stock"), {
      target: { value: "12" },
    });
    fireEvent.click(within(createVarDialog).getByRole("button", { name: "Create Variant" }));

    expect(
      await within(createVarDialog).findByText(
        "Compare-at price must be greater than or equal to price."
      )
    ).toBeInTheDocument();

    fireEvent.change(within(createVarDialog).getByLabelText("Compare-at Price ($)"), {
      target: { value: "60.00" },
    });
    fireEvent.click(within(createVarDialog).getByRole("button", { name: "Create Variant" }));

    // 409 SKU_CONFLICT surfaced
    expect(
      await within(createVarDialog).findByText("A variant with this SKU already exists.")
    ).toBeInTheDocument();

    fireEvent.change(within(createVarDialog).getByLabelText("SKU"), {
      target: { value: "MERINO-CHR-M" },
    });
    fireEvent.click(within(createVarDialog).getByRole("button", { name: "Create Variant" }));

    expect(await screen.findByText("Variant created")).toBeInTheDocument();
    const createVarCalls = fetchMock.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith(`/admin/products/${prodId}/variants`) && init?.method === "POST"
    );
    expect(createVarCalls).toHaveLength(2);
    const successVarCall = createVarCalls[1];
    expect((successVarCall?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "csrf-admin"
    );
    expect(JSON.parse(String(successVarCall?.[1]?.body))).toMatchObject({
      sku: "MERINO-CHR-M",
      price_cents: 4550,
      compare_at_price_cents: 6000,
      initial_on_hand: 12,
    });

    // 4. Variant Edit Modal: read-only stock counts + /admin/inventory?sku=... link
    const variantRow = (await screen.findByText("MERINO-CHR-M")).closest("tr") as HTMLTableRowElement;
    fireEvent.click(within(variantRow).getByRole("button", { name: "Edit" }));

    const editVarDialog = await screen.findByRole("dialog");
    expect(
      within(editVarDialog).getByText("Edit Variant: MERINO-CHR-M")
    ).toBeInTheDocument();
    expect(within(editVarDialog).queryByLabelText("Initial On-Hand Stock")).not.toBeInTheDocument();
    expect(within(editVarDialog).getByText("12")).toBeInTheDocument();
    expect(within(editVarDialog).getByText("2")).toBeInTheDocument();
    expect(within(editVarDialog).getByText("10")).toBeInTheDocument();

    const invLink = within(editVarDialog).getByRole("link", {
      name: /Adjust stock in Inventory/i,
    });
    expect(invLink).toHaveAttribute("href", "/admin/inventory?sku=MERINO-CHR-M");

    fireEvent.change(within(editVarDialog).getByLabelText("Price ($)"), {
      target: { value: "49.95" },
    });
    fireEvent.click(within(editVarDialog).getByRole("button", { name: "Save Variant" }));

    await waitFor(() => {
      const patchVarCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/admin/variants/var-created") && init?.method === "PATCH"
      );
      expect(patchVarCall).toBeDefined();
      expect(JSON.parse(String(patchVarCall?.[1]?.body)).price_cents).toBe(4995);
    });
  });

  it("surfaces server-side 422 PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT on product save", async () => {
    const prodId = "11111111-1111-4111-8111-111111111111";
    const productWithVariant = makeAdminProductDetail({
      id: prodId,
      status: "DRAFT",
      variants: [makeVariant()],
    });

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("admin"), csrf_token: "csrf-admin" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/admin/categories") && method === "GET") {
        return jsonResponse({ items: [makeCategory()] });
      }
      if (url.endsWith(`/admin/products/${prodId}`) && method === "GET") {
        return jsonResponse(productWithVariant);
      }
      if (url.endsWith(`/admin/products/${prodId}`) && method === "PATCH") {
        return errorResponse(
          422,
          "PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT",
          "Product requires at least one active variant before activation."
        );
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderWithAppProviders(<AdminProductEditorView mode="edit" productId={prodId} />);
    expect(await screen.findByRole("heading", { name: "Merino Crewneck" })).toBeInTheDocument();

    await selectRadixOption(screen.getByRole("combobox", { name: "Status" }), "Active");
    fireEvent.click(screen.getByRole("button", { name: "Save Product Changes" }));

    expect(
      await screen.findByText("Product requires at least one active variant before activation.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Add and activate at least 1 variant before setting status to ACTIVE")
    ).toBeInTheDocument();
  });
});

describe("AdminInventoryView (SKU Prefill, Stock Invariants, CSRF & Append-Only Ledger)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/admin/inventory";
    mockSearchParams = new URLSearchParams("sku=TEE-CHR-M");
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    installDomPolyfills();
  });

  afterEach(() => {
    cleanup();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("prefills q from ?sku=..., enforces stock adjustment invariants, attaches X-CSRF-Token on adjust, and displays append-only ledger with order links", async () => {
    let invItem: AdminInventoryItemDTO = {
      variant_id: "var-inv-1",
      product_id: "11111111-1111-4111-8111-111111111111",
      sku: "TEE-CHR-M",
      product_title: "Merino Crewneck",
      variant_title: "Charcoal / M",
      product_status: "ACTIVE",
      is_active: true,
      on_hand: 10,
      reserved: 4,
      available_quantity: 6,
      low_stock_threshold: 5,
      in_stock: true,
      low_stock: false,
      updated_at: "2026-11-19T10:00:00Z",
    };

    const ledgerEntries: InventoryLedgerEntryDTO[] = [
      {
        id: "led-1",
        variant_id: "var-inv-1",
        order_id: null,
        order_number: null,
        actor_user_id: "user-admin",
        actor_email: "admin@atelier.test",
        reason: "MANUAL_ADJUSTMENT",
        delta_on_hand: 5,
        delta_reserved: 0,
        on_hand_after: 15,
        reserved_after: 4,
        note: "Restock PO-1042",
        created_at: "2026-11-19T12:00:00Z",
      },
      {
        id: "led-2",
        variant_id: "var-inv-1",
        order_id: "order-1",
        order_number: "ORD-20261119-8F3K2A",
        actor_user_id: null,
        actor_email: null,
        reason: "RESERVE",
        delta_on_hand: 0,
        delta_reserved: 4,
        on_hand_after: 10,
        reserved_after: 4,
        note: "Order reservation",
        created_at: "2026-11-19T11:00:00Z",
      },
    ];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";

      if (url.pathname.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-inv-staff" });
      }
      if (url.pathname.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.pathname.endsWith("/admin/inventory") && method === "GET") {
        return jsonResponse({
          items: [invItem],
          total: 1,
          page: 1,
          page_size: 20,
          has_next: false,
        });
      }
      if (url.pathname.endsWith("/admin/inventory/var-inv-1/adjust") && method === "POST") {
        const body = JSON.parse(String(init?.body)) as {
          delta_on_hand: number;
          note: string;
        };
        if (body.delta_on_hand < 0) {
          return errorResponse(
            409,
            "INSUFFICIENT_STOCK",
            "Cannot reduce on_hand below server-reserved quantity."
          );
        }
        invItem = {
          ...invItem,
          on_hand: invItem.on_hand + body.delta_on_hand,
          available_quantity: invItem.on_hand + body.delta_on_hand - invItem.reserved,
        };
        return jsonResponse(invItem);
      }
      if (url.pathname.endsWith("/admin/inventory/var-inv-1/ledger") && method === "GET") {
        return jsonResponse({
          items: ledgerEntries,
          total: ledgerEntries.length,
          page: 1,
          page_size: 15,
          has_next: false,
        });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url.pathname}`);
    });

    renderWithAppProviders(<AdminInventoryView />);

    // Prefills search input from ?sku=TEE-CHR-M and passes q=TEE-CHR-M
    const searchInput = screen.getByLabelText("Search SKU, product, or variant");
    expect(searchInput).toHaveValue("TEE-CHR-M");
    expect(await screen.findByText("Merino Crewneck")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url).includes("/admin/inventory") && String(url).includes("q=TEE-CHR-M")
      )
    ).toBe(true);

    // Open Adjust Stock modal
    fireEvent.click(screen.getByRole("button", { name: "Adjust Stock" }));
    const adjustDialog = await screen.findByRole("dialog");
    expect(within(adjustDialog).getByText("Adjust Stock — TEE-CHR-M")).toBeInTheDocument();

    const deltaInput = within(adjustDialog).getByLabelText("Delta On Hand (signed integer)");
    const noteInput = within(adjustDialog).getByLabelText("Adjustment Note (required)");
    const submitBtn = within(adjustDialog).getByRole("button", { name: "Apply Adjustment" });

    // Invariant 1: delta_on_hand === 0 blocked
    fireEvent.change(deltaInput, { target: { value: "0" } });
    fireEvent.change(noteInput, { target: { value: "Cycle count" } });
    expect(
      within(adjustDialog).getByText("Stock adjustment delta must be non-zero.")
    ).toBeInTheDocument();
    expect(submitBtn).toBeDisabled();

    // Invariant 2: on_hand + delta_on_hand < reserved (10 + (-7) = 3 < 4) blocked
    fireEvent.change(deltaInput, { target: { value: "-7" } });
    expect(
      within(adjustDialog).getByText(
        "Cannot reduce physical on-hand stock below currently reserved units (4)."
      )
    ).toBeInTheDocument();
    expect(submitBtn).toBeDisabled();

    // Invariant 3: blank note blocked even with valid delta
    fireEvent.change(deltaInput, { target: { value: "+5" } });
    fireEvent.change(noteInput, { target: { value: "   " } });
    expect(submitBtn).toBeDisabled();

    const adjustCallsBeforeValid = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/adjust")
    );
    expect(adjustCallsBeforeValid).toHaveLength(0);

    // Server-side invariant rejection surfaced cleanly inside modal
    fireEvent.change(deltaInput, { target: { value: "-5" } });
    fireEvent.change(noteInput, { target: { value: "Write-off check" } });
    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);

    expect(
      await within(adjustDialog).findByText(
        "Cannot reduce on_hand below server-reserved quantity."
      )
    ).toBeInTheDocument();

    // Valid positive adjustment submits with X-CSRF-Token
    fireEvent.change(deltaInput, { target: { value: "+5" } });
    fireEvent.change(noteInput, { target: { value: "Restock PO-1042" } });
    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);

    expect(await screen.findByText("Stock adjusted")).toBeInTheDocument();
    const adjustCalls = fetchMock.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith("/admin/inventory/var-inv-1/adjust") && init?.method === "POST"
    );
    expect(adjustCalls).toHaveLength(2);
    const adjustCall = adjustCalls[1];
    expect((adjustCall?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "csrf-inv-staff"
    );
    expect(JSON.parse(String(adjustCall?.[1]?.body))).toEqual({
      delta_on_hand: 5,
      note: "Restock PO-1042",
    });

    // Open Append-Only Stock Ledger drawer
    fireEvent.click(screen.getByRole("button", { name: "View Ledger" }));
    const ledgerDialog = await screen.findByRole("dialog");
    expect(within(ledgerDialog).getByText("Stock Ledger — TEE-CHR-M")).toBeInTheDocument();
    expect(within(ledgerDialog).getByText("Append-Only Stock Ledger")).toBeInTheDocument();
    expect(await within(ledgerDialog).findByText("MANUAL_ADJUSTMENT")).toBeInTheDocument();
    expect(within(ledgerDialog).getByText("Restock PO-1042")).toBeInTheDocument();
    expect(within(ledgerDialog).getByText("admin@atelier.test")).toBeInTheDocument();
    expect(within(ledgerDialog).getByText("15 / 4")).toBeInTheDocument();

    const orderLink = within(ledgerDialog).getByRole("link", { name: "ORD-20261119-8F3K2A" });
    expect(orderLink).toHaveAttribute("href", "/admin/orders/ORD-20261119-8F3K2A");
  });
});

describe("AdminOrdersView & AdminOrderDetailView (3-Axis Status Columns, Staff vs Admin RBAC)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/admin/orders";
    mockSearchParams = new URLSearchParams();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    installDomPolyfills();
  });

  afterEach(() => {
    cleanup();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders 3 distinct status columns (Order, Payment, Fulfillment) and applies URL filter params", async () => {
    mockSearchParams = new URLSearchParams(
      "status=CONFIRMED&from_ts=2026-11-01T00:00:00Z&to_ts=2026-11-19T23:59:59.999Z"
    );

    const orderItems: AdminOrderListItemDTO[] = [
      {
        id: "ord-1",
        order_number: "ORD-20261119-8F3K2A",
        user_id: "cust-1",
        customer_email: "alice@example.com",
        status: "CONFIRMED",
        payment_status: "PAID",
        fulfillment_status: "ALLOCATED",
        currency: "USD",
        subtotal_cents: 9000,
        discount_cents: 0,
        shipping_cents: 1000,
        tax_cents: 720,
        total_cents: 10720,
        item_count: 2,
        placed_at: "2026-11-19T12:00:00Z",
        paid_at: "2026-11-19T12:02:00Z",
        shipped_at: null,
        completed_at: null,
        cancelled_at: null,
      },
    ];

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-staff" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.includes("/admin/orders")) {
        return jsonResponse({
          items: orderItems,
          total: 1,
          page: 1,
          page_size: 20,
          has_next: false,
        });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(<AdminOrdersView />);

    expect(await screen.findByText("ORD-20261119-8F3K2A")).toBeInTheDocument();

    // 3 distinct status column headers
    expect(screen.getByRole("columnheader", { name: "Order Status" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Payment Status" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Fulfillment Status" })).toBeInTheDocument();

    const row = screen.getByText("ORD-20261119-8F3K2A").closest("tr") as HTMLTableRowElement;
    expect(within(row).getByText("Confirmed")).toBeInTheDocument();
    expect(within(row).getByText("Paid")).toBeInTheDocument();
    expect(within(row).getByText("Allocated")).toBeInTheDocument();

    expect(
      fetchMock.mock.calls.some(
        ([url]) =>
          String(url).includes("/admin/orders?") &&
          String(url).includes("status=CONFIRMED") &&
          String(url).includes("from_ts=2026-11-01T00%3A00%3A00Z")
      )
    ).toBe(true);
  });

  it("enforces staff RBAC on order detail: allows fulfillment & unpaid cancel, and NEVER renders Issue Refund", async () => {
    const unpaidOrder = makeAdminOrderDetail({
      order_number: "ORD-UNPAID-1",
      status: "PENDING_PAYMENT",
      payment_status: "REQUIRES_PAYMENT",
      fulfillment_status: "UNFULFILLED",
      paid_at: null,
    });

    let paidOrder = makeAdminOrderDetail({
      order_number: "ORD-PAID-1",
      status: "CONFIRMED",
      payment_status: "PAID",
      fulfillment_status: "ALLOCATED",
    });

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-staff-order" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/admin/orders/ORD-UNPAID-1") && method === "GET") {
        return jsonResponse(unpaidOrder);
      }
      if (url.endsWith("/admin/orders/ORD-UNPAID-1/cancel") && method === "POST") {
        return jsonResponse({
          ...unpaidOrder,
          status: "CANCELLED",
          payment_status: "CANCELLED",
          fulfillment_status: "CANCELLED",
        });
      }
      if (url.endsWith("/admin/orders/ORD-PAID-1") && method === "GET") {
        return jsonResponse(paidOrder);
      }
      if (url.endsWith("/admin/orders/ORD-PAID-1/fulfill") && method === "POST") {
        const body = JSON.parse(String(init?.body)) as { action: string };
        if (body.action === "START_PROCESSING") {
          paidOrder = { ...paidOrder, status: "PROCESSING" };
        } else if (body.action === "SHIP") {
          paidOrder = {
            ...paidOrder,
            status: "SHIPPED",
            fulfillment_status: "SHIPPED",
            shipped_at: "2026-11-19T15:00:00Z",
          };
        }
        return jsonResponse(paidOrder);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    // 1. Staff on PENDING_PAYMENT order sees Cancel Unpaid Order, never Issue Refund
    renderWithAppProviders(<AdminOrderDetailView orderNumber="ORD-UNPAID-1" />);
    expect(await screen.findByRole("heading", { name: "Order ORD-UNPAID-1" })).toBeInTheDocument();

    const cancelUnpaidBtn = screen.getByRole("button", { name: "Cancel Unpaid Order" });
    expect(cancelUnpaidBtn).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Start Processing" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Mark Shipped" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Issue Refund/i })).not.toBeInTheDocument();

    fireEvent.click(cancelUnpaidBtn);
    const cancelDialog = await screen.findByRole("dialog");
    fireEvent.click(within(cancelDialog).getByRole("button", { name: "Cancel Unpaid Order" }));
    expect(await screen.findByText("Order cancelled")).toBeInTheDocument();

    cleanup();

    // 2. Staff on CONFIRMED / PAID order can Start Processing / Mark Shipped, NEVER sees Issue Refund or Cancel Unpaid enabled
    renderWithAppProviders(<AdminOrderDetailView orderNumber="ORD-PAID-1" />);
    expect(await screen.findByRole("heading", { name: "Order ORD-PAID-1" })).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Start Processing" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Mark Shipped" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel Unpaid Order" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Issue Refund/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Start Processing" }));
    const processDialog = await screen.findByRole("dialog");
    fireEvent.click(within(processDialog).getByRole("button", { name: "Start Processing" }));
    expect(await screen.findByText("Order fulfillment updated")).toBeInTheDocument();

    // Now PROCESSING -> Mark Shipped with carrier & tracking_number
    fireEvent.click(screen.getByRole("button", { name: "Mark Shipped" }));
    const shipDialog = await screen.findByRole("dialog");
    fireEvent.change(within(shipDialog).getByLabelText("Carrier"), {
      target: { value: "UPS" },
    });
    fireEvent.change(within(shipDialog).getByLabelText("Tracking Number"), {
      target: { value: "1Z999AA10123456784" },
    });
    fireEvent.change(within(shipDialog).getByLabelText("Fulfillment Note (optional)"), {
      target: { value: "Handed to driver" },
    });
    fireEvent.click(within(shipDialog).getByRole("button", { name: "Mark Shipped" }));

    await waitFor(() => {
      const shipCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/admin/orders/ORD-PAID-1/fulfill") &&
          init?.method === "POST" &&
          String(init.body).includes('"SHIP"')
      );
      expect(shipCall).toBeDefined();
      expect((shipCall?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
        "csrf-staff-order"
      );
      expect(JSON.parse(String(shipCall?.[1]?.body))).toEqual({
        action: "SHIP",
        carrier: "UPS",
        tracking_number: "1Z999AA10123456784",
        note: "Handed to driver",
      });
    });
  });

  it("allows admin user on PAID order to open Issue Refund dialog, toggle restock, enter reason, and submit with X-CSRF-Token", async () => {
    const paidOrder = makeAdminOrderDetail({
      order_number: "ORD-REFUND-1",
      status: "CONFIRMED",
      payment_status: "PAID",
      fulfillment_status: "ALLOCATED",
    });

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("admin"), csrf_token: "csrf-admin-refund" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/admin/orders/ORD-REFUND-1") && method === "GET") {
        return jsonResponse(paidOrder);
      }
      if (url.endsWith("/admin/orders/ORD-REFUND-1/refund") && method === "POST") {
        return jsonResponse({
          ...paidOrder,
          status: "REFUNDED",
          payment_status: "REFUNDED",
          fulfillment_status: "CANCELLED",
        });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderWithAppProviders(<AdminOrderDetailView orderNumber="ORD-REFUND-1" />);
    expect(await screen.findByRole("heading", { name: "Order ORD-REFUND-1" })).toBeInTheDocument();

    const refundBtn = screen.getByRole("button", { name: "Issue Refund" });
    fireEvent.click(refundBtn);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Issue Full Refund — ORD-REFUND-1")).toBeInTheDocument();
    expect(within(dialog).getByText("Full Refund Amount: $107.20")).toBeInTheDocument();

    const restockCheckbox = within(dialog).getByRole("checkbox");
    expect(restockCheckbox).toHaveAttribute("aria-checked", "true");
    fireEvent.click(restockCheckbox);
    expect(restockCheckbox).toHaveAttribute("aria-checked", "false");

    fireEvent.change(within(dialog).getByLabelText("Refund Reason (optional)"), {
      target: { value: "Damaged package return" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm Full Refund" }));

    expect(await screen.findByText("Refund processed")).toBeInTheDocument();

    const refundCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/admin/orders/ORD-REFUND-1/refund") && init?.method === "POST"
    );
    expect(refundCall).toBeDefined();
    expect((refundCall?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "csrf-admin-refund"
    );
    expect(JSON.parse(String(refundCall?.[1]?.body))).toEqual({
      reason: "Damaged package return",
      restock: false,
    });
  });
});

describe("AdminUsersView & AdminAuditLogsView (Admin-Only Guard, Session Revocation, Last Admin & Redacted Diffs)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/admin/users";
    mockSearchParams = new URLSearchParams();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    installDomPolyfills();
  });

  afterEach(() => {
    cleanup();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("blocks staff users with 403 Restricted to Administrators and issues ZERO /admin/users or /admin/audit-logs fetches", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-staff" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    // 1. AdminUsersView as staff
    renderWithAppProviders(<AdminUsersView />);
    const usersAlert = await screen.findByRole("alert");
    expect(usersAlert).toHaveTextContent("403 Restricted to Administrators");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/admin/users"))
    ).toBe(false);

    cleanup();
    fetchMock.mockClear();

    // 2. AdminAuditLogsView as staff
    mockPathname = "/admin/audit-logs";
    renderWithAppProviders(<AdminAuditLogsView />);
    const auditAlert = await screen.findByRole("alert");
    expect(auditAlert).toHaveTextContent("403 Restricted to Administrators");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/admin/audit-logs"))
    ).toBe(false);
  });

  it("disables self role/status controls, opens mandatory session-revocation confirmation Dialog, displays revoked_sessions_count toast, and surfaces 409 CANNOT_REMOVE_LAST_ADMIN", async () => {
    const selfAdmin: AdminUserDTO = {
      id: "user-admin",
      email: "admin@atelier.test",
      full_name: "Primary Admin",
      role: "admin",
      is_active: true,
      created_at: "2026-11-01T10:00:00Z",
      updated_at: "2026-11-01T10:00:00Z",
    };
    const otherAdmin: AdminUserDTO = {
      id: "user-admin-2",
      email: "backup-admin@atelier.test",
      full_name: "Backup Admin",
      role: "admin",
      is_active: true,
      created_at: "2026-11-02T10:00:00Z",
      updated_at: "2026-11-02T10:00:00Z",
    };
    let staffUser: AdminUserDTO = {
      id: "user-staff-2",
      email: "ops@atelier.test",
      full_name: "Ops Staff",
      role: "staff",
      is_active: true,
      created_at: "2026-11-03T10:00:00Z",
      updated_at: "2026-11-03T10:00:00Z",
    };

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("admin"), csrf_token: "csrf-admin-users" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.includes("/admin/users") && method === "GET") {
        return jsonResponse({
          items: [selfAdmin, otherAdmin, staffUser],
          total: 3,
          page: 1,
          page_size: 20,
          has_next: false,
        });
      }
      if (url.endsWith("/admin/users/user-staff-2/status") && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as { is_active: boolean };
        staffUser = { ...staffUser, is_active: body.is_active };
        return jsonResponse({
          user: staffUser,
          revoked_sessions_count: 3,
        });
      }
      if (url.endsWith("/admin/users/user-admin-2/role") && method === "PATCH") {
        return errorResponse(
          409,
          "CANNOT_REMOVE_LAST_ADMIN",
          "Cannot demote the last active administrator."
        );
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderWithAppProviders(<AdminUsersView />);

    expect(await screen.findByText("Primary Admin")).toBeInTheDocument();

    // Self row has disabled role & active-status controls
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Role for admin@atelier.test" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Active status for admin@atelier.test" })).toBeDisabled();

    // Toggle staff user status -> mandatory session-revocation Dialog opens before PATCH
    const staffSwitch = screen.getByRole("switch", { name: "Active status for ops@atelier.test" });
    fireEvent.click(staffSwitch);

    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/status"))
    ).toHaveLength(0);

    const statusDialog = await screen.findByRole("dialog");
    expect(within(statusDialog).getByText("Confirm Account Status Change")).toBeInTheDocument();
    expect(
      within(statusDialog).getByText(
        /immediately revokes all their active sessions across devices/i
      )
    ).toBeInTheDocument();

    fireEvent.click(within(statusDialog).getByRole("button", { name: "Confirm & Revoke Sessions" }));

    expect(await screen.findByText("Session revocation complete")).toBeInTheDocument();
    expect(
      screen.getByText("ops@atelier.test updated (3 active session(s) revoked).")
    ).toBeInTheDocument();

    // Verify CSRF header on PATCH /admin/users/{id}/status
    const statusPatchCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/admin/users/user-staff-2/status") && init?.method === "PATCH"
    );
    expect((statusPatchCall?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "csrf-admin-users"
    );

    // Role change returning 409 CANNOT_REMOVE_LAST_ADMIN
    const backupRoleSelect = screen.getByRole("combobox", {
      name: "Role for backup-admin@atelier.test",
    });
    await selectRadixOption(backupRoleSelect, "staff");

    const roleDialog = await screen.findByRole("dialog");
    expect(within(roleDialog).getByText("Confirm User Role Change")).toBeInTheDocument();
    fireEvent.click(within(roleDialog).getByRole("button", { name: "Confirm & Revoke Sessions" }));

    expect(
      await within(roleDialog).findByText(
        "Cannot remove or deactivate the last active administrator."
      )
    ).toBeInTheDocument();
  });

  it("renders audit log rows and opens Before/After JSON Diff Sheet handling null and preserving [REDACTED]", async () => {
    mockPathname = "/admin/audit-logs";

    const auditLogs: AuditLogEntryDTO[] = [
      {
        id: "aud-1",
        actor_user_id: "user-admin",
        actor_email: "admin@atelier.test",
        actor_full_name: "Primary Admin",
        action: "USER_CREATED",
        resource_type: "user",
        resource_id: "user-staff-2",
        before_state: null,
        after_state: {
          email: "ops@atelier.test",
          role: "staff",
          password_hash: "[REDACTED]",
        },
        ip_address: "203.0.113.10",
        created_at: "2026-11-19T13:00:00Z",
      },
      {
        id: "aud-2",
        actor_user_id: "user-admin",
        actor_email: "admin@atelier.test",
        actor_full_name: "Primary Admin",
        action: "ORDER_REFUNDED",
        resource_type: "order",
        resource_id: "ORD-20261119-8F3K2A",
        before_state: {
          status: "CONFIRMED",
          payment_status: "PAID",
          provider_secret: "[REDACTED]",
        },
        after_state: {
          status: "REFUNDED",
          payment_status: "REFUNDED",
          provider_secret: "[REDACTED]",
        },
        ip_address: "203.0.113.10",
        created_at: "2026-11-19T14:00:00Z",
      },
    ];

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("admin"), csrf_token: "csrf-admin-audit" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      if (url.includes("/admin/audit-logs")) {
        return jsonResponse({
          items: auditLogs,
          total: auditLogs.length,
          page: 1,
          page_size: 20,
          has_next: false,
        });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(<AdminAuditLogsView />);

    expect(await screen.findByText("USER_CREATED")).toBeInTheDocument();
    expect(screen.getByText("ORDER_REFUNDED")).toBeInTheDocument();

    // Open Sheet for entry with null before_state & [REDACTED] after_state
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect audit event USER_CREATED on user-staff-2" })
    );
    const diffSheet1 = await screen.findByRole("dialog");
    expect(within(diffSheet1).getByText("Before State")).toBeInTheDocument();
    expect(within(diffSheet1).getByText("After State")).toBeInTheDocument();
    expect(within(diffSheet1).getByText("—")).toBeInTheDocument();
    expect(within(diffSheet1).getByText('"[REDACTED]"')).toBeInTheDocument();

    // Close and inspect ORDER_REFUNDED entry with both before and after JSON diff
    fireEvent.click(within(diffSheet1).getByRole("button", { name: "Close panel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Inspect audit event ORDER_REFUNDED on ORD-20261119-8F3K2A",
      })
    );
    const diffSheet2 = await screen.findByRole("dialog");
    expect(within(diffSheet2).getByText('"CONFIRMED"')).toBeInTheDocument();
    expect(within(diffSheet2).getAllByText('"REFUNDED"')).toHaveLength(2);
    expect(within(diffSheet2).getAllByText('"[REDACTED]"')).toHaveLength(2);
  });
});
