"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Clock, ShieldCheck, Truck } from "lucide-react";
import { catalogApi } from "@/lib/api/client";
import type { CategoryPublicDTO, ProductSummaryDTO } from "@/lib/api/types";
import { EmptyState, ErrorState } from "@/components/shared/state-feedback";
import { ProductCard, ProductCardSkeleton } from "@/components/storefront/product-card";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export interface HomeViewProps {
  initialCategories?: CategoryPublicDTO[] | null;
  initialProducts?: ProductSummaryDTO[] | null;
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

  const [products, setProducts] = React.useState<ProductSummaryDTO[]>(() =>
    hasInitialProducts ? initialProducts : []
  );
  const [productsLoading, setProductsLoading] = React.useState<boolean>(
    () => !hasInitialProducts
  );
  const [productsError, setProductsError] = React.useState<string | null>(null);

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
        setProducts(prodResult.value.items);
      } else if (
        !(prodResult.reason instanceof DOMException && prodResult.reason.name === "AbortError")
      ) {
        setProductsError(
          prodResult.reason instanceof Error
            ? prodResult.reason.message
            : "Unable to load new arrivals right now."
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

  return (
    <div className="space-y-16 pb-20">
      {/* 1. Editorial Hero */}
      <section className="border-b border-border bg-accent text-accent-foreground">
        <div className="mx-auto grid max-w-[1440px] gap-8 px-4 py-16 sm:px-6 md:py-24 lg:grid-cols-12 lg:items-center lg:px-8">
          <div className="space-y-6 lg:col-span-8">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent-foreground/80">
              Seasonal Studio Collection
            </p>
            <h1 className="font-display text-4xl font-medium leading-[1.1] tracking-[-0.02em] sm:text-5xl">
              Considered pieces crafted for quiet everyday utility.
            </h1>
            <p className="max-w-xl text-base leading-relaxed text-accent-foreground/90">
              Explore natural fibers, tailored silhouettes, and stoneware objects backed by live
              studio inventory and transparent pricing.
            </p>
            <div className="flex flex-wrap items-center gap-3 pt-2">
              <Button
                size="lg"
                asChild
                className="bg-background text-foreground hover:bg-background/90"
              >
                <Link href="/products">
                  Explore Collection
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Link>
              </Button>
              <Button
                size="lg"
                variant="outline"
                asChild
                className="border-accent-foreground/35 bg-transparent text-accent-foreground hover:bg-accent-foreground/10"
              >
                <Link href="/products?in_stock=true">In-Stock Ready to Ship</Link>
              </Button>
            </div>
          </div>

          <div className="rounded-lg border border-accent-foreground/20 bg-accent-foreground/10 p-6 lg:col-span-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-accent-foreground/80">
              Atelier Standards
            </p>
            <ul className="mt-4 space-y-3 text-sm leading-relaxed text-accent-foreground/95">
              <li className="border-b border-accent-foreground/15 pb-3">
                Server-verified stock availability updated in real time per SKU.
              </li>
              <li className="border-b border-accent-foreground/15 pb-3">
                Complimentary standard shipping on orders over $100.00.
              </li>
              <li>Clean session security with zero browser token exposure.</li>
            </ul>
          </div>
        </div>
      </section>

      {/* 2. Live Category Strip */}
      {(categoriesLoading || categories.length > 0) && (
        <section
          aria-labelledby="home-categories-heading"
          className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8"
        >
          <div className="mb-6 flex items-end justify-between gap-4">
            <div>
              <h2
                id="home-categories-heading"
                className="font-display text-3xl font-medium tracking-[-0.02em] text-foreground"
              >
                Curated Categories
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Browse active collections arranged by category.
              </p>
            </div>
            <Link
              href="/products"
              className="text-sm font-medium text-accent underline-offset-4 hover:underline"
            >
              Shop all
            </Link>
          </div>

          {categoriesLoading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[1, 2, 3, 4].map((idx) => (
                <Card key={idx} className="p-6">
                  <Skeleton className="h-5 w-32" />
                  <Skeleton className="mt-3 h-4 w-full" />
                  <Skeleton className="mt-1.5 h-4 w-2/3" />
                </Card>
              ))}
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {categories.map((category) => (
                <Link
                  key={category.id}
                  href={`/products?category_slug=${encodeURIComponent(category.slug)}`}
                  className="group rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Card className="flex h-full flex-col justify-between p-6 transition-shadow group-hover:shadow-sm">
                    <div>
                      <h3 className="text-base font-semibold text-foreground group-hover:text-accent">
                        {category.name}
                      </h3>
                      {category.description ? (
                        <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
                          {category.description}
                        </p>
                      ) : null}
                    </div>
                    <span className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-accent">
                      Browse {category.name}
                      <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </span>
                  </Card>
                </Link>
              ))}
            </div>
          )}
        </section>
      )}

      {/* 3. Live New Arrivals Grid */}
      <section
        aria-labelledby="home-arrivals-heading"
        className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8"
      >
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h2
              id="home-arrivals-heading"
              className="font-display text-3xl font-medium tracking-[-0.02em] text-foreground"
            >
              New Arrivals
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              The latest active pieces from the live catalog.
            </p>
          </div>
          <Link
            href="/products"
            className="text-sm font-medium text-accent underline-offset-4 hover:underline"
          >
            View all products
          </Link>
        </div>

        {productsLoading ? (
          <div
            role="status"
            aria-label="Loading new arrivals"
            className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4"
          >
            {Array.from({ length: 8 }, (_, idx) => (
              <ProductCardSkeleton key={idx} />
            ))}
          </div>
        ) : productsError ? (
          <ErrorState
            title="Unable to load products"
            message={productsError}
            onRetry={() => void loadHomeData()}
          />
        ) : products.length === 0 ? (
          <EmptyState
            title="Our collection is being prepared."
            description="New pieces will appear here as soon as they are published to the storefront."
            action={
              <Button variant="outline" asChild>
                <Link href="/products">Check Catalog</Link>
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {products.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </section>

      {/* 4. Shipping & Sandbox Service Strip */}
      <section
        aria-label="Storefront guarantees"
        className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8"
      >
        <div className="grid gap-4 rounded-lg border border-border bg-surface p-6 md:grid-cols-3 md:gap-8">
          <div className="flex items-start gap-3">
            <Truck className="mt-0.5 h-5 w-5 shrink-0 text-accent" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-foreground">
                Complimentary shipping over $100
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Orders at or above $100.00 qualify automatically for $0.00 standard shipping.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3">
            <Clock className="mt-0.5 h-5 w-5 shrink-0 text-accent" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-foreground">
                15-minute checkout stock hold
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Server-side row locking reserves inventory cleanly while completing payment.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-accent" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-foreground">
                Sandbox-safe simulated checkout
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Zero payment card fields collected; webhooks and reservations run deterministically.
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
