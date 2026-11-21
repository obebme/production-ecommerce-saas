import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
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
      className="group block rounded-[12px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      <Card className="overflow-hidden rounded-[12px] border border-border bg-card transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-accent/50 group-hover:shadow-sm">
        <div className="relative aspect-[4/5] w-full overflow-hidden bg-muted bg-[radial-gradient(circle_at_50%_30%,rgba(216,195,161,0.12),transparent_70%)]">
          <ProductImage
            src={product.primary_image?.url}
            alt={product.primary_image?.alt_text || product.title}
            fallbackLabel={product.title}
            sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 25vw"
            className="bg-transparent"
            imgClassName="transition-transform duration-300 group-hover:scale-[1.03]"
          />
          {!product.in_stock ? (
            <div className="absolute left-3 top-3">
              <Badge variant="destructive" className="rounded-full px-2.5 py-0.5">
                Out of Stock
              </Badge>
            </div>
          ) : null}
        </div>

        <div className="space-y-2 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {product.category.name}
          </p>
          <Heading className="line-clamp-2 min-h-[2.5rem] text-sm font-semibold leading-snug text-foreground transition-colors group-hover:text-accent">
            {product.title}
          </Heading>
          <div className="flex items-center justify-between gap-2 pt-1">
            <p className="text-base font-bold text-foreground tabular-nums">{formattedPrice}</p>
            <span
              aria-hidden="true"
              className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-border bg-muted/60 text-muted-foreground transition-colors group-hover:border-accent/50 group-hover:bg-accent group-hover:text-accent-foreground"
            >
              <ArrowUpRight className="h-4 w-4" />
            </span>
          </div>
        </div>
      </Card>
    </Link>
  );
}

export function ProductCardSkeleton() {
  return (
    <Card className="overflow-hidden rounded-[12px] border border-border bg-card">
      <Skeleton className="aspect-[4/5] w-full rounded-none" />
      <div className="space-y-2.5 p-4">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-4 w-4/5" />
        <div className="flex items-center justify-between pt-1">
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-8 w-8 rounded-full" />
        </div>
      </div>
    </Card>
  );
}
