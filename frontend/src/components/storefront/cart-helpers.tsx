"use client";

import * as React from "react";
import type { CartItemDTO } from "@/lib/api/types";
import { FREE_SHIPPING_THRESHOLD_CENTS, formatCents } from "@/lib/format";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface CartItemStatusFlags {
  isOutOfStock: boolean;
  exceedsAvailable: boolean;
  isDeactivated: boolean;
  maxAllowed: number;
}

/** Classify server-reconciled stock/availability flags for a cart line item. */
export function classifyCartItem(item: CartItemDTO): CartItemStatusFlags {
  const isOutOfStock = !item.in_stock || item.available_quantity === 0;
  const exceedsAvailable =
    item.in_stock && item.available_quantity > 0 && item.quantity > item.available_quantity;
  const isDeactivated =
    !item.is_available && item.in_stock && item.quantity <= item.available_quantity;
  const maxAllowed = Math.min(99, item.available_quantity);
  return { isOutOfStock, exceedsAvailable, isDeactivated, maxAllowed };
}

/** Free-shipping threshold progress bar matching server rule ($100.00 threshold). */
export function FreeShippingProgress({
  subtotalCents,
  currency,
  className,
}: {
  subtotalCents: number;
  currency: string;
  className?: string;
}) {
  const remaining = Math.max(0, FREE_SHIPPING_THRESHOLD_CENTS - subtotalCents);
  const progressRatio = Math.min(
    100,
    Math.round((subtotalCents / FREE_SHIPPING_THRESHOLD_CENTS) * 100)
  );

  return (
    <div className={className ?? "rounded-md border border-border bg-muted/60 p-3"}>
      {remaining > 0 ? (
        <>
          <p className="text-xs font-medium text-foreground tabular-nums">
            Add {formatCents(remaining, currency)} more for free standard shipping
          </p>
          <div
            role="progressbar"
            aria-label="Free standard shipping progress"
            aria-valuemin={0}
            aria-valuemax={FREE_SHIPPING_THRESHOLD_CENTS}
            aria-valuenow={subtotalCents}
            className="mt-2 h-2 w-full overflow-hidden rounded-full bg-secondary"
          >
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${progressRatio}%` }}
            />
          </div>
        </>
      ) : (
        <p className="text-xs font-medium text-success tabular-nums">
          Complimentary standard shipping unlocked ($0.00)
        </p>
      )}
    </div>
  );
}

/** Shared confirmation dialog for clearing the active shopping bag. */
export function ClearCartDialog({
  open,
  onOpenChange,
  itemCount,
  isClearing,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  itemCount: number;
  isClearing: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="storefront-theme">
        <DialogHeader>
          <DialogTitle>Clear shopping bag?</DialogTitle>
          <DialogDescription>
            This removes all {itemCount} {itemCount === 1 ? "item" : "items"} from your shopping bag.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isClearing}
          >
            Keep items
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={isClearing}
          >
            {isClearing ? "Clearing..." : "Clear Bag"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
