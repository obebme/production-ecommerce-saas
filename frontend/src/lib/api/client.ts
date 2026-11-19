import type {
  AddCartItemRequest,
  AdminCategoryDTO,
  AdminCategoryListResponse,
  AdminInventoryItemDTO,
  AdminOrderDetailDTO,
  AdminOrderListItemDTO,
  AdminProductDetailDTO,
  AdminProductSummaryDTO,
  AdminUserDTO,
  AdminUserMutationResponse,
  AdminVariantDTO,
  AnalyticsSummaryResponse,
  AnalyticsTimeseriesResponse,
  AuditLogEntryDTO,
  AuthSessionResponse,
  CancelOrderRequest,
  CartDTO,
  CategoryListResponse,
  CheckoutOrderResponseDTO,
  CreateCategoryRequest,
  CreateCheckoutOrderRequest,
  CreatePaymentIntentRequest,
  CreateProductRequest,
  CreateVariantRequest,
  ErrorEnvelope,
  FulfillOrderRequest,
  FulfillmentStatus,
  HealthDTO,
  InventoryHealthResponse,
  InventoryLedgerEntryDTO,
  LoginRequest,
  OrderDetailDTO,
  OrderListItemDTO,
  OrderStatus,
  PaginatedResponse,
  PaymentIntentResponse,
  PaymentStatus,
  ProductDetailDTO,
  ProductListQuery,
  ProductStatus,
  ProductSummaryDTO,
  RefundOrderRequest,
  RegisterRequest,
  SimulateSandboxPaymentRequest,
  SimulateSandboxPaymentResponse,
  StockAdjustmentRequest,
  StockStatusFilter,
  TopProductSortBy,
  TopProductsResponse,
  UpdateCartItemRequest,
  UpdateCategoryRequest,
  UpdateProductRequest,
  UpdateProfileRequest,
  UpdateUserRoleRequest,
  UpdateUserStatusRequest,
  UpdateVariantRequest,
  UserRole,
  UserUpdateResponse,
  ValidationFieldError,
  VariantAvailabilityDTO,
} from "./types";

const DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1";
const CSRF_COOKIE_NAME = "ecom_csrf";
const MUTATING_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

/** Module-memory CSRF token (never stored in localStorage or sessionStorage). */
let memoryCsrfToken: string | null = null;

type AuthInvalidationListener = (reason: "session_expired" | "account_disabled") => void;
let authInvalidationListener: AuthInvalidationListener | null = null;

export function setMemoryCsrfToken(token: string | null): void {
  memoryCsrfToken = token;
}

export function getMemoryCsrfToken(): string | null {
  return memoryCsrfToken;
}

export function registerAuthInvalidationListener(
  listener: AuthInvalidationListener | null
): void {
  authInvalidationListener = listener;
}

/**
 * Safely read a non-HttpOnly cookie (such as ecom_csrf) in browser environments.
 */
export function getCookieValue(name: string): string | null {
  if (typeof document === "undefined" || !document.cookie) {
    return null;
  }
  const cookies = document.cookie.split(";");
  for (const part of cookies) {
    const trimmed = part.trim();
    if (!trimmed) {
      continue;
    }
    const eqIndex = trimmed.indexOf("=");
    if (eqIndex <= 0) {
      continue;
    }
    const key = trimmed.slice(0, eqIndex).trim();
    if (key === name) {
      const rawValue = trimmed.slice(eqIndex + 1);
      try {
        return decodeURIComponent(rawValue);
      } catch {
        return rawValue;
      }
    }
  }
  return null;
}

/**
 * Resolve API base URL for browser or server execution without duplicating /api/v1.
 */
export function getApiBaseUrl(): string {
  const raw =
    typeof window !== "undefined"
      ? process.env.NEXT_PUBLIC_API_BASE_URL
      : process.env.INTERNAL_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL;

  const trimmed = (raw || DEFAULT_API_BASE_URL).trim().replace(/\/+$/, "");
  if (!trimmed) {
    return DEFAULT_API_BASE_URL;
  }

  const isRelativePath =
    trimmed.startsWith("/") &&
    !trimmed.startsWith("//") &&
    !trimmed.includes("\\") &&
    !/[\u0000-\u001F\u007F]/.test(trimmed);

  if (!isRelativePath) {
    let parsed: URL;
    try {
      parsed = new URL(trimmed);
    } catch {
      throw new Error("Invalid API base URL configuration.");
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      throw new Error("API base URL must use http://, https://, or a relative / path.");
    }
  }

  if (trimmed.endsWith("/api/v1")) {
    return trimmed;
  }
  return `${trimmed}/api/v1`;
}

function buildEndpointUrl(path: string): string {
  const base = getApiBaseUrl();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const cleanPath = normalizedPath.startsWith("/api/v1")
    ? normalizedPath.slice("/api/v1".length) || "/"
    : normalizedPath;
  return `${base}${cleanPath}`;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(params: {
    status: number;
    code: string;
    message: string;
    details?: Record<string, unknown>;
    requestId?: string | null;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.status = params.status;
    this.code = params.code;
    this.details = params.details ?? {};
    this.requestId = params.requestId ?? null;
  }
}

export function extractValidationErrors(error: ApiError): ValidationFieldError[] {
  const raw = error.details.errors;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter(
    (entry): entry is ValidationFieldError =>
      typeof entry === "object" &&
      entry !== null &&
      Array.isArray((entry as ValidationFieldError).loc) &&
      typeof (entry as ValidationFieldError).msg === "string"
  );
}

export function isNetworkApiError(err: unknown): err is ApiError {
  return err instanceof ApiError && err.status === 0;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  skipCsrf?: boolean;
  skipAuthInvalidationHook?: boolean;
  signal?: AbortSignal;
}

async function parseErrorResponse(response: Response): Promise<ApiError> {
  const headerRequestId = response.headers.get("X-Request-ID");
  try {
    const data = (await response.json()) as Partial<ErrorEnvelope>;
    if (data && typeof data === "object" && data.error && typeof data.error === "object") {
      return new ApiError({
        status: response.status,
        code: typeof data.error.code === "string" ? data.error.code : `HTTP_${response.status}`,
        message:
          typeof data.error.message === "string"
            ? data.error.message
            : response.statusText || "Request failed.",
        details:
          data.error.details && typeof data.error.details === "object"
            ? data.error.details
            : {},
        requestId:
          typeof data.error.request_id === "string" ? data.error.request_id : headerRequestId,
      });
    }
  } catch {
    // Non-JSON error body
  }

  return new ApiError({
    status: response.status,
    code: `HTTP_${response.status}`,
    message: response.statusText || "Request failed.",
    requestId: headerRequestId,
  });
}

function getEffectiveCsrfToken(): string | null {
  return memoryCsrfToken || getCookieValue(CSRF_COOKIE_NAME);
}

async function refreshCsrfFromSession(): Promise<string | null> {
  try {
    const res = await fetch(buildEndpointUrl("/auth/me"), {
      method: "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
      },
    });
    if (!res.ok) {
      const refreshError = await parseErrorResponse(res);
      if (
        (refreshError.status === 401 && refreshError.code === "AUTH_SESSION_EXPIRED") ||
        (refreshError.status === 403 && refreshError.code === "AUTH_ACCOUNT_DISABLED")
      ) {
        memoryCsrfToken = null;
        authInvalidationListener?.(
          refreshError.code === "AUTH_ACCOUNT_DISABLED"
            ? "account_disabled"
            : "session_expired"
        );
      }
      return null;
    }
    const payload = (await res.json()) as AuthSessionResponse;
    if (payload && typeof payload.csrf_token === "string") {
      memoryCsrfToken = payload.csrf_token;
      return payload.csrf_token;
    }
  } catch {
    // Ignore refresh transport failure; caller handles original error
  }
  return null;
}

async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
  isCsrfRetry = false
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };

  let serializedBody: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    serializedBody = JSON.stringify(options.body);
  }

  if (MUTATING_METHODS.has(method) && !options.skipCsrf) {
    const csrfToken = getEffectiveCsrfToken();
    if (csrfToken) {
      headers["X-CSRF-Token"] = csrfToken;
    }
  }

  let response: Response;
  try {
    response = await fetch(buildEndpointUrl(path), {
      method,
      credentials: "include",
      headers,
      body: serializedBody,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    throw new ApiError({
      status: 0,
      code: "NETWORK_ERROR",
      message: "Unable to reach the server. Please check your connection and try again.",
    });
  }

  if (!response.ok) {
    const apiError = await parseErrorResponse(response);

    // Retry once with refreshed CSRF token if server rejects CSRF on an active session
    if (
      !isCsrfRetry &&
      MUTATING_METHODS.has(method) &&
      !options.skipCsrf &&
      apiError.status === 403 &&
      apiError.code === "CSRF_INVALID"
    ) {
      const refreshedToken = await refreshCsrfFromSession();
      if (refreshedToken) {
        return apiRequest<T>(path, options, true);
      }
    }

    if (!options.skipAuthInvalidationHook) {
      if (apiError.status === 401 && apiError.code === "AUTH_SESSION_EXPIRED") {
        const isAnonymousAuthRequired =
          !getEffectiveCsrfToken() && apiError.message === "Authentication required.";
        if (!isAnonymousAuthRequired) {
          memoryCsrfToken = null;
          authInvalidationListener?.("session_expired");
        }
      } else if (apiError.status === 403 && apiError.code === "AUTH_ACCOUNT_DISABLED") {
        memoryCsrfToken = null;
        authInvalidationListener?.("account_disabled");
      }
    }

    throw apiError;
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

function buildQueryString(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export const healthApi = {
  getHealth(signal?: AbortSignal): Promise<HealthDTO> {
    return apiRequest<HealthDTO>("/health", { signal, skipAuthInvalidationHook: true });
  },
};

export const authApi = {
  async me(options?: { skipAuthInvalidationHook?: boolean; signal?: AbortSignal }): Promise<AuthSessionResponse> {
    const res = await apiRequest<AuthSessionResponse>("/auth/me", {
      method: "GET",
      skipAuthInvalidationHook: options?.skipAuthInvalidationHook ?? true,
      signal: options?.signal,
    });
    memoryCsrfToken = res.csrf_token;
    return res;
  },

  async login(payload: LoginRequest): Promise<AuthSessionResponse> {
    const res = await apiRequest<AuthSessionResponse>("/auth/login", {
      method: "POST",
      body: payload,
      skipCsrf: true,
      skipAuthInvalidationHook: true,
    });
    memoryCsrfToken = res.csrf_token;
    return res;
  },

  async register(payload: RegisterRequest): Promise<AuthSessionResponse> {
    const res = await apiRequest<AuthSessionResponse>("/auth/register", {
      method: "POST",
      body: payload,
      skipCsrf: true,
      skipAuthInvalidationHook: true,
    });
    memoryCsrfToken = res.csrf_token;
    return res;
  },

  async logout(): Promise<void> {
    try {
      await apiRequest<void>("/auth/logout", {
        method: "POST",
        skipAuthInvalidationHook: true,
      });
    } finally {
      memoryCsrfToken = null;
    }
  },

  updateProfile(payload: UpdateProfileRequest): Promise<UserUpdateResponse> {
    return apiRequest<UserUpdateResponse>("/auth/me", {
      method: "PATCH",
      body: payload,
    });
  },
};

export const catalogApi = {
  listCategories(signal?: AbortSignal): Promise<CategoryListResponse> {
    return apiRequest<CategoryListResponse>("/categories", {
      signal,
      skipAuthInvalidationHook: true,
    });
  },

  listProducts(
    query: ProductListQuery = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<ProductSummaryDTO>> {
    const qs = buildQueryString({
      category_slug: query.category_slug,
      q: query.q,
      min_price_cents: query.min_price_cents,
      max_price_cents: query.max_price_cents,
      in_stock: query.in_stock,
      sort: query.sort,
      page: query.page,
      page_size: query.page_size,
    });
    return apiRequest<PaginatedResponse<ProductSummaryDTO>>(`/products${qs}`, {
      signal,
      skipAuthInvalidationHook: true,
    });
  },

  getProductBySlug(slug: string, signal?: AbortSignal): Promise<ProductDetailDTO> {
    return apiRequest<ProductDetailDTO>(`/products/${encodeURIComponent(slug)}`, {
      signal,
      skipAuthInvalidationHook: true,
    });
  },
};

export const inventoryApi = {
  getVariantAvailability(
    variantId: string,
    signal?: AbortSignal
  ): Promise<VariantAvailabilityDTO> {
    return apiRequest<VariantAvailabilityDTO>(
      `/variants/${encodeURIComponent(variantId)}/availability`,
      { signal, skipAuthInvalidationHook: true }
    );
  },
};

export const cartApi = {
  getCart(signal?: AbortSignal): Promise<CartDTO> {
    return apiRequest<CartDTO>("/cart", { signal });
  },

  addItem(payload: AddCartItemRequest): Promise<CartDTO> {
    return apiRequest<CartDTO>("/cart/items", {
      method: "POST",
      body: payload,
    });
  },

  updateItem(itemId: string, payload: UpdateCartItemRequest): Promise<CartDTO> {
    return apiRequest<CartDTO>(`/cart/items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: payload,
    });
  },

  removeItem(itemId: string): Promise<CartDTO> {
    return apiRequest<CartDTO>(`/cart/items/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
    });
  },

  clearCart(): Promise<CartDTO> {
    return apiRequest<CartDTO>("/cart", {
      method: "DELETE",
    });
  },

  mergeCart(): Promise<CartDTO> {
    return apiRequest<CartDTO>("/cart/merge", {
      method: "POST",
    });
  },
};

function buildOrderHeaders(
  guestToken?: string,
  extra?: Record<string, string>
): Record<string, string> | undefined {
  const headers: Record<string, string> = { ...extra };
  const trimmedGuestToken = guestToken?.trim();
  if (trimmedGuestToken) {
    headers["X-Guest-Order-Token"] = trimmedGuestToken;
  }
  return Object.keys(headers).length > 0 ? headers : undefined;
}

export const checkoutApi = {
  createOrder(
    payload: CreateCheckoutOrderRequest,
    idempotencyKey: string
  ): Promise<CheckoutOrderResponseDTO> {
    return apiRequest<CheckoutOrderResponseDTO>("/checkout/orders", {
      method: "POST",
      body: payload,
      headers: {
        "Idempotency-Key": idempotencyKey,
      },
    });
  },
};

export const ordersApi = {
  listOrders(
    params: { page?: number; page_size?: number } = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<OrderListItemDTO>> {
    const qs = buildQueryString(params);
    return apiRequest<PaginatedResponse<OrderListItemDTO>>(`/orders${qs}`, {
      signal,
    });
  },

  getOrder(
    orderNumber: string,
    guestToken?: string,
    signal?: AbortSignal
  ): Promise<OrderDetailDTO> {
    return apiRequest<OrderDetailDTO>(
      `/orders/${encodeURIComponent(orderNumber)}`,
      {
        headers: buildOrderHeaders(guestToken),
        signal,
      }
    );
  },

  cancelOrder(
    orderNumber: string,
    payload?: CancelOrderRequest,
    guestToken?: string
  ): Promise<OrderDetailDTO> {
    return apiRequest<OrderDetailDTO>(
      `/orders/${encodeURIComponent(orderNumber)}/cancel`,
      {
        method: "POST",
        body: payload,
        headers: buildOrderHeaders(guestToken),
      }
    );
  },
};

export const paymentsApi = {
  createIntent(
    payload: CreatePaymentIntentRequest,
    idempotencyKey: string,
    guestToken?: string
  ): Promise<PaymentIntentResponse> {
    return apiRequest<PaymentIntentResponse>("/payments/intents", {
      method: "POST",
      body: payload,
      headers: buildOrderHeaders(guestToken, {
        "Idempotency-Key": idempotencyKey,
      }),
    });
  },

  simulateSandbox(
    providerIntentId: string,
    payload: SimulateSandboxPaymentRequest,
    guestToken?: string
  ): Promise<SimulateSandboxPaymentResponse> {
    return apiRequest<SimulateSandboxPaymentResponse>(
      `/payments/sandbox/${encodeURIComponent(providerIntentId)}/simulate`,
      {
        method: "POST",
        body: payload,
        headers: buildOrderHeaders(guestToken),
      }
    );
  },
};

/* --- Administrative APIs (BE-002 / BE-004 / FE-003) --- */

export const adminCatalogApi = {
  listCategories(signal?: AbortSignal): Promise<AdminCategoryListResponse> {
    return apiRequest<AdminCategoryListResponse>("/admin/categories", { signal });
  },

  createCategory(payload: CreateCategoryRequest): Promise<AdminCategoryDTO> {
    return apiRequest<AdminCategoryDTO>("/admin/categories", {
      method: "POST",
      body: payload,
    });
  },

  updateCategory(categoryId: string, payload: UpdateCategoryRequest): Promise<AdminCategoryDTO> {
    return apiRequest<AdminCategoryDTO>(
      `/admin/categories/${encodeURIComponent(categoryId)}`,
      {
        method: "PATCH",
        body: payload,
      }
    );
  },

  listProducts(
    params: {
      q?: string;
      status?: ProductStatus;
      category_id?: string;
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<AdminProductSummaryDTO>> {
    const qs = buildQueryString(params);
    return apiRequest<PaginatedResponse<AdminProductSummaryDTO>>(`/admin/products${qs}`, {
      signal,
    });
  },

  getProduct(productId: string, signal?: AbortSignal): Promise<AdminProductDetailDTO> {
    return apiRequest<AdminProductDetailDTO>(
      `/admin/products/${encodeURIComponent(productId)}`,
      { signal }
    );
  },

  createProduct(payload: CreateProductRequest): Promise<AdminProductDetailDTO> {
    return apiRequest<AdminProductDetailDTO>("/admin/products", {
      method: "POST",
      body: payload,
    });
  },

  updateProduct(
    productId: string,
    payload: UpdateProductRequest
  ): Promise<AdminProductDetailDTO> {
    return apiRequest<AdminProductDetailDTO>(
      `/admin/products/${encodeURIComponent(productId)}`,
      {
        method: "PATCH",
        body: payload,
      }
    );
  },

  archiveProduct(productId: string): Promise<AdminProductDetailDTO> {
    return apiRequest<AdminProductDetailDTO>(
      `/admin/products/${encodeURIComponent(productId)}`,
      {
        method: "DELETE",
      }
    );
  },

  createVariant(
    productId: string,
    payload: CreateVariantRequest
  ): Promise<AdminVariantDTO> {
    return apiRequest<AdminVariantDTO>(
      `/admin/products/${encodeURIComponent(productId)}/variants`,
      {
        method: "POST",
        body: payload,
      }
    );
  },

  updateVariant(
    variantId: string,
    payload: UpdateVariantRequest
  ): Promise<AdminVariantDTO> {
    return apiRequest<AdminVariantDTO>(
      `/admin/variants/${encodeURIComponent(variantId)}`,
      {
        method: "PATCH",
        body: payload,
      }
    );
  },
};

export const adminInventoryApi = {
  listInventory(
    params: {
      q?: string;
      stock_status?: StockStatusFilter;
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<AdminInventoryItemDTO>> {
    const qs = buildQueryString(params);
    return apiRequest<PaginatedResponse<AdminInventoryItemDTO>>(`/admin/inventory${qs}`, {
      signal,
    });
  },

  adjustStock(
    variantId: string,
    payload: StockAdjustmentRequest
  ): Promise<AdminInventoryItemDTO> {
    return apiRequest<AdminInventoryItemDTO>(
      `/admin/inventory/${encodeURIComponent(variantId)}/adjust`,
      {
        method: "POST",
        body: payload,
      }
    );
  },

  listLedger(
    variantId: string,
    params: { page?: number; page_size?: number } = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<InventoryLedgerEntryDTO>> {
    const qs = buildQueryString(params);
    return apiRequest<PaginatedResponse<InventoryLedgerEntryDTO>>(
      `/admin/inventory/${encodeURIComponent(variantId)}/ledger${qs}`,
      { signal }
    );
  },
};

export const adminOrdersApi = {
  listOrders(
    params: {
      status?: OrderStatus;
      payment_status?: PaymentStatus;
      fulfillment_status?: FulfillmentStatus;
      q?: string;
      from_ts?: string;
      to_ts?: string;
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<AdminOrderListItemDTO>> {
    const qs = buildQueryString(params);
    return apiRequest<PaginatedResponse<AdminOrderListItemDTO>>(`/admin/orders${qs}`, {
      signal,
    });
  },

  getOrder(orderNumber: string, signal?: AbortSignal): Promise<AdminOrderDetailDTO> {
    return apiRequest<AdminOrderDetailDTO>(
      `/admin/orders/${encodeURIComponent(orderNumber)}`,
      { signal }
    );
  },

  fulfillOrder(
    orderNumber: string,
    payload: FulfillOrderRequest
  ): Promise<AdminOrderDetailDTO> {
    return apiRequest<AdminOrderDetailDTO>(
      `/admin/orders/${encodeURIComponent(orderNumber)}/fulfill`,
      {
        method: "POST",
        body: payload,
      }
    );
  },

  cancelOrder(
    orderNumber: string,
    payload?: CancelOrderRequest
  ): Promise<AdminOrderDetailDTO> {
    return apiRequest<AdminOrderDetailDTO>(
      `/admin/orders/${encodeURIComponent(orderNumber)}/cancel`,
      {
        method: "POST",
        body: payload,
      }
    );
  },

  refundOrder(
    orderNumber: string,
    payload: RefundOrderRequest
  ): Promise<AdminOrderDetailDTO> {
    return apiRequest<AdminOrderDetailDTO>(
      `/admin/orders/${encodeURIComponent(orderNumber)}/refund`,
      {
        method: "POST",
        body: payload,
      }
    );
  },
};

export const adminUsersApi = {
  listUsers(
    params: {
      role?: UserRole;
      is_active?: boolean;
      q?: string;
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<AdminUserDTO>> {
    const qs = buildQueryString(params);
    return apiRequest<PaginatedResponse<AdminUserDTO>>(`/admin/users${qs}`, {
      signal,
    });
  },

  updateRole(
    userId: string,
    payload: UpdateUserRoleRequest
  ): Promise<AdminUserMutationResponse> {
    return apiRequest<AdminUserMutationResponse>(
      `/admin/users/${encodeURIComponent(userId)}/role`,
      {
        method: "PATCH",
        body: payload,
      }
    );
  },

  updateStatus(
    userId: string,
    payload: UpdateUserStatusRequest
  ): Promise<AdminUserMutationResponse> {
    return apiRequest<AdminUserMutationResponse>(
      `/admin/users/${encodeURIComponent(userId)}/status`,
      {
        method: "PATCH",
        body: payload,
      }
    );
  },
};

export const adminAuditApi = {
  listAuditLogs(
    params: {
      resource_type?: string;
      actor_user_id?: string;
      action?: string;
      from_ts?: string;
      to_ts?: string;
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal
  ): Promise<PaginatedResponse<AuditLogEntryDTO>> {
    const qs = buildQueryString(params);
    return apiRequest<PaginatedResponse<AuditLogEntryDTO>>(`/admin/audit-logs${qs}`, {
      signal,
    });
  },
};

export const adminAnalyticsApi = {
  getSummary(
    params: { from?: string; to?: string } = {},
    signal?: AbortSignal
  ): Promise<AnalyticsSummaryResponse> {
    const qs = buildQueryString(params);
    return apiRequest<AnalyticsSummaryResponse>(`/admin/analytics/summary${qs}`, {
      signal,
    });
  },

  getTimeseries(
    params: { from?: string; to?: string } = {},
    signal?: AbortSignal
  ): Promise<AnalyticsTimeseriesResponse> {
    const qs = buildQueryString(params);
    return apiRequest<AnalyticsTimeseriesResponse>(`/admin/analytics/timeseries${qs}`, {
      signal,
    });
  },

  getTopProducts(
    params: {
      from?: string;
      to?: string;
      sort_by?: TopProductSortBy;
      limit?: number;
    } = {},
    signal?: AbortSignal
  ): Promise<TopProductsResponse> {
    const qs = buildQueryString(params);
    return apiRequest<TopProductsResponse>(`/admin/analytics/top-products${qs}`, {
      signal,
    });
  },

  getInventoryHealth(
    params: { page?: number; page_size?: number } = {},
    signal?: AbortSignal
  ): Promise<InventoryHealthResponse> {
    const qs = buildQueryString(params);
    return apiRequest<InventoryHealthResponse>(`/admin/analytics/inventory-health${qs}`, {
      signal,
    });
  },
};
