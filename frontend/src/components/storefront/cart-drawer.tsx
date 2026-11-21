"use client";

import * as React from "react";
import Link from "next/link";
import { AlertTriangle, Minus, Plus, ShoppingBag, Trash2 } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { formatCents } from "@/lib/format";
import { useAuth } from "@/providers/auth-provider";
import { useCart } from "@/providers/cart-provider";
import { ProductImage } from "@/components/shared/product-image";
import {
  classifyCartItem,
  ClearCartDialog,
  FreeShippingProgress,
} from "@/components/storefront/cart-helpers";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

export function CartDrawer() {
  const { sessionExpired } = useAuth();
  const {
    cart,
    isLoading,
    error,
    isDrawerOpen,
    setDrawerOpen,
    closeDrawer,
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
    <>
      <Sheet open={isDrawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent side="right" className="storefront-theme flex h-full flex-col p-0">
          <SheetHeader className="pr-14">
            <SheetTitle className="flex items-center gap-2">
              <span>Shopping Bag</span>
              <span className="text-sm font-normal text-muted-foreground tabular-nums">
                ({itemCount})
              </span>
            </SheetTitle>
            <SheetDescription>
              Live prices and inventory verified directly with the server.
            </SheetDescription>
          </SheetHeader>

          <div className="flex-1 overflow-y-auto px-6 py-4">
            {sessionExpired ? (
              <Alert variant="warning" role="alert" className="mb-4">
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                <AlertTitle>Session expired</AlertTitle>
                <AlertDescription className="mt-1 space-y-3">
                  <p>Please sign in again to access and modify your account shopping bag.</p>
                  <Button size="sm" asChild onClick={closeDrawer}>
                    <Link href="/login?returnTo=%2Fproducts">Sign In</Link>
                  </Button>
                </AlertDescription>
              </Alert>
            ) : null}

            {mutationError ? (
              <Alert variant="destructive" role="alert" className="mb-4">
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                <AlertDescription>{mutationError}</AlertDescription>
              </Alert>
            ) : null}

            {error && !sessionExpired ? (
              <Alert variant="destructive" role="alert" className="mb-4">
                <AlertDescription className="flex items-center justify-between gap-2">
                  <span>{error}</span>
                  <Button variant="outline" size="sm" onClick={() => void refreshCart()}>
                    Retry
                  </Button>
                </AlertDescription>
              </Alert>
            ) : null}

            {/* Free Shipping Progress */}
            {!sessionExpired && itemCount > 0 ? (
              <FreeShippingProgress
                subtotalCents={subtotalCents}
                currency={currency}
                className="mb-5 rounded-md border border-border bg-muted/60 p-3"
              />
            ) : null}

            {/* Cart Body */}
            {isLoading && !cart ? (
              <div className="space-y-4" role="status" aria-label="Loading shopping bag">
                {[1, 2].map((idx) => (
                  <div key={idx} className="flex gap-4 border-b border-border pb-4">
                    <Skeleton className="aspect-[4/5] w-20 shrink-0" />
                    <div className="flex-1 space-y-2">
                      <Skeleton className="h-4 w-3/4" />
                      <Skeleton className="h-3 w-1/2" />
                      <Skeleton className="h-8 w-28" />
                    </div>
                  </div>
                ))}
              </div>
            ) : !sessionExpired && items.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <ShoppingBag className="h-5 w-5" aria-hidden="true" />
                </div>
                <p className="text-base font-semibold text-foreground">Your bag is empty</p>
                <p className="mt-1 max-w-xs text-sm text-muted-foreground">
                  Explore our latest collection and add items to begin.
                </p>
                <Button className="mt-5" asChild onClick={closeDrawer}>
                  <Link href="/products">Explore Products</Link>
                </Button>
              </div>
            ) : (
              <ul className="space-y-4">
                {items.map((item) => {
                  const isPending = pendingItemIds.has(item.id);
                  const { isOutOfStock, exceedsAvailable, isDeactivated, maxAllowed } =
                    classifyCartItem(item);
                  const updatedUnitPrice = priceNotices[item.variant_id];

                  return (
                    <li
                      key={item.id}
                      className={`rounded-lg border p-3 ${
                        isOutOfStock || isDeactivated
                          ? "border-destructive bg-destructive-subtle/40"
                          : exceedsAvailable
                            ? "border-warning bg-warning-subtle/50"
                            : "border-border bg-surface"
                      }`}
                    >
                      <div className="flex gap-3.5">
                        <Link
                          href={`/products/${encodeURIComponent(item.product_slug)}`}
                          onClick={closeDrawer}
                          className="aspect-[4/5] w-20 shrink-0 overflow-hidden rounded-md border border-border bg-muted"
                        >
                          <ProductImage
                            src={item.image_url}
                            alt={item.product_title}
                            fallbackLabel={item.product_title}
                            sizes="80px"
                          />
                        </Link>

                        <div className="flex min-w-0 flex-1 flex-col justify-between">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <Link
                                href={`/products/${encodeURIComponent(item.product_slug)}`}
                                onClick={closeDrawer}
                                className="block truncate text-sm font-semibold text-foreground hover:text-accent"
                              >
                                {item.product_title}
                              </Link>
                              <p className="truncate text-xs text-muted-foreground">
                                {item.variant_title}
                              </p>
                              <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                                {item.sku}
                              </p>
                            </div>

                            <div className="text-right">
                              <p className="text-sm font-semibold text-foreground tabular-nums">
                                {formatCents(item.line_total_cents, currency)}
                              </p>
                              <p className="text-xs text-muted-foreground tabular-nums">
                                {formatCents(item.unit_price_cents, currency)} ea
                              </p>
                            </div>
                          </div>

                          {updatedUnitPrice !== undefined ? (
                            <div className="mt-1.5">
                              <Badge variant="outline" className="text-[11px]">
                                Price updated to {formatCents(updatedUnitPrice, currency)}
                              </Badge>
                            </div>
                          ) : null}

                          {item.is_available && item.low_stock ? (
                            <p className="mt-1.5 text-xs font-medium text-warning tabular-nums">
                              Only {item.available_quantity} left
                            </p>
                          ) : null}

                          <div className="mt-3 flex items-center justify-between gap-2">
                            <div className="inline-flex items-center rounded-md border border-border-control bg-surface">
                              <button
                                type="button"
                                disabled={isPending || item.quantity <= 1}
                                onClick={() => void handleQuantityChange(item.id, item.quantity - 1)}
                                aria-label={`Decrease quantity for ${item.product_title}`}
                                className="inline-flex h-11 w-11 items-center justify-center text-foreground transition-colors hover:bg-muted disabled:opacity-40 lg:h-8 lg:w-8"
                              >
                                <Minus className="h-3.5 w-3.5" aria-hidden="true" />
                              </button>
                              <span
                                className="min-w-[2rem] text-center text-sm font-medium tabular-nums"
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
                                className="inline-flex h-11 w-11 items-center justify-center text-foreground transition-colors hover:bg-muted disabled:opacity-40 lg:h-8 lg:w-8"
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
                              <span className="sr-only sm:not-sr-only">Remove</span>
                            </Button>
                          </div>
                        </div>
                      </div>

                      {isOutOfStock ? (
                        <p className="mt-2.5 text-xs font-medium text-destructive">
                          Out of stock — remove this item to proceed to checkout.
                        </p>
                      ) : exceedsAvailable ? (
                        <div className="mt-2.5 flex items-center justify-between gap-2 rounded-sm bg-warning-subtle px-2.5 py-1.5 text-xs text-warning-subtle-foreground">
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
                            className="h-7 px-2 text-xs"
                          >
                            Adjust to {item.available_quantity}
                          </Button>
                        </div>
                      ) : isDeactivated ? (
                        <p className="mt-2.5 text-xs font-medium text-destructive">
                          This item is no longer available — remove it to proceed to checkout.
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {!sessionExpired && items.length > 0 && cart ? (
            <SheetFooter className="space-y-4">
              <div className="space-y-1.5 text-sm">
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
                <div className="flex justify-between border-t border-border pt-2 text-base font-semibold">
                  <span>Estimated Total</span>
                  <span className="tabular-nums">
                    {formatCents(cart.estimated_total_cents, currency)}
                  </span>
                </div>
              </div>

              {hasUnavailableItems ? (
                <p className="text-xs font-medium text-destructive" role="alert">
                  Resolve unavailable or out-of-stock items before proceeding to checkout.
                </p>
              ) : null}

              <div className="space-y-2">
                {hasUnavailableItems || items.length === 0 ? (
                  <Button size="lg" className="w-full" disabled>
                    Proceed to Checkout
                  </Button>
                ) : (
                  <Button size="lg" className="w-full" asChild>
                    <Link href="/checkout" onClick={closeDrawer}>
                      Proceed to Checkout
                    </Link>
                  </Button>
                )}
                <div className="flex items-center justify-between gap-2 pt-1 text-xs">
                  <Link
                    href="/cart"
                    onClick={closeDrawer}
                    className="font-medium text-accent underline-offset-4 hover:underline"
                  >
                    View Full Cart
                  </Link>
                  <button
                    type="button"
                    onClick={() => setConfirmClearOpen(true)}
                    className="shrink-0 font-medium text-destructive hover:underline"
                  >
                    Clear bag
                  </button>
                </div>
              </div>
            </SheetFooter>
          ) : null}
        </SheetContent>
      </Sheet>

      <ClearCartDialog
        open={confirmClearOpen}
        onOpenChange={setConfirmClearOpen}
        itemCount={itemCount}
        isClearing={isClearing}
        onConfirm={() => void handleConfirmClear()}
      />
    </>
  );
}
