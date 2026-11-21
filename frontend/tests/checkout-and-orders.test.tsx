import "@testing-library/jest-dom/vitest";
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  registerAuthInvalidationListener,
  setMemoryCsrfToken,
} from "@/lib/api/client";
import type {
  AddressDTO,
  CartDTO,
  CartItemDTO,
  CheckoutOrderResponseDTO,
  OrderDetailDTO,
  OrderListItemDTO,
  PaymentIntentDTO,
  UserDTO,
  UserRole,
} from "@/lib/api/types";
import {
  clearGuestOrderToken,
  getGuestOrderToken,
  saveGuestOrderToken,
} from "@/lib/guest-order-memory";
import { generateUuidV4 } from "@/lib/uuid";
import OrderDetailPage from "@/app/(storefront)/orders/[orderNumber]/page";
import { CartDrawer } from "@/components/storefront/cart-drawer";
import { CartPageView } from "@/components/storefront/cart-page-view";
import { CheckoutView } from "@/components/storefront/checkout-view";
import { OrderDetailView } from "@/components/storefront/order-detail-view";
import { OrderHistoryView } from "@/components/storefront/order-history-view";
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

let mockPathname = "/checkout";
let mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => mockRouter,
  useSearchParams: () => mockSearchParams,
  notFound: () => mockNotFound(),
}));

const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const FORBIDDEN_CLIENT_PRICE_KEYS = [
  "subtotal_cents",
  "shipping_cents",
  "tax_cents",
  "total_cents",
  "discount_cents",
  "unit_price_cents",
] as const;

const touchedOrderNumbers = new Set<string>();

function trackOrderNumber(orderNumber: string): string {
  touchedOrderNumbers.add(orderNumber);
  return orderNumber;
}

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
        request_id: "req-qa-fe-002",
      },
    },
    status
  );
}

function makeUser(role: UserRole = "customer"): UserDTO {
  return {
    id: `user-${role}`,
    email: `${role}@atelier.test`,
    full_name: `Test ${role.toUpperCase()}`,
    role,
    is_active: true,
    created_at: "2026-11-18T10:00:00Z",
  };
}

function makeCartItem(overrides: Partial<CartItemDTO> = {}): CartItemDTO {
  const unitPrice = overrides.unit_price_cents ?? 4500;
  const quantity = overrides.quantity ?? 1;
  return {
    id: "item-1",
    variant_id: "var-1",
    product_id: "prod-1",
    product_slug: "merino-crewneck",
    product_title: "Merino Crewneck",
    variant_title: "Charcoal / M",
    sku: "TEE-CHR-M",
    image_url: "/assets/merino.png",
    unit_price_cents: unitPrice,
    quantity,
    line_total_cents: overrides.line_total_cents ?? unitPrice * quantity,
    available_quantity: 5,
    in_stock: true,
    low_stock: false,
    is_available: true,
    ...overrides,
  };
}

function makeCart(items: CartItemDTO[] = [], overrides: Partial<CartDTO> = {}): CartDTO {
  const itemCount = items.reduce((sum, item) => sum + item.quantity, 0);
  const subtotal = items.reduce((sum, item) => sum + item.line_total_cents, 0);
  const shipping = subtotal === 0 || subtotal >= 10_000 ? 0 : 1_000;
  return {
    id: "cart-fe-002",
    currency: "USD",
    items,
    item_count: itemCount,
    subtotal_cents: subtotal,
    estimated_shipping_cents: shipping,
    estimated_total_cents: subtotal + shipping,
    ...overrides,
  };
}

function makeAddress(overrides: Partial<AddressDTO> = {}): AddressDTO {
  return {
    full_name: "Alice Mercer",
    line1: "120 Market St",
    line2: "Apt 4B",
    city: "San Francisco",
    state: "CA",
    postal_code: "94105",
    country: "US",
    phone: "+14155550101",
    ...overrides,
  };
}

function makePaymentIntent(overrides: Partial<PaymentIntentDTO> = {}): PaymentIntentDTO {
  return {
    id: "pi-uuid-1",
    provider: "sandbox_simpay",
    provider_intent_id: "pi_sandbox_123",
    amount_cents: 10_720,
    currency: "USD",
    status: "REQUIRES_ACTION",
    failure_code: null,
    failure_message: null,
    created_at: "2026-11-19T12:00:00Z",
    ...overrides,
  };
}

function makeOrderDetail(overrides: Partial<OrderDetailDTO> = {}): OrderDetailDTO {
  const orderNumber = trackOrderNumber(overrides.order_number ?? "ORD-20261119-8F3K2A");
  return {
    id: "order-uuid-1",
    user_id: "user-customer",
    status: "PENDING_PAYMENT",
    payment_status: "REQUIRES_PAYMENT",
    fulfillment_status: "UNFULFILLED",
    customer_email: "alice@example.com",
    currency: "USD",
    subtotal_cents: 9_000,
    discount_cents: 0,
    shipping_cents: 1_000,
    tax_cents: 720,
    total_cents: 10_720,
    shipping_address: makeAddress(),
    billing_address: makeAddress(),
    items: [
      {
        id: "oi-1",
        product_id: "prod-1",
        variant_id: "var-1",
        sku_snapshot: "TEE-CHR-M",
        product_title_snapshot: "Merino Crewneck",
        variant_title_snapshot: "Charcoal / M",
        attributes_snapshot: { color: "Charcoal", size: "M" },
        unit_price_cents: 4_500,
        quantity: 2,
        line_total_cents: 9_000,
      },
    ],
    payment_intent: makePaymentIntent(),
    reservation_expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    events: [
      {
        id: "evt-1",
        actor_user_id: "user-customer",
        event_type: "ORDER_PLACED",
        from_status: null,
        to_status: "PENDING_PAYMENT",
        metadata: {},
        created_at: "2026-11-19T12:00:00Z",
      },
    ],
    placed_at: "2026-11-19T12:00:00Z",
    paid_at: null,
    shipped_at: null,
    completed_at: null,
    cancelled_at: null,
    cancellation_reason: null,
    ...overrides,
    order_number: orderNumber,
  };
}

function makeCheckoutResponse(
  orderNumber: string,
  guestToken: string | null = null
): CheckoutOrderResponseDTO {
  const tracked = trackOrderNumber(orderNumber);
  return {
    order: {
      id: "order-uuid-new",
      order_number: tracked,
      status: "PENDING_PAYMENT",
      payment_status: "REQUIRES_PAYMENT",
      fulfillment_status: "UNFULFILLED",
      customer_email: "guest@example.com",
      currency: "USD",
      subtotal_cents: 4_500,
      discount_cents: 0,
      shipping_cents: 1_000,
      tax_cents: 360,
      total_cents: 5_860,
      items: [
        {
          id: "oi-new",
          variant_id: "var-1",
          product_id: "prod-1",
          sku_snapshot: "TEE-CHR-M",
          product_title_snapshot: "Merino Crewneck",
          variant_title_snapshot: "Charcoal / M",
          attributes_snapshot: { color: "Charcoal", size: "M" },
          unit_price_cents: 4_500,
          quantity: 1,
          line_total_cents: 4_500,
        },
      ],
      placed_at: "2026-11-19T12:00:00Z",
      paid_at: null,
    },
    payment_intent: makePaymentIntent({ amount_cents: 5_860 }),
    reservation_expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
    guest_access_token: guestToken,
  };
}

function makeOrderListItem(overrides: Partial<OrderListItemDTO> = {}): OrderListItemDTO {
  const orderNumber = trackOrderNumber(overrides.order_number ?? "ORD-20261119-001");
  return {
    id: `ord-${orderNumber}`,
    status: "PROCESSING",
    payment_status: "PAID",
    fulfillment_status: "ALLOCATED",
    currency: "USD",
    subtotal_cents: 9_000,
    discount_cents: 0,
    shipping_cents: 1_000,
    tax_cents: 720,
    total_cents: 10_720,
    item_count: 2,
    placed_at: "2026-11-19T11:30:00Z",
    paid_at: "2026-11-19T11:32:00Z",
    reservation_expires_at: null,
    ...overrides,
    order_number: orderNumber,
  };
}

function renderStorefront(ui: React.ReactNode) {
  return render(
    <ToastProvider>
      <AuthProvider>
        <CartProvider>{ui}</CartProvider>
      </AuthProvider>
    </ToastProvider>
  );
}

function CartDrawerHarness() {
  const { openDrawer } = useCart();
  return (
    <div>
      <button type="button" onClick={openDrawer}>
        Open Drawer
      </button>
      <CartDrawer />
    </div>
  );
}

function fillAddressGroup(groupName: string, addr: AddressDTO): void {
  const group = screen.getByRole("group", { name: groupName });
  fireEvent.change(within(group).getByLabelText("Full name"), {
    target: { value: addr.full_name },
  });
  fireEvent.change(within(group).getByLabelText("Address line 1"), {
    target: { value: addr.line1 },
  });
  fireEvent.change(within(group).getByLabelText("Apartment, suite, etc. (optional)"), {
    target: { value: addr.line2 ?? "" },
  });
  fireEvent.change(within(group).getByLabelText("City"), {
    target: { value: addr.city },
  });
  fireEvent.change(within(group).getByLabelText("State / Province"), {
    target: { value: addr.state },
  });
  fireEvent.change(within(group).getByLabelText("Postal code"), {
    target: { value: addr.postal_code },
  });
  fireEvent.change(within(group).getByLabelText("Phone"), {
    target: { value: addr.phone },
  });
}

function fillCheckoutForm(params: {
  email?: string;
  shipping?: AddressDTO;
  billing?: AddressDTO;
}): void {
  if (params.email !== undefined) {
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: params.email },
    });
  }
  fillAddressGroup("Shipping recipient and destination", params.shipping ?? makeAddress());
  if (params.billing) {
    fillAddressGroup("Billing recipient and address", params.billing);
  }
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function getHeader(init: RequestInit | undefined, name: string): string | undefined {
  const headers = init?.headers;
  if (!headers) {
    return undefined;
  }
  if (headers instanceof Headers) {
    return headers.get(name) ?? undefined;
  }
  if (Array.isArray(headers)) {
    const match = headers.find(([k]) => k.toLowerCase() === name.toLowerCase());
    return match?.[1];
  }
  const target = name.toLowerCase();
  for (const [k, v] of Object.entries(headers)) {
    if (k.toLowerCase() === target) {
      return v;
    }
  }
  return undefined;
}

describe("CartDrawer & CartPageView", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/cart";
    mockSearchParams = new URLSearchParams();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    for (const orderNo of touchedOrderNumbers) {
      clearGuestOrderToken(orderNo);
    }
    touchedOrderNumbers.clear();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders CartDrawer checkout/full-cart links when ready and disables checkout on empty or unavailable items", async () => {
    // 1. Valid non-empty cart
    const validCart = makeCart([makeCartItem({ unit_price_cents: 4500, quantity: 1 })]);
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(validCart);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<CartDrawerHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Open Drawer" }));

    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByText("Merino Crewneck")).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: "Proceed to Checkout" })).toHaveAttribute(
      "href",
      "/checkout"
    );
    expect(within(drawer).getByRole("link", { name: "View Full Cart" })).toHaveAttribute(
      "href",
      "/cart"
    );

    cleanup();
    fetchMock.mockReset();

    // 2. Empty cart
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<CartDrawerHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Open Drawer" }));

    const emptyDrawer = await screen.findByRole("dialog");
    expect(await within(emptyDrawer).findByText("Your bag is empty")).toBeInTheDocument();
    expect(within(emptyDrawer).getByRole("link", { name: "Explore Products" })).toHaveAttribute(
      "href",
      "/products"
    );
    expect(
      within(emptyDrawer).queryByRole("link", { name: "Proceed to Checkout" })
    ).not.toBeInTheDocument();
    expect(
      within(emptyDrawer).queryByRole("button", { name: "Proceed to Checkout" })
    ).not.toBeInTheDocument();

    cleanup();
    fetchMock.mockReset();

    // 3. Unavailable (out-of-stock) item disables Proceed to Checkout button while retaining /cart link
    const unavailableCart = makeCart([
      makeCartItem({
        available_quantity: 0,
        in_stock: false,
        is_available: false,
      }),
    ]);
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(unavailableCart);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<CartDrawerHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Open Drawer" }));

    const conflictDrawer = await screen.findByRole("dialog");
    expect(
      await within(conflictDrawer).findByText(
        "Out of stock — remove this item to proceed to checkout."
      )
    ).toBeInTheDocument();
    const disabledCheckoutBtn = within(conflictDrawer).getByRole("button", {
      name: "Proceed to Checkout",
    });
    expect(disabledCheckoutBtn).toBeDisabled();
    expect(
      within(conflictDrawer).queryByRole("link", { name: "Proceed to Checkout" })
    ).not.toBeInTheDocument();
    expect(within(conflictDrawer).getByRole("link", { name: "View Full Cart" })).toHaveAttribute(
      "href",
      "/cart"
    );
  });

  it("disables row stepper while PATCH is in-flight, recovers stock conflict via Adjust to {available_quantity}, and steps quantities", async () => {
    const conflictItem1 = makeCartItem({
      id: "item-1",
      product_title: "Merino Crewneck",
      unit_price_cents: 4500,
      quantity: 3,
      available_quantity: 2,
      in_stock: true,
      is_available: false,
    });
    const okItem2 = makeCartItem({
      id: "item-2",
      variant_id: "var-2",
      product_id: "prod-2",
      product_slug: "cashmere-scarf",
      product_title: "Cashmere Scarf",
      variant_title: "Oatmeal / OS",
      sku: "SCF-OAT-OS",
      unit_price_cents: 2500,
      quantity: 1,
      available_quantity: 5,
      in_stock: true,
      is_available: true,
    });

    const initialCart = makeCart([conflictItem1, okItem2]);
    const adjustedCart = makeCart([
      makeCartItem({ ...conflictItem1, quantity: 2, line_total_cents: 9000, is_available: true }),
      okItem2,
    ]);
    const incrementedCart = makeCart([
      makeCartItem({ ...conflictItem1, quantity: 2, line_total_cents: 9000, is_available: true }),
      makeCartItem({ ...okItem2, quantity: 2, line_total_cents: 5000 }),
    ]);

    const patchItem1Deferred = createDeferred<Response>();
    const patchItem2Deferred = createDeferred<Response>();
    const patchBodies: Array<{ url: string; body: unknown }> = [];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(initialCart);
      }
      if (url.endsWith("/cart/items/item-1") && method === "PATCH") {
        patchBodies.push({ url, body: JSON.parse(String(init?.body)) });
        return patchItem1Deferred.promise;
      }
      if (url.endsWith("/cart/items/item-2") && method === "PATCH") {
        patchBodies.push({ url, body: JSON.parse(String(init?.body)) });
        return patchItem2Deferred.promise;
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<CartPageView />);

    expect(await screen.findByText("Only 2 left in stock")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Proceed to Checkout" })).toBeDisabled();

    // Trigger 1-click "Adjust to 2" recovery and verify in-flight disable on item-1 controls
    const adjustBtn = screen.getByRole("button", { name: "Adjust to 2" });
    fireEvent.click(adjustBtn);

    await waitFor(() => {
      expect(patchBodies).toHaveLength(1);
    });
    expect(patchBodies[0].body).toEqual({ quantity: 2 });
    expect(screen.getByRole("button", { name: "Decrease quantity for Merino Crewneck" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Increase quantity for Merino Crewneck" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove Merino Crewneck" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Adjust to 2" })).toBeDisabled();
    // Row 2 remains enabled while row 1 is in-flight
    expect(screen.getByRole("button", { name: "Increase quantity for Cashmere Scarf" })).toBeEnabled();

    patchItem1Deferred.resolve(jsonResponse(adjustedCart));

    await waitFor(() => {
      expect(screen.getByLabelText("Quantity for Merino Crewneck")).toHaveTextContent("2");
    });
    expect(screen.queryByRole("button", { name: "Adjust to 2" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Proceed to Checkout" })).toHaveAttribute(
      "href",
      "/checkout"
    );

    // Step item-2 quantity up -> disables item-2 stepper while PATCH is in-flight
    fireEvent.click(screen.getByRole("button", { name: "Increase quantity for Cashmere Scarf" }));
    await waitFor(() => {
      expect(patchBodies).toHaveLength(2);
    });
    expect(patchBodies[1].body).toEqual({ quantity: 2 });
    expect(screen.getByRole("button", { name: "Decrease quantity for Cashmere Scarf" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Increase quantity for Cashmere Scarf" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove Cashmere Scarf" })).toBeDisabled();

    patchItem2Deferred.resolve(jsonResponse(incrementedCart));

    await waitFor(() => {
      expect(screen.getByLabelText("Quantity for Cashmere Scarf")).toHaveTextContent("2");
    });
    expect(screen.getByRole("button", { name: "Decrease quantity for Cashmere Scarf" })).toBeEnabled();
  });

  it("renders $100 free-shipping threshold progressbar, unlocks free shipping at $100+, and requires dialog confirmation to clear cart", async () => {
    const initialCart = makeCart([
      makeCartItem({ id: "item-1", unit_price_cents: 4500, quantity: 1, available_quantity: 5 }),
    ]);
    const unlockedCart = makeCart([
      makeCartItem({
        id: "item-1",
        unit_price_cents: 4500,
        quantity: 3,
        line_total_cents: 13_500,
        available_quantity: 5,
      }),
    ]);
    const clearedCart = makeCart([]);

    let deleteCartCalls = 0;

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(initialCart);
      }
      if (url.endsWith("/cart/items/item-1") && method === "PATCH") {
        return jsonResponse(unlockedCart);
      }
      if (url.endsWith("/cart") && method === "DELETE") {
        deleteCartCalls += 1;
        return jsonResponse(clearedCart);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<CartPageView />);

    expect(
      await screen.findByText("Add $55.00 more for free standard shipping")
    ).toBeInTheDocument();
    const progress = screen.getByRole("progressbar", { name: "Free standard shipping progress" });
    expect(progress).toHaveAttribute("aria-valuemin", "0");
    expect(progress).toHaveAttribute("aria-valuemax", "10000");
    expect(progress).toHaveAttribute("aria-valuenow", "4500");

    // Step up past $100.00 threshold -> unlocks free shipping
    fireEvent.click(screen.getByRole("button", { name: "Increase quantity for Merino Crewneck" }));
    expect(
      await screen.findByText("Complimentary standard shipping unlocked ($0.00)")
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText("Free")).toBeInTheDocument();

    // Clear Cart dialog -> cancel keeps items
    fireEvent.click(screen.getByRole("button", { name: "Clear Cart" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Clear shopping bag?")).toBeInTheDocument();
    expect(
      within(dialog).getByText("This removes all 3 items from your shopping bag.")
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Keep items" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(deleteCartCalls).toBe(0);

    // Clear Cart dialog -> confirm deletes cart
    fireEvent.click(screen.getByRole("button", { name: "Clear Cart" }));
    const confirmDialog = await screen.findByRole("dialog");
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Clear Bag" }));

    expect(await screen.findByText("Your shopping bag is empty")).toBeInTheDocument();
    expect(deleteCartCalls).toBe(1);
  });
});

class MockResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

describe("CheckoutView (/checkout)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/checkout";
    mockSearchParams = new URLSearchParams();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    cleanup();
    for (const orderNo of touchedOrderNumbers) {
      clearGuestOrderToken(orderNo);
    }
    touchedOrderNumbers.clear();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("enforces empty-cart guard, unavailable-item alert, and 401 AUTH_SESSION_EXPIRED fail-closed re-auth banner", async () => {
    // 1. Empty cart guard
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<CheckoutView />);
    expect(await screen.findByText("Your cart is empty")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse Catalog" })).toHaveAttribute(
      "href",
      "/products"
    );

    cleanup();
    fetchMock.mockReset();

    // 2. Unavailable item alert + disabled submit
    const conflictCart = makeCart([
      makeCartItem({ available_quantity: 1, quantity: 3, is_available: false }),
    ]);
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(conflictCart);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<CheckoutView />);
    expect(await screen.findByText("Stock or availability changed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Update Cart" })).toHaveAttribute("href", "/cart");
    expect(screen.getByRole("button", { name: "Place Order & Reserve Stock" })).toBeDisabled();

    cleanup();
    fetchMock.mockReset();

    // 3. Authenticated session expires during POST /checkout/orders -> 401 AUTH_SESSION_EXPIRED fail-closed banner
    const activeCart = makeCart([makeCartItem({ quantity: 1 })]);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-live-1" });
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(activeCart);
      }
      if (url.endsWith("/checkout/orders") && method === "POST") {
        return errorResponse(401, "AUTH_SESSION_EXPIRED", "Session has expired.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<CheckoutView />);
    expect(await screen.findByDisplayValue("customer@atelier.test")).toBeInTheDocument();
    fillCheckoutForm({ shipping: makeAddress() });

    fireEvent.click(screen.getByRole("button", { name: "Place Order & Reserve Stock" }));

    const expiredMsg = await screen.findByText(
      "Your session has expired. Please sign in again to continue checkout."
    );
    const expiredAlert = expiredMsg.closest('[role="alert"]');
    expect(expiredAlert).not.toBeNull();
    expect(
      screen.getByRole("link", { name: "Sign In to Continue" })
    ).toHaveAttribute("href", "/login?expired=1&returnTo=%2Fcheckout");
    expect(
      screen.queryByRole("button", { name: "Place Order & Reserve Stock" })
    ).not.toBeInTheDocument();
  });

  it("toggles billing address fieldset and sends zero client-side pricing fields in POST /checkout/orders", async () => {
    const activeCart = makeCart([makeCartItem({ unit_price_cents: 4500, quantity: 2 })]);
    const capturedBodies: Array<Record<string, unknown>> = [];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(activeCart);
      }
      if (url.endsWith("/checkout/orders") && method === "POST") {
        capturedBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return jsonResponse(makeCheckoutResponse("ORD-20261119-BILL1"), 201);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<CheckoutView />);
    const billingToggle = await screen.findByRole("checkbox", {
      name: "Billing address same as shipping",
    });
    expect(billingToggle).toHaveAttribute("aria-checked", "true");
    expect(
      screen.queryByRole("group", { name: "Billing recipient and address" })
    ).not.toBeInTheDocument();

    fireEvent.click(billingToggle);
    expect(billingToggle).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("group", { name: "Billing recipient and address" })
    ).toBeInTheDocument();

    const shipping = makeAddress();
    const billing = makeAddress({
      full_name: "Atelier Accounting",
      line1: "500 Howard St",
      line2: "",
      city: "San Francisco",
      state: "CA",
      postal_code: "94105",
      phone: "+14155550199",
    });

    fillCheckoutForm({
      email: "  Buyer@Example.com  ",
      shipping,
      billing,
    });

    fireEvent.click(screen.getByRole("button", { name: "Place Order & Reserve Stock" }));

    await waitFor(() => {
      expect(capturedBodies).toHaveLength(1);
    });

    const sent = capturedBodies[0];
    expect(Object.keys(sent).sort()).toEqual([
      "billing_address",
      "billing_same_as_shipping",
      "customer_email",
      "shipping_address",
    ]);
    expect(sent.customer_email).toBe("buyer@example.com");
    expect(sent.billing_same_as_shipping).toBe(false);
    expect(sent.billing_address).toEqual({
      ...billing,
      line2: null,
    });
    expect(sent.shipping_address).toEqual(shipping);

    for (const forbidden of FORBIDDEN_CLIENT_PRICE_KEYS) {
      expect(sent).not.toHaveProperty(forbidden);
      expect(sent.shipping_address).not.toHaveProperty(forbidden);
      expect(sent.billing_address).not.toHaveProperty(forbidden);
    }
  });

  it("reuses Idempotency-Key across transient retry and regenerates on 409 IDEMPOTENCY_KEY_CONFLICT or edited fields", async () => {
    const activeCart = makeCart([makeCartItem({ quantity: 1 })]);
    const capturedKeys: string[] = [];
    let attempt = 0;

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(activeCart);
      }
      if (url.endsWith("/checkout/orders") && method === "POST") {
        attempt += 1;
        const key = getHeader(init, "Idempotency-Key") ?? "";
        capturedKeys.push(key);
        if (attempt === 1) {
          throw new TypeError("Network dropped");
        }
        if (attempt === 2) {
          return errorResponse(500, "INTERNAL_ERROR", "Temporary server timeout.");
        }
        if (attempt === 3) {
          return errorResponse(409, "IDEMPOTENCY_KEY_CONFLICT", "Payload mismatch.");
        }
        if (attempt === 4) {
          return errorResponse(500, "INTERNAL_ERROR", "Retry failed before edit.");
        }
        return jsonResponse(makeCheckoutResponse("ORD-20261119-IDEM1"), 201);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<CheckoutView />);
    await screen.findByLabelText("Email address");
    fillCheckoutForm({ email: "idem@example.com", shipping: makeAddress() });

    const submitBtn = screen.getByRole("button", { name: "Place Order & Reserve Stock" });

    // Attempt 1: transient network failure
    fireEvent.click(submitBtn);
    await waitFor(() => expect(capturedKeys).toHaveLength(1));
    expect(capturedKeys[0]).toMatch(UUID_V4_RE);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to reach the server. Please check your connection and try again."
    );

    // Attempt 2: retry unchanged after network drop -> reuses exact same Idempotency-Key; returns 500
    fireEvent.click(submitBtn);
    await waitFor(() => expect(capturedKeys).toHaveLength(2));
    expect(capturedKeys[1]).toBe(capturedKeys[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("Temporary server timeout.");

    // Attempt 3: retry unchanged after 500 -> reuses exact same Idempotency-Key; returns 409 IDEMPOTENCY_KEY_CONFLICT
    fireEvent.click(submitBtn);
    await waitFor(() => expect(capturedKeys).toHaveLength(3));
    expect(capturedKeys[2]).toBe(capturedKeys[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Checkout details changed since last attempt. Please review and submit again."
    );

    // Attempt 4: submit after 409 IDEMPOTENCY_KEY_CONFLICT -> regenerates Idempotency-Key
    fireEvent.click(submitBtn);
    await waitFor(() => expect(capturedKeys).toHaveLength(4));
    expect(capturedKeys[3]).toMatch(UUID_V4_RE);
    expect(capturedKeys[3]).not.toBe(capturedKeys[2]);
    expect(await screen.findByRole("alert")).toHaveTextContent("Retry failed before edit.");

    // Attempt 5: user edits shipping address -> regenerates Idempotency-Key again and succeeds
    const shippingGroup = screen.getByRole("group", { name: "Shipping recipient and destination" });
    fireEvent.change(within(shippingGroup).getByLabelText("Address line 1"), {
      target: { value: "250 Howard St" },
    });
    fireEvent.click(submitBtn);
    await waitFor(() => expect(capturedKeys).toHaveLength(5));
    expect(capturedKeys[4]).toMatch(UUID_V4_RE);
    expect(capturedKeys[4]).not.toBe(capturedKeys[3]);
    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/orders/ORD-20261119-IDEM1");
    });

    // Verify generateUuidV4 produces distinct RFC 4122 v4 UUIDs directly
    const u1 = generateUuidV4();
    const u2 = generateUuidV4();
    expect(u1).toMatch(UUID_V4_RE);
    expect(u2).toMatch(UUID_V4_RE);
    expect(u1).not.toBe(u2);
  });

  it("maps 422 VALIDATION_ERROR field errors, 409 INSUFFICIENT_STOCK with /cart link, and 429 RATE_LIMIT_EXCEEDED", async () => {
    const activeCart = makeCart([makeCartItem({ quantity: 2 })]);
    let postStep = 0;

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(activeCart);
      }
      if (url.endsWith("/checkout/orders") && method === "POST") {
        postStep += 1;
        if (postStep === 1) {
          return errorResponse(422, "VALIDATION_ERROR", "Validation failed.", {
            errors: [
              {
                loc: ["body", "customer_email"],
                msg: "Email domain is not deliverable.",
                type: "value_error",
              },
              {
                loc: ["body", "shipping_address", "postal_code"],
                msg: "Invalid postal code format.",
                type: "value_error",
              },
            ],
          });
        }
        if (postStep === 2) {
          return errorResponse(409, "INSUFFICIENT_STOCK", "Not enough stock for TEE-CHR-M.", {
            requested: 2,
            available: 1,
          });
        }
        return errorResponse(429, "RATE_LIMIT_EXCEEDED", "Rate limit exceeded.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<CheckoutView />);
    await screen.findByLabelText("Email address");
    fillCheckoutForm({ email: "buyer@example.com", shipping: makeAddress() });

    const submitBtn = screen.getByRole("button", { name: "Place Order & Reserve Stock" });

    // 1. 422 VALIDATION_ERROR
    fireEvent.click(submitBtn);
    expect(await screen.findByText("Email domain is not deliverable.")).toBeInTheDocument();
    expect(screen.getByText("Invalid postal code format.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email address")).toHaveAttribute("aria-invalid", "true");
    const shippingGroup = screen.getByRole("group", { name: "Shipping recipient and destination" });
    expect(within(shippingGroup).getByLabelText("Postal code")).toHaveAttribute(
      "aria-invalid",
      "true"
    );

    // 2. 409 INSUFFICIENT_STOCK
    fireEvent.click(submitBtn);
    const stockAlert = await screen.findByRole("alert");
    expect(stockAlert).toHaveTextContent("Not enough stock for TEE-CHR-M. (requested 2, available 1)");
    expect(within(stockAlert).getByRole("link", { name: "Update Cart" })).toHaveAttribute(
      "href",
      "/cart"
    );

    // 3. 429 RATE_LIMIT_EXCEEDED
    fireEvent.click(submitBtn);
    const rateAlert = await screen.findByRole("alert");
    expect(rateAlert).toHaveTextContent(
      "Too many checkout attempts or active unpaid reservations (max 3 active pending orders per guest session)."
    );
  });

  it("stores guest_access_token strictly in memory without touching localStorage or sessionStorage and navigates to order page", async () => {
    const getItemSpy = vi.spyOn(Storage.prototype, "getItem");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const removeItemSpy = vi.spyOn(Storage.prototype, "removeItem");
    const clearSpy = vi.spyOn(Storage.prototype, "clear");

    const activeCart = makeCart([makeCartItem({ quantity: 1 })]);
    let postCompleted = false;

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart") && method === "GET") {
        return jsonResponse(postCompleted ? makeCart([]) : activeCart);
      }
      if (url.endsWith("/checkout/orders") && method === "POST") {
        postCompleted = true;
        return jsonResponse(
          makeCheckoutResponse("ORD-20261119-GUEST1", "hmac-guest-secret-999"),
          201
        );
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<CheckoutView />);
    await screen.findByLabelText("Email address");
    fillCheckoutForm({ email: "guest@example.com", shipping: makeAddress() });

    fireEvent.click(screen.getByRole("button", { name: "Place Order & Reserve Stock" }));

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/orders/ORD-20261119-GUEST1");
    });

    expect(getGuestOrderToken("ORD-20261119-GUEST1")).toBe("hmac-guest-secret-999");
    expect(getItemSpy).not.toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalled();
    expect(removeItemSpy).not.toHaveBeenCalled();
    expect(clearSpy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});

describe("OrderDetailPage & OrderDetailView (/orders/[orderNumber])", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/orders/ORD-20261119-8F3K2A";
    mockSearchParams = new URLSearchParams();
    mockNotFound.mockClear();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    for (const orderNo of touchedOrderNumbers) {
      clearGuestOrderToken(orderNo);
    }
    touchedOrderNumbers.clear();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("rejects malformed %ZZ and traversal order slugs via notFound() while passing valid ORD-... order numbers", async () => {
    for (const badOrder of ["%ZZ", "..", "%2e%2e", "../admin", "ORD/../../etc", "A".repeat(33)]) {
      await expect(
        OrderDetailPage({ params: Promise.resolve({ orderNumber: badOrder }) })
      ).rejects.toThrow("NEXT_NOT_FOUND");
    }
    expect(mockNotFound).toHaveBeenCalledTimes(6);

    const validResult = await OrderDetailPage({
      params: Promise.resolve({ orderNumber: "ORD-20261119-8F3K2A" }),
    });
    expect(React.isValidElement(validResult)).toBe(true);
    expect(mockNotFound).toHaveBeenCalledTimes(6);
  });

  it("renders reservation countdown, zero card inputs, and refetches to CONFIRMED/PAID on Simulate Payment Success", async () => {
    const orderNumber = "ORD-20261119-PAY1";
    const pendingOrder = makeOrderDetail({
      order_number: orderNumber,
      reservation_expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    });
    const paidOrder = makeOrderDetail({
      order_number: orderNumber,
      status: "CONFIRMED",
      payment_status: "PAID",
      paid_at: "2026-11-19T12:04:00Z",
      reservation_expires_at: null,
      payment_intent: makePaymentIntent({ status: "SUCCEEDED" }),
      events: [
        ...pendingOrder.events,
        {
          id: "evt-2",
          actor_user_id: null,
          event_type: "PAYMENT_SUCCEEDED",
          from_status: "PENDING_PAYMENT",
          to_status: "CONFIRMED",
          metadata: {},
          created_at: "2026-11-19T12:04:00Z",
        },
      ],
    });

    let paid = false;
    const simulatePayloads: unknown[] = [];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-order-1" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.endsWith(`/orders/${orderNumber}`) && method === "GET") {
        return jsonResponse(paid ? paidOrder : pendingOrder);
      }
      if (url.endsWith("/payments/sandbox/pi_sandbox_123/simulate") && method === "POST") {
        simulatePayloads.push(JSON.parse(String(init?.body)));
        paid = true;
        return jsonResponse({
          event_id: "evt_sandbox_ok",
          webhook_status: "PROCESSED",
          order_status: "CONFIRMED",
          payment_status: "PAID",
        });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    const { container } = renderStorefront(<OrderDetailView orderNumber={orderNumber} />);

    expect(await screen.findByRole("heading", { name: orderNumber })).toBeInTheDocument();
    expect(await screen.findByText("Stock reservation active")).toBeInTheDocument();
    expect(screen.getByText(/Stock reserved for/)).toHaveTextContent(/\d{2}:\d{2}/);
    expect(screen.getByText("Sandbox SimPay · Test Payment Mode")).toBeInTheDocument();
    expect(screen.getByText("pi_sandbox_123")).toBeInTheDocument();

    // Explicit Zero-Card rule: zero PAN, CVV, or expiry inputs
    expect(container.querySelectorAll("input")).toHaveLength(0);
    expect(screen.queryByLabelText(/card|pan|cvv|cvc|expir/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Simulate Payment Success" }));

    await waitFor(() => {
      expect(simulatePayloads).toEqual([{ outcome: "succeeded", failure_code: null }]);
    });

    expect(
      await screen.findByText("Payment confirmed! Your order is now queued for fulfillment.")
    ).toBeInTheDocument();
    expect(screen.queryByText("Sandbox SimPay · Test Payment Mode")).not.toBeInTheDocument();
    expect(screen.queryByText("Stock reservation active")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel Order" })).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Paid orders cannot be self-cancelled online. Contact support for refund assistance."
      )
    ).toBeInTheDocument();
  });

  it("renders <=03:00 expiry warning, handles Simulate Payment Failure (card_declined), and creates retry intent with Idempotency-Key", async () => {
    const orderNumber = "ORD-20261119-FAIL1";
    const expiresAt = new Date(Date.now() + 2 * 60 * 1000).toISOString();

    const initialOrder = makeOrderDetail({
      order_number: orderNumber,
      reservation_expires_at: expiresAt,
    });
    const failedOrder = makeOrderDetail({
      order_number: orderNumber,
      payment_status: "FAILED",
      reservation_expires_at: expiresAt,
      payment_intent: makePaymentIntent({
        status: "FAILED",
        failure_code: "card_declined",
        failure_message: "Card declined by sandbox issuer.",
      }),
    });
    const retriedOrder = makeOrderDetail({
      order_number: orderNumber,
      payment_status: "FAILED",
      reservation_expires_at: expiresAt,
      payment_intent: makePaymentIntent({
        id: "pi-uuid-2",
        provider_intent_id: "pi_sandbox_retry_2",
        status: "REQUIRES_ACTION",
      }),
    });

    let stage: "initial" | "failed" | "retried" = "initial";
    const simulatePayloads: unknown[] = [];
    const intentCalls: Array<{ body: unknown; idempotencyKey: string | undefined }> = [];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-order-fail" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.endsWith(`/orders/${orderNumber}`) && method === "GET") {
        if (stage === "failed") return jsonResponse(failedOrder);
        if (stage === "retried") return jsonResponse(retriedOrder);
        return jsonResponse(initialOrder);
      }
      if (url.endsWith("/payments/sandbox/pi_sandbox_123/simulate") && method === "POST") {
        simulatePayloads.push(JSON.parse(String(init?.body)));
        stage = "failed";
        return jsonResponse({
          event_id: "evt_sandbox_fail",
          webhook_status: "PROCESSED",
          order_status: "PENDING_PAYMENT",
          payment_status: "FAILED",
        });
      }
      if (url.endsWith("/payments/intents") && method === "POST") {
        intentCalls.push({
          body: JSON.parse(String(init?.body)),
          idempotencyKey: getHeader(init, "Idempotency-Key"),
        });
        stage = "retried";
        return jsonResponse({ payment_intent: retriedOrder.payment_intent }, 201);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<OrderDetailView orderNumber={orderNumber} />);

    expect(await screen.findByText("Reservation expiring soon")).toBeInTheDocument();
    expect(screen.getByText(/Reservation expires in/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Simulate Payment Failure" }));

    await waitFor(() => {
      expect(simulatePayloads).toEqual([{ outcome: "failed", failure_code: "card_declined" }]);
    });

    expect(
      await screen.findByText(/Simulated payment failed \(card_declined\)\. Your stock reservation remains active/)
    ).toBeInTheDocument();
    // Self-cancel remains available when PENDING_PAYMENT + FAILED
    expect(screen.getByRole("button", { name: "Cancel Order" })).toBeInTheDocument();

    const retryBtn = screen.getByRole("button", { name: "Retry / Create Payment Intent" });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(intentCalls).toHaveLength(1);
    });
    expect(intentCalls[0].body).toEqual({ order_number: orderNumber });
    expect(intentCalls[0].idempotencyKey).toMatch(UUID_V4_RE);

    expect(await screen.findByText("pi_sandbox_retry_2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Simulate Payment Success" })).toBeInTheDocument();
  });

  it("hides simulate controls with explanatory message on 403 SANDBOX_MODE_DISABLED", async () => {
    const orderNumber = "ORD-20261119-SBXOFF";
    const pendingOrder = makeOrderDetail({ order_number: orderNumber });

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-sbx" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.endsWith(`/orders/${orderNumber}`) && method === "GET") {
        return jsonResponse(pendingOrder);
      }
      if (url.endsWith("/payments/sandbox/pi_sandbox_123/simulate") && method === "POST") {
        return errorResponse(403, "SANDBOX_MODE_DISABLED", "Sandbox mode disabled.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<OrderDetailView orderNumber={orderNumber} />);
    fireEvent.click(await screen.findByRole("button", { name: "Simulate Payment Success" }));

    expect(
      await screen.findAllByText("Sandbox payment simulation is disabled in this environment.")
    ).not.toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: "Simulate Payment Success" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Simulate Payment Failure" })
    ).not.toBeInTheDocument();
  });

  it("shows self-cancel confirmation Dialog for PENDING_PAYMENT + REQUIRES_PAYMENT and omits it once PAID", async () => {
    const cancelOrderNo = "ORD-20261119-CAN1";
    const pendingOrder = makeOrderDetail({ order_number: cancelOrderNo });
    const cancelledOrder = makeOrderDetail({
      order_number: cancelOrderNo,
      status: "CANCELLED",
      payment_status: "CANCELLED",
      fulfillment_status: "CANCELLED",
      reservation_expires_at: null,
      cancelled_at: "2026-11-19T12:05:00Z",
      cancellation_reason: "Changed shipping details",
    });

    let cancelled = false;
    const cancelBodies: unknown[] = [];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-can" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.endsWith(`/orders/${cancelOrderNo}`) && method === "GET") {
        return jsonResponse(cancelled ? cancelledOrder : pendingOrder);
      }
      if (url.endsWith(`/orders/${cancelOrderNo}/cancel`) && method === "POST") {
        cancelBodies.push(JSON.parse(String(init?.body)));
        cancelled = true;
        return jsonResponse(cancelledOrder);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    renderStorefront(<OrderDetailView orderNumber={cancelOrderNo} />);

    const cancelBtn = await screen.findByRole("button", { name: "Cancel Order" });
    fireEvent.click(cancelBtn);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Cancel Order & Release Reserved Stock?")).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("Cancellation reason (optional)"), {
      target: { value: "Changed shipping details" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm Cancellation" }));

    await waitFor(() => {
      expect(cancelBodies).toEqual([{ reason: "Changed shipping details" }]);
    });
    expect(
      await screen.findByText(
        "Cancelled (Changed shipping details). Reserved inventory has been released."
      )
    ).toBeInTheDocument();
    expect(screen.getAllByText("Order cancelled").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole("button", { name: "Cancel Order" })).not.toBeInTheDocument();

    cleanup();
    fetchMock.mockReset();

    // Paid order never exposes self-cancel button and renders support helper note
    const paidOrderNo = "ORD-20261119-PAIDONLY";
    const paidOrder = makeOrderDetail({
      order_number: paidOrderNo,
      status: "CONFIRMED",
      payment_status: "PAID",
      paid_at: "2026-11-19T12:02:00Z",
      reservation_expires_at: null,
      payment_intent: makePaymentIntent({ status: "SUCCEEDED" }),
    });

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-paid" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.endsWith(`/orders/${paidOrderNo}`)) {
        return jsonResponse(paidOrder);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<OrderDetailView orderNumber={paidOrderNo} />);
    expect(await screen.findByText("Payment Confirmed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel Order" })).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Paid orders cannot be self-cancelled online. Contact support for refund assistance."
      )
    ).toBeInTheDocument();
  });

  it("renders Order Access Required on unauthenticated 403 FORBIDDEN and submits X-Guest-Order-Token strictly in memory", async () => {
    const getItemSpy = vi.spyOn(Storage.prototype, "getItem");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const removeItemSpy = vi.spyOn(Storage.prototype, "removeItem");
    const clearSpy = vi.spyOn(Storage.prototype, "clear");

    const orderNumber = "ORD-20261119-GST1";
    const guestOrder = makeOrderDetail({
      order_number: orderNumber,
      user_id: null,
      customer_email: "guest@example.com",
    });

    const receivedGuestHeaders: Array<string | undefined> = [];

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.endsWith(`/orders/${orderNumber}`)) {
        const token = getHeader(init, "X-Guest-Order-Token");
        receivedGuestHeaders.push(token);
        if (token === "guest-token-live-42") {
          return jsonResponse(guestOrder);
        }
        return errorResponse(403, "FORBIDDEN", "Order access token required.");
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<OrderDetailView orderNumber={orderNumber} />);

    expect(await screen.findByText("Order Access Required")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in to your account" })).toHaveAttribute(
      "href",
      `/login?returnTo=${encodeURIComponent(`/orders/${orderNumber}`)}`
    );

    // Empty token validation
    fireEvent.click(screen.getByRole("button", { name: "View Guest Order" }));
    expect(
      await screen.findByText("Enter the guest order token from your checkout confirmation.")
    ).toBeInTheDocument();
    expect(receivedGuestHeaders).toHaveLength(1);

    // Valid token submission -> attaches X-Guest-Order-Token header without touching Storage
    fireEvent.change(screen.getByLabelText("Enter Guest Order Token"), {
      target: { value: "  guest-token-live-42  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "View Guest Order" }));

    expect(await screen.findByRole("heading", { name: orderNumber })).toBeInTheDocument();
    expect(receivedGuestHeaders).toEqual([undefined, "guest-token-live-42"]);
    expect(getGuestOrderToken(orderNumber)).toBe("guest-token-live-42");

    // Verify guest-order-memory helper respects explicit saves/clears cleanly
    saveGuestOrderToken(orderNumber, "guest-token-live-42");
    expect(getGuestOrderToken(orderNumber)).toBe("guest-token-live-42");

    expect(getItemSpy).not.toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalled();
    expect(removeItemSpy).not.toHaveBeenCalled();
    expect(clearSpy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});

describe("OrderHistoryView (/orders)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockPathname = "/orders";
    mockSearchParams = new URLSearchParams();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    for (const orderNo of touchedOrderNumbers) {
      clearGuestOrderToken(orderNo);
    }
    touchedOrderNumbers.clear();
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("redirects anonymous users to /login?returnTo=%2Forders without calling GET /orders", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<OrderHistoryView />);

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/login?returnTo=%2Forders");
    });
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/api/v1/orders"))
    ).toBe(false);
  });

  it("renders order rows with all 3 distinct status badges and handles pagination", async () => {
    const row1 = makeOrderListItem({
      order_number: "ORD-20261119-001",
      status: "PROCESSING",
      payment_status: "PAID",
      fulfillment_status: "ALLOCATED",
      item_count: 2,
      total_cents: 10_720,
    });
    const row2 = makeOrderListItem({
      order_number: "ORD-20261119-002",
      status: "PENDING_PAYMENT",
      payment_status: "REQUIRES_PAYMENT",
      fulfillment_status: "UNFULFILLED",
      item_count: 1,
      total_cents: 5_500,
    });
    const page2Row = makeOrderListItem({
      order_number: "ORD-20261119-021",
      status: "COMPLETED",
      payment_status: "PAID",
      fulfillment_status: "DELIVERED",
      item_count: 3,
      total_cents: 15_000,
    });

    const requestedUrls: string[] = [];

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-hist" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.includes("/orders")) {
        requestedUrls.push(url);
        if (url.includes("page=2")) {
          return jsonResponse({
            items: [page2Row],
            total: 25,
            page: 2,
            page_size: 20,
            has_next: false,
          });
        }
        return jsonResponse({
          items: [row1, row2],
          total: 25,
          page: 1,
          page_size: 20,
          has_next: true,
        });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<OrderHistoryView />);

    const table = await screen.findByRole("table");
    expect(within(table).getByRole("link", { name: "ORD-20261119-001" })).toHaveAttribute(
      "href",
      "/orders/ORD-20261119-001"
    );
    expect(within(table).getByText("Processing")).toBeInTheDocument();
    expect(within(table).getByText("Paid")).toBeInTheDocument();
    expect(within(table).getByText("Allocated")).toBeInTheDocument();

    expect(within(table).getByText("Pending Payment")).toBeInTheDocument();
    expect(within(table).getByText("Requires Payment")).toBeInTheDocument();
    expect(within(table).getByText("Unfulfilled")).toBeInTheDocument();

    expect(screen.getByText("Showing 1–20 of 25 orders")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    const page2Table = await screen.findByRole("table");
    expect(await within(page2Table).findByText("ORD-20261119-021")).toBeInTheDocument();
    expect(within(page2Table).getByText("Completed")).toBeInTheDocument();
    expect(within(page2Table).getByText("Paid")).toBeInTheDocument();
    expect(within(page2Table).getByText("Delivered")).toBeInTheDocument();
    expect(screen.getByText("Showing 21–25 of 25 orders")).toBeInTheDocument();
    expect(requestedUrls.some((u) => u.includes("page=2&page_size=20"))).toBe(true);
  });

  it("renders empty state when customer has zero orders", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-empty" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.includes("/orders")) {
        return jsonResponse({
          items: [],
          total: 0,
          page: 1,
          page_size: 20,
          has_next: false,
        });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<OrderHistoryView />);

    expect(await screen.findByText("No orders yet")).toBeInTheDocument();
    expect(
      screen.getByText("You haven't placed any orders yet. Explore the catalog to begin.")
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start Shopping" })).toHaveAttribute(
      "href",
      "/products"
    );
  });

  it("renders REFUND_PENDING payment badge and pending-refund event labels in storefront OrderDetailView", async () => {
    mockPathname = "/orders/ORD-20261121-REFPND";
    const order = makeOrderDetail({
      order_number: "ORD-20261121-REFPND",
      status: "CANCELLED",
      payment_status: "REFUND_PENDING",
      fulfillment_status: "CANCELLED",
      payment_intent: makePaymentIntent({ status: "REFUND_PENDING" }),
      events: [
        {
          id: "evt-stale-pend",
          actor_user_id: null,
          event_type: "STALE_INTENT_PAYMENT_REFUND_PENDING",
          from_status: "PENDING_PAYMENT",
          to_status: "PENDING_PAYMENT",
          metadata: {},
          created_at: "2026-11-21T10:02:00Z",
        },
      ],
    });

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-refpend" });
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(makeCart([]));
      }
      if (url.endsWith("/orders/ORD-20261121-REFPND")) {
        return jsonResponse(order);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<OrderDetailView orderNumber="ORD-20261121-REFPND" />);

    expect((await screen.findAllByText("Refund Pending")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Stale Payment Refund Pending")).toBeInTheDocument();
  });

  it("renders dynamic tax_rate_bps / estimated_tax_cents and currency-aware free shipping on CheckoutView", async () => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
    mockPathname = "/checkout";
    const eurCart = makeCart(
      [makeCartItem({ unit_price_cents: 12000, quantity: 1 })],
      {
        currency: "EUR",
        subtotal_cents: 12000,
        estimated_shipping_cents: 0,
        tax_rate_bps: 1250,
        estimated_tax_cents: 1500,
        estimated_total_cents: 12000,
      }
    );

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
      }
      if (url.endsWith("/cart")) {
        return jsonResponse(eurCart);
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
    });

    renderStorefront(<CheckoutView />);

    expect(await screen.findByText("Estimated Tax (12.50% preview)")).toBeInTheDocument();
    expect(screen.getByText("€0.00 (Free)")).toBeInTheDocument();
    expect(screen.getByText("€15.00")).toBeInTheDocument();
    expect(screen.getAllByText("€135.00").length).toBeGreaterThanOrEqual(1);
  });

  it("clears all in-memory guest order access tokens on AuthProvider.logout()", async () => {
    saveGuestOrderToken("ORD-20261121-A1", "guest-tok-a1");
    saveGuestOrderToken("ORD-20261121-B2", "guest-tok-b2");
    expect(getGuestOrderToken("ORD-20261121-A1")).toBe("guest-tok-a1");
    expect(getGuestOrderToken("ORD-20261121-B2")).toBe("guest-tok-b2");

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: makeUser("customer"), csrf_token: "csrf-logout-test" });
      }
      if (url.endsWith("/auth/logout") && method === "POST") {
        return new Response(null, { status: 204 });
      }
      return errorResponse(404, "NOT_FOUND", `Unexpected ${method} ${url}`);
    });

    function LogoutProbe() {
      const { user, logout } = useAuth();
      return (
        <div>
          <span>{user ? user.email : "signed-out"}</span>
          <button type="button" onClick={() => void logout()}>
            Trigger Logout
          </button>
        </div>
      );
    }

    render(
      <ToastProvider>
        <AuthProvider>
          <LogoutProbe />
        </AuthProvider>
      </ToastProvider>
    );

    expect(await screen.findByText("customer@atelier.test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Trigger Logout" }));
    expect(await screen.findByText("signed-out")).toBeInTheDocument();

    expect(getGuestOrderToken("ORD-20261121-A1")).toBeUndefined();
    expect(getGuestOrderToken("ORD-20261121-B2")).toBeUndefined();
  });
});
