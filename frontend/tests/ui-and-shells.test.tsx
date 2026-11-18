import "@testing-library/jest-dom/vitest";
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, cartApi, inventoryApi } from "@/lib/api/client";
import type {
  CartDTO,
  ProductDetailDTO,
  ProductSummaryDTO,
  UserDTO,
  UserRole,
  VariantAvailabilityDTO,
} from "@/lib/api/types";
import {
  AdminDashboardView,
  AdminProductsStarterView,
} from "@/components/admin/admin-dashboard-view";
import { AdminRoleGuard } from "@/components/admin/admin-primitives";
import { AdminShell } from "@/components/admin/admin-shell";
import ProductDetailPage from "@/app/(storefront)/products/[slug]/page";
import { ThemeProvider } from "@/components/shared/theme-provider";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "@/components/shared/state-feedback";
import { LoginView } from "@/components/storefront/auth-views";
import { StorefrontHeader } from "@/components/storefront/header";
import { ProductCard } from "@/components/storefront/product-card";
import { ProductDetailView } from "@/components/storefront/product-detail-view";
import { ToastProvider } from "@/components/ui/toast";
import { AuthProvider, useAuth } from "@/providers/auth-provider";
import { CartProvider, useCart } from "@/providers/cart-provider";

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

let mockPathname = "/admin/products";
let mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => mockRouter,
  useSearchParams: () => mockSearchParams,
  notFound: () => mockNotFound(),
}));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse(
    {
      error: {
        code,
        message,
        details: {},
        request_id: "req-ui-test",
      },
    },
    status
  );
}

function makeUser(role: UserRole): UserDTO {
  return {
    id: `user-${role}`,
    email: `${role}@atelier.test`,
    full_name: `Test ${role.toUpperCase()}`,
    role,
    is_active: true,
    created_at: "2026-11-18T10:00:00Z",
  };
}

function makeEmptyCart(items: CartDTO["items"] = []): CartDTO {
  const itemCount = items.reduce((sum, item) => sum + item.quantity, 0);
  const subtotal = items.reduce((sum, item) => sum + item.line_total_cents, 0);
  return {
    id: "cart-test",
    currency: "USD",
    items,
    item_count: itemCount,
    subtotal_cents: subtotal,
    estimated_shipping_cents: subtotal > 0 ? 1000 : 0,
    estimated_total_cents: subtotal > 0 ? subtotal + 1000 : 0,
  };
}

function makeProductSummary(overrides: Partial<ProductSummaryDTO> = {}): ProductSummaryDTO {
  return {
    id: "prod-1",
    title: "Merino Crewneck",
    slug: "merino-crewneck",
    category: { id: "cat-1", name: "Knitwear", slug: "knitwear" },
    primary_image: {
      id: "img-1",
      url: "https://cdn.example.com/merino.webp",
      alt_text: "Merino Crewneck front view",
      sort_order: 0,
      is_primary: true,
    },
    min_price_cents: 4500,
    max_price_cents: 4500,
    currency: "USD",
    in_stock: true,
    ...overrides,
  };
}

function makeProductDetail(): ProductDetailDTO {
  return {
    id: "prod-1",
    title: "Merino Crewneck",
    slug: "merino-crewneck",
    description: "Fine-gauge Italian merino knit.",
    status: "ACTIVE",
    category: { id: "cat-1", name: "Knitwear", slug: "knitwear" },
    images: [
      {
        id: "img-1",
        url: "/assets/merino-front.png",
        alt_text: "Merino Crewneck front",
        sort_order: 0,
        is_primary: true,
      },
    ],
    variants: [
      {
        id: "var-charcoal-m",
        sku: "TEE-CHR-M",
        title: "Charcoal / M",
        attributes: { color: "Charcoal", size: "M" },
        price_cents: 4500,
        compare_at_price_cents: 6000,
        currency: "USD",
        is_active: true,
        available_quantity: 12,
        in_stock: true,
        low_stock: false,
      },
      {
        id: "var-charcoal-l",
        sku: "TEE-CHR-L",
        title: "Charcoal / L",
        attributes: { color: "Charcoal", size: "L" },
        price_cents: 4900,
        compare_at_price_cents: 6000,
        currency: "USD",
        is_active: true,
        available_quantity: 2,
        in_stock: true,
        low_stock: true,
      },
      {
        id: "var-ivory-s",
        sku: "TEE-IVR-S",
        title: "Ivory / S",
        attributes: { color: "Ivory", size: "S" },
        price_cents: 4500,
        compare_at_price_cents: null,
        currency: "USD",
        is_active: true,
        available_quantity: 0,
        in_stock: false,
        low_stock: false,
      },
    ],
  };
}

function renderWithAppProviders(ui: React.ReactNode) {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <CartProvider>{ui}</CartProvider>
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}

describe("Shared feedback primitives & StatusBadge", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders LoadingState, EmptyState with action callback, and ErrorState with alert role and retry callback", () => {
    const onAction = vi.fn();
    const onRetry = vi.fn();

    render(
      <div>
        <LoadingState label="Fetching catalog" rows={2} />
        <EmptyState
          title="No items found"
          description="Try resetting your filters."
          action={
            <button type="button" onClick={onAction}>
              Reset Filters
            </button>
          }
        />
        <ErrorState
          title="Catalog unavailable"
          message="Request timed out."
          onRetry={onRetry}
          retryLabel="Reload Catalog"
        />
      </div>
    );

    const status = screen.getByRole("status", { name: "Fetching catalog" });
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.getByText("Fetching catalog")).toBeInTheDocument();

    expect(screen.getByText("No items found")).toBeInTheDocument();
    expect(screen.getByText("Try resetting your filters.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset Filters" }));
    expect(onAction).toHaveBeenCalledTimes(1);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Catalog unavailable");
    expect(alert).toHaveTextContent("Request timed out.");
    fireEvent.click(within(alert).getByRole("button", { name: "Reload Catalog" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders StatusBadge labels across product, stock, role, order, payment, fulfillment, and active domains", () => {
    render(
      <div>
        <StatusBadge domain="product" value="ACTIVE" />
        <StatusBadge domain="product" value="DRAFT" />
        <StatusBadge domain="product" value="ARCHIVED" />
        <StatusBadge domain="stock" value="in_stock" />
        <StatusBadge domain="stock" value="low_stock" availableQuantity={3} />
        <StatusBadge domain="stock" value="out_of_stock" />
        <StatusBadge domain="role" value="customer" />
        <StatusBadge domain="role" value="staff" />
        <StatusBadge domain="role" value="admin" />
        <StatusBadge domain="order" value="PENDING_PAYMENT" />
        <StatusBadge domain="payment" value="PAID" />
        <StatusBadge domain="fulfillment" value="SHIPPED" />
        <StatusBadge domain="active" value="true" />
        <StatusBadge domain="active" value="false" />
      </div>
    );

    expect(screen.getAllByText("Active")).toHaveLength(2);
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(screen.getByText("In stock")).toBeInTheDocument();
    expect(screen.getByText("Low stock — only 3 left")).toBeInTheDocument();
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
    expect(screen.getByText("Customer")).toBeInTheDocument();
    expect(screen.getByText("Staff")).toBeInTheDocument();
    expect(screen.getByText("Admin")).toBeInTheDocument();
    expect(screen.getByText("Pending Payment")).toBeInTheDocument();
    expect(screen.getByText("Paid")).toBeInTheDocument();
    expect(screen.getByText("Shipped")).toBeInTheDocument();
    expect(screen.getByText("Inactive")).toBeInTheDocument();
  });
});

describe("ProductCard", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders exact price, category label, and safe https image when in stock", () => {
    const product = makeProductSummary();
    render(<ProductCard product={product} />);

    expect(screen.getByText("Knitwear")).toBeInTheDocument();
    expect(screen.getByText("Merino Crewneck")).toBeInTheDocument();
    expect(screen.getByText("$45.00")).toBeInTheDocument();
    expect(screen.queryByText("Out of Stock")).not.toBeInTheDocument();

    const img = screen.getByRole("img", { name: "Merino Crewneck front view" });
    expect(img).toHaveAttribute("src", "https://cdn.example.com/merino.webp");
  });

  it("renders From price for range, safe /assets image, Out of Stock badge when unavailable, and placeholder for null or unsafe image URLs", () => {
    const rangedOutOfStock = makeProductSummary({
      id: "prod-2",
      title: "Cashmere Scarf",
      slug: "cashmere-scarf",
      primary_image: null,
      min_price_cents: 4500,
      max_price_cents: 6500,
      in_stock: false,
    });

    const safeAssetProduct = makeProductSummary({
      id: "prod-2b",
      title: "Wool Trouser",
      slug: "wool-trouser",
      primary_image: {
        id: "img-asset",
        url: "/assets/trouser.png",
        alt_text: "Wool Trouser front",
        sort_order: 0,
        is_primary: true,
      },
      in_stock: true,
    });

    const unsafeMediaProduct = makeProductSummary({
      id: "prod-3",
      title: "Silk Shirt",
      slug: "silk-shirt",
      primary_image: {
        id: "img-unsafe",
        url: "javascript:alert(1)",
        alt_text: "Unsafe image",
        sort_order: 0,
        is_primary: true,
      },
      in_stock: true,
    });

    render(
      <div>
        <ProductCard product={rangedOutOfStock} />
        <ProductCard product={safeAssetProduct} />
        <ProductCard product={unsafeMediaProduct} />
      </div>
    );

    expect(screen.getByText("From $45.00")).toBeInTheDocument();
    expect(screen.getByText("Out of Stock")).toBeInTheDocument();
    const imgs = screen.getAllByRole("img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].getAttribute("src")).toMatch(/\/assets\/trouser\.png$/);
    expect(screen.getAllByText("Cashmere Scarf").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Silk Shirt").length).toBeGreaterThanOrEqual(2);
  });
});

describe("ProductDetailView", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/products/merino-crewneck";
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("switches attribute radio options, updates SKU/price/savings/stock badges, disables out-of-stock CTA, and recovers on 409 INSUFFICIENT_STOCK", async () => {
    const detail = makeProductDetail();
    const updatedCart: CartDTO = makeEmptyCart([
      {
        id: "item-1",
        variant_id: "var-charcoal-l",
        product_id: detail.id,
        product_slug: detail.slug,
        product_title: detail.title,
        variant_title: "Charcoal / L",
        sku: "TEE-CHR-L",
        image_url: "/assets/merino-front.png",
        unit_price_cents: 4900,
        quantity: 1,
        line_total_cents: 4900,
        available_quantity: 2,
        in_stock: true,
        low_stock: true,
        is_available: true,
      },
    ]);

    const addItemSpy = vi.spyOn(cartApi, "addItem");
    const availabilitySpy = vi.spyOn(inventoryApi, "getVariantAvailability");

    let addCallCount = 0;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_SESSION_EXPIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/products/merino-crewneck") && method === "GET") {
        return jsonResponse(detail);
      }
      if (url.endsWith("/cart/items") && method === "POST") {
        addCallCount += 1;
        if (addCallCount === 1) {
          return errorResponse(409, "INSUFFICIENT_STOCK", "Only 1 available.");
        }
        return jsonResponse(updatedCart);
      }
      if (url.endsWith("/variants/var-charcoal-l/availability") && method === "GET") {
        const fresh: VariantAvailabilityDTO = {
          variant_id: "var-charcoal-l",
          sku: "TEE-CHR-L",
          is_active: true,
          available_quantity: 1,
          in_stock: true,
          low_stock: true,
        };
        return jsonResponse(fresh);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderWithAppProviders(<ProductDetailView slug="merino-crewneck" />);

    expect(await screen.findByRole("heading", { name: "Merino Crewneck" })).toBeInTheDocument();
    expect(screen.getByText("SKU: TEE-CHR-M")).toBeInTheDocument();
    expect(screen.getByText("$45.00")).toBeInTheDocument();
    expect(screen.getByText("$60.00")).toBeInTheDocument();
    expect(screen.getByText("Save 25%")).toBeInTheDocument();
    expect(screen.getByText("In stock")).toBeInTheDocument();

    const colorGroup = screen.getByRole("radiogroup", { name: "Color" });
    const sizeGroup = screen.getByRole("radiogroup", { name: "Size" });
    expect(colorGroup).toBeInTheDocument();
    expect(sizeGroup).toBeInTheDocument();

    // Switch to Size L -> updates SKU, price, savings, and Low stock badge
    fireEvent.click(within(sizeGroup).getByRole("radio", { name: /^L/ }));
    expect(screen.getByText("SKU: TEE-CHR-L")).toBeInTheDocument();
    expect(screen.getByText("$49.00")).toBeInTheDocument();
    expect(screen.getByText("Save 18%")).toBeInTheDocument();
    expect(screen.getByText("Low stock — only 2 left")).toBeInTheDocument();

    // Switch to Ivory -> maps to out-of-stock Ivory / S variant and disables CTA
    fireEvent.click(within(colorGroup).getByRole("radio", { name: /^Ivory/ }));
    expect(screen.getByText("SKU: TEE-IVR-S")).toBeInTheDocument();
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
    const disabledCta = screen.getByRole("button", { name: "Out of Stock" });
    expect(disabledCta).toBeDisabled();

    // Switch back to Charcoal -> L (available_quantity 2) and test 409 INSUFFICIENT_STOCK recovery
    fireEvent.click(within(colorGroup).getByRole("radio", { name: /^Charcoal/ }));
    fireEvent.click(within(sizeGroup).getByRole("radio", { name: /^L/ }));
    fireEvent.click(screen.getByRole("button", { name: "Increase quantity" }));
    expect(screen.getByLabelText("Selected quantity")).toHaveTextContent("2");

    fireEvent.click(screen.getByRole("button", { name: "Add to Cart" }));

    await waitFor(() => {
      expect(addItemSpy).toHaveBeenCalledWith({
        variant_id: "var-charcoal-l",
        quantity: 2,
      });
    });

    await waitFor(() => {
      expect(availabilitySpy).toHaveBeenCalledWith("var-charcoal-l");
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/variants\/var-charcoal-l\/availability$/),
      expect.objectContaining({ method: "GET", credentials: "include" })
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Only 1 unit(s) currently available.");
    expect(screen.getByText("Low stock — only 1 left")).toBeInTheDocument();
    expect(screen.getByLabelText("Selected quantity")).toHaveTextContent("1");

    // Retry Add to Cart with clamped quantity -> succeeds
    fireEvent.click(screen.getByRole("button", { name: "Add to Cart" }));
    await waitFor(() => {
      expect(addItemSpy).toHaveBeenLastCalledWith({
        variant_id: "var-charcoal-l",
        quantity: 1,
      });
    });
    expect(await screen.findByText("Added to bag")).toBeInTheDocument();

    // Ensure ApiError class invariant wasn't disrupted
    expect(new ApiError({ status: 409, code: "INSUFFICIENT_STOCK", message: "x" })).toBeInstanceOf(
      ApiError
    );
  });
});

describe("AdminShell & AdminRoleGuard RBAC", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/admin/products";
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("redirects unauthenticated users to /login?returnTo=... without leaking admin navigation", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_SESSION_EXPIRED", "Authentication required.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(
      <AdminShell>
        <div>Protected Admin Body</div>
      </AdminShell>
    );

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/login?returnTo=%2Fadmin%2Fproducts");
    });

    expect(screen.queryByRole("navigation", { name: "Admin navigation" })).not.toBeInTheDocument();
    expect(screen.queryByText("Operations")).not.toBeInTheDocument();
    expect(screen.queryByText("Protected Admin Body")).not.toBeInTheDocument();
  });

  it("renders 403 Access Denied alert and hides all admin navigation for customer role", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-cust" });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(
      <AdminShell>
        <div>Protected Admin Body</div>
      </AdminShell>
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("403 Access Denied");
    expect(screen.queryByRole("navigation", { name: "Admin navigation" })).not.toBeInTheDocument();
    expect(screen.queryByText("Operations")).not.toBeInTheDocument();
    expect(screen.queryByText("Administration")).not.toBeInTheDocument();
    expect(screen.queryByText("Protected Admin Body")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/health"))
    ).toBe(false);
  });

  it("renders Operations links only, hides Administration section, and blocks AdminRoleGuard for staff", async () => {
    mockPathname = "/admin/users";
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-staff" });
      }
      if (url.endsWith("/health")) {
        return jsonResponse({ status: "ok", database: "ok", sandbox_payments: true });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(
      <AdminShell>
        <AdminRoleGuard requiredRole="admin">
          <div>Administrator Secret Panel</div>
        </AdminRoleGuard>
      </AdminShell>
    );

    const nav = await screen.findByRole("navigation", { name: "Admin navigation" });
    expect(within(nav).getByText("Operations")).toBeInTheDocument();
    for (const label of ["Dashboard", "Products", "Categories", "Inventory", "Orders"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }

    expect(within(nav).queryByText("Administration")).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Users & Roles" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Audit Logs" })).not.toBeInTheDocument();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Restricted to Administrators");
    expect(screen.queryByText("Administrator Secret Panel")).not.toBeInTheDocument();
  });

  it("renders both Operations and Administration links and passes AdminRoleGuard for admin", async () => {
    mockPathname = "/admin/users";
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("admin"), csrf_token: "csrf-admin" });
      }
      if (url.endsWith("/health")) {
        return jsonResponse({ status: "ok", database: "ok", sandbox_payments: true });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(
      <AdminShell>
        <AdminRoleGuard requiredRole="admin">
          <div>Administrator Secret Panel</div>
        </AdminRoleGuard>
      </AdminShell>
    );

    const nav = await screen.findByRole("navigation", { name: "Admin navigation" });
    expect(within(nav).getByText("Operations")).toBeInTheDocument();
    expect(within(nav).getByText("Administration")).toBeInTheDocument();
    for (const label of [
      "Dashboard",
      "Products",
      "Categories",
      "Inventory",
      "Orders",
      "Users & Roles",
      "Audit Logs",
    ]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }

    expect(await screen.findByText("Administrator Secret Panel")).toBeInTheDocument();
    expect(screen.queryByText(/Restricted to Administrators/)).not.toBeInTheDocument();
  });

  it("issues zero /api/v1/admin/* or /health fetch calls when wrapping real admin views for unauthenticated or customer users", async () => {
    // 1. Unauthenticated (user === null)
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_SESSION_EXPIRED", "Authentication required.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(
      <AdminShell>
        <AdminDashboardView />
        <AdminProductsStarterView />
      </AdminShell>
    );

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/login?returnTo=%2Fadmin%2Fproducts");
    });

    const unauthAdminOrHealthCalls = fetchMock.mock.calls.filter(([url]) => {
      const s = String(url);
      return s.includes("/admin/") || s.endsWith("/health");
    });
    expect(unauthAdminOrHealthCalls).toHaveLength(0);

    cleanup();
    fetchMock.mockReset();

    // 2. Customer role (user.role === "customer")
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-cust" });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(
      <AdminShell>
        <AdminDashboardView />
        <AdminProductsStarterView />
      </AdminShell>
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("403 Access Denied");
    const customerAdminOrHealthCalls = fetchMock.mock.calls.filter(([url]) => {
      const s = String(url);
      return s.includes("/admin/") || s.endsWith("/health");
    });
    expect(customerAdminOrHealthCalls).toHaveLength(0);
  });
});

function AuthCartProbe() {
  const { user, isLoading, sessionExpired, accountDisabled } = useAuth();
  const { cart, isLoading: cartLoading, addItem } = useCart();
  return (
    <div>
      <span data-testid="auth-user">{user ? user.email : "null"}</span>
      <span data-testid="auth-loading">{String(isLoading)}</span>
      <span data-testid="auth-expired">{String(sessionExpired)}</span>
      <span data-testid="auth-disabled">{String(accountDisabled)}</span>
      <span data-testid="cart-state">{cart ? cart.id : "null"}</span>
      <span data-testid="cart-loading">{String(cartLoading)}</span>
      <button
        type="button"
        onClick={() => {
          void addItem("var-1", 1).catch(() => {});
        }}
      >
        Trigger Add
      </button>
    </div>
  );
}

describe("ProductDetailPage route validation, Auth/Cart fail-closed states & LoginView redirect safety", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/login";
    mockSearchParams = new URLSearchParams();
    mockNotFound.mockClear();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("triggers notFound() on malformed %ZZ and traversal slugs without crashing 500", async () => {
    for (const badSlug of ["%ZZ", "..", "%2e%2e", "../admin"]) {
      await expect(
        ProductDetailPage({ params: Promise.resolve({ slug: badSlug }) })
      ).rejects.toThrow("NEXT_NOT_FOUND");
    }
    expect(mockNotFound).toHaveBeenCalledTimes(4);

    const validResult = await ProductDetailPage({
      params: Promise.resolve({ slug: "merino-crewneck" }),
    });
    expect(React.isValidElement(validResult)).toBe(true);
    expect(mockNotFound).toHaveBeenCalledTimes(4);
  });

  it("fails closed in AuthProvider and CartProvider on 401 AUTH_SESSION_EXPIRED and 403 AUTH_ACCOUNT_DISABLED without extra guest GET /cart calls", async () => {
    // 1. Initial hydration -> 401 AUTH_SESSION_EXPIRED (non-anonymous expired session)
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_SESSION_EXPIRED", "Session has expired.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(<AuthCartProbe />);

    await waitFor(() => {
      expect(screen.getByTestId("auth-loading")).toHaveTextContent("false");
      expect(screen.getByTestId("cart-loading")).toHaveTextContent("false");
    });

    expect(screen.getByTestId("auth-user")).toHaveTextContent("null");
    expect(screen.getByTestId("auth-expired")).toHaveTextContent("true");
    expect(screen.getByTestId("auth-disabled")).toHaveTextContent("false");
    expect(screen.getByTestId("cart-state")).toHaveTextContent("null");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/cart"))
    ).toBe(false);

    cleanup();
    fetchMock.mockReset();

    // 2. Initial hydration -> 403 AUTH_ACCOUNT_DISABLED
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(403, "AUTH_ACCOUNT_DISABLED", "Account disabled.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(<AuthCartProbe />);

    await waitFor(() => {
      expect(screen.getByTestId("auth-loading")).toHaveTextContent("false");
      expect(screen.getByTestId("cart-loading")).toHaveTextContent("false");
    });

    expect(screen.getByTestId("auth-user")).toHaveTextContent("null");
    expect(screen.getByTestId("auth-expired")).toHaveTextContent("false");
    expect(screen.getByTestId("auth-disabled")).toHaveTextContent("true");
    expect(screen.getByTestId("cart-state")).toHaveTextContent("null");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/cart"))
    ).toBe(false);

    cleanup();
    fetchMock.mockReset();

    // 3. Active session transitions to disabled mid-session -> clears cart with zero guest GET /cart
    let cartGetCount = 0;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-active" });
      }
      if (url.endsWith("/cart") && method === "GET") {
        cartGetCount += 1;
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/cart/items") && method === "POST") {
        return errorResponse(403, "AUTH_ACCOUNT_DISABLED", "Account disabled.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderWithAppProviders(<AuthCartProbe />);

    await waitFor(() => {
      expect(screen.getByTestId("auth-user")).toHaveTextContent("customer@atelier.test");
      expect(screen.getByTestId("cart-state")).toHaveTextContent("cart-test");
    });
    expect(cartGetCount).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Trigger Add" }));

    await waitFor(() => {
      expect(screen.getByTestId("auth-user")).toHaveTextContent("null");
      expect(screen.getByTestId("auth-disabled")).toHaveTextContent("true");
      expect(screen.getByTestId("cart-state")).toHaveTextContent("null");
    });
    expect(cartGetCount).toBe(1);
  });

  it("sanitizes ?returnTo=//evil.com in LoginView to /account upon login", async () => {
    mockSearchParams = new URLSearchParams("returnTo=//evil.com");
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_SESSION_EXPIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(makeEmptyCart());
      }
      if (url.endsWith("/auth/login") && method === "POST") {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-login" });
      }
      if (url.endsWith("/cart/merge") && method === "POST") {
        return jsonResponse(makeEmptyCart());
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderWithAppProviders(<LoginView />);

    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute(
      "href",
      "/register?returnTo=%2Faccount"
    );

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "customer@atelier.test" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "Password123!" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/account");
    });
    expect(mockRouter.replace).not.toHaveBeenCalledWith("//evil.com");
  });
});

describe("StorefrontHeader", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/";
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows cart count, navigates search queries, and shows Admin Console link only for staff/admin", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("staff"), csrf_token: "csrf-staff" });
      }
      if (url.endsWith("/categories")) {
        return jsonResponse({
          items: [
            {
              id: "cat-1",
              parent_id: null,
              name: "Knitwear",
              slug: "knitwear",
              description: null,
              sort_order: 0,
            },
          ],
        });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(
          makeEmptyCart([
            {
              id: "item-1",
              variant_id: "var-1",
              product_id: "prod-1",
              product_slug: "merino-crewneck",
              product_title: "Merino Crewneck",
              variant_title: "Charcoal / M",
              sku: "TEE-CHR-M",
              image_url: null,
              unit_price_cents: 4500,
              quantity: 3,
              line_total_cents: 13500,
              available_quantity: 10,
              in_stock: true,
              low_stock: false,
              is_available: true,
            },
          ])
        );
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(<StorefrontHeader />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Open cart, 3 items" })).toBeInTheDocument();
    });

    const searchInput = screen.getByRole("searchbox", { name: "Search products" });
    fireEvent.change(searchInput, { target: { value: "merino coat" } });
    fireEvent.submit(searchInput.closest("form") as HTMLFormElement);
    expect(mockRouter.push).toHaveBeenCalledWith("/products?q=merino%20coat");

    fireEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    expect(await screen.findByRole("link", { name: "Admin Console" })).toHaveAttribute(
      "href",
      "/admin"
    );
  });

  it("hides Admin Console link for customer role", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-cust" });
      }
      if (url.endsWith("/categories")) {
        return jsonResponse({ items: [] });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(<StorefrontHeader />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Open cart, 0 items" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    expect(await screen.findByRole("link", { name: "My Account" })).toHaveAttribute(
      "href",
      "/account"
    );
    expect(screen.queryByRole("link", { name: "Admin Console" })).not.toBeInTheDocument();
  });

  it("hides Admin Console link for anonymous users (user === null)", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_SESSION_EXPIRED", "Authentication required.");
      }
      if (url.endsWith("/categories")) {
        return jsonResponse({ items: [] });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeEmptyCart());
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderWithAppProviders(<StorefrontHeader />);

    expect(await screen.findByRole("link", { name: "Sign In" })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: "Create Account" })).toHaveAttribute(
      "href",
      "/register"
    );
    expect(screen.queryByRole("link", { name: "Admin Console" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    await screen.findByRole("navigation", { name: "Mobile navigation" });
    expect(screen.queryByRole("link", { name: "Admin Console" })).not.toBeInTheDocument();
  });
});
