"use client";

import * as React from "react";
import Link from "next/link";
import { AlertCircle, ChevronRight, Minus, Plus, ShoppingBag } from "lucide-react";
import { ApiError, catalogApi, inventoryApi } from "@/lib/api/client";
import type {
  ProductDetailDTO,
  ProductVariantPublicDTO,
  VariantAvailabilityDTO,
} from "@/lib/api/types";
import { computeSavingsPercent, formatCents } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useCart } from "@/providers/cart-provider";
import { ProductImage } from "@/components/shared/product-image";
import { EmptyState, ErrorState } from "@/components/shared/state-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface AttributeGroup {
  key: string;
  label: string;
  values: string[];
}

function formatAttributeLabel(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function extractAttributeGroups(
  variants: ProductVariantPublicDTO[]
): AttributeGroup[] | null {
  if (variants.length <= 1) {
    return null;
  }

  const firstKeys = Object.keys(variants[0].attributes || {}).sort();
  if (firstKeys.length === 0) {
    return null;
  }

  const seenTuples = new Set<string>();
  for (const variant of variants) {
    const attrs = variant.attributes || {};
    const keys = Object.keys(attrs).sort();
    if (
      keys.length !== firstKeys.length ||
      keys.some((k, idx) => k !== firstKeys[idx])
    ) {
      return null;
    }

    const tupleParts: string[] = [];
    for (const key of firstKeys) {
      const val = attrs[key];
      if (typeof val !== "string" && typeof val !== "number" && typeof val !== "boolean") {
        return null;
      }
      const strVal = String(val).trim();
      if (!strVal) {
        return null;
      }
      tupleParts.push(`${key}=${strVal}`);
    }
    const tupleKey = tupleParts.join("|");
    if (seenTuples.has(tupleKey)) {
      return null;
    }
    seenTuples.add(tupleKey);
  }

  return firstKeys.map((key) => {
    const values: string[] = [];
    const seen = new Set<string>();
    for (const variant of variants) {
      const val = String(variant.attributes[key]).trim();
      if (!seen.has(val)) {
        seen.add(val);
        values.push(val);
      }
    }
    return {
      key,
      label: formatAttributeLabel(key),
      values,
    };
  });
}

export interface ProductDetailViewProps {
  slug: string;
  initialProduct?: ProductDetailDTO | null;
  initialError?: string | null;
}

function getInitialImageIndex(product: ProductDetailDTO | null | undefined): number {
  if (!product) {
    return 0;
  }
  const sortedImgs = [...product.images].sort((a, b) => a.sort_order - b.sort_order);
  const primaryIdx = sortedImgs.findIndex((img) => img.is_primary);
  return primaryIdx >= 0 ? primaryIdx : 0;
}

function getInitialVariantId(product: ProductDetailDTO | null | undefined): string | null {
  if (!product) {
    return null;
  }
  const activeVariants = product.variants.filter((v) => v.is_active);
  const initialVariant = activeVariants.find((v) => v.in_stock) ?? activeVariants[0] ?? null;
  return initialVariant?.id ?? null;
}

export function ProductDetailView({
  slug,
  initialProduct,
  initialError,
}: ProductDetailViewProps) {
  const { addItem } = useCart();

  const hasInitialProduct =
    initialProduct !== undefined &&
    initialProduct !== null &&
    initialProduct.slug === slug;
  const hasInitialError =
    !hasInitialProduct && typeof initialError === "string" && initialError.length > 0;

  const [product, setProduct] = React.useState<ProductDetailDTO | null>(() =>
    hasInitialProduct ? initialProduct : null
  );
  const [isLoading, setIsLoading] = React.useState<boolean>(
    () => !hasInitialProduct && !hasInitialError
  );
  const [notFound, setNotFound] = React.useState(false);
  const [error, setError] = React.useState<string | null>(() =>
    hasInitialError ? initialError : null
  );
  const [reloadNonce, setReloadNonce] = React.useState(0);

  const [selectedImageIndex, setSelectedImageIndex] = React.useState<number>(() =>
    getInitialImageIndex(hasInitialProduct ? initialProduct : null)
  );
  const [selectedVariantId, setSelectedVariantId] = React.useState<string | null>(() =>
    getInitialVariantId(hasInitialProduct ? initialProduct : null)
  );
  const [availabilityOverrides, setAvailabilityOverrides] = React.useState<
    Record<string, VariantAvailabilityDTO>
  >({});
  const [quantity, setQuantity] = React.useState(1);

  const [isAdding, setIsAdding] = React.useState(false);
  const [addFeedback, setAddFeedback] = React.useState<{
    type: "error" | "reauth" | "success";
    message: string;
  } | null>(null);

  const thumbnailRefs = React.useRef<(HTMLButtonElement | null)[]>([]);
  const hydratedSlugRef = React.useRef<string | null>(hasInitialProduct ? slug : null);
  const initialErrorSlugRef = React.useRef<string | null>(hasInitialError ? slug : null);

  React.useEffect(() => {
    if (
      reloadNonce === 0 &&
      (hydratedSlugRef.current === slug || initialErrorSlugRef.current === slug)
    ) {
      return;
    }
    hydratedSlugRef.current = null;
    initialErrorSlugRef.current = null;

    const controller = new AbortController();
    setIsLoading(true);
    setNotFound(false);
    setError(null);
    setAddFeedback(null);
    setAvailabilityOverrides({});

    catalogApi
      .getProductBySlug(slug, controller.signal)
      .then((data) => {
        setProduct(data);
        setSelectedImageIndex(getInitialImageIndex(data));
        setSelectedVariantId(getInitialVariantId(data));
        setQuantity(1);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load product details.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [slug, reloadNonce]);

  const sortedImages = React.useMemo(() => {
    if (!product) {
      return [];
    }
    return [...product.images].sort((a, b) => a.sort_order - b.sort_order);
  }, [product]);

  const activeVariants = React.useMemo(() => {
    if (!product) {
      return [];
    }
    return product.variants.filter((v) => v.is_active);
  }, [product]);

  const attributeGroups = React.useMemo(
    () => extractAttributeGroups(activeVariants),
    [activeVariants]
  );

  const selectedVariant = React.useMemo(() => {
    if (!selectedVariantId) {
      return activeVariants[0] ?? null;
    }
    return activeVariants.find((v) => v.id === selectedVariantId) ?? activeVariants[0] ?? null;
  }, [activeVariants, selectedVariantId]);

  const liveAvailability = React.useMemo(() => {
    if (!selectedVariant) {
      return null;
    }
    const override = availabilityOverrides[selectedVariant.id];
    if (override) {
      return {
        isActive: override.is_active,
        availableQuantity: override.available_quantity,
        inStock: override.is_active && override.in_stock && override.available_quantity > 0,
        lowStock: override.is_active && override.low_stock,
      };
    }
    return {
      isActive: selectedVariant.is_active,
      availableQuantity: selectedVariant.available_quantity,
      inStock: selectedVariant.in_stock && selectedVariant.available_quantity > 0,
      lowStock: selectedVariant.low_stock,
    };
  }, [selectedVariant, availabilityOverrides]);

  const maxOrderQuantity = liveAvailability?.inStock
    ? Math.min(99, Math.max(1, liveAvailability.availableQuantity))
    : 1;

  React.useEffect(() => {
    setQuantity((prev) => Math.min(Math.max(1, prev), maxOrderQuantity));
  }, [maxOrderQuantity, selectedVariantId]);

  const handleSelectVariant = (variantId: string) => {
    setSelectedVariantId(variantId);
    setQuantity(1);
    setAddFeedback(null);
  };

  const handleAttributeSelect = (attrKey: string, attrValue: string) => {
    if (!selectedVariant || activeVariants.length === 0) {
      return;
    }
    const candidates = activeVariants.filter(
      (v) => String(v.attributes[attrKey]).trim() === attrValue
    );
    if (candidates.length === 0) {
      return;
    }

    // Prefer variant matching all other currently selected attributes
    let bestMatch = candidates[0];
    let bestScore = -1;
    for (const candidate of candidates) {
      let score = 0;
      for (const [k, v] of Object.entries(selectedVariant.attributes)) {
        if (k !== attrKey && String(candidate.attributes[k]).trim() === String(v).trim()) {
          score += 2;
        }
      }
      if (candidate.in_stock) {
        score += 1;
      }
      if (score > bestScore) {
        bestScore = score;
        bestMatch = candidate;
      }
    }
    handleSelectVariant(bestMatch.id);
  };

  const handleThumbnailKeyDown = (e: React.KeyboardEvent, idx: number) => {
    if (sortedImages.length <= 1) {
      return;
    }
    let nextIdx: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      nextIdx = (idx + 1) % sortedImages.length;
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      nextIdx = (idx - 1 + sortedImages.length) % sortedImages.length;
    }
    if (nextIdx !== null) {
      e.preventDefault();
      setSelectedImageIndex(nextIdx);
      thumbnailRefs.current[nextIdx]?.focus();
    }
  };

  const handleAddToCart = async () => {
    if (!product || !selectedVariant || !liveAvailability?.inStock) {
      return;
    }
    setIsAdding(true);
    setAddFeedback(null);

    try {
      await addItem(selectedVariant.id, quantity, product.title);
      setAddFeedback({
        type: "success",
        message: `Added ${quantity} × ${product.title} to your cart`,
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401 && err.code === "AUTH_SESSION_EXPIRED") {
          setAddFeedback({
            type: "reauth",
            message: "Your session has expired. Please sign in again to add items to your bag.",
          });
          return;
        }

        if (
          err.status === 409 &&
          (err.code === "INSUFFICIENT_STOCK" || err.code === "VARIANT_UNAVAILABLE")
        ) {
          try {
            const fresh = await inventoryApi.getVariantAvailability(selectedVariant.id);
            setAvailabilityOverrides((prev) => ({
              ...prev,
              [selectedVariant.id]: fresh,
            }));
            if (!fresh.is_active || !fresh.in_stock || fresh.available_quantity <= 0) {
              setAddFeedback({
                type: "error",
                message: "This option is no longer available.",
              });
            } else {
              setQuantity((prev) => Math.min(prev, fresh.available_quantity));
              setAddFeedback({
                type: "error",
                message: `Only ${fresh.available_quantity} unit(s) currently available.`,
              });
            }
            return;
          } catch {
            // Fall through to generic API message if availability revalidation fails
          }
        }

        setAddFeedback({
          type: "error",
          message: err.message,
        });
        return;
      }

      setAddFeedback({
        type: "error",
        message: err instanceof Error ? err.message : "Could not add item to cart.",
      });
    } finally {
      setIsAdding(false);
    }
  };

  if (isLoading) {
    return (
      <div
        role="status"
        aria-label="Loading product details"
        className="mx-auto max-w-[1440px] px-4 py-10 sm:px-6 lg:px-8"
      >
        <Skeleton className="mb-6 h-4 w-56" />
        <div className="grid gap-10 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <Skeleton className="aspect-[4/5] w-full" />
          </div>
          <div className="space-y-4 lg:col-span-5">
            <Skeleton className="h-8 w-3/4" />
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-7 w-28" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        </div>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16 sm:px-6">
        <EmptyState
          title="Product unavailable"
          description="This item may have been archived or moved from our active catalog."
          action={
            <Button asChild>
              <Link href="/products">Back to Catalog</Link>
            </Button>
          }
        />
      </div>
    );
  }

  if (error || !product) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16 sm:px-6">
        <ErrorState
          title="Unable to load product"
          message={error || "Product details could not be loaded."}
          onRetry={() => setReloadNonce((n) => n + 1)}
          action={
            <Button variant="outline" asChild>
              <Link href="/products">Browse Catalog</Link>
            </Button>
          }
        />
      </div>
    );
  }

  const activeImage = sortedImages[selectedImageIndex] ?? sortedImages[0] ?? null;
  const savingsPct = selectedVariant
    ? computeSavingsPercent(selectedVariant.price_cents, selectedVariant.compare_at_price_cents)
    : null;

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="mb-6 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Link href="/" className="hover:text-foreground">
          Home
        </Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        <Link
          href={`/products?category_slug=${encodeURIComponent(product.category.slug)}`}
          className="hover:text-foreground"
        >
          {product.category.name}
        </Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        <span aria-current="page" className="truncate font-medium text-foreground">
          {product.title}
        </span>
      </nav>

      {/* Main 2-Col PDP */}
      <div className="grid gap-10 lg:grid-cols-12 lg:gap-12">
        {/* Gallery (55% -> 7/12) */}
        <div className="flex flex-col-reverse gap-4 lg:col-span-7 lg:flex-row">
          {sortedImages.length > 1 ? (
            <div
              role="list"
              aria-label="Product gallery thumbnails"
              className="flex gap-3 overflow-x-auto pb-1 lg:w-20 lg:flex-col lg:overflow-visible"
            >
              {sortedImages.map((img, idx) => {
                const isSelected = idx === selectedImageIndex;
                return (
                  <button
                    key={img.id}
                    ref={(el) => {
                      thumbnailRefs.current[idx] = el;
                    }}
                    type="button"
                    aria-label={`View image ${idx + 1} of ${sortedImages.length}`}
                    aria-current={isSelected ? "true" : undefined}
                    onClick={() => setSelectedImageIndex(idx)}
                    onKeyDown={(e) => handleThumbnailKeyDown(e, idx)}
                    className={cn(
                      "relative aspect-[4/5] w-16 shrink-0 overflow-hidden rounded-md border bg-muted transition-all lg:w-20",
                      isSelected
                        ? "border-accent ring-2 ring-accent ring-offset-2 ring-offset-background"
                        : "border-border opacity-75 hover:opacity-100"
                    )}
                  >
                    <ProductImage
                      src={img.url}
                      alt={img.alt_text || `${product.title} view ${idx + 1}`}
                      fallbackLabel={product.title}
                      sizes="80px"
                    />
                  </button>
                );
              })}
            </div>
          ) : null}

          <div className="relative aspect-[4/5] flex-1 overflow-hidden rounded-lg border border-border bg-muted">
            <ProductImage
              src={activeImage?.url}
              alt={activeImage?.alt_text || product.title}
              fallbackLabel={product.title}
              sizes="(max-width: 1024px) 100vw, 55vw"
              priority
            />
          </div>
        </div>

        {/* Buy Box (45% -> 5/12) */}
        <div className="space-y-6 lg:col-span-5">
          <div className="space-y-2 border-b border-border pb-6">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {product.category.name}
            </p>
            <h1 className="text-2xl font-semibold tracking-[-0.015em] text-foreground sm:text-3xl">
              {product.title}
            </h1>
            {selectedVariant ? (
              <p className="font-mono text-xs text-muted-foreground">
                SKU: {selectedVariant.sku}
              </p>
            ) : null}

            {/* Price Display */}
            {selectedVariant ? (
              <div className="flex flex-wrap items-baseline gap-3 pt-2">
                <span className="text-2xl font-semibold text-foreground tabular-nums">
                  {formatCents(selectedVariant.price_cents, selectedVariant.currency)}
                </span>
                {selectedVariant.compare_at_price_cents != null &&
                selectedVariant.compare_at_price_cents > selectedVariant.price_cents ? (
                  <span className="text-base text-muted-foreground line-through tabular-nums">
                    {formatCents(
                      selectedVariant.compare_at_price_cents,
                      selectedVariant.currency
                    )}
                  </span>
                ) : null}
                {savingsPct !== null ? (
                  <Badge variant="accent">Save {savingsPct}%</Badge>
                ) : null}
              </div>
            ) : null}
          </div>

          {/* Variants */}
          {activeVariants.length === 0 ? (
            <Alert variant="warning">
              <AlertTitle>Currently unavailable</AlertTitle>
              <AlertDescription>
                No active options are currently available for this product.
              </AlertDescription>
            </Alert>
          ) : (
            <div className="space-y-5">
              {attributeGroups ? (
                attributeGroups.map((group) => {
                  const currentValue = selectedVariant
                    ? String(selectedVariant.attributes[group.key]).trim()
                    : "";
                  return (
                    <div key={group.key} className="space-y-2.5">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium text-foreground">{group.label}</span>
                        <span className="text-muted-foreground">{currentValue}</span>
                      </div>
                      <div
                        role="radiogroup"
                        aria-label={group.label}
                        className="flex flex-wrap gap-2"
                      >
                        {group.values.map((val) => {
                          const isChecked = val === currentValue;
                          // Determine if choosing this value alongside other active attributes maps to an out-of-stock variant
                          const exactVariant = activeVariants.find((v) => {
                            if (String(v.attributes[group.key]).trim() !== val) {
                              return false;
                            }
                            if (!selectedVariant) {
                              return true;
                            }
                            return Object.entries(selectedVariant.attributes).every(
                              ([k, otherVal]) =>
                                k === group.key ||
                                String(v.attributes[k]).trim() === String(otherVal).trim()
                            );
                          });
                          const variantStockKnown = exactVariant ?? activeVariants.find(
                            (v) => String(v.attributes[group.key]).trim() === val
                          );
                          const isOutOfStock = variantStockKnown
                            ? !variantStockKnown.in_stock ||
                              variantStockKnown.available_quantity <= 0
                            : false;

                          return (
                            <button
                              key={val}
                              type="button"
                              role="radio"
                              aria-checked={isChecked}
                              onClick={() => handleAttributeSelect(group.key, val)}
                              className={cn(
                                "min-h-[44px] rounded-md border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background lg:min-h-[38px]",
                                isChecked
                                  ? "border-primary bg-primary text-primary-foreground"
                                  : isOutOfStock
                                    ? "border-dashed border-border-control bg-surface text-muted-foreground hover:border-foreground"
                                    : "border-border-control bg-surface text-foreground hover:border-foreground"
                              )}
                            >
                              <span>{val}</span>
                              {isOutOfStock ? (
                                <span className="sr-only"> (Out of stock)</span>
                              ) : null}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="space-y-2.5">
                  <span className="block text-sm font-medium text-foreground">Option</span>
                  <div
                    role="radiogroup"
                    aria-label="Option"
                    className="flex flex-wrap gap-2"
                  >
                    {activeVariants.map((variant) => {
                      const isChecked = variant.id === selectedVariant?.id;
                      const override = availabilityOverrides[variant.id];
                      const inStock = override
                        ? override.is_active && override.in_stock && override.available_quantity > 0
                        : variant.in_stock && variant.available_quantity > 0;
                      return (
                        <button
                          key={variant.id}
                          type="button"
                          role="radio"
                          aria-checked={isChecked}
                          onClick={() => handleSelectVariant(variant.id)}
                          className={cn(
                            "min-h-[44px] rounded-md border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background lg:min-h-[38px]",
                            isChecked
                              ? "border-primary bg-primary text-primary-foreground"
                              : !inStock
                                ? "border-dashed border-border-control bg-surface text-muted-foreground hover:border-foreground"
                                : "border-border-control bg-surface text-foreground hover:border-foreground"
                          )}
                        >
                          <span>{variant.title}</span>
                          {!inStock ? <span className="sr-only"> (Out of stock)</span> : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Live Stock Indicator */}
              {liveAvailability ? (
                <div aria-live="polite" className="flex items-center gap-2 pt-1">
                  {!liveAvailability.inStock || liveAvailability.availableQuantity <= 0 ? (
                    <Badge variant="destructive">Out of stock</Badge>
                  ) : liveAvailability.lowStock ? (
                    <Badge variant="warning" className="tabular-nums">
                      Low stock — only {liveAvailability.availableQuantity} left
                    </Badge>
                  ) : (
                    <span className="inline-flex items-center gap-2 text-sm font-medium text-success">
                      <span
                        className="h-2 w-2 rounded-full bg-success"
                        aria-hidden="true"
                      />
                      In stock
                    </span>
                  )}
                </div>
              ) : null}

              {/* Add Feedback Alert */}
              {addFeedback?.type === "error" ? (
                <Alert variant="destructive" role="alert">
                  <AlertCircle className="h-4 w-4" aria-hidden="true" />
                  <AlertDescription>{addFeedback.message}</AlertDescription>
                </Alert>
              ) : addFeedback?.type === "reauth" ? (
                <Alert variant="warning" role="alert">
                  <AlertCircle className="h-4 w-4" aria-hidden="true" />
                  <AlertTitle>Authentication required</AlertTitle>
                  <AlertDescription className="mt-1 space-y-2">
                    <p>{addFeedback.message}</p>
                    <Button size="sm" asChild>
                      <Link href={`/login?returnTo=${encodeURIComponent(`/products/${product.slug}`)}`}>
                        Sign In to Continue
                      </Link>
                    </Button>
                  </AlertDescription>
                </Alert>
              ) : null}

              {/* Polite announcement for screen readers on Add to Cart success */}
              <div role="status" aria-live="polite" className="sr-only">
                {addFeedback?.type === "success" ? addFeedback.message : ""}
              </div>

              {/* Quantity Stepper + Add to Cart CTA */}
              <div className="flex flex-col gap-3 sm:flex-row">
                <div className="inline-flex items-center justify-between rounded-md border border-border-control bg-surface sm:w-36">
                  <button
                    type="button"
                    disabled={!liveAvailability?.inStock || isAdding || quantity <= 1}
                    onClick={() => setQuantity((q) => Math.max(1, q - 1))}
                    aria-label="Decrease quantity"
                    className="inline-flex h-12 w-12 items-center justify-center text-foreground transition-colors hover:bg-muted disabled:opacity-40"
                  >
                    <Minus className="h-4 w-4" aria-hidden="true" />
                  </button>
                  <span
                    className="text-sm font-semibold tabular-nums"
                    aria-label="Selected quantity"
                  >
                    {liveAvailability?.inStock ? quantity : 0}
                  </span>
                  <button
                    type="button"
                    disabled={
                      !liveAvailability?.inStock || isAdding || quantity >= maxOrderQuantity
                    }
                    onClick={() => setQuantity((q) => Math.min(maxOrderQuantity, q + 1))}
                    aria-label="Increase quantity"
                    className="inline-flex h-12 w-12 items-center justify-center text-foreground transition-colors hover:bg-muted disabled:opacity-40"
                  >
                    <Plus className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>

                <Button
                  size="lg"
                  className="flex-1"
                  disabled={!liveAvailability?.inStock || isAdding}
                  aria-busy={isAdding || undefined}
                  onClick={() => void handleAddToCart()}
                >
                  <ShoppingBag className="h-4 w-4" aria-hidden="true" />
                  {!liveAvailability?.inStock
                    ? "Out of Stock"
                    : isAdding
                      ? "Adding..."
                      : "Add to Cart"}
                </Button>
              </div>
            </div>
          )}

          {/* Product Editorial Description */}
          {product.description ? (
            <div className="border-t border-border pt-6">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                Details
              </h2>
              <p className="mt-3 whitespace-pre-line text-base leading-relaxed text-foreground">
                {product.description}
              </p>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
