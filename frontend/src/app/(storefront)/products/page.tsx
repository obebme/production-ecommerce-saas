import * as React from "react";
import type { Metadata } from "next";
import {
  CATALOG_DEFAULT_PAGE_SIZE,
  fetchServerCategories,
  fetchServerProducts,
  parseCatalogSearchParams,
} from "@/lib/api/server-catalog";
import { CatalogView } from "@/components/storefront/catalog-view";
import { ProductCardSkeleton } from "@/components/storefront/product-card";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Shop All Products",
  description: "Filter and browse the active Vellora Store catalog with live variant availability.",
};

function CatalogFallback() {
  return (
    <div className="mx-auto max-w-[1440px] px-4 py-10 sm:px-6 lg:px-8">
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }, (_, idx) => (
          <ProductCardSkeleton key={idx} />
        ))}
      </div>
    </div>
  );
}

export default async function ProductsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const resolvedParams = (await searchParams) ?? {};
  const parsed = parseCatalogSearchParams(resolvedParams);

  const [categories, productsRes] = await Promise.all([
    fetchServerCategories(),
    fetchServerProducts({
      category_slug: parsed.category_slug,
      q: parsed.q,
      min_price_cents: parsed.min_price_cents,
      max_price_cents: parsed.max_price_cents,
      in_stock: parsed.in_stock,
      sort: parsed.sort,
      page: parsed.page,
      page_size: CATALOG_DEFAULT_PAGE_SIZE,
    }),
  ]);

  return (
    <React.Suspense fallback={<CatalogFallback />}>
      <CatalogView
        initialCategories={categories}
        initialResults={productsRes}
        initialQueryKey={parsed.queryKey}
      />
    </React.Suspense>
  );
}
