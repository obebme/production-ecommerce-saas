"use client";

import * as React from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, Minus, Plus, ShoppingBag, Trash2 } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { formatCents } from "@/lib/format";
import { useAuth } from "@/providers/auth-provider";
import { useCart } from "@/providers/cart-provider";
import { ProductImage } from "@/components/shared/product-image";
import { EmptyState, ErrorState } from "@/components/shared/state-feedback";
import {
  classifyCartItem,
  ClearCartDialog,
  FreeShippingProgress,
} from "@/components/storefront/cart-helpers";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function CartPageSkeleton() {
  return (
    <div className="grid gap-8 lg:grid-cols-12" role="status" aria-label="Loading shopping bag">
      <span className="sr-only">Loading shopping bag...</span>
      <div className="space-y-4 lg:col-span-8">
        {[1, 2].map((idx) => (
          <div
            key={idx}
            className="flex gap-4 rounded-lg border border-border bg-surface p-4 sm:p-6"
          >
            <Skeleton className="aspect-[4/5] w-24 shrink-0 sm:w-28" />
            <div className="flex-1 space-y-3">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-9 w-32" />
            </div>
          </div>
        ))}
      </div>
      <div className="lg:col-span-4">
        <Skeleton className="h-72 w-full rounded-lg" />
      </div>
    </div>
  );
}

export function CartPageView() {
  const { isLoading: authLoading, sessionExpired, accountDisabled } = useAuth();
  const {
    cart,
    isLoading,
    error,
    pendingItemIds,
    priceNotices,
    refreshCart,
    updateQuantity,
    removeItem,
    clearCart,
  } = useCart();

  const [mutationError, setMutationError] = React.useState<string | null>(null);
  const [confirmClearOpen, setConfirmClearOpen] = React.useState(false);
  const [isClearing, setIsClearing] = React.useState(false);

  const items = cart?.items ?? [];
  const itemCount = cart?.item_count ?? 0;
  const subtotalCents = cart?.subtotal_cents ?? 0;
  const currency = cart?.currency ?? "USD";
  const hasUnavailableItems = items.some((item) => !item.is_available);

  const handleQuantityChange = async (itemId: string, nextQty: number) => {
    setMutationError(null);
    try {
      await updateQuantity(itemId, nextQty);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
        setMutationError(err.message);
        await refreshCart();
      } else {
        setMutationError(err instanceof Error ? err.message : "Could not update item quantity.");
      }
    }
  };

  const handleRemove = async (itemId: string) => {
    setMutationError(null);
    try {
      await removeItem(itemId);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        await refreshCart();
        return;
      }
      setMutationError(err instanceof Error ? err.message : "Could not remove item.");
    }
  };

  const handleConfirmClear = async () => {
    setIsClearing(true);
    setMutationError(null);
    try {
      await clearCart();
      setConfirmClearOpen(false);
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : "Could not clear shopping bag.");
    } finally {
      setIsClearing(false);
    }
  };

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-10 sm:px-6 lg:px-8">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="font-display text-3xl font-medium tracking-[-0.02em] text-foreground">
            Shopping Bag
          </h1>
          <p className="mt-1 text-sm text-muted-foreground tabular-nums">
            {itemCount} {itemCount === 1 ? "item" : "items"} in your bag · prices and stock
            reconciled by the server
          </p>
        </div>

        <Link
          href="/products"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-accent underline-offset-4 hover:underline"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Continue Shopping
        </Link>
      </div>

      {sessionExpired ? (
        <Alert variant="warning" role="alert" className="mb-6">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Session expired</AlertTitle>
          <AlertDescription className="mt-1 space-y-3">
            <p>Your session has expired. Please sign in again to access your shopping bag.</p>
            <Button size="sm" asChild>
              <Link href="/login?expired=1&returnTo=%2Fcart">Sign In</Link>
            </Button>
          </AlertDescription>
        </Alert>
      ) : accountDisabled ? (
        <Alert variant="destructive" role="alert" className="mb-6">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Account disabled</AlertTitle>
          <AlertDescription>
            Your account has been deactivated. Please contact an administrator.
          </AlertDescription>
        </Alert>
      ) : (authLoading || isLoading) && !cart ? (
        <CartPageSkeleton />
      ) : error && !cart ? (
        <ErrorState
          title="Unable to load shopping bag"
          message={error}
          onRetry={() => void refreshCart()}
        />
      ) : items.length === 0 || !cart ? (
        <EmptyState
          title="Your shopping bag is empty"
          description="Explore our catalog to find wardrobe staples and everyday essentials."
          icon={<ShoppingBag className="h-5 w-5" aria-hidden="true" />}
          action={
            <Button asChild size="lg">
              <Link href="/products">Explore Products</Link>
            </Button>
          }
        />
      ) : (
        <div className="grid gap-8 lg:grid-cols-12">
          <div className="space-y-4 lg:col-span-8">
            {mutationError ? (
              <Alert variant="destructive" role="alert">
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                <AlertDescription>{mutationError}</AlertDescription>
              </Alert>
            ) : null}

            {error ? (
              <Alert variant="destructive" role="alert">
                <AlertDescription className="flex items-center justify-between gap-2">
                  <span>{error}</span>
                  <Button variant="outline" size="sm" onClick={() => void refreshCart()}>
                    Retry
                  </Button>
                </AlertDescription>
              </Alert>
            ) : null}

            <ul className="space-y-4">
              {items.map((item) => {
                const isPending = pendingItemIds.has(item.id);
                const { isOutOfStock, exceedsAvailable, isDeactivated, maxAllowed } =
                  classifyCartItem(item);
                const updatedUnitPrice = priceNotices[item.variant_id];

                return (
                  <li
                    key={item.id}
                    className={`rounded-lg border p-4 sm:p-6 ${
                      isOutOfStock || isDeactivated
                        ? "border-destructive bg-destructive-subtle/40"
                        : exceedsAvailable
                          ? "border-warning bg-warning-subtle/50"
                          : "border-border bg-surface"
                    }`}
                  >
                    <div className="flex gap-4 sm:gap-6">
                      <Link
                        href={`/products/${encodeURIComponent(item.product_slug)}`}
                        className="aspect-[4/5] w-24 shrink-0 overflow-hidden rounded-md border border-border bg-muted sm:w-28"
                      >
                        <ProductImage
                          src={item.image_url}
                          alt={item.product_title}
                          fallbackLabel={item.product_title}
                          sizes="(max-width: 640px) 96px, 112px"
                        />
                      </Link>

                      <div className="flex min-w-0 flex-1 flex-col justify-between">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0 space-y-1">
                            <Link
                              href={`/products/${encodeURIComponent(item.product_slug)}`}
                              className="block truncate text-base font-semibold text-foreground hover:text-accent"
                            >
                              {item.product_title}
                            </Link>
                            <p className="text-sm text-muted-foreground">{item.variant_title}</p>
                            <p className="font-mono text-xs text-muted-foreground">
                              SKU: {item.sku}
                            </p>
                          </div>

                          <div className="text-right">
                            <p className="text-base font-semibold text-foreground tabular-nums">
                              {formatCents(item.line_total_cents, currency)}
                            </p>
                            <p className="text-xs text-muted-foreground tabular-nums">
                              {formatCents(item.unit_price_cents, currency)} each
                            </p>
                          </div>
                        </div>

                        {updatedUnitPrice !== undefined ? (
                          <div className="mt-2">
                            <Badge variant="outline" className="text-xs">
                              Price updated to {formatCents(updatedUnitPrice, currency)}
                            </Badge>
                          </div>
                        ) : null}

                        {item.is_available && item.low_stock ? (
                          <p className="mt-2 text-xs font-medium text-warning tabular-nums">
                            Only {item.available_quantity} left
                          </p>
                        ) : null}

                        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                          <div className="inline-flex items-center rounded-md border border-border-control bg-surface">
                            <button
                              type="button"
                              disabled={isPending || item.quantity <= 1}
                              onClick={() => void handleQuantityChange(item.id, item.quantity - 1)}
                              aria-label={`Decrease quantity for ${item.product_title}`}
                              className="inline-flex h-11 w-11 items-center justify-center text-foreground transition-colors hover:bg-muted disabled:opacity-40 lg:h-9 lg:w-9"
                            >
                              <Minus className="h-3.5 w-3.5" aria-hidden="true" />
                            </button>
                            <span
                              className="min-w-[2.5rem] text-center text-sm font-medium tabular-nums"
                              aria-label={`Quantity for ${item.product_title}`}
                            >
                              {item.quantity}
                            </span>
                            <button
                              type="button"
                              disabled={
                                isPending ||
                                isOutOfStock ||
                                isDeactivated ||
                                item.quantity >= maxAllowed
                              }
                              onClick={() => void handleQuantityChange(item.id, item.quantity + 1)}
                              aria-label={`Increase quantity for ${item.product_title}`}
                              className="inline-flex h-11 w-11 items-center justify-center text-foreground transition-colors hover:bg-muted disabled:opacity-40 lg:h-9 lg:w-9"
                            >
                              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                            </button>
                          </div>

                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={isPending}
                            onClick={() => void handleRemove(item.id)}
                            aria-label={`Remove ${item.product_title}`}
                            className="text-muted-foreground hover:text-destructive"
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                            <span>Remove</span>
                          </Button>
                        </div>
                      </div>
                    </div>

                    {isOutOfStock ? (
                      <p className="mt-3 text-xs font-medium text-destructive" role="alert">
                        Out of stock — remove this item to proceed to checkout.
                      </p>
                    ) : exceedsAvailable ? (
                      <div
                        role="alert"
                        className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-sm bg-warning-subtle px-3 py-2 text-xs text-warning-subtle-foreground"
                      >
                        <span className="tabular-nums">
                          Only {item.available_quantity} left in stock
                        </span>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isPending}
                          onClick={() =>
                            void handleQuantityChange(item.id, item.available_quantity)
                          }
                          className="h-8 px-2.5 text-xs"
                        >
                          Adjust to {item.available_quantity}
                        </Button>
                      </div>
                    ) : isDeactivated ? (
                      <p className="mt-3 text-xs font-medium text-destructive" role="alert">
                        This item is no longer available — remove it to proceed to checkout.
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>

            <div className="flex justify-end pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmClearOpen(true)}
                className="text-destructive hover:text-destructive"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                Clear Cart
              </Button>
            </div>
          </div>

          <div className="lg:col-span-4">
            <Card className="lg:sticky lg:top-24">
              <CardHeader>
                <CardTitle>Order Summary</CardTitle>
              </CardHeader>
              <CardContent className="space-y-5">
                <FreeShippingProgress subtotalCents={subtotalCents} currency={currency} />

                <div className="space-y-2.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Subtotal</span>
                    <span className="font-medium tabular-nums">
                      {formatCents(cart.subtotal_cents, currency)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Estimated Shipping</span>
                    <span className="font-medium tabular-nums">
                      {cart.estimated_shipping_cents === 0
                        ? "Free"
                        : formatCents(cart.estimated_shipping_cents, currency)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Tax</span>
                    <span className="text-xs text-muted-foreground">Calculated at checkout</span>
                  </div>
                  <div className="flex justify-between border-t border-border pt-3 text-base font-semibold">
                    <span>Estimated Total</span>
                    <span className="tabular-nums">
                      {formatCents(cart.estimated_total_cents, currency)}
                    </span>
                  </div>
                </div>

                {hasUnavailableItems ? (
                  <Alert variant="destructive" role="alert">
                    <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                    <AlertDescription>
                      Resolve unavailable or out-of-stock items before proceeding to checkout.
                    </AlertDescription>
                  </Alert>
                ) : null}

                {hasUnavailableItems ? (
                  <Button size="lg" className="w-full" disabled>
                    Proceed to Checkout
                  </Button>
                ) : (
                  <Button size="lg" className="w-full" asChild>
                    <Link href="/checkout">Proceed to Checkout</Link>
                  </Button>
                )}

                <p className="text-center text-xs text-muted-foreground">
                  Taxes, shipping, and stock reservations are verified on the server at checkout.
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      <ClearCartDialog
        open={confirmClearOpen}
        onOpenChange={setConfirmClearOpen}
        itemCount={itemCount}
        isClearing={isClearing}
        onConfirm={() => void handleConfirmClear()}
      />
    </div>
  );
}
