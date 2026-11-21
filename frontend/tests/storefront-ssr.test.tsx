import "@testing-library/jest-dom/vitest";
import fs from "node:fs";
import path from "node:path";
import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/(storefront)/page";
import ProductsPage from "@/app/(storefront)/products/page";
import ProductDetailPage, { generateMetadata } from "@/app/(storefront)/products/[slug]/page";
import { metadata as rootMetadata } from "@/app/layout";
import { catalogApi } from "@/lib/api/client";
import { resolveSiteUrl } from "@/lib/site-url";
import {
  fetchServerCategories,
  fetchServerProductBySlug,
  fetchServerProducts,
  parseCatalogSearchParams,
} from "@/lib/api/server-catalog";
import type {
  CategoryPublicDTO,
  PaginatedResponse,
  ProductDetailDTO,
  ProductSummaryDTO,
} from "@/lib/api/types";
import { ProductImage } from "@/components/shared/product-image";
import { ThemeProvider } from "@/components/shared/theme-provider";
import { CatalogView } from "@/components/storefront/catalog-view";
import { HomeView } from "@/components/storefront/home-view";
import { ToastProvider } from "@/components/ui/toast";
import { AuthProvider } from "@/providers/auth-provider";
import { CartProvider } from "@/providers/cart-provider";

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

let mockPathname = "/products";
let mockSearchParams = new URLSearchParams();
const searchParamListeners = new Set<() => void>();

function setMockSearchParams(next: URLSearchParams) {
  mockSearchParams = next;
  for (const listener of searchParamListeners) {
    listener();
  }
}

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => mockRouter,
  useSearchParams: () =>
    React.useSyncExternalStore(
      (listener) => {
        searchParamListeners.add(listener);
        return () => searchParamListeners.delete(listener);
      },
      () => mockSearchParams,
      () => mockSearchParams
    ),
  notFound: () => mockNotFound(),
}));

const SEEDED_PRODUCT_SLUGS = [
  "linen-overshirt",
  "merino-crewneck",
  "leather-weekender",
  "canvas-tote",
  "stoneware-mug-set",
  "walnut-serving-board",
  "ceramic-table-lamp",
  "wool-throw-blanket",
] as const;

function jsonResponse(body: unknown, status = 200, statusText = "OK"): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
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
        request_id: "req-ssr-test",
      },
    },
    status,
    status === 404 ? "Not Found" : "Internal Server Error"
  );
}

function makeCategories(): CategoryPublicDTO[] {
  return [
    {
      id: "cat-apparel",
      parent_id: null,
      name: "Apparel",
      slug: "apparel",
      description: "Tailored natural fiber garments.",
      sort_order: 1,
    },
    {
      id: "cat-accessories",
      parent_id: null,
      name: "Accessories",
      slug: "accessories",
      description: "Bridle leather and waxed canvas carryalls.",
      sort_order: 2,
    },
    {
      id: "cat-home",
      parent_id: null,
      name: "Home",
      slug: "home",
      description: "Stoneware, timber, and textiles.",
      sort_order: 3,
    },
    {
      id: "cat-lighting",
      parent_id: null,
      name: "Lighting",
      slug: "lighting",
      description: "Ceramic table lamps.",
      sort_order: 4,
    },
    {
      id: "cat-overflow",
      parent_id: null,
      name: "Archive",
      slug: "archive",
      description: "Should be sliced off on HomePage.",
      sort_order: 5,
    },
  ];
}

function makeProductSummary(overrides: Partial<ProductSummaryDTO> = {}): ProductSummaryDTO {
  return {
    id: "prod-linen",
    title: "Atelier Linen Overshirt",
    slug: "linen-overshirt",
    category: { id: "cat-apparel", name: "Apparel", slug: "apparel" },
    primary_image: {
      id: "img-linen",
      url: "/assets/products/linen-overshirt.png",
      alt_text: "Atelier Linen Overshirt in undyed sand",
      sort_order: 0,
      is_primary: true,
    },
    min_price_cents: 14500,
    max_price_cents: 14500,
    currency: "USD",
    in_stock: true,
    ...overrides,
  };
}

function makeProductDetail(overrides: Partial<ProductDetailDTO> = {}): ProductDetailDTO {
  return {
    id: "prod-linen",
    title: "Atelier Linen Overshirt",
    slug: "linen-overshirt",
    description: "Midweight washed Belgian linen overshirt with corozo buttons.",
    status: "ACTIVE",
    category: { id: "cat-apparel", name: "Apparel", slug: "apparel" },
    images: [
      {
        id: "img-secondary",
        url: "/assets/products/merino-crewneck.png",
        alt_text: "Secondary detail shot",
        sort_order: 0,
        is_primary: false,
      },
      {
        id: "img-primary",
        url: "/assets/products/linen-overshirt.png",
        alt_text: "Atelier Linen Overshirt primary image",
        sort_order: 1,
        is_primary: true,
      },
    ],
    variants: [
      {
        id: "var-linen-m",
        sku: "ATL-LINEN-SND-M",
        title: "Sand / Medium",
        attributes: { color: "Sand", size: "M" },
        price_cents: 14500,
        compare_at_price_cents: 16500,
        currency: "USD",
        is_active: true,
        available_quantity: 24,
        in_stock: true,
        low_stock: false,
      },
      {
        id: "var-linen-l",
        sku: "ATL-LINEN-SND-L",
        title: "Sand / Large",
        attributes: { color: "Sand", size: "L" },
        price_cents: 14500,
        compare_at_price_cents: 16500,
        currency: "USD",
        is_active: true,
        available_quantity: 3,
        in_stock: true,
        low_stock: true,
      },
    ],
    ...overrides,
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

describe("REM-P2-M09: Storefront SSR, Metadata, 404 & Asset Integrity", () => {
  const originalEnv = { ...process.env };
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.INTERNAL_API_BASE_URL;
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    delete process.env.NEXT_PUBLIC_SITE_URL;
    delete process.env.NEXT_PUBLIC_APP_ENV;
    delete process.env.APP_ENV;
    delete process.env.ENVIRONMENT;
    mockPathname = "/products";
    mockSearchParams = new URLSearchParams();
    mockNotFound.mockClear();
    Object.values(mockRouter).forEach((fn) => fn.mockReset());

    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    process.env = { ...originalEnv };
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  describe("server-catalog.ts URL resolution & error handling", () => {
    it("uses INTERNAL_API_BASE_URL when set (taking precedence over NEXT_PUBLIC_API_BASE_URL) and normalizes /api/v1", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000";
      process.env.NEXT_PUBLIC_API_BASE_URL = "https://public.example.com/api/v1";

      fetchMock
        .mockResolvedValueOnce(jsonResponse({ items: makeCategories() }))
        .mockResolvedValueOnce(
          jsonResponse({ items: [makeProductSummary()], total: 1, page: 1, page_size: 20, has_next: false })
        );

      const categories = await fetchServerCategories();
      const products = await fetchServerProducts({ q: "linen", page: 1 });

      expect(categories).toHaveLength(5);
      expect(products?.total).toBe(1);
      expect(String(fetchMock.mock.calls[0][0])).toBe("http://127.0.0.1:8000/api/v1/categories");
      expect(String(fetchMock.mock.calls[1][0])).toBe("http://127.0.0.1:8000/api/v1/products?q=linen&page=1");
    });

    it("falls back cleanly to localhost:8000/api/v1 when given a relative /api/v1 path", async () => {
      process.env.INTERNAL_API_BASE_URL = "/api/v1";
      fetchMock.mockResolvedValueOnce(jsonResponse({ items: [] }));

      const categories = await fetchServerCategories();

      expect(categories).toEqual([]);
      expect(String(fetchMock.mock.calls[0][0])).toBe("http://localhost:8000/api/v1/categories");
    });

    it("rejects unsafe non-HTTP(S) URLs and malformed relative paths without issuing fetch", async () => {
      for (const unsafeUrl of [
        "javascript:alert(1)",
        "ftp://example.com/api/v1",
        "file:///etc/passwd",
        "//evil.example.com/api/v1",
        "/api/v1\nHeader: inject",
        "/api\\backslash",
      ]) {
        process.env.INTERNAL_API_BASE_URL = unsafeUrl;

        const catRes = await fetchServerCategories();
        const prodRes = await fetchServerProducts();
        const detailRes = await fetchServerProductBySlug("linen-overshirt");

        expect(catRes).toBeNull();
        expect(prodRes).toBeNull();
        expect(detailRes.status).toBe("error");
      }

      expect(fetchMock).not.toHaveBeenCalled();
    });

    it("distinguishes HTTP 404 (not_found) vs 5xx and network errors (error)", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

      fetchMock.mockResolvedValueOnce(errorResponse(404, "PRODUCT_NOT_FOUND", "Product not found."));
      const notFoundRes = await fetchServerProductBySlug("missing-item");
      expect(notFoundRes).toEqual({ status: "not_found" });

      fetchMock.mockResolvedValueOnce(errorResponse(503, "SERVICE_UNAVAILABLE", "Database temporarily down."));
      const serverErrRes = await fetchServerProductBySlug("linen-overshirt");
      expect(serverErrRes).toEqual({ status: "error", message: "Database temporarily down." });

      fetchMock.mockRejectedValueOnce(new Error("connect ECONNREFUSED 127.0.0.1:8000"));
      const networkErrRes = await fetchServerProductBySlug("linen-overshirt");
      expect(networkErrRes).toEqual({
        status: "error",
        message: "Unable to load product details.",
      });
      if (networkErrRes.status === "error") {
        expect(networkErrRes.message).not.toContain("ECONNREFUSED");
        expect(networkErrRes.message).not.toContain("127.0.0.1:8000");
      }

      fetchMock.mockRejectedValueOnce(new DOMException("The operation was aborted.", "AbortError"));
      const abortErrRes = await fetchServerProductBySlug("linen-overshirt");
      expect(abortErrRes).toEqual({
        status: "error",
        message: "Product request timed out.",
      });
    });
  });

  describe("HomePage SSR rendering", () => {
    it("renders server-fetched categories (top 4) and products without calling client catalogApi.listProducts", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";
      const categories = makeCategories();
      const products: PaginatedResponse<ProductSummaryDTO> = {
        items: [
          makeProductSummary(),
          makeProductSummary({
            id: "prod-tote",
            title: "Waxed Canvas Studio Tote",
            slug: "canvas-tote",
            min_price_cents: 9500,
            max_price_cents: 9500,
          }),
        ],
        total: 2,
        page: 1,
        page_size: 8,
        has_next: false,
      };

      fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/categories")) {
          return jsonResponse({ items: categories });
        }
        if (url.includes("/products")) {
          return jsonResponse(products);
        }
        return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
      });

      const listProductsSpy = vi.spyOn(catalogApi, "listProducts");
      const listCategoriesSpy = vi.spyOn(catalogApi, "listCategories");

      const element = await HomePage();
      render(element);

      expect(screen.getByText("Atelier Linen Overshirt")).toBeInTheDocument();
      expect(screen.getByText("Waxed Canvas Studio Tote")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Apparel" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Lighting" })).toBeInTheDocument();
      // HomePage slices categories to 4 items
      expect(screen.queryByRole("heading", { name: "Archive" })).not.toBeInTheDocument();

      expect(listProductsSpy).not.toHaveBeenCalled();
      expect(listCategoriesSpy).not.toHaveBeenCalled();
    });

    it("HomeView Featured Collection filter pills fetch via catalogApi.listProducts and restore SSR products on All", async () => {
      const categories = makeCategories().slice(0, 4);
      const initialProducts = [
        makeProductSummary(),
        makeProductSummary({
          id: "prod-tote",
          title: "Waxed Canvas Studio Tote",
          slug: "canvas-tote",
          category: { id: "cat-accessories", name: "Accessories", slug: "accessories" },
          min_price_cents: 9500,
          max_price_cents: 9500,
        }),
      ];
      const apparelOnly = [makeProductSummary()];
      const inStockOnly = [initialProducts[1]];

      const listProductsSpy = vi
        .spyOn(catalogApi, "listProducts")
        .mockResolvedValueOnce({
          items: apparelOnly,
          total: 1,
          page: 1,
          page_size: 8,
          has_next: false,
        })
        .mockResolvedValueOnce({
          items: inStockOnly,
          total: 1,
          page: 1,
          page_size: 8,
          has_next: false,
        });

      render(<HomeView initialCategories={categories} initialProducts={initialProducts} />);

      expect(listProductsSpy).not.toHaveBeenCalled();
      expect(screen.getByText("Atelier Linen Overshirt")).toBeInTheDocument();
      expect(screen.getByText("Waxed Canvas Studio Tote")).toBeInTheDocument();

      const filterGroup = screen.getByRole("group", { name: "Filter featured collection" });
      const allBtn = filterGroup.querySelector("button");
      expect(allBtn).toHaveAttribute("aria-pressed", "true");

      // Click category pill "Apparel" -> calls catalogApi.listProducts with category_slug
      fireEvent.click(screen.getByRole("button", { name: "Apparel" }));
      await waitFor(() => {
        expect(listProductsSpy).toHaveBeenCalledTimes(1);
      });
      expect(listProductsSpy).toHaveBeenNthCalledWith(
        1,
        { category_slug: "apparel", sort: "newest", page: 1, page_size: 8 },
        expect.any(AbortSignal)
      );
      await waitFor(() => {
        expect(screen.queryByText("Waxed Canvas Studio Tote")).not.toBeInTheDocument();
      });
      expect(screen.getByText("Atelier Linen Overshirt")).toBeInTheDocument();

      // Click "In Stock Only" -> calls catalogApi.listProducts with in_stock: true
      fireEvent.click(screen.getByRole("button", { name: "In Stock Only" }));
      await waitFor(() => {
        expect(listProductsSpy).toHaveBeenCalledTimes(2);
      });
      expect(listProductsSpy).toHaveBeenNthCalledWith(
        2,
        { in_stock: true, sort: "newest", page: 1, page_size: 8 },
        expect.any(AbortSignal)
      );
      await waitFor(() => {
        expect(screen.getByText("Waxed Canvas Studio Tote")).toBeInTheDocument();
      });
      expect(screen.queryByText("Atelier Linen Overshirt")).not.toBeInTheDocument();

      // Click "All" -> restores initial SSR products from cache without another fetch
      fireEvent.click(screen.getByRole("button", { name: "All" }));
      expect(listProductsSpy).toHaveBeenCalledTimes(2);
      expect(screen.getByText("Atelier Linen Overshirt")).toBeInTheDocument();
      expect(screen.getByText("Waxed Canvas Studio Tote")).toBeInTheDocument();
    });
  });

  describe("ProductsPage SSR & interactive filtering", () => {
    it("parses searchParams, renders SSR products without duplicate mount fetch, and fetches when parameters change", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";
      const rawParams = {
        q: "linen",
        category_slug: "apparel",
        in_stock: "true",
        sort: "price_asc",
        page: "2",
      };
      const parsed = parseCatalogSearchParams(rawParams);
      expect(parsed).toMatchObject({
        q: "linen",
        category_slug: "apparel",
        in_stock: true,
        sort: "price_asc",
        page: 2,
      });

      const ssrResults: PaginatedResponse<ProductSummaryDTO> = {
        items: [makeProductSummary()],
        total: 21,
        page: 2,
        page_size: 20,
        has_next: false,
      };

      fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/categories")) {
          return jsonResponse({ items: makeCategories().slice(0, 3) });
        }
        if (url.includes("/products")) {
          return jsonResponse(ssrResults);
        }
        return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
      });

      const element = await ProductsPage({
        searchParams: Promise.resolve(rawParams),
      });

      const serverProductFetchUrl = String(
        fetchMock.mock.calls.find(([u]) => String(u).includes("/products"))?.[0] ?? ""
      );
      expect(serverProductFetchUrl).toContain("category_slug=apparel");
      expect(serverProductFetchUrl).toContain("q=linen");
      expect(serverProductFetchUrl).toContain("in_stock=true");
      expect(serverProductFetchUrl).toContain("sort=price_asc");
      expect(serverProductFetchUrl).toContain("page=2");

      // Match browser URL query params so CatalogView recognizes hydrated SSR state
      mockSearchParams = new URLSearchParams(
        "q=linen&category_slug=apparel&in_stock=true&sort=price_asc&page=2"
      );

      const listProductsSpy = vi.spyOn(catalogApi, "listProducts");
      const listCategoriesSpy = vi.spyOn(catalogApi, "listCategories");

      const { unmount } = render(element);

      expect(screen.getByText("Atelier Linen Overshirt")).toBeInTheDocument();
      expect(screen.getAllByText("Showing 21–21 of 21 products").length).toBeGreaterThanOrEqual(1);
      expect(listProductsSpy).not.toHaveBeenCalled();
      expect(listCategoriesSpy).not.toHaveBeenCalled();

      // Interactive filter action pushes updated URL params
      const inStockSwitch = screen.getByRole("switch", { name: "In stock only" });
      fireEvent.click(inStockSwitch);
      expect(mockRouter.push).toHaveBeenCalledWith(
        "/products?q=linen&category_slug=apparel&sort=price_asc"
      );

      // Simulate Next.js URL param update on client navigation -> triggers client catalogApi.listProducts
      const updatedResults: PaginatedResponse<ProductSummaryDTO> = {
        items: [
          makeProductSummary({
            id: "prod-merino",
            title: "Fine Merino Crewneck",
            slug: "merino-crewneck",
            min_price_cents: 12000,
            max_price_cents: 12000,
          }),
        ],
        total: 1,
        page: 1,
        page_size: 20,
        has_next: false,
      };
      listProductsSpy.mockResolvedValueOnce(updatedResults);
      act(() => {
        setMockSearchParams(new URLSearchParams("q=linen&category_slug=apparel&sort=price_asc"));
      });

      await waitFor(() => {
        expect(listProductsSpy).toHaveBeenCalledTimes(1);
      });
      expect(listProductsSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "linen",
          category_slug: "apparel",
          in_stock: undefined,
          sort: "price_asc",
          page: 1,
          page_size: 20,
        }),
        expect.any(AbortSignal)
      );
      expect(await screen.findByText("Fine Merino Crewneck")).toBeInTheDocument();
      unmount();
    });
  });

  describe("ProductDetailPage & generateMetadata", () => {
    it("generateMetadata returns dynamic title, description, and primary openGraph image", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";
      const detail = makeProductDetail();
      fetchMock.mockResolvedValueOnce(jsonResponse(detail));

      const metadata = await generateMetadata({
        params: Promise.resolve({ slug: "linen-overshirt" }),
      });

      expect(metadata.title).toBe("Atelier Linen Overshirt");
      expect(metadata.description).toBe(
        "Midweight washed Belgian linen overshirt with corozo buttons."
      );
      expect(metadata.openGraph).toMatchObject({
        title: "Atelier Linen Overshirt",
        description: "Midweight washed Belgian linen overshirt with corozo buttons.",
        images: [
          {
            url: "/assets/products/linen-overshirt.png",
            alt: "Atelier Linen Overshirt primary image",
          },
        ],
      });
    });

    it("generateMetadata handles invalid slugs, 404s, and 5xx fallbacks cleanly", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

      const invalidMeta = await generateMetadata({
        params: Promise.resolve({ slug: "../bad" }),
      });
      expect(invalidMeta.title).toBe("Product Not Found");
      expect(fetchMock).not.toHaveBeenCalled();

      fetchMock.mockResolvedValueOnce(errorResponse(404, "PRODUCT_NOT_FOUND", "Missing"));
      const notFoundMeta = await generateMetadata({
        params: Promise.resolve({ slug: "nonexistent-slug" }),
      });
      expect(notFoundMeta.title).toBe("Product Not Found");

      fetchMock.mockResolvedValueOnce(errorResponse(500, "INTERNAL_ERROR", "Server down"));
      const errorMeta = await generateMetadata({
        params: Promise.resolve({ slug: "linen-overshirt" }),
      });
      expect(errorMeta.title).toBe("Product Details");
    });

    it("renders server-fetched ProductDetailDTO without duplicate initial catalogApi.getProductBySlug call", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";
      const detail = makeProductDetail();

      fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/products/linen-overshirt")) {
          return jsonResponse(detail);
        }
        if (url.endsWith("/auth/me")) {
          return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
        }
        if (url.endsWith("/cart")) {
          return jsonResponse({
            id: "cart-ssr",
            currency: "USD",
            items: [],
            item_count: 0,
            subtotal_cents: 0,
            estimated_shipping_cents: 0,
            estimated_tax_cents: 0,
            estimated_total_cents: 0,
          });
        }
        return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
      });

      const getBySlugSpy = vi.spyOn(catalogApi, "getProductBySlug");

      const element = await ProductDetailPage({
        params: Promise.resolve({ slug: "linen-overshirt" }),
      });
      renderWithAppProviders(element);

      expect(screen.getByRole("heading", { name: "Atelier Linen Overshirt" })).toBeInTheDocument();
      expect(screen.getByText("SKU: ATL-LINEN-SND-M")).toBeInTheDocument();
      expect(screen.getByText("$145.00")).toBeInTheDocument();
      expect(getBySlugSpy).not.toHaveBeenCalled();
      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some(([url]) => String(url).endsWith("/cart"))
        ).toBe(true);
      });
    });

    it("invokes Next.js notFound() on invalid slug syntax and backend 404", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

      for (const badSlug of ["%ZZ", "../admin", "invalid slug!"]) {
        await expect(
          ProductDetailPage({ params: Promise.resolve({ slug: badSlug }) })
        ).rejects.toThrow("NEXT_NOT_FOUND");
      }
      expect(mockNotFound).toHaveBeenCalledTimes(3);
      expect(fetchMock).not.toHaveBeenCalled();

      fetchMock.mockResolvedValueOnce(errorResponse(404, "PRODUCT_NOT_FOUND", "Product missing"));
      await expect(
        ProductDetailPage({ params: Promise.resolve({ slug: "nonexistent-product-slug-404" }) })
      ).rejects.toThrow("NEXT_NOT_FOUND");
      expect(mockNotFound).toHaveBeenCalledTimes(4);
    });

    it("renders ErrorState via initialError on backend 5xx without invoking notFound()", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";
      fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/products/linen-overshirt")) {
          return errorResponse(503, "SERVICE_UNAVAILABLE", "Catalog backend unavailable.");
        }
        if (url.endsWith("/auth/me")) {
          return errorResponse(401, "AUTH_REQUIRED", "Authentication required.");
        }
        if (url.endsWith("/cart")) {
          return jsonResponse({
            id: "cart-ssr",
            currency: "USD",
            items: [],
            item_count: 0,
            subtotal_cents: 0,
            estimated_shipping_cents: 0,
            estimated_tax_cents: 0,
            estimated_total_cents: 0,
          });
        }
        return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
      });

      const element = await ProductDetailPage({
        params: Promise.resolve({ slug: "linen-overshirt" }),
      });
      renderWithAppProviders(element);

      expect(mockNotFound).not.toHaveBeenCalled();
      expect(screen.getByText("Unable to load product")).toBeInTheDocument();
      expect(screen.getByText("Catalog backend unavailable.")).toBeInTheDocument();
      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some(([url]) => String(url).endsWith("/cart"))
        ).toBe(true);
      });
    });

    it("SEC-M09-01: generateMetadata emits https:// or /assets/products/ openGraph.images, omits unsafe URLs, and normalizes mixed-case slugs", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

      // 1. Valid https:// image URL + mixed-case slug normalization
      const httpsProduct = makeProductDetail({
        images: [
          {
            id: "img-https",
            url: "https://cdn.example.com/products/linen-overshirt.png",
            alt_text: "Linen Overshirt CDN",
            sort_order: 0,
            is_primary: true,
          },
        ],
      });
      fetchMock.mockResolvedValueOnce(jsonResponse(httpsProduct));

      const mixedCaseMeta = await generateMetadata({
        params: Promise.resolve({ slug: "Linen-Overshirt" }),
      });
      expect(String(fetchMock.mock.calls[0][0])).toBe(
        "http://127.0.0.1:8000/api/v1/products/linen-overshirt"
      );
      expect(mixedCaseMeta.openGraph).toMatchObject({
        images: [
          {
            url: "https://cdn.example.com/products/linen-overshirt.png",
            alt: "Linen Overshirt CDN",
          },
        ],
      });

      // 2. Unsafe image schemes / relative traversal URLs are omitted from openGraph.images
      for (const unsafeUrl of [
        "http://cdn.example.com/insecure.png",
        "javascript:alert(1)",
        "../secret.png",
      ]) {
        const unsafeProduct = makeProductDetail({
          images: [
            {
              id: "img-unsafe",
              url: unsafeUrl,
              alt_text: "Unsafe image",
              sort_order: 0,
              is_primary: true,
            },
          ],
        });
        fetchMock.mockResolvedValueOnce(jsonResponse(unsafeProduct));
        const meta = await generateMetadata({
          params: Promise.resolve({ slug: "linen-overshirt" }),
        });
        expect(meta.openGraph?.images).toBeUndefined();
      }
    });
  });

  describe("resolveSiteUrl & RootLayout metadataBase", () => {
    it("defaults to http://localhost:3000 in local dev and exposes RootLayout metadata.metadataBase and Vellora Store title template", () => {
      const localUrl = resolveSiteUrl();
      expect(localUrl.origin).toBe("http://localhost:3000");
      expect(rootMetadata.metadataBase).toBeInstanceOf(URL);
      expect(rootMetadata.metadataBase?.origin).toBe("http://localhost:3000");
      expect(rootMetadata.title).toEqual({
        default: "Vellora Store — Considered Everyday Goods",
        template: "%s | Vellora Store",
      });
    });

    it("rejects unset, http://, and loopback URLs in staging/production and normalizes https:// origin", () => {
      for (const envName of ["staging", "production"] as const) {
        process.env.ENVIRONMENT = envName;
        delete process.env.NEXT_PUBLIC_SITE_URL;

        expect(() => resolveSiteUrl()).toThrow(/NEXT_PUBLIC_SITE_URL/);
        expect(() => resolveSiteUrl("http://shop.example.com")).toThrow(/https:\/\//);
        expect(() => resolveSiteUrl("https://localhost:3000")).toThrow(/loopback|wildcard/);
        expect(() => resolveSiteUrl("https://127.0.0.1:3000")).toThrow(/loopback|wildcard/);

        process.env.NEXT_PUBLIC_SITE_URL = "https://shop.example.com/path";
        const normalized = resolveSiteUrl();
        expect(normalized.origin).toBe("https://shop.example.com");
        expect(normalized.href).toBe("https://shop.example.com/");
      }
    });
  });

  describe("A11Y-M09-01: Storefront heading hierarchy", () => {
    it("renders CatalogView product card titles as <h2> without skipped h1 -> h3 and renders HomeView guarantees <h2> above <h3>", async () => {
      process.env.INTERNAL_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

      fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/categories")) {
          return jsonResponse({ items: makeCategories() });
        }
        if (url.includes("/products")) {
          return jsonResponse({
            items: [makeProductSummary()],
            total: 1,
            page: 1,
            page_size: 20,
            has_next: false,
          });
        }
        return errorResponse(404, "NOT_FOUND", `Unexpected ${url}`);
      });

      const catalogEl = await ProductsPage({
        searchParams: Promise.resolve({}),
      });
      const { container: catalogContainer, unmount: unmountCatalog } = render(catalogEl);

      const catalogH1 = screen.getByRole("heading", { level: 1, name: "Collection" });
      expect(catalogH1).toBeInTheDocument();
      const cardHeading = screen.getByRole("heading", {
        level: 2,
        name: "Atelier Linen Overshirt",
      });
      expect(cardHeading.tagName).toBe("H2");
      expect(catalogContainer.querySelectorAll("h3")).toHaveLength(0);
      unmountCatalog();

      const homeEl = await HomePage();
      const { container: homeContainer, unmount: unmountHome } = render(homeEl);
      const guaranteesHeading = homeContainer.querySelector("h2#home-guarantees-heading");
      expect(guaranteesHeading).not.toBeNull();
      expect(guaranteesHeading).toHaveTextContent("Storefront guarantees");
      expect(
        screen.getByRole("heading", { level: 3, name: "Free Shipping Over $100" })
      ).toBeInTheDocument();
      unmountHome();
    });
  });

  describe("Seeded product image assets on disk", () => {
    it("verifies hero-showcase.png and all 8 seeded PNG files exist and begin with valid PNG signature bytes", () => {
      const assetsDir = path.resolve(__dirname, "..", "public", "assets", "products");
      const pngSignature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

      for (const assetName of ["hero-showcase", ...SEEDED_PRODUCT_SLUGS]) {
        const filePath = path.join(assetsDir, `${assetName}.png`);
        expect(fs.existsSync(filePath), `Missing product image: ${filePath}`).toBe(true);
        const bytes = fs.readFileSync(filePath);
        expect(bytes.length).toBeGreaterThan(100);
        expect(Array.from(bytes.subarray(0, 8))).toEqual(pngSignature);
      }
    });
  });

  describe("FE-4C-UI: Honest trust strip, 2-col mobile grid & eager ProductImage", () => {
    it("renders honest commerce trust strip items in HomeView and omits fabricated claims", () => {
      render(
        <HomeView
          initialCategories={makeCategories().slice(0, 4)}
          initialProducts={[makeProductSummary()]}
        />
      );

      expect(
        screen.getByRole("heading", { level: 3, name: "Live Per-SKU Inventory" })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { level: 3, name: "15-Minute Checkout Hold" })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { level: 3, name: "Self-Service Cancellation" })
      ).toBeInTheDocument();

      expect(screen.queryByText(/24-Month Quality Guarantee/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/14-Day Easy Returns/i)).not.toBeInTheDocument();
    });

    it("renders CatalogView product grid with grid-cols-2 responsive class and ProductImage with eager loading when priority is false", () => {
      const initialResults: PaginatedResponse<ProductSummaryDTO> = {
        items: [makeProductSummary()],
        total: 1,
        page: 1,
        page_size: 20,
        has_next: false,
      };

      const { container, unmount } = render(
        <CatalogView
          initialCategories={makeCategories().slice(0, 3)}
          initialResults={initialResults}
          initialQueryKey="sort=newest&page=1&page_size=20"
        />
      );

      const cardLink = screen.getByRole("link", { name: /Atelier Linen Overshirt/i });
      const gridContainer = cardLink.closest("div.grid");
      expect(gridContainer).not.toBeNull();
      expect(gridContainer).toHaveClass("grid-cols-2");

      const catalogImg = container.querySelector("img");
      expect(catalogImg).not.toBeNull();
      expect(catalogImg).toHaveAttribute("loading", "eager");
      unmount();

      render(
        <ProductImage
          src="/assets/products/linen-overshirt.png"
          alt="Direct ProductImage eager default"
        />
      );
      const directImg = screen.getByRole("img", { name: "Direct ProductImage eager default" });
      expect(directImg).toHaveAttribute("loading", "eager");
    });

    it("renders HomeView Popular Categories with #home-categories-rail and accessible scroll controls", () => {
      render(
        <HomeView
          initialCategories={makeCategories().slice(0, 4)}
          initialProducts={[makeProductSummary()]}
        />
      );

      const rail = screen.getByRole("region", { name: "Popular categories" });
      expect(rail).toHaveAttribute("id", "home-categories-rail");
      expect(rail).toHaveClass("snap-x", "overflow-x-auto", "md:grid-cols-3");

      const scrollLeftBtn = screen.getByRole("button", { name: "Scroll categories left" });
      const scrollRightBtn = screen.getByRole("button", { name: "Scroll categories right" });
      expect(scrollLeftBtn).toHaveAttribute("aria-controls", "home-categories-rail");
      expect(scrollRightBtn).toHaveAttribute("aria-controls", "home-categories-rail");
    });
  });
});
