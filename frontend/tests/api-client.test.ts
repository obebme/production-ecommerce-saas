import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  authApi,
  cartApi,
  catalogApi,
  getApiBaseUrl,
  getMemoryCsrfToken,
  registerAuthInvalidationListener,
  setMemoryCsrfToken,
} from "@/lib/api/client";
import type { AuthSessionResponse, CartDTO } from "@/lib/api/types";

function jsonResponse(
  body: unknown,
  init: { status?: number; statusText?: string; headers?: Record<string, string> } = {}
): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    statusText: init.statusText,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  requestId = "req-test-1"
): Response {
  return jsonResponse(
    {
      error: {
        code,
        message,
        details: {},
        request_id: requestId,
      },
    },
    {
      status,
      headers: {
        "X-Request-ID": requestId,
      },
    }
  );
}

const SAMPLE_SESSION: AuthSessionResponse = {
  user: {
    id: "user-1",
    email: "buyer@example.com",
    full_name: "Buyer One",
    role: "customer",
    is_active: true,
    created_at: "2026-11-18T10:00:00Z",
  },
  csrf_token: "csrf-from-session",
};

const SAMPLE_CART: CartDTO = {
  id: "cart-1",
  currency: "USD",
  items: [],
  item_count: 1,
  subtotal_cents: 4500,
  estimated_shipping_cents: 1000,
  estimated_total_cents: 5500,
};

function clearCsrfCookie(): void {
  document.cookie = "ecom_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
}

describe("API client & CSRF/session security behavior", () => {
  const originalEnv = { ...process.env };
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    delete process.env.INTERNAL_API_BASE_URL;
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();

    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    process.env = { ...originalEnv };
    setMemoryCsrfToken(null);
    registerAuthInvalidationListener(null);
    clearCsrfCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("normalizes getApiBaseUrl without duplicating /api/v1 and always sets credentials: include", async () => {
    expect(getApiBaseUrl()).toBe("http://localhost:8000/api/v1");

    process.env.NEXT_PUBLIC_API_BASE_URL = "https://shop.example.com";
    expect(getApiBaseUrl()).toBe("https://shop.example.com/api/v1");

    process.env.NEXT_PUBLIC_API_BASE_URL = "https://shop.example.com/";
    expect(getApiBaseUrl()).toBe("https://shop.example.com/api/v1");

    process.env.NEXT_PUBLIC_API_BASE_URL = "https://shop.example.com/api/v1";
    expect(getApiBaseUrl()).toBe("https://shop.example.com/api/v1");

    process.env.NEXT_PUBLIC_API_BASE_URL = "https://shop.example.com/api/v1/";
    expect(getApiBaseUrl()).toBe("https://shop.example.com/api/v1");

    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ items: [], total: 0, page: 1, page_size: 20, has_next: false })
      )
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART));

    setMemoryCsrfToken("csrf-123");
    await catalogApi.listProducts({ page: 1 });
    await cartApi.addItem({ variant_id: "var-1", quantity: 1 });
    await cartApi.updateItem("item-1", { quantity: 2 });
    await cartApi.removeItem("item-1");

    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(String(url)).toMatch(/^https:\/\/shop\.example\.com\/api\/v1\//);
      expect(String(url)).not.toContain("/api/v1/api/v1");
      expect((init as RequestInit).credentials).toBe("include");
    }

    // Reject unsafe schemes and protocol-relative API base URLs (LOW-3)
    for (const unsafeBase of [
      "javascript:alert(1)",
      "ftp://example.com/api",
      "//evil.com/api",
    ]) {
      process.env.NEXT_PUBLIC_API_BASE_URL = unsafeBase;
      expect(() => getApiBaseUrl()).toThrow();
    }

    process.env.NEXT_PUBLIC_API_BASE_URL = "/backend";
    expect(getApiBaseUrl()).toBe("/backend/api/v1");
  });

  it("never reads or writes localStorage or sessionStorage across login, CSRF, and mutations", async () => {
    const getItemSpy = vi.spyOn(Storage.prototype, "getItem");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const removeItemSpy = vi.spyOn(Storage.prototype, "removeItem");
    const clearSpy = vi.spyOn(Storage.prototype, "clear");

    fetchMock
      .mockResolvedValueOnce(jsonResponse(SAMPLE_SESSION))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_SESSION))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await authApi.login({ email: "buyer@example.com", password: "Password123!" });
    expect(getMemoryCsrfToken()).toBe("csrf-from-session");

    setMemoryCsrfToken("csrf-updated");
    await authApi.me();
    await cartApi.addItem({ variant_id: "var-1", quantity: 1 });
    await cartApi.updateItem("item-1", { quantity: 3 });
    await cartApi.removeItem("item-1");
    await authApi.logout();

    expect(getMemoryCsrfToken()).toBeNull();
    expect(getItemSpy).not.toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalled();
    expect(removeItemSpy).not.toHaveBeenCalled();
    expect(clearSpy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("attaches X-CSRF-Token on POST, PATCH, and DELETE mutations only, with ecom_csrf cookie fallback", async () => {
    setMemoryCsrfToken("mem-csrf-token");

    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART));

    await catalogApi.listCategories();
    await cartApi.getCart();
    await cartApi.addItem({ variant_id: "var-1", quantity: 1 });
    await cartApi.updateItem("item-1", { quantity: 2 });
    await cartApi.removeItem("item-1");

    const getCallHeaders0 = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<
      string,
      string
    >;
    const getCallHeaders1 = (fetchMock.mock.calls[1][1] as RequestInit).headers as Record<
      string,
      string
    >;
    expect(getCallHeaders0["X-CSRF-Token"]).toBeUndefined();
    expect(getCallHeaders1["X-CSRF-Token"]).toBeUndefined();

    for (const idx of [2, 3, 4]) {
      const init = fetchMock.mock.calls[idx][1] as RequestInit;
      const headers = init.headers as Record<string, string>;
      expect(["POST", "PATCH", "DELETE"]).toContain(init.method);
      expect(headers["X-CSRF-Token"]).toBe("mem-csrf-token");
    }

    // Fallback to non-HttpOnly ecom_csrf cookie when memory token is cleared
    fetchMock.mockReset();
    setMemoryCsrfToken(null);
    document.cookie = "other_cookie=ignored; path=/";
    document.cookie = "ecom_csrf=cookie-csrf-fallback; path=/";

    fetchMock
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART))
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART));

    await cartApi.getCart();
    await cartApi.clearCart();

    const getHeaders = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<
      string,
      string
    >;
    const deleteHeaders = (fetchMock.mock.calls[1][1] as RequestInit).headers as Record<
      string,
      string
    >;
    expect(getHeaders["X-CSRF-Token"]).toBeUndefined();
    expect(deleteHeaders["X-CSRF-Token"]).toBe("cookie-csrf-fallback");
  });

  it("refreshes CSRF once via GET /auth/me on 403 CSRF_INVALID and retries once without looping", async () => {
    setMemoryCsrfToken("stale-csrf");

    fetchMock
      .mockResolvedValueOnce(errorResponse(403, "CSRF_INVALID", "CSRF token invalid."))
      .mockResolvedValueOnce(
        jsonResponse({ ...SAMPLE_SESSION, csrf_token: "refreshed-csrf-token" })
      )
      .mockResolvedValueOnce(jsonResponse(SAMPLE_CART));

    const cart = await cartApi.addItem({ variant_id: "var-1", quantity: 1 });
    expect(cart).toEqual(SAMPLE_CART);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    const firstHeaders = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<
      string,
      string
    >;
    const refreshCall = fetchMock.mock.calls[1];
    const retryHeaders = (fetchMock.mock.calls[2][1] as RequestInit).headers as Record<
      string,
      string
    >;

    expect(firstHeaders["X-CSRF-Token"]).toBe("stale-csrf");
    expect(String(refreshCall[0])).toBe("http://localhost:8000/api/v1/auth/me");
    expect((refreshCall[1] as RequestInit).method).toBe("GET");
    expect((refreshCall[1] as RequestInit).credentials).toBe("include");
    expect(retryHeaders["X-CSRF-Token"]).toBe("refreshed-csrf-token");
    expect(getMemoryCsrfToken()).toBe("refreshed-csrf-token");

    // Repeated 403 CSRF_INVALID on retry must stop after 1 retry
    fetchMock.mockReset();
    setMemoryCsrfToken("stale-2");
    fetchMock
      .mockResolvedValueOnce(errorResponse(403, "CSRF_INVALID", "CSRF token invalid."))
      .mockResolvedValueOnce(jsonResponse({ ...SAMPLE_SESSION, csrf_token: "still-bad" }))
      .mockResolvedValueOnce(errorResponse(403, "CSRF_INVALID", "CSRF token invalid."));

    await expect(cartApi.addItem({ variant_id: "var-1", quantity: 1 })).rejects.toMatchObject({
      status: 403,
      code: "CSRF_INVALID",
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);

    // Non-CSRF 403 must NOT trigger /auth/me refresh or retry
    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(
      errorResponse(403, "INSUFFICIENT_PERMISSIONS", "Admin privileges required.")
    );

    await expect(cartApi.addItem({ variant_id: "var-1", quantity: 1 })).rejects.toMatchObject({
      status: 403,
      code: "INSUFFICIENT_PERMISSIONS",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Failed /auth/me refresh returning 401 AUTH_SESSION_EXPIRED or 403 AUTH_ACCOUNT_DISABLED
    // must clear memory CSRF token and trigger fail-closed auth invalidation listener (LOW-3)
    const refreshListener = vi.fn();
    registerAuthInvalidationListener(refreshListener);

    fetchMock.mockReset();
    setMemoryCsrfToken("stale-3");
    fetchMock
      .mockResolvedValueOnce(errorResponse(403, "CSRF_INVALID", "CSRF token invalid."))
      .mockResolvedValueOnce(errorResponse(401, "AUTH_SESSION_EXPIRED", "Expired."));

    await expect(cartApi.addItem({ variant_id: "var-1", quantity: 1 })).rejects.toMatchObject({
      status: 403,
      code: "CSRF_INVALID",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(getMemoryCsrfToken()).toBeNull();
    expect(refreshListener).toHaveBeenCalledTimes(1);
    expect(refreshListener).toHaveBeenCalledWith("session_expired");

    refreshListener.mockClear();
    fetchMock.mockReset();
    setMemoryCsrfToken("stale-4");
    fetchMock
      .mockResolvedValueOnce(errorResponse(403, "CSRF_INVALID", "CSRF token invalid."))
      .mockResolvedValueOnce(errorResponse(403, "AUTH_ACCOUNT_DISABLED", "Account disabled."));

    await expect(cartApi.addItem({ variant_id: "var-1", quantity: 1 })).rejects.toMatchObject({
      status: 403,
      code: "CSRF_INVALID",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(getMemoryCsrfToken()).toBeNull();
    expect(refreshListener).toHaveBeenCalledTimes(1);
    expect(refreshListener).toHaveBeenCalledWith("account_disabled");
    registerAuthInvalidationListener(null);
  });

  it("fires fail-closed registerAuthInvalidationListener and rejects with ApiError on 401 AUTH_SESSION_EXPIRED and 403 AUTH_ACCOUNT_DISABLED", async () => {
    const listener = vi.fn();
    registerAuthInvalidationListener(listener);

    setMemoryCsrfToken("active-token");
    fetchMock.mockResolvedValueOnce(
      errorResponse(401, "AUTH_SESSION_EXPIRED", "Session expired.", "req-401")
    );

    let captured401: unknown;
    try {
      await cartApi.getCart();
    } catch (err) {
      captured401 = err;
    }

    expect(captured401).toBeInstanceOf(ApiError);
    expect(captured401).toMatchObject({
      status: 401,
      code: "AUTH_SESSION_EXPIRED",
      message: "Session expired.",
      requestId: "req-401",
    });
    expect(getMemoryCsrfToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith("session_expired");

    listener.mockClear();
    setMemoryCsrfToken("active-token-2");
    fetchMock.mockResolvedValueOnce(
      errorResponse(403, "AUTH_ACCOUNT_DISABLED", "Account disabled.", "req-403")
    );

    let captured403: unknown;
    try {
      await cartApi.addItem({ variant_id: "var-1", quantity: 1 });
    } catch (err) {
      captured403 = err;
    }

    expect(captured403).toBeInstanceOf(ApiError);
    expect(captured403).toMatchObject({
      status: 403,
      code: "AUTH_ACCOUNT_DISABLED",
      message: "Account disabled.",
      requestId: "req-403",
    });
    expect(getMemoryCsrfToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith("account_disabled");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
