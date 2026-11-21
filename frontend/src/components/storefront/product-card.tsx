import Link from "next/link";
import type { ProductSummaryDTO } from "@/lib/api/types";
import { formatCents } from "@/lib/format";
import { ProductImage } from "@/components/shared/product-image";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function ProductCard({
  product,
  headingAs: Heading = "h3",
}: {
  product: ProductSummaryDTO;
  headingAs?: "h2" | "h3";
}) {
  const hasRange = product.min_price_cents !== product.max_price_cents;
  const formattedPrice = hasRange
    ? `From ${formatCents(product.min_price_cents, product.currency)}`
    : formatCents(product.min_price_cents, product.currency);

  return (
    <Link
      href={`/products/${encodeURIComponent(product.slug)}`}
      className="group block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      <Card className="overflow-hidden transition-shadow group-hover:shadow-sm">
        <div className="relative aspect-[4/5] w-full overflow-hidden bg-muted">
          <ProductImage
            src={product.primary_image?.url}
            alt={product.primary_image?.alt_text || product.title}
            fallbackLabel={product.title}
            sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 25vw"
            imgClassName="transition-transform duration-300 group-hover:scale-[1.03]"
          />
          {!product.in_stock ? (
            <div className="absolute left-3 top-3">
              <Badge variant="destructive">Out of Stock</Badge>
            </div>
          ) : null}
        </div>

        <div className="space-y-1.5 p-4">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {product.category.name}
          </p>
          <Heading className="line-clamp-2 text-sm font-semibold leading-snug text-foreground group-hover:text-accent">
            {product.title}
          </Heading>
          <p className="pt-0.5 text-sm font-medium text-foreground tabular-nums">
            {formattedPrice}
          </p>
        </div>
      </Card>
    </Link>
  );
}

export function ProductCardSkeleton() {
  return (
    <Card className="overflow-hidden">
      <Skeleton className="aspect-[4/5] w-full rounded-none" />
      <div className="space-y-2 p-4">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-4 w-4/5" />
        <Skeleton className="h-4 w-16" />
      </div>
    </Card>
  );
}
