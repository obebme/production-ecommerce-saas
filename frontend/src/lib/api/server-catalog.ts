import * as React from "react";
import type {
  CategoryListResponse,
  CategoryPublicDTO,
  ErrorEnvelope,
  PaginatedResponse,
  ProductDetailDTO,
  ProductListQuery,
  ProductSortOrder,
  ProductSummaryDTO,
} from "./types";

/**
 * Server-only catalog data fetchers and query parser for Next.js App Router SSR.
 * Do NOT import this module into `"use client"` components.
 */

const DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1";
const SERVER_FETCH_TIMEOUT_MS = 8_000;
const CONTROL_CHAR_RE = /[\u0000-\u001F\u007F]/;

export const CATALOG_DEFAULT_PAGE_SIZE = 20;

const VALID_SORTS = new Set<ProductSortOrder>([
  "newest",
  "price_asc",
  "price_desc",
  "title_asc",
]);

export interface ParsedCatalogSearchParams {
  category_slug: string | undefined;
  q: string | undefined;
  min_price_cents: number | undefined;
  max_price_cents: number | undefined;
  in_stock: boolean | undefined;
  sort: ProductSortOrder;
  page: number;
  queryKey: string;
}

export type ServerProductResult =
  | { status: "ok"; product: ProductDetailDTO }
  | { status: "not_found" }
  | { status: "error"; message: string };

function resolveServerApiBaseUrl(): string {
  const raw =
    process.env.INTERNAL_API_BASE_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    DEFAULT_API_BASE_URL;

  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed) {
    return DEFAULT_API_BASE_URL;
  }

  if (trimmed.startsWith("/")) {
    const isSafeRelativePath =
      !trimmed.startsWith("//") &&
      !trimmed.includes("\\") &&
      !CONTROL_CHAR_RE.test(trimmed);

    if (isSafeRelativePath) {
      return DEFAULT_API_BASE_URL;
    }
    throw new Error("Invalid server API base URL path.");
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error("Invalid server API base URL configuration.");
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("Server API base URL must use http:// or https://.");
  }

  if (trimmed.endsWith("/api/v1")) {
    return trimmed;
  }
  return `${trimmed}/api/v1`;
}

function getFirstString(value: string | string[] | undefined): string | null {
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value) && typeof value[0] === "string") {
    return value[0];
  }
  return null;
}

function parsePositiveInt(raw: string | null): number | undefined {
  if (!raw || !/^\d+$/.test(raw)) {
    return undefined;
  }
  const num = Number.parseInt(raw, 10);
  if (!Number.isSafeInteger(num) || num < 0 || num > 2_147_483_647) {
    return undefined;
  }
  return num;
}

export function buildCatalogQueryKey(params: {
  category_slug?: string;
  q?: string;
  min_price_cents?: number;
  max_price_cents?: number;
  in_stock?: boolean;
  sort: ProductSortOrder;
  page: number;
  page_size?: number;
}): string {
  const search = new URLSearchParams();
  if (params.category_slug) {
    search.set("category_slug", params.category_slug);
  }
  if (params.q) {
    search.set("q", params.q);
  }
  if (params.min_price_cents !== undefined) {
    search.set("min_price_cents", String(params.min_price_cents));
  }
  if (params.max_price_cents !== undefined) {
    search.set("max_price_cents", String(params.max_price_cents));
  }
  if (params.in_stock) {
    search.set("in_stock", "true");
  }
  search.set("sort", params.sort);
  search.set("page", String(params.page));
  search.set("page_size", String(params.page_size ?? CATALOG_DEFAULT_PAGE_SIZE));
  return search.toString();
}

export function parseCatalogSearchParams(
  raw: Record<string, string | string[] | undefined>
): ParsedCatalogSearchParams {
  const category_slug = getFirstString(raw.category_slug)?.trim() || undefined;
  const q = getFirstString(raw.q)?.trim().slice(0, 100) || undefined;
  const min_price_cents = parsePositiveInt(getFirstString(raw.min_price_cents));
  const max_price_cents = parsePositiveInt(getFirstString(raw.max_price_cents));
  const in_stock = getFirstString(raw.in_stock) === "true" ? true : undefined;

  const rawSort = getFirstString(raw.sort) as ProductSortOrder | null;
  const sort: ProductSortOrder = rawSort && VALID_SORTS.has(rawSort) ? rawSort : "newest";

  const parsedPage = parsePositiveInt(getFirstString(raw.page));
  const page = parsedPage && parsedPage >= 1 ? parsedPage : 1;

  const queryKey = buildCatalogQueryKey({
    category_slug,
    q,
    min_price_cents,
    max_price_cents,
    in_stock,
    sort,
    page,
    page_size: CATALOG_DEFAULT_PAGE_SIZE,
  });

  return {
    category_slug,
    q,
    min_price_cents,
    max_price_cents,
    in_stock,
    sort,
    page,
    queryKey,
  };
}

function buildProductQueryString(query: ProductListQuery): string {
  const search = new URLSearchParams();
  const entries: Record<string, string | number | boolean | undefined> = {
    category_slug: query.category_slug,
    q: query.q,
    min_price_cents: query.min_price_cents,
    max_price_cents: query.max_price_cents,
    in_stock: query.in_stock,
    sort: query.sort,
    page: query.page,
    page_size: query.page_size,
  };
  for (const [key, value] of Object.entries(entries)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

async function serverFetchWithTimeout(url: string): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), SERVER_FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, {
      method: "GET",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function fetchServerCategories(): Promise<CategoryPublicDTO[] | null> {
  try {
    const baseUrl = resolveServerApiBaseUrl();
    const res = await serverFetchWithTimeout(`${baseUrl}/categories`);
    if (!res.ok) {
      return null;
    }
    const data = (await res.json()) as Partial<CategoryListResponse>;
    return Array.isArray(data?.items) ? data.items : null;
  } catch {
    return null;
  }
}

export async function fetchServerProducts(
  query: ProductListQuery = {}
): Promise<PaginatedResponse<ProductSummaryDTO> | null> {
  try {
    const baseUrl = resolveServerApiBaseUrl();
    const qs = buildProductQueryString(query);
    const res = await serverFetchWithTimeout(`${baseUrl}/products${qs}`);
    if (!res.ok) {
      return null;
    }
    const data = (await res.json()) as Partial<PaginatedResponse<ProductSummaryDTO>>;
    if (!data || !Array.isArray(data.items)) {
      return null;
    }
    return data as PaginatedResponse<ProductSummaryDTO>;
  } catch {
    return null;
  }
}

async function fetchProductBySlugImpl(slug: string): Promise<ServerProductResult> {
  try {
    const baseUrl = resolveServerApiBaseUrl();
    const res = await serverFetchWithTimeout(`${baseUrl}/products/${encodeURIComponent(slug)}`);
    if (res.status === 404) {
      return { status: "not_found" };
    }
    if (!res.ok) {
      let message = res.statusText || "Unable to load product details.";
      try {
        const errBody = (await res.json()) as Partial<ErrorEnvelope>;
        if (errBody?.error?.message && typeof errBody.error.message === "string") {
          message = errBody.error.message;
        }
      } catch {
        // Ignore non-JSON error body
      }
      return { status: "error", message };
    }
    const product = (await res.json()) as ProductDetailDTO;
    return { status: "ok", product };
  } catch (err) {
    return {
      status: "error",
      message: err instanceof Error ? err.message : "Unable to load product details.",
    };
  }
}

const cacheWrapper: <Args extends unknown[], Result>(
  fn: (...args: Args) => Result
) => (...args: Args) => Result =
  typeof React.cache === "function" ? React.cache : (fn) => fn;

export const fetchServerProductBySlug = cacheWrapper(fetchProductBySlugImpl);
