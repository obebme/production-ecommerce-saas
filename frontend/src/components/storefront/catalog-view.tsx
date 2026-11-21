"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { SlidersHorizontal, X } from "lucide-react";
import { catalogApi } from "@/lib/api/client";
import type {
  CategoryPublicDTO,
  PaginatedResponse,
  ProductSortOrder,
  ProductSummaryDTO,
} from "@/lib/api/types";
import { formatCents, formatCentsForInput, parseDollarsToCents } from "@/lib/format";
import { EmptyState, ErrorState } from "@/components/shared/state-feedback";
import { ProductCard, ProductCardSkeleton } from "@/components/storefront/product-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";

const DEFAULT_PAGE_SIZE = 20;
const VALID_SORTS = new Set<ProductSortOrder>([
  "newest",
  "price_asc",
  "price_desc",
  "title_asc",
]);

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

export interface CatalogViewProps {
  initialCategories?: CategoryPublicDTO[] | null;
  initialResults?: PaginatedResponse<ProductSummaryDTO> | null;
  initialQueryKey?: string;
}

export function CatalogView({
  initialCategories,
  initialResults,
  initialQueryKey,
}: CatalogViewProps = {}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const gridTopRef = React.useRef<HTMLDivElement>(null);

  // Parsed URL state (single source of truth)
  const categorySlug = searchParams.get("category_slug")?.trim() || undefined;
  const searchQuery = searchParams.get("q")?.trim().slice(0, 100) || undefined;
  const minPriceCents = parsePositiveInt(searchParams.get("min_price_cents"));
  const maxPriceCents = parsePositiveInt(searchParams.get("max_price_cents"));
  const inStockOnly = searchParams.get("in_stock") === "true";
  const rawSort = searchParams.get("sort") as ProductSortOrder | null;
  const sort: ProductSortOrder = rawSort && VALID_SORTS.has(rawSort) ? rawSort : "newest";
  const parsedPage = parsePositiveInt(searchParams.get("page"));
  const page = parsedPage && parsedPage >= 1 ? parsedPage : 1;

  const currentQueryKey = React.useMemo(() => {
    const keyParams = new URLSearchParams();
    if (categorySlug) {
      keyParams.set("category_slug", categorySlug);
    }
    if (searchQuery) {
      keyParams.set("q", searchQuery);
    }
    if (minPriceCents !== undefined) {
      keyParams.set("min_price_cents", String(minPriceCents));
    }
    if (maxPriceCents !== undefined) {
      keyParams.set("max_price_cents", String(maxPriceCents));
    }
    if (inStockOnly) {
      keyParams.set("in_stock", "true");
    }
    keyParams.set("sort", sort);
    keyParams.set("page", String(page));
    keyParams.set("page_size", String(DEFAULT_PAGE_SIZE));
    return keyParams.toString();
  }, [categorySlug, searchQuery, minPriceCents, maxPriceCents, inStockOnly, sort, page]);

  const hasInitialCategories = initialCategories !== undefined && initialCategories !== null;
  const matchesInitialQuery =
    initialResults !== undefined &&
    initialResults !== null &&
    initialQueryKey !== undefined &&
    currentQueryKey === initialQueryKey;

  const [categories, setCategories] = React.useState<CategoryPublicDTO[]>(() =>
    hasInitialCategories ? initialCategories : []
  );
  const [results, setResults] = React.useState<PaginatedResponse<ProductSummaryDTO> | null>(() =>
    matchesInitialQuery ? initialResults : null
  );
  const [isLoading, setIsLoading] = React.useState<boolean>(() => !matchesInitialQuery);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  const skipInitialCategoriesRef = React.useRef<boolean>(hasInitialCategories);
  const hydratedQueryKeyRef = React.useRef<string | null>(
    matchesInitialQuery && initialQueryKey ? initialQueryKey : null
  );

  // Controlled filter inputs synced with URL params
  const [minPriceInput, setMinPriceInput] = React.useState(formatCentsForInput(minPriceCents));
  const [maxPriceInput, setMaxPriceInput] = React.useState(formatCentsForInput(maxPriceCents));
  const [priceValidationError, setPriceValidationError] = React.useState<string | null>(null);
  const [inlineSearchInput, setInlineSearchInput] = React.useState(searchQuery ?? "");
  const [mobileFiltersOpen, setMobileFiltersOpen] = React.useState(false);

  React.useEffect(() => {
    setMinPriceInput(formatCentsForInput(minPriceCents));
    setMaxPriceInput(formatCentsForInput(maxPriceCents));
    setPriceValidationError(null);
  }, [minPriceCents, maxPriceCents]);

  React.useEffect(() => {
    setInlineSearchInput(searchQuery ?? "");
  }, [searchQuery]);

  React.useEffect(() => {
    if (skipInitialCategoriesRef.current) {
      return;
    }
    const controller = new AbortController();
    catalogApi
      .listCategories(controller.signal)
      .then((res) => setCategories(res.items))
      .catch(() => {});
    return () => controller.abort();
  }, []);

  React.useEffect(() => {
    if (reloadNonce === 0 && hydratedQueryKeyRef.current === currentQueryKey) {
      return;
    }
    hydratedQueryKeyRef.current = null;

    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    catalogApi
      .listProducts(
        {
          category_slug: categorySlug,
          q: searchQuery,
          min_price_cents: minPriceCents,
          max_price_cents: maxPriceCents,
          in_stock: inStockOnly ? true : undefined,
          sort,
          page,
          page_size: DEFAULT_PAGE_SIZE,
        },
        controller.signal
      )
      .then((data) => {
        setResults(data);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load products.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [
    categorySlug,
    searchQuery,
    minPriceCents,
    maxPriceCents,
    inStockOnly,
    sort,
    page,
    currentQueryKey,
    reloadNonce,
  ]);

  const updateUrlParams = React.useCallback(
    (updates: Record<string, string | undefined>, resetPage = true) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, val] of Object.entries(updates)) {
        if (val === undefined || val === "") {
          next.delete(key);
        } else {
          next.set(key, val);
        }
      }
      if (resetPage) {
        next.delete("page");
      }
      const qs = next.toString();
      router.push(qs ? `${pathname}?${qs}` : pathname);
    },
    [pathname, router, searchParams]
  );

  const handleResetAllFilters = React.useCallback(() => {
    setPriceValidationError(null);
    setMobileFiltersOpen(false);
    router.push(pathname);
  }, [pathname, router]);

  const handlePriceApply = (e: React.FormEvent) => {
    e.preventDefault();
    const minTrimmed = minPriceInput.trim();
    const maxTrimmed = maxPriceInput.trim();

    const parsedMin = minTrimmed ? parseDollarsToCents(minTrimmed) : undefined;
    const parsedMax = maxTrimmed ? parseDollarsToCents(maxTrimmed) : undefined;

    if (minTrimmed && parsedMin === null) {
      setPriceValidationError("Enter a valid minimum dollar amount (e.g., 25 or 25.00).");
      return;
    }
    if (maxTrimmed && parsedMax === null) {
      setPriceValidationError("Enter a valid maximum dollar amount (e.g., 150 or 150.00).");
      return;
    }
    if (
      parsedMin !== undefined &&
      parsedMin !== null &&
      parsedMax !== undefined &&
      parsedMax !== null &&
      parsedMin > parsedMax
    ) {
      setPriceValidationError("Minimum price cannot exceed maximum price.");
      return;
    }

    setPriceValidationError(null);
    setMobileFiltersOpen(false);
    updateUrlParams({
      min_price_cents:
        parsedMin !== undefined && parsedMin !== null ? String(parsedMin) : undefined,
      max_price_cents:
        parsedMax !== undefined && parsedMax !== null ? String(parsedMax) : undefined,
    });
  };

  const handleInlineSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = inlineSearchInput.trim().slice(0, 100);
    setMobileFiltersOpen(false);
    updateUrlParams({ q: trimmed || undefined });
  };

  const handlePageChange = (nextPage: number) => {
    updateUrlParams({ page: nextPage > 1 ? String(nextPage) : undefined }, false);
    if (typeof window !== "undefined") {
      const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      gridTopRef.current?.scrollIntoView({
        behavior: reducedMotion ? "auto" : "smooth",
        block: "start",
      });
    }
  };

  const activeCategoryName = React.useMemo(() => {
    if (!categorySlug) {
      return undefined;
    }
    return categories.find((c) => c.slug === categorySlug)?.name ?? categorySlug;
  }, [categories, categorySlug]);

  const activeFilterCount =
    (categorySlug ? 1 : 0) +
    (searchQuery ? 1 : 0) +
    (minPriceCents !== undefined || maxPriceCents !== undefined ? 1 : 0) +
    (inStockOnly ? 1 : 0);

  const total = results?.total ?? 0;
  const items = results?.items ?? [];
  const startCount = total === 0 ? 0 : (page - 1) * DEFAULT_PAGE_SIZE + 1;
  const endCount = total === 0 ? 0 : Math.min(total, page * DEFAULT_PAGE_SIZE);

  const renderFilterControls = () => (
    <div className="space-y-6">
      {/* Search inside filters */}
      <form onSubmit={handleInlineSearch} className="space-y-2">
        <Label htmlFor="catalog-search-input">Search</Label>
        <div className="flex gap-2">
          <Input
            id="catalog-search-input"
            type="search"
            value={inlineSearchInput}
            onChange={(e) => setInlineSearchInput(e.target.value)}
            maxLength={100}
            placeholder="Keyword..."
          />
          <Button type="submit" variant="outline" size="default">
            Go
          </Button>
        </div>
      </form>

      {/* Categories */}
      <div className="space-y-2.5 border-t border-border pt-5">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Category
        </p>
        <RadioGroup
          value={categorySlug ?? "__all__"}
          onValueChange={(val) => {
            setMobileFiltersOpen(false);
            updateUrlParams({ category_slug: val === "__all__" ? undefined : val });
          }}
        >
          <label className="flex cursor-pointer items-center gap-2.5 py-1 text-sm">
            <RadioGroupItem value="__all__" id="cat-all" />
            <span>All Categories</span>
          </label>
          {categories.map((cat) => (
            <label key={cat.id} className="flex cursor-pointer items-center gap-2.5 py-1 text-sm">
              <RadioGroupItem value={cat.slug} id={`cat-${cat.slug}`} />
              <span>{cat.name}</span>
            </label>
          ))}
        </RadioGroup>
      </div>

      {/* Price Range */}
      <form onSubmit={handlePriceApply} className="space-y-3 border-t border-border pt-5">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Price (USD)
        </p>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label htmlFor="min-price-input" className="text-xs text-muted-foreground">
              Min ($)
            </Label>
            <Input
              id="min-price-input"
              inputMode="decimal"
              placeholder="0"
              value={minPriceInput}
              onChange={(e) => setMinPriceInput(e.target.value)}
              hasError={Boolean(priceValidationError)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="max-price-input" className="text-xs text-muted-foreground">
              Max ($)
            </Label>
            <Input
              id="max-price-input"
              inputMode="decimal"
              placeholder="500"
              value={maxPriceInput}
              onChange={(e) => setMaxPriceInput(e.target.value)}
              hasError={Boolean(priceValidationError)}
            />
          </div>
        </div>
        {priceValidationError ? (
          <p className="text-xs text-destructive" role="alert">
            {priceValidationError}
          </p>
        ) : null}
        <Button type="submit" variant="outline" size="sm" className="w-full">
          Apply Price
        </Button>
      </form>

      {/* Availability */}
      <div className="flex items-center justify-between gap-3 border-t border-border pt-5">
        <Label htmlFor="in-stock-switch" className="cursor-pointer text-sm">
          In stock only
        </Label>
        <Switch
          id="in-stock-switch"
          checked={inStockOnly}
          onCheckedChange={(checked) => {
            setMobileFiltersOpen(false);
            updateUrlParams({ in_stock: checked ? "true" : undefined });
          }}
        />
      </div>

      {activeFilterCount > 0 ? (
        <div className="border-t border-border pt-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleResetAllFilters}
            className="w-full text-muted-foreground"
          >
            Clear All Filters
          </Button>
        </div>
      ) : null}
    </div>
  );

  return (
    <div ref={gridTopRef} className="mx-auto max-w-[1440px] px-4 py-10 sm:px-6 lg:px-8">
      {/* Page Header */}
      <div className="mb-8 border-b border-border pb-6">
        <h1 className="font-display text-3xl font-medium tracking-[-0.02em] text-foreground sm:text-4xl">
          {activeCategoryName ?? "Collection"}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {searchQuery
            ? `Search results for “${searchQuery}”`
            : "Curated goods with live variant stock visibility."}
        </p>
      </div>

      {/* Main Split: Desktop Sidebar + Grid */}
      <div className="flex flex-col gap-8 lg:flex-row">
        {/* Desktop Sidebar */}
        <aside
          aria-label="Product filters"
          className="hidden w-[260px] shrink-0 lg:block"
        >
          <div className="sticky top-24 rounded-lg border border-border bg-surface p-5">
            {renderFilterControls()}
          </div>
        </aside>

        {/* Catalog Results Column */}
        <div className="min-w-0 flex-1 space-y-6">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                size="default"
                className="lg:hidden"
                onClick={() => setMobileFiltersOpen(true)}
              >
                <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
                <span>Filters</span>
                {activeFilterCount > 0 ? (
                  <Badge variant="accent" className="ml-1 tabular-nums">
                    {activeFilterCount}
                  </Badge>
                ) : null}
              </Button>

              <p className="text-sm text-muted-foreground tabular-nums" aria-live="polite">
                {isLoading
                  ? "Updating products..."
                  : total === 0
                    ? "Showing 0 products"
                    : `Showing ${startCount}–${endCount} of ${total} products`}
              </p>
            </div>

            <div className="flex items-center gap-2">
              <Label htmlFor="catalog-sort-select" className="text-xs text-muted-foreground">
                Sort
              </Label>
              <Select
                value={sort}
                onValueChange={(value) => updateUrlParams({ sort: value })}
              >
                <SelectTrigger id="catalog-sort-select" className="w-[190px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="newest">Newest</SelectItem>
                  <SelectItem value="price_asc">Price: Low to High</SelectItem>
                  <SelectItem value="price_desc">Price: High to Low</SelectItem>
                  <SelectItem value="title_asc">Title: A–Z</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Active Filter Chips */}
          {activeFilterCount > 0 ? (
            <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
              {categorySlug ? (
                <Badge variant="secondary" className="gap-1.5 py-1 pl-2.5 pr-1.5">
                  <span>Category: {activeCategoryName}</span>
                  <button
                    type="button"
                    aria-label="Remove category filter"
                    onClick={() => updateUrlParams({ category_slug: undefined })}
                    className="rounded-sm p-0.5 hover:bg-muted"
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </Badge>
              ) : null}

              {searchQuery ? (
                <Badge variant="secondary" className="gap-1.5 py-1 pl-2.5 pr-1.5">
                  <span>Search: {searchQuery}</span>
                  <button
                    type="button"
                    aria-label="Remove search filter"
                    onClick={() => updateUrlParams({ q: undefined })}
                    className="rounded-sm p-0.5 hover:bg-muted"
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </Badge>
              ) : null}

              {minPriceCents !== undefined || maxPriceCents !== undefined ? (
                <Badge variant="secondary" className="gap-1.5 py-1 pl-2.5 pr-1.5 tabular-nums">
                  <span>
                    Price: {minPriceCents !== undefined ? formatCents(minPriceCents) : "$0.00"} –{" "}
                    {maxPriceCents !== undefined ? formatCents(maxPriceCents) : "Any"}
                  </span>
                  <button
                    type="button"
                    aria-label="Remove price filter"
                    onClick={() =>
                      updateUrlParams({
                        min_price_cents: undefined,
                        max_price_cents: undefined,
                      })
                    }
                    className="rounded-sm p-0.5 hover:bg-muted"
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </Badge>
              ) : null}

              {inStockOnly ? (
                <Badge variant="secondary" className="gap-1.5 py-1 pl-2.5 pr-1.5">
                  <span>In stock only</span>
                  <button
                    type="button"
                    aria-label="Remove in-stock filter"
                    onClick={() => updateUrlParams({ in_stock: undefined })}
                    className="rounded-sm p-0.5 hover:bg-muted"
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </Badge>
              ) : null}

              <button
                type="button"
                onClick={handleResetAllFilters}
                className="text-xs font-medium text-accent hover:underline"
              >
                Reset all
              </button>
            </div>
          ) : null}

          {/* Product Grid States */}
          {isLoading ? (
            <div
              role="status"
              aria-label="Loading catalog products"
              className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
            >
              {Array.from({ length: 8 }, (_, idx) => (
                <ProductCardSkeleton key={idx} />
              ))}
            </div>
          ) : error ? (
            <ErrorState
              title="Unable to load products"
              message={error}
              onRetry={() => setReloadNonce((n) => n + 1)}
            />
          ) : items.length === 0 ? (
            <EmptyState
              title={
                activeFilterCount > 0
                  ? "No products match your filters."
                  : "Catalog coming soon."
              }
              description={
                activeFilterCount > 0
                  ? "Try clearing one or more active filters to view available products."
                  : "No active products have been published to the storefront yet."
              }
              action={
                activeFilterCount > 0 ? (
                  <Button onClick={handleResetAllFilters}>Reset Filters</Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {items.map((product) => (
                  <ProductCard key={product.id} product={product} />
                ))}
              </div>

              {results ? (
                <Pagination
                  page={results.page}
                  pageSize={results.page_size}
                  total={results.total}
                  hasNext={results.has_next}
                  onPageChange={handlePageChange}
                  itemLabel="products"
                />
              ) : null}
            </>
          )}
        </div>
      </div>

      {/* Mobile Filters Sheet */}
      <Sheet open={mobileFiltersOpen} onOpenChange={setMobileFiltersOpen}>
        <SheetContent side="left" className="flex flex-col p-0">
          <SheetHeader>
            <SheetTitle>Filter Products</SheetTitle>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-6 py-5">{renderFilterControls()}</div>
          <SheetFooter>
            <Button className="w-full" onClick={() => setMobileFiltersOpen(false)}>
              Done
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  );
}
