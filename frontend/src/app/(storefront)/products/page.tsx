import * as React from "react";
import type { Metadata } from "next";
import { CatalogView } from "@/components/storefront/catalog-view";
import { ProductCardSkeleton } from "@/components/storefront/product-card";

export const metadata: Metadata = {
  title: "Shop All Products",
  description: "Filter and browse the active Atelier catalog with live variant availability.",
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

export default function ProductsPage() {
  return (
    <React.Suspense fallback={<CatalogFallback />}>
      <CatalogView />
    </React.Suspense>
  );
}
