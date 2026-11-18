"use client";

import * as React from "react";
import { ApiError, cartApi } from "@/lib/api/client";
import type { CartDTO } from "@/lib/api/types";
import { useAuth } from "./auth-provider";
import { useToast } from "@/components/ui/toast";

interface CartContextValue {
  cart: CartDTO | null;
  isLoading: boolean;
  error: string | null;
  isDrawerOpen: boolean;
  pendingItemIds: Set<string>;
  priceNotices: Record<string, number>;
  openDrawer: () => void;
  closeDrawer: () => void;
  setDrawerOpen: (open: boolean) => void;
  refreshCart: () => Promise<CartDTO | null>;
  addItem: (variantId: string, quantity: number, productTitle?: string) => Promise<CartDTO>;
  updateQuantity: (itemId: string, quantity: number) => Promise<CartDTO>;
  removeItem: (itemId: string) => Promise<CartDTO>;
  clearCart: () => Promise<CartDTO>;
}

const CartContext = React.createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const {
    user,
    isLoading: authLoading,
    sessionExpired,
    accountDisabled,
    subscribeMergedCart,
    subscribeLogout,
  } = useAuth();
  const { toast } = useToast();

  const [cart, setCart] = React.useState<CartDTO | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = React.useState(false);
  const [pendingItemIds, setPendingItemIds] = React.useState<Set<string>>(() => new Set());
  const [priceNotices, setPriceNotices] = React.useState<Record<string, number>>({});

  const observedPricesRef = React.useRef<Map<string, number>>(new Map());
  const guestSnapshotRef = React.useRef<CartDTO | null>(null);
  const skipNextAutoRefreshRef = React.useRef(false);

  const reconcileCartState = React.useCallback((nextCart: CartDTO, isAnonymous: boolean) => {
    const nextNotices: Record<string, number> = {};
    for (const item of nextCart.items) {
      const previousPrice = observedPricesRef.current.get(item.variant_id);
      if (previousPrice !== undefined && previousPrice !== item.unit_price_cents) {
        nextNotices[item.variant_id] = item.unit_price_cents;
      }
      observedPricesRef.current.set(item.variant_id, item.unit_price_cents);
    }
    if (Object.keys(nextNotices).length > 0) {
      setPriceNotices((prev) => ({ ...prev, ...nextNotices }));
    }

    if (isAnonymous) {
      guestSnapshotRef.current = nextCart;
    } else {
      guestSnapshotRef.current = null;
    }

    setCart(nextCart);
    setError(null);
  }, []);

  const refreshCart = React.useCallback(
    async (signal?: AbortSignal): Promise<CartDTO | null> => {
      setIsLoading(true);
      try {
        const nextCart = await cartApi.getCart(signal);
        reconcileCartState(nextCart, user === null);
        return nextCart;
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return null;
        }
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          // Fail closed: clear cart when session expired/disabled
          setCart(null);
          setError(err.message);
          return null;
        }
        setError(err instanceof Error ? err.message : "Unable to load your shopping bag.");
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [reconcileCartState, user]
  );

  // Initial cart load and reload when auth settles
  React.useEffect(() => {
    if (authLoading) {
      return;
    }
    if ((sessionExpired || accountDisabled) && user === null) {
      setCart(null);
      setIsLoading(false);
      return;
    }
    if (skipNextAutoRefreshRef.current) {
      skipNextAutoRefreshRef.current = false;
      return;
    }
    const controller = new AbortController();
    void refreshCart(controller.signal);
    return () => {
      controller.abort();
    };
  }, [authLoading, user, sessionExpired, accountDisabled, refreshCart]);

  // Listen to guest-to-user cart merge after login/register
  React.useEffect(() => {
    return subscribeMergedCart((mergedCart) => {
      const priorGuest = guestSnapshotRef.current;
      skipNextAutoRefreshRef.current = true;
      reconcileCartState(mergedCart, false);
      setIsLoading(false);

      if (priorGuest && priorGuest.item_count > 0) {
        const mergedByVariant = new Map(
          mergedCart.items.map((item) => [item.variant_id, item])
        );
        const wasClamped = priorGuest.items.some((guestItem) => {
          const mergedItem = mergedByVariant.get(guestItem.variant_id);
          return !mergedItem || mergedItem.quantity < guestItem.quantity;
        });

        toast({
          title: "Cart merged",
          description: wasClamped
            ? "Your guest cart items were merged. Quantities were adjusted to current stock limits."
            : "Your guest cart items were merged.",
          variant: wasClamped ? "info" : "success",
        });
      }
    });
  }, [subscribeMergedCart, reconcileCartState, toast]);

  // Reset price tracking and load fresh anonymous cart on explicit logout
  React.useEffect(() => {
    return subscribeLogout(() => {
      observedPricesRef.current.clear();
      guestSnapshotRef.current = null;
      setPriceNotices({});
      setCart(null);
    });
  }, [subscribeLogout]);

  const setItemPending = React.useCallback((itemId: string, pending: boolean) => {
    setPendingItemIds((prev) => {
      const next = new Set(prev);
      if (pending) {
        next.add(itemId);
      } else {
        next.delete(itemId);
      }
      return next;
    });
  }, []);

  const addItem = React.useCallback(
    async (variantId: string, quantity: number, productTitle?: string): Promise<CartDTO> => {
      const updated = await cartApi.addItem({ variant_id: variantId, quantity });
      reconcileCartState(updated, user === null);
      setIsDrawerOpen(true);
      toast({
        title: "Added to bag",
        description: productTitle
          ? `${quantity} × ${productTitle} added to your shopping bag.`
          : "Item added to your shopping bag.",
        variant: "success",
      });
      return updated;
    },
    [reconcileCartState, toast, user]
  );

  const updateQuantity = React.useCallback(
    async (itemId: string, quantity: number): Promise<CartDTO> => {
      setItemPending(itemId, true);
      try {
        const updated = await cartApi.updateItem(itemId, { quantity });
        reconcileCartState(updated, user === null);
        return updated;
      } finally {
        setItemPending(itemId, false);
      }
    },
    [reconcileCartState, setItemPending, user]
  );

  const removeItem = React.useCallback(
    async (itemId: string): Promise<CartDTO> => {
      setItemPending(itemId, true);
      try {
        const updated = await cartApi.removeItem(itemId);
        reconcileCartState(updated, user === null);
        return updated;
      } finally {
        setItemPending(itemId, false);
      }
    },
    [reconcileCartState, setItemPending, user]
  );

  const clearCart = React.useCallback(async (): Promise<CartDTO> => {
    const updated = await cartApi.clearCart();
    reconcileCartState(updated, user === null);
    toast({
      title: "Bag cleared",
      description: "All items were removed from your shopping bag.",
      variant: "info",
    });
    return updated;
  }, [reconcileCartState, toast, user]);

  const openDrawer = React.useCallback(() => setIsDrawerOpen(true), []);
  const closeDrawer = React.useCallback(() => setIsDrawerOpen(false), []);

  const value = React.useMemo<CartContextValue>(
    () => ({
      cart,
      isLoading,
      error,
      isDrawerOpen,
      pendingItemIds,
      priceNotices,
      openDrawer,
      closeDrawer,
      setDrawerOpen: setIsDrawerOpen,
      refreshCart,
      addItem,
      updateQuantity,
      removeItem,
      clearCart,
    }),
    [
      cart,
      isLoading,
      error,
      isDrawerOpen,
      pendingItemIds,
      priceNotices,
      openDrawer,
      closeDrawer,
      refreshCart,
      addItem,
      updateQuantity,
      removeItem,
      clearCart,
    ]
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const ctx = React.useContext(CartContext);
  if (!ctx) {
    throw new Error("useCart must be used within a CartProvider.");
  }
  return ctx;
}
