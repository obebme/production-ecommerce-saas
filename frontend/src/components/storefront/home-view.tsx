"use client";

import * as React from "react";
import Image from "next/image";
import Link from "next/link";
import { ArrowRight, Award, RotateCcw, ShieldCheck, Truck } from "lucide-react";
import { catalogApi } from "@/lib/api/client";
import type { CategoryPublicDTO, ProductSummaryDTO } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { ProductImage } from "@/components/shared/product-image";
import { EmptyState, ErrorState } from "@/components/shared/state-feedback";
import { ProductCard, ProductCardSkeleton } from "@/components/storefront/product-card";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export interface HomeViewProps {
  initialCategories?: CategoryPublicDTO[] | null;
  initialProducts?: ProductSummaryDTO[] | null;
}

const CATEGORY_PREVIEW_BY_SLUG: Record<string, string> = {
  apparel: "/assets/products/linen-overshirt.png",
  clothing: "/assets/products/linen-overshirt.png",
  accessories: "/assets/products/leather-weekender.png",
  bags: "/assets/products/canvas-tote.png",
  home: "/assets/products/ceramic-table-lamp.png",
  "home-living": "/assets/products/ceramic-table-lamp.png",
  living: "/assets/products/wool-throw-blanket.png",
  kitchen: "/assets/products/stoneware-mug-set.png",
  dining: "/assets/products/walnut-serving-board.png",
};

type FeaturedFilter = "all" | "in_stock" | `category:${string}`;

function resolveCategoryPreviewUrl(
  category: CategoryPublicDTO,
  products: ProductSummaryDTO[]
): string | null {
  const mapped = CATEGORY_PREVIEW_BY_SLUG[category.slug.toLowerCase()];
  if (mapped) {
    return mapped;
  }
  const matchingProduct = products.find(
    (p) => p.category.slug === category.slug && p.primary_image?.url
  );
  return matchingProduct?.primary_image?.url ?? null;
}

export function HomeView({ initialCategories, initialProducts }: HomeViewProps = {}) {
  const hasInitialCategories = initialCategories !== undefined && initialCategories !== null;
  const hasInitialProducts = initialProducts !== undefined && initialProducts !== null;

  const [categories, setCategories] = React.useState<CategoryPublicDTO[]>(() =>
    hasInitialCategories ? initialCategories.slice(0, 4) : []
  );
  const [categoriesLoading, setCategoriesLoading] = React.useState<boolean>(
    () => !hasInitialCategories
  );

  const [allProductsCache, setAllProductsCache] = React.useState<ProductSummaryDTO[]>(() =>
    hasInitialProducts ? initialProducts : []
  );
  const [products, setProducts] = React.useState<ProductSummaryDTO[]>(() =>
    hasInitialProducts ? initialProducts : []
  );
  const [productsLoading, setProductsLoading] = React.useState<boolean>(
    () => !hasInitialProducts
  );
  const [productsError, setProductsError] = React.useState<string | null>(null);
  const [activeFilter, setActiveFilter] = React.useState<FeaturedFilter>("all");

  const skipInitialFetchRef = React.useRef<boolean>(
    hasInitialCategories && hasInitialProducts
  );

  const loadHomeData = React.useCallback(
    async (signal?: AbortSignal, options?: { preserveCategories?: boolean }) => {
      if (!options?.preserveCategories) {
        setCategoriesLoading(true);
      }
      setProductsLoading(true);
      setProductsError(null);

      const [catResult, prodResult] = await Promise.allSettled([
        catalogApi.listCategories(signal),
        catalogApi.listProducts({ sort: "newest", page: 1, page_size: 8 }, signal),
      ]);

      if (signal?.aborted) {
        return;
      }

      if (catResult.status === "fulfilled") {
        setCategories(catResult.value.items.slice(0, 4));
      }
      setCategoriesLoading(false);

      if (prodResult.status === "fulfilled") {
        setAllProductsCache(prodResult.value.items);
        setProducts(prodResult.value.items);
      } else if (
        !(prodResult.reason instanceof DOMException && prodResult.reason.name === "AbortError")
      ) {
        setProductsError(
          prodResult.reason instanceof Error
            ? prodResult.reason.message
            : "Unable to load featured collection right now."
        );
      }
      setProductsLoading(false);
    },
    []
  );

  React.useEffect(() => {
    if (skipInitialFetchRef.current) {
      return;
    }
    const controller = new AbortController();
    void loadHomeData(controller.signal, { preserveCategories: hasInitialCategories });
    return () => controller.abort();
  }, [loadHomeData, hasInitialCategories]);

  // Fetch filtered featured collection only when user selects a non-default filter pill
  const activeFilterAbortRef = React.useRef<AbortController | null>(null);

  const handleFilterChange = React.useCallback(
    (nextFilter: FeaturedFilter) => {
      setActiveFilter(nextFilter);
      activeFilterAbortRef.current?.abort();

      if (nextFilter === "all" && allProductsCache.length > 0) {
        setProductsError(null);
        setProductsLoading(false);
        setProducts(allProductsCache);
        return;
      }

      const controller = new AbortController();
      activeFilterAbortRef.current = controller;
      setProductsLoading(true);
      setProductsError(null);

      const params =
        nextFilter === "in_stock"
          ? { in_stock: true, sort: "newest" as const, page: 1, page_size: 8 }
          : nextFilter.startsWith("category:")
            ? {
                category_slug: nextFilter.slice("category:".length),
                sort: "newest" as const,
                page: 1,
                page_size: 8,
              }
            : { sort: "newest" as const, page: 1, page_size: 8 };

      catalogApi
        .listProducts(params, controller.signal)
        .then((res) => {
          if (controller.signal.aborted) {
            return;
          }
          if (nextFilter === "all") {
            setAllProductsCache(res.items);
          }
          setProducts(res.items);
        })
        .catch((err) => {
          if (err instanceof DOMException && err.name === "AbortError") {
            return;
          }
          setProductsError(
            err instanceof Error ? err.message : "Unable to load featured products right now."
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setProductsLoading(false);
          }
        });
    },
    [allProductsCache]
  );

  React.useEffect(() => {
    return () => {
      activeFilterAbortRef.current?.abort();
    };
  }, []);

  return (
    <div className="space-y-14 pb-20">
      {/* 1. Architectural Hero Banner */}
      <section className="mx-auto max-w-[1440px] px-4 pt-6 sm:px-6 lg:px-8">
        <div className="relative overflow-hidden rounded-[20px] border border-border bg-gradient-to-br from-[#EFEAE0] via-[#FAF8F3] to-[#E6DFD2] p-8 sm:p-12 lg:p-14 dark:from-[#16191F] dark:via-[#111419] dark:to-[#0B0D0F]">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full bg-accent/15 blur-3xl"
          />
          <div className="relative grid items-center gap-10 lg:grid-cols-12 lg:gap-12">
            <div className="space-y-6 lg:col-span-6">
              <p className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent-subtle px-3.5 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-accent">
                Curated Studio Collection
              </p>
              <h1 className="text-3xl font-bold leading-[1.08] tracking-[-0.025em] text-foreground sm:text-5xl">
                Considered pieces crafted for everyday utility.
              </h1>
              <p className="max-w-xl text-base leading-relaxed text-muted-foreground">
                Explore natural fibers, architectural lighting, and tactile stoneware objects backed
                by live studio inventory and transparent pricing.
              </p>
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <Button
                  size="lg"
                  variant="accent"
                  asChild
                  className="rounded-lg font-semibold shadow-sm"
                >
                  <Link href="/products">
                    Explore Catalog
                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
                  </Link>
                </Button>
                <Button
                  size="lg"
                  variant="outline"
                  asChild
                  className="rounded-lg border-border-control bg-surface/80 backdrop-blur-sm hover:bg-muted"
                >
                  <Link href="/products?in_stock=true">In-Stock Ready</Link>
                </Button>
              </div>
              <div className="flex items-center gap-2 pt-2" aria-hidden="true">
                <span className="h-1.5 w-6 rounded-full bg-accent" />
                <span className="h-1.5 w-1.5 rounded-full bg-border-control" />
                <span className="h-1.5 w-1.5 rounded-full bg-border-control" />
              </div>
            </div>

            <div className="lg:col-span-6">
              <div className="relative aspect-[4/3] w-full overflow-hidden rounded-[16px] border border-border bg-[#0E1115] shadow-md">
                <Image
                  src="/assets/products/hero-showcase.png"
                  alt="Vellora Store studio composition featuring ceramic table lamp, leather weekender bag, and wool throw blanket"
                  fill
                  sizes="(max-width: 1024px) 100vw, 50vw"
                  priority
                  unoptimized
                  className="object-cover object-center"
                />
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-0 bg-gradient-to-t from-[#0B0D0F]/70 via-transparent to-transparent"
                />
                <div className="absolute bottom-4 left-4 right-4 flex items-center justify-between gap-3">
                  <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-[#0B0D0F]/75 px-3.5 py-1.5 text-xs font-medium text-[#F5F3EE] backdrop-blur-md">
                    <span className="h-2 w-2 rounded-full bg-[#34D399]" aria-hidden="true" />
                    Live Studio Inventory · Verified Per SKU
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 2. 4-Column Service Trust Strip */}
      <section
        aria-labelledby="home-guarantees-heading"
        className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8"
      >
        <h2 id="home-guarantees-heading" className="sr-only">
          Storefront guarantees
        </h2>
        <div className="grid gap-6 rounded-[12px] border border-border bg-surface p-6 sm:grid-cols-2 lg:grid-cols-4 dark:bg-[#14171B]">
          <div className="flex items-start gap-3.5">
            <div className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-subtle text-accent">
              <Truck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-foreground">Free Shipping Over $100</h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Qualifying orders receive automatic $0.00 standard shipping.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3.5">
            <div className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-subtle text-accent">
              <Award className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-foreground">24-Month Quality Guarantee</h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Crafted from durable textiles, ceramics, and solid hardware.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3.5">
            <div className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-subtle text-accent">
              <RotateCcw className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-foreground">14-Day Easy Returns</h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Self-service order cancellation and straightforward return support.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3.5">
            <div className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-subtle text-accent">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-foreground">Secure Checkout Hold</h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                15-minute server-locked inventory hold during sandbox checkout.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* 3. Popular Categories Strip */}
      {(categoriesLoading || categories.length > 0) && (
        <section
          aria-labelledby="home-categories-heading"
          className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8"
        >
          <div className="mb-6 flex items-end justify-between gap-4">
            <div>
              <h2
                id="home-categories-heading"
                className="text-2xl font-bold tracking-[-0.02em] text-foreground sm:text-3xl"
              >
                Popular Categories
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Browse active collections arranged by studio department.
              </p>
            </div>
            <Link
              href="/products"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-accent underline-offset-4 hover:underline"
            >
              View all
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
          </div>

          {categoriesLoading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[1, 2, 3, 4].map((idx) => (
                <Card key={idx} className="overflow-hidden rounded-[12px] p-4">
                  <Skeleton className="aspect-[4/3] w-full rounded-lg" />
                  <Skeleton className="mx-auto mt-4 h-5 w-28" />
                  <Skeleton className="mx-auto mt-2 h-3.5 w-36" />
                </Card>
              ))}
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {categories.map((category) => {
                const previewUrl = resolveCategoryPreviewUrl(category, allProductsCache);
                return (
                  <Link
                    key={category.id}
                    href={`/products?category_slug=${encodeURIComponent(category.slug)}`}
                    className="group block rounded-[12px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Card className="flex h-full flex-col overflow-hidden rounded-[12px] border border-border bg-card p-3 transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-accent/60 group-hover:shadow-sm">
                      <div className="relative aspect-[4/3] w-full overflow-hidden rounded-lg bg-muted bg-[radial-gradient(circle_at_50%_35%,rgba(216,195,161,0.12),transparent_70%)]">
                        <ProductImage
                          src={previewUrl}
                          alt={category.name}
                          fallbackLabel={category.name}
                          sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 25vw"
                          className="bg-transparent"
                          imgClassName="transition-transform duration-300 group-hover:scale-[1.04]"
                        />
                      </div>
                      <div className="flex flex-1 flex-col items-center justify-center px-2 py-4 text-center">
                        <h3 className="text-base font-semibold text-foreground transition-colors group-hover:text-accent">
                          {category.name}
                        </h3>
                        <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                          {category.description || "Explore collection"}
                        </p>
                      </div>
                    </Card>
                  </Link>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* 4. Featured Collection Grid with Interactive Filter Pills */}
      <section
        aria-labelledby="home-arrivals-heading"
        className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8"
      >
        <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h2
              id="home-arrivals-heading"
              className="text-2xl font-bold tracking-[-0.02em] text-foreground sm:text-3xl"
            >
              Featured Collection
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              The latest active pieces from our live studio catalog.
            </p>
          </div>

          <div
            role="group"
            aria-label="Filter featured collection"
            className="flex flex-wrap items-center gap-2"
          >
            <button
              type="button"
              onClick={() => handleFilterChange("all")}
              aria-pressed={activeFilter === "all"}
              className={cn(
                "rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                activeFilter === "all"
                  ? "border-accent bg-accent text-accent-foreground"
                  : "border-border bg-card text-muted-foreground hover:border-border-control hover:text-foreground"
              )}
            >
              All
            </button>
            {categories.map((category) => {
              const key: FeaturedFilter = `category:${category.slug}`;
              const selected = activeFilter === key;
              return (
                <button
                  key={category.id}
                  type="button"
                  onClick={() => handleFilterChange(key)}
                  aria-pressed={selected}
                  className={cn(
                    "rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selected
                      ? "border-accent bg-accent text-accent-foreground"
                      : "border-border bg-card text-muted-foreground hover:border-border-control hover:text-foreground"
                  )}
                >
                  {category.name}
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => handleFilterChange("in_stock")}
              aria-pressed={activeFilter === "in_stock"}
              className={cn(
                "rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                activeFilter === "in_stock"
                  ? "border-accent bg-accent text-accent-foreground"
                  : "border-border bg-card text-muted-foreground hover:border-border-control hover:text-foreground"
              )}
            >
              In Stock Only
            </button>
          </div>
        </div>

        {productsLoading ? (
          <div
            role="status"
            aria-label="Loading featured collection"
            className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4"
          >
            {Array.from({ length: 8 }, (_, idx) => (
              <ProductCardSkeleton key={idx} />
            ))}
          </div>
        ) : productsError ? (
          <ErrorState
            title="Unable to load products"
            message={productsError}
            onRetry={() =>
              activeFilter === "all"
                ? void loadHomeData()
                : handleFilterChange(activeFilter)
            }
          />
        ) : products.length === 0 ? (
          <EmptyState
            title={
              activeFilter === "all"
                ? "Our collection is being prepared."
                : "No products match this filter."
            }
            description={
              activeFilter === "all"
                ? "New pieces will appear here as soon as they are published to the storefront."
                : "Try selecting another category or viewing the full catalog."
            }
            action={
              activeFilter === "all" ? (
                <Button variant="outline" asChild>
                  <Link href="/products">Check Catalog</Link>
                </Button>
              ) : (
                <Button variant="outline" onClick={() => handleFilterChange("all")}>
                  Show All Featured
                </Button>
              )
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {products.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
