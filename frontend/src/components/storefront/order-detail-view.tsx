"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock,
  Copy,
  KeyRound,
  ShieldAlert,
  X,
} from "lucide-react";
import { ApiError, ordersApi, paymentsApi } from "@/lib/api/client";
import type {
  AddressDTO,
  OrderDetailDTO,
  OrderEventDTO,
  PaymentIntentStatus,
} from "@/lib/api/types";
import { formatCents, formatUtcTimestamp } from "@/lib/format";
import {
  getGuestOrderToken,
  saveGuestOrderToken,
} from "@/lib/guest-order-memory";
import { generateUuidV4 } from "@/lib/uuid";
import { useAuth } from "@/providers/auth-provider";
import { EmptyState, ErrorState, StatusBadge } from "@/components/shared/state-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";

const INTENT_STATUS_BADGE: Record<
  PaymentIntentStatus,
  { label: string; variant: BadgeProps["variant"] }
> = {
  REQUIRES_ACTION: { label: "Requires Action", variant: "warning" },
  PROCESSING: { label: "Processing", variant: "accentSubtle" },
  SUCCEEDED: { label: "Succeeded", variant: "success" },
  FAILED: { label: "Failed", variant: "destructive" },
  CANCELLED: { label: "Cancelled", variant: "secondary" },
  REFUND_PENDING: { label: "Refund Pending", variant: "warning" },
  REFUNDED: { label: "Refunded", variant: "destructive" },
};

const EVENT_LABELS: Record<string, string> = {
  ORDER_PLACED: "Order Placed",
  PAYMENT_SUCCEEDED: "Payment Succeeded",
  PAYMENT_FAILED: "Payment Failed",
  ORDER_CANCELLED: "Order Cancelled",
  ORDER_PROCESSING_STARTED: "Processing Started",
  PROCESSING_STARTED: "Processing Started",
  ORDER_SHIPPED: "Order Shipped",
  ORDER_COMPLETED: "Order Completed",
  PAYMENT_REFUNDED: "Payment Refunded",
  ORDER_REFUNDED: "Order Refunded",
  DUPLICATE_PAYMENT_REFUNDED: "Duplicate Payment Refunded",
  DUPLICATE_PAYMENT_REFUND_PENDING: "Duplicate Payment Refund Pending",
  STALE_INTENT_PAYMENT_REFUND_PENDING: "Stale Payment Refund Pending",
  PAYMENT_SUCCEEDED_OUT_OF_STOCK: "Payment Refunded (Stock Expired)",
  PAYMENT_SUCCEEDED_AFTER_CANCEL: "Payment Refunded (Order Cancelled)",
  PAYMENT_INTENT_REFUNDED: "Payment Intent Refunded",
};

function formatEventTitle(eventType: string): string {
  if (EVENT_LABELS[eventType]) {
    return EVENT_LABELS[eventType];
  }
  return eventType
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function extractEventMetadataEntries(metadata: Record<string, unknown>): Array<[string, string]> {
  const orderedKeys = [
    "carrier",
    "tracking_number",
    "reason",
    "cancellation_reason",
    "failure_code",
    "failure_message",
    "note",
  ];
  const entries: Array<[string, string]> = [];
  for (const key of orderedKeys) {
    const val = metadata[key];
    if (typeof val === "string" && val.trim()) {
      const label = key
        .replace(/_/g, " ")
        .replace(/\b\w/g, (ch) => ch.toUpperCase());
      entries.push([label, val]);
    }
  }
  return entries;
}

function resolveReservationExpiryMs(order: OrderDetailDTO): number | null {
  if (order.status !== "PENDING_PAYMENT") {
    return null;
  }
  if (order.reservation_expires_at) {
    const parsed = Date.parse(order.reservation_expires_at);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  const placedMs = Date.parse(order.placed_at);
  if (!Number.isNaN(placedMs)) {
    return placedMs + 15 * 60 * 1000;
  }
  return null;
}

function formatCountdown(totalSeconds: number): string {
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const mins = Math.floor(clamped / 60);
  const secs = clamped % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function AddressCard({ title, address }: { title: string; address: AddressDTO }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm text-muted-foreground">
        <p className="font-medium text-foreground">{address.full_name}</p>
        <p>{address.line1}</p>
        {address.line2 ? <p>{address.line2}</p> : null}
        <p>
          {address.city}, {address.state} {address.postal_code}
        </p>
        <p>{address.country}</p>
        <p className="pt-1 font-mono text-xs">{address.phone}</p>
      </CardContent>
    </Card>
  );
}

function OrderDetailSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading order details">
      <span className="sr-only">Loading order details...</span>
      <Skeleton className="h-24 w-full rounded-lg" />
      <Skeleton className="h-48 w-full rounded-lg" />
      <div className="grid gap-6 md:grid-cols-2">
        <Skeleton className="h-44 w-full rounded-lg" />
        <Skeleton className="h-44 w-full rounded-lg" />
      </div>
    </div>
  );
}

export function OrderDetailView({ orderNumber }: { orderNumber: string }) {
  const { user, isLoading: authLoading, sessionExpired, accountDisabled } = useAuth();
  const { toast } = useToast();

  const [activeGuestToken, setActiveGuestToken] = React.useState<string | undefined>(() =>
    getGuestOrderToken(orderNumber)
  );
  const [showReceiptNotice, setShowReceiptNotice] = React.useState<boolean>(() =>
    Boolean(getGuestOrderToken(orderNumber))
  );
  const [copiedToken, setCopiedToken] = React.useState(false);

  const [order, setOrder] = React.useState<OrderDetailDTO | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<{
    kind: "access_required" | "not_found" | "forbidden" | "generic";
    message: string;
  } | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  // Guest token recovery form
  const [tokenInput, setTokenInput] = React.useState("");
  const [tokenError, setTokenError] = React.useState<string | null>(null);

  // Reservation countdown state
  const [remainingSeconds, setRemainingSeconds] = React.useState<number | null>(null);
  const [timerAnnouncement, setTimerAnnouncement] = React.useState("");
  const [isExpiryRefreshing, setIsExpiryRefreshing] = React.useState(false);
  const expiredRefreshKeyRef = React.useRef<string | null>(null);
  const prevSecondsRef = React.useRef<number | null>(null);

  // Sandbox payment & retry intent state
  const [sandboxDisabled, setSandboxDisabled] = React.useState(false);
  const [isSimulating, setIsSimulating] = React.useState(false);
  const [isCreatingIntent, setIsCreatingIntent] = React.useState(false);
  const [paymentBanner, setPaymentBanner] = React.useState<{
    variant: "success" | "destructive" | "warning" | "info";
    message: string;
  } | null>(null);

  // Self-cancel dialog state
  const [cancelDialogOpen, setCancelDialogOpen] = React.useState(false);
  const [cancelReason, setCancelReason] = React.useState("");
  const [cancelError, setCancelError] = React.useState<string | null>(null);
  const [isCancelling, setIsCancelling] = React.useState(false);

  const mountedRef = React.useRef(true);
  React.useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  React.useEffect(() => {
    const mem = getGuestOrderToken(orderNumber);
    setActiveGuestToken(mem);
    setShowReceiptNotice(Boolean(mem));
    setCopiedToken(false);
    setPaymentBanner(null);
    setSandboxDisabled(false);
    setTokenError(null);
    expiredRefreshKeyRef.current = null;
  }, [orderNumber]);

  const fetchOrderDetail = React.useCallback(
    async (tokenOverride?: string, signal?: AbortSignal): Promise<OrderDetailDTO | null> => {
      const tokenToUse = tokenOverride ?? activeGuestToken;
      return ordersApi.getOrder(orderNumber, tokenToUse, signal);
    },
    [orderNumber, activeGuestToken]
  );

  React.useEffect(() => {
    if (authLoading) {
      return;
    }
    if ((sessionExpired || accountDisabled) && !user) {
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    setIsLoading(true);
    setLoadError(null);

    fetchOrderDetail(activeGuestToken, controller.signal)
      .then((dto) => {
        setOrder(dto);
        setTokenError(null);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError) {
          if (!user && (err.status === 403 || err.status === 404 || err.status === 401)) {
            setOrder(null);
            setLoadError({
              kind: "access_required",
              message: err.message,
            });
            if (activeGuestToken) {
              setTokenError("Invalid or expired guest order token. Please verify and try again.");
            }
            return;
          }
          if (err.status === 404) {
            setOrder(null);
            setLoadError({ kind: "not_found", message: err.message });
            return;
          }
          if (err.status === 403) {
            setOrder(null);
            setLoadError({ kind: "forbidden", message: err.message });
            return;
          }
          setLoadError({ kind: "generic", message: err.message });
          return;
        }
        setLoadError({
          kind: "generic",
          message: err instanceof Error ? err.message : "Unable to load order details.",
        });
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [authLoading, user, sessionExpired, accountDisabled, activeGuestToken, reloadNonce, fetchOrderDetail]);

  // 15-minute stock reservation countdown timer
  const reservationExpiryMs = React.useMemo(
    () => (order ? resolveReservationExpiryMs(order) : null),
    [order]
  );

  React.useEffect(() => {
    if (!order || order.status !== "PENDING_PAYMENT" || reservationExpiryMs === null) {
      setRemainingSeconds(null);
      prevSecondsRef.current = null;
      return;
    }

    const computeRemaining = () =>
      Math.max(0, Math.ceil((reservationExpiryMs - Date.now()) / 1000));

    const initial = computeRemaining();
    setRemainingSeconds(initial);
    prevSecondsRef.current = initial;

    if (initial === 0) {
      const expiryKey = `${order.order_number}:${reservationExpiryMs}`;
      if (expiredRefreshKeyRef.current !== expiryKey) {
        expiredRefreshKeyRef.current = expiryKey;
        setTimerAnnouncement("Stock reservation expired. Refreshing order status.");
        setIsExpiryRefreshing(true);
        void fetchOrderDetail()
          .then((refreshed) => {
            if (mountedRef.current && refreshed) {
              setOrder(refreshed);
            }
          })
          .catch(() => {})
          .finally(() => {
            if (mountedRef.current) {
              setIsExpiryRefreshing(false);
            }
          });
      }
      return;
    }

    const interval = setInterval(() => {
      const next = computeRemaining();
      const prev = prevSecondsRef.current;
      setRemainingSeconds(next);
      prevSecondsRef.current = next;

      if (prev !== null) {
        if (prev > 300 && next <= 300 && next > 60) {
          setTimerAnnouncement("5 minutes remaining on your stock reservation.");
        } else if (prev > 60 && next <= 60 && next > 0) {
          setTimerAnnouncement("1 minute remaining on your stock reservation.");
        } else if (prev > 0 && next === 0) {
          setTimerAnnouncement("Stock reservation expired. Refreshing order status.");
        }
      }

      if (next === 0) {
        clearInterval(interval);
        const expiryKey = `${order.order_number}:${reservationExpiryMs}`;
        if (expiredRefreshKeyRef.current !== expiryKey) {
          expiredRefreshKeyRef.current = expiryKey;
          setIsExpiryRefreshing(true);
          void fetchOrderDetail()
            .then((refreshed) => {
              if (mountedRef.current && refreshed) {
                setOrder(refreshed);
              }
            })
            .catch(() => {})
            .finally(() => {
              if (mountedRef.current) {
                setIsExpiryRefreshing(false);
              }
            });
        }
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [order, reservationExpiryMs, fetchOrderDetail]);

  const handleGuestTokenSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = tokenInput.trim();
    if (!trimmed) {
      setTokenError("Enter the guest order token from your checkout confirmation.");
      return;
    }
    setTokenError(null);
    saveGuestOrderToken(orderNumber, trimmed);
    setActiveGuestToken(trimmed);
    setReloadNonce((n) => n + 1);
  };

  const handleCopyToken = async () => {
    if (!activeGuestToken) return;
    try {
      await navigator.clipboard.writeText(activeGuestToken);
      setCopiedToken(true);
      setTimeout(() => {
        if (mountedRef.current) setCopiedToken(false);
      }, 2500);
    } catch {
      toast({
        title: "Copy manually",
        description: "Select and copy the guest receipt token shown in the notice.",
        variant: "info",
      });
    }
  };

  const pollOrderAfterSimulate = async (
    expectedOrderStatus: string,
    expectedPaymentStatus: string
  ): Promise<OrderDetailDTO | null> => {
    let latest: OrderDetailDTO | null = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (!mountedRef.current) {
        return latest;
      }
      latest = await fetchOrderDetail();
      if (!latest) {
        continue;
      }
      const settled =
        expectedPaymentStatus === "PAID"
          ? latest.payment_status === "PAID" || latest.status !== "PENDING_PAYMENT"
          : expectedPaymentStatus === "FAILED"
            ? latest.payment_status === "FAILED" || latest.payment_intent?.status === "FAILED"
            : latest.status === expectedOrderStatus;

      if (settled || attempt === 2) {
        return latest;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return latest;
  };

  const handleSimulateOutcome = async (outcome: "succeeded" | "failed") => {
    if (!order?.payment_intent || isSimulating || isCreatingIntent || isCancelling) {
      return;
    }
    setIsSimulating(true);
    setPaymentBanner(null);

    const failureCode = outcome === "failed" ? "card_declined" : null;

    try {
      const simRes = await paymentsApi.simulateSandbox(
        order.payment_intent.provider_intent_id,
        { outcome, failure_code: failureCode },
        activeGuestToken
      );

      const refreshed = await pollOrderAfterSimulate(
        simRes.order_status,
        simRes.payment_status
      );
      if (mountedRef.current && refreshed) {
        setOrder(refreshed);
        if (refreshed.payment_status === "PAID" || refreshed.status === "CONFIRMED") {
          setPaymentBanner({
            variant: "success",
            message: "Payment confirmed! Your order is now queued for fulfillment.",
          });
        } else if (refreshed.payment_status === "FAILED") {
          const codeLabel = refreshed.payment_intent?.failure_code || failureCode || "card_declined";
          const expiryLabel =
            remainingSeconds !== null ? formatCountdown(remainingSeconds) : "expiration";
          setPaymentBanner({
            variant: "destructive",
            message: `Simulated payment failed (${codeLabel}). Your stock reservation remains active until ${expiryLabel} — you may retry payment or cancel.`,
          });
        }
      }
    } catch (err) {
      if (!mountedRef.current) return;
      if (err instanceof ApiError) {
        if (err.status === 403 && err.code === "SANDBOX_MODE_DISABLED") {
          setSandboxDisabled(true);
          setPaymentBanner({
            variant: "warning",
            message: "Sandbox payment simulation is disabled in this environment.",
          });
          return;
        }
        if (
          err.status === 409 &&
          (err.code === "PAYMENT_INTENT_ALREADY_FINAL" ||
            err.code === "INVALID_ORDER_STATE_TRANSITION")
        ) {
          try {
            const synced = await fetchOrderDetail();
            if (mountedRef.current && synced) {
              setOrder(synced);
            }
          } catch {
            // Fall through with original message
          }
          setPaymentBanner({
            variant: "warning",
            message: err.message,
          });
          return;
        }
        setPaymentBanner({
          variant: "destructive",
          message: err.message,
        });
      } else {
        setPaymentBanner({
          variant: "destructive",
          message: "Unable to simulate sandbox payment. Please try again.",
        });
      }
    } finally {
      if (mountedRef.current) {
        setIsSimulating(false);
      }
    }
  };

  const handleRetryCreateIntent = async () => {
    if (!order || isCreatingIntent || isSimulating || isCancelling) {
      return;
    }
    setIsCreatingIntent(true);
    setPaymentBanner(null);
    try {
      const intentRes = await paymentsApi.createIntent(
        { order_number: order.order_number },
        generateUuidV4(),
        activeGuestToken
      );
      const refreshed = await fetchOrderDetail().catch(() => null);
      if (!mountedRef.current) return;
      if (refreshed) {
        setOrder(refreshed);
      } else {
        setOrder((prev) =>
          prev
            ? {
                ...prev,
                payment_intent: intentRes.payment_intent,
              }
            : prev
        );
      }
      toast({
        title: "New payment intent ready",
        description: "You can now simulate the sandbox payment outcome.",
        variant: "info",
      });
    } catch (err) {
      if (!mountedRef.current) return;
      if (err instanceof ApiError && err.status === 409) {
        const synced = await fetchOrderDetail().catch(() => null);
        if (mountedRef.current && synced) {
          setOrder(synced);
        }
      }
      setPaymentBanner({
        variant: "destructive",
        message: err instanceof Error ? err.message : "Could not create a new payment intent.",
      });
    } finally {
      if (mountedRef.current) {
        setIsCreatingIntent(false);
      }
    }
  };

  const handleConfirmCancel = async () => {
    if (!order || isCancelling) {
      return;
    }
    setIsCancelling(true);
    setCancelError(null);
    const trimmedReason = cancelReason.trim();
    try {
      const updated = await ordersApi.cancelOrder(
        order.order_number,
        trimmedReason ? { reason: trimmedReason } : undefined,
        activeGuestToken
      );
      if (!mountedRef.current) return;
      setOrder(updated);
      setCancelDialogOpen(false);
      setCancelReason("");
      toast({
        title: "Order cancelled",
        description: "Reserved inventory has been released back to stock.",
        variant: "info",
      });
    } catch (err) {
      if (!mountedRef.current) return;
      if (err instanceof ApiError && err.status === 409) {
        const synced = await fetchOrderDetail().catch(() => null);
        if (mountedRef.current && synced) {
          setOrder(synced);
        }
      }
      setCancelError(err instanceof Error ? err.message : "Unable to cancel order.");
    } finally {
      if (mountedRef.current) {
        setIsCancelling(false);
      }
    }
  };

  const returnToSelf = encodeURIComponent(`/orders/${orderNumber}`);
  const sortedEvents = React.useMemo<OrderEventDTO[]>(() => {
    if (!order) return [];
    return [...order.events].sort(
      (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)
    );
  }, [order]);

  const canSelfCancel =
    order !== null &&
    order.status === "PENDING_PAYMENT" &&
    (order.payment_status === "REQUIRES_PAYMENT" || order.payment_status === "FAILED");

  const activeIntent = order?.payment_intent ?? null;
  const intentIsActionable =
    activeIntent !== null &&
    (activeIntent.status === "REQUIRES_ACTION" || activeIntent.status === "PROCESSING");
  const canCreateRetryIntent =
    order !== null &&
    order.status === "PENDING_PAYMENT" &&
    (activeIntent === null ||
      activeIntent.status === "FAILED" ||
      activeIntent.status === "CANCELLED") &&
    (remainingSeconds === null || remainingSeconds > 0);

  return (
    <div className="mx-auto max-w-[1440px] space-y-8 px-4 py-10 sm:px-6 lg:px-8">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Link href="/" className="hover:text-foreground">
          Home
        </Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        {user ? (
          <>
            <Link href="/orders" className="hover:text-foreground">
              Orders
            </Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </>
        ) : null}
        <span className="font-mono font-medium text-foreground">{orderNumber}</span>
      </nav>

      {/* Screen-reader-only milestone announcements (05:00, 01:00, 00:00) */}
      <div className="sr-only" aria-live="assertive" role="status">
        {timerAnnouncement}
      </div>

      {sessionExpired && !user ? (
        <Alert variant="warning" role="alert">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Session expired</AlertTitle>
          <AlertDescription className="mt-1 space-y-3">
            <p>Your session has expired. Please sign in again to access this order.</p>
            <Button size="sm" asChild>
              <Link href={`/login?expired=1&returnTo=${returnToSelf}`}>Sign in to your account</Link>
            </Button>
          </AlertDescription>
        </Alert>
      ) : accountDisabled ? (
        <Alert variant="destructive" role="alert">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Account disabled</AlertTitle>
          <AlertDescription>
            Your account has been deactivated. Please contact an administrator.
          </AlertDescription>
        </Alert>
      ) : authLoading || (isLoading && !order && !loadError) ? (
        <OrderDetailSkeleton />
      ) : loadError?.kind === "access_required" ? (
        <Card className="mx-auto max-w-xl">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <KeyRound className="h-5 w-5 text-accent" aria-hidden="true" />
              <span>Order Access Required</span>
            </CardTitle>
            <CardDescription>
              Sign in to the account that placed order{" "}
              <span className="font-mono font-medium text-foreground">{orderNumber}</span>, or
              enter your guest order receipt token below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="space-y-2">
              <Button size="lg" className="w-full" asChild>
                <Link href={`/login?returnTo=${returnToSelf}`}>Sign in to your account</Link>
              </Button>
            </div>

            <div className="relative flex items-center py-1">
              <div className="flex-grow border-t border-border" />
              <span className="mx-3 shrink-0 text-xs uppercase tracking-wider text-muted-foreground">
                Or use guest receipt token
              </span>
              <div className="flex-grow border-t border-border" />
            </div>

            <form onSubmit={handleGuestTokenSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="guest-order-token">Enter Guest Order Token</Label>
                <Input
                  id="guest-order-token"
                  type="text"
                  value={tokenInput}
                  onChange={(e) => {
                    setTokenInput(e.target.value);
                    setTokenError(null);
                  }}
                  placeholder="Paste token from your guest order receipt"
                  hasError={Boolean(tokenError)}
                  aria-describedby={tokenError ? "guest-token-error" : "guest-token-hint"}
                  className="font-mono text-sm"
                />
                <p id="guest-token-hint" className="text-xs text-muted-foreground">
                  Submitted only in memory via request header — never saved to browser storage.
                </p>
                {tokenError ? (
                  <p id="guest-token-error" className="text-xs text-destructive" role="alert">
                    {tokenError}
                  </p>
                ) : null}
              </div>
              <Button type="submit" variant="outline" className="w-full">
                View Guest Order
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : loadError?.kind === "not_found" ? (
        <EmptyState
          title="Order not found"
          description={`We couldn't find an order matching ${orderNumber}.`}
          action={
            <Button asChild>
              <Link href={user ? "/orders" : "/products"}>
                {user ? "Back to My Orders" : "Browse Catalog"}
              </Link>
            </Button>
          }
        />
      ) : loadError?.kind === "forbidden" ? (
        <ErrorState
          title="Access denied"
          message="You do not have permission to view this order."
          action={
            <Button variant="outline" size="sm" asChild>
              <Link href="/orders">Back to My Orders</Link>
            </Button>
          }
        />
      ) : loadError ? (
        <ErrorState
          title="Unable to load order"
          message={loadError.message}
          onRetry={() => setReloadNonce((n) => n + 1)}
        />
      ) : !order ? null : (
        <div className="space-y-8">
          {/* Header & Distinct Status Pills */}
          <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-6">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="font-mono text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
                  {order.order_number}
                </h1>
              </div>
              <p className="text-sm text-muted-foreground">
                Placed{" "}
                <span className="font-mono text-xs text-foreground">
                  {formatUtcTimestamp(order.placed_at)}
                </span>{" "}
                · {order.customer_email}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-muted-foreground">Order:</span>
                <StatusBadge domain="order" value={order.status} />
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-muted-foreground">Payment:</span>
                <StatusBadge domain="payment" value={order.payment_status} />
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-muted-foreground">Fulfillment:</span>
                <StatusBadge domain="fulfillment" value={order.fulfillment_status} />
              </div>
            </div>
          </div>

          {/* One-time copyable guest receipt token notice */}
          {showReceiptNotice && activeGuestToken && order.user_id === null ? (
            <Alert variant="info" role="status" className="relative pr-12">
              <KeyRound className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>Guest Receipt Access Token</AlertTitle>
              <AlertDescription className="mt-1 space-y-2.5">
                <p>
                  Save this guest receipt token if you want to check your order from another
                  browser; it is never stored in browser storage.
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <code className="rounded-sm border border-border bg-surface px-2.5 py-1 font-mono text-xs text-foreground break-all">
                    {activeGuestToken}
                  </code>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void handleCopyToken()}
                  >
                    {copiedToken ? (
                      <>
                        <Check className="h-3.5 w-3.5 text-success" aria-hidden="true" />
                        Copied
                      </>
                    ) : (
                      <>
                        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                        Copy token
                      </>
                    )}
                  </Button>
                </div>
              </AlertDescription>
              <button
                type="button"
                onClick={() => setShowReceiptNotice(false)}
                aria-label="Dismiss guest receipt token notice"
                className="absolute right-3 top-3 inline-flex h-8 w-8 items-center justify-center rounded-sm text-muted-foreground hover:bg-secondary hover:text-foreground"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </Alert>
          ) : null}

          {/* Post-simulate / payment status feedback banner */}
          {paymentBanner ? (
            <Alert variant={paymentBanner.variant} role="alert">
              {paymentBanner.variant === "success" ? (
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              ) : (
                <AlertCircle className="h-4 w-4" aria-hidden="true" />
              )}
              <AlertDescription>{paymentBanner.message}</AlertDescription>
            </Alert>
          ) : null}

          {/* Confirmed order banner (when paid and no transient banner already shown) */}
          {!paymentBanner && order.payment_status === "PAID" && order.status !== "REFUNDED" ? (
            <Alert variant="success" role="status">
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>Payment Confirmed</AlertTitle>
              <AlertDescription>
                Paid on{" "}
                <span className="font-mono text-xs">{formatUtcTimestamp(order.paid_at)}</span>.
                Your order is queued for fulfillment.
              </AlertDescription>
            </Alert>
          ) : null}

          {/* Cancelled / expired banner */}
          {order.status === "CANCELLED" ? (
            <Alert
              variant={order.cancellation_reason === "RESERVATION_EXPIRED" ? "warning" : "info"}
              role="status"
            >
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>
                {order.cancellation_reason === "RESERVATION_EXPIRED"
                  ? "Stock reservation expired"
                  : "Order cancelled"}
              </AlertTitle>
              <AlertDescription className="mt-1 space-y-3">
                <p>
                  {order.cancellation_reason === "RESERVATION_EXPIRED"
                    ? "The 15-minute stock reservation window elapsed before payment was completed. Reserved inventory has been released."
                    : `Cancelled (${order.cancellation_reason || "CUSTOMER_CANCELLED"}). Reserved inventory has been released.`}
                </p>
                <Button size="sm" variant="outline" asChild>
                  <Link href="/products">Browse Catalog to Reorder</Link>
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}

          {/* 15-Minute Stock Reservation Countdown Banner */}
          {order.status === "PENDING_PAYMENT" && remainingSeconds !== null ? (
            <Alert
              variant={remainingSeconds <= 180 ? "warning" : "info"}
              role="status"
            >
              <Clock className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>
                {remainingSeconds === 0
                  ? "Reservation window elapsed"
                  : remainingSeconds <= 180
                    ? "Reservation expiring soon"
                    : "Stock reservation active"}
              </AlertTitle>
              <AlertDescription className="tabular-nums">
                {remainingSeconds === 0 ? (
                  isExpiryRefreshing ? (
                    "Refreshing order reservation status..."
                  ) : (
                    "Reservation timer reached 00:00."
                  )
                ) : remainingSeconds <= 180 ? (
                  <>
                    Reservation expires in{" "}
                    <span className="font-mono font-semibold">
                      {formatCountdown(remainingSeconds)}
                    </span>{" "}
                    — complete payment soon.
                  </>
                ) : (
                  <>
                    Stock reserved for{" "}
                    <span className="font-mono font-semibold">
                      {formatCountdown(remainingSeconds)}
                    </span>
                    . Complete sandbox payment before timer expires.
                  </>
                )}
              </AlertDescription>
            </Alert>
          ) : null}

          {/* Sandbox SimPay Panel (ZERO card/PAN/CVV/expiry fields) */}
          {order.status === "PENDING_PAYMENT" ? (
            <Card className="border-border bg-surface shadow-sm">
              <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
                <div>
                  <CardTitle className="text-lg">Sandbox SimPay · Test Payment Mode</CardTitle>
                  <CardDescription>
                    Simulate a verified payment webhook outcome without entering any card details.
                  </CardDescription>
                </div>
                <Badge variant="warning">Sandbox Mode</Badge>
              </CardHeader>
              <CardContent className="space-y-5 pt-5">
                {activeIntent ? (
                  <dl className="grid gap-4 rounded-md border border-border bg-muted/40 p-4 sm:grid-cols-4">
                    <div className="sm:col-span-2">
                      <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        Provider Intent ID
                      </dt>
                      <dd className="mt-1 truncate font-mono text-xs text-foreground">
                        {activeIntent.provider_intent_id}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        Amount ({activeIntent.currency})
                      </dt>
                      <dd className="mt-1 text-sm font-semibold text-foreground tabular-nums">
                        {formatCents(activeIntent.amount_cents, activeIntent.currency)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        Intent Status
                      </dt>
                      <dd className="mt-1">
                        <Badge
                          variant={
                            INTENT_STATUS_BADGE[activeIntent.status]?.variant ?? "outline"
                          }
                        >
                          {INTENT_STATUS_BADGE[activeIntent.status]?.label ?? activeIntent.status}
                        </Badge>
                      </dd>
                    </div>
                  </dl>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No active payment intent is currently attached to this order.
                  </p>
                )}

                {activeIntent?.status === "FAILED" &&
                (activeIntent.failure_code || activeIntent.failure_message) ? (
                  <Alert variant="destructive" role="alert">
                    <AlertCircle className="h-4 w-4" aria-hidden="true" />
                    <AlertDescription>
                      {activeIntent.failure_message || "Payment attempt failed."}{" "}
                      {activeIntent.failure_code ? (
                        <span className="font-mono text-xs">({activeIntent.failure_code})</span>
                      ) : null}
                    </AlertDescription>
                  </Alert>
                ) : null}

                {sandboxDisabled ? (
                  <Alert variant="warning" role="status">
                    <ShieldAlert className="h-4 w-4" aria-hidden="true" />
                    <AlertDescription>
                      Sandbox payment simulation is disabled in this environment.
                    </AlertDescription>
                  </Alert>
                ) : (
                  <div className="flex flex-wrap items-center gap-3">
                    {intentIsActionable ? (
                      <>
                        <Button
                          size="lg"
                          disabled={
                            isSimulating ||
                            isCreatingIntent ||
                            isCancelling ||
                            isExpiryRefreshing ||
                            remainingSeconds === 0
                          }
                          onClick={() => void handleSimulateOutcome("succeeded")}
                        >
                          {isSimulating ? "Processing..." : "Simulate Payment Success"}
                        </Button>
                        <Button
                          size="lg"
                          variant="outline"
                          disabled={
                            isSimulating ||
                            isCreatingIntent ||
                            isCancelling ||
                            isExpiryRefreshing ||
                            remainingSeconds === 0
                          }
                          onClick={() => void handleSimulateOutcome("failed")}
                          className="border-destructive/40 text-destructive hover:bg-destructive-subtle"
                        >
                          Simulate Payment Failure
                        </Button>
                      </>
                    ) : null}

                    {canCreateRetryIntent ? (
                      <Button
                        size="lg"
                        variant={intentIsActionable ? "outline" : "default"}
                        disabled={
                          isCreatingIntent ||
                          isSimulating ||
                          isCancelling ||
                          isExpiryRefreshing
                        }
                        onClick={() => void handleRetryCreateIntent()}
                      >
                        {isCreatingIntent
                          ? "Creating Intent..."
                          : "Retry / Create Payment Intent"}
                      </Button>
                    ) : null}
                  </div>
                )}
              </CardContent>
            </Card>
          ) : null}

          {/* Self-Cancel Action Bar or Paid Order Helper Note */}
          {canSelfCancel ? (
            <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-surface p-4 sm:px-6">
              <div>
                <p className="text-sm font-semibold text-foreground">Need to cancel this order?</p>
                <p className="text-xs text-muted-foreground">
                  Unpaid orders can be cancelled immediately to release reserved stock.
                </p>
              </div>
              <Button
                variant="outline"
                onClick={() => {
                  setCancelError(null);
                  setCancelDialogOpen(true);
                }}
                disabled={isSimulating || isCreatingIntent || isCancelling || isExpiryRefreshing}
                className="border-destructive/40 text-destructive hover:bg-destructive-subtle"
              >
                Cancel Order
              </Button>
            </div>
          ) : order.payment_status === "PAID" ? (
            <p className="rounded-md border border-border bg-muted/40 px-4 py-3 text-xs text-muted-foreground">
              Paid orders cannot be self-cancelled online. Contact support for refund assistance.
            </p>
          ) : null}

          {/* Order Items & Financial Summary */}
          <div className="grid gap-8 lg:grid-cols-12">
            <Card className="lg:col-span-8">
              <CardHeader>
                <CardTitle>Order Items</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Item</TableHead>
                      <TableHead>SKU</TableHead>
                      <TableHead className="text-right">Unit Price</TableHead>
                      <TableHead className="text-right">Qty</TableHead>
                      <TableHead className="text-right">Line Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {order.items.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell>
                          <p className="font-medium text-foreground">
                            {item.product_title_snapshot}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {item.variant_title_snapshot}
                          </p>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {item.sku_snapshot}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatCents(item.unit_price_cents, order.currency)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{item.quantity}</TableCell>
                        <TableCell className="text-right font-medium tabular-nums">
                          {formatCents(item.line_total_cents, order.currency)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card className="lg:col-span-4">
              <CardHeader>
                <CardTitle>Totals</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Subtotal</span>
                  <span className="font-medium tabular-nums">
                    {formatCents(order.subtotal_cents, order.currency)}
                  </span>
                </div>
                {order.discount_cents > 0 ? (
                  <div className="flex justify-between text-success">
                    <span>Discount</span>
                    <span className="font-medium tabular-nums">
                      -{formatCents(order.discount_cents, order.currency)}
                    </span>
                  </div>
                ) : null}
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Shipping</span>
                  <span className="font-medium tabular-nums">
                    {order.shipping_cents === 0
                      ? "Free ($0.00)"
                      : formatCents(order.shipping_cents, order.currency)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Tax</span>
                  <span className="font-medium tabular-nums">
                    {formatCents(order.tax_cents, order.currency)}
                  </span>
                </div>
                <div className="flex justify-between border-t border-border pt-3 text-base font-semibold">
                  <span>Total</span>
                  <span className="tabular-nums">
                    {formatCents(order.total_cents, order.currency)}
                  </span>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Shipping & Billing Addresses */}
          <div className="grid gap-6 md:grid-cols-2">
            <AddressCard title="Shipping Address" address={order.shipping_address} />
            <AddressCard title="Billing Address" address={order.billing_address} />
          </div>

          {/* Order Event Timeline */}
          <Card>
            <CardHeader>
              <CardTitle>Order Timeline</CardTitle>
              <CardDescription>
                Chronological status transitions and payment/fulfillment milestones.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {sortedEvents.length === 0 ? (
                <p className="text-sm text-muted-foreground">No order events recorded yet.</p>
              ) : (
                <ol className="relative space-y-6 border-l border-border pl-6">
                  {sortedEvents.map((event) => {
                    const metaEntries = extractEventMetadataEntries(event.metadata);
                    return (
                      <li key={event.id} className="relative">
                        <span
                          className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full border border-border bg-accent"
                          aria-hidden="true"
                        />
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-foreground">
                              {formatEventTitle(event.event_type)}
                            </p>
                            <Badge variant="outline" className="font-mono text-[11px]">
                              {event.from_status
                                ? `${event.from_status} -> ${event.to_status}`
                                : event.to_status}
                            </Badge>
                          </div>
                          <span className="font-mono text-xs text-muted-foreground">
                            {formatUtcTimestamp(event.created_at)}
                          </span>
                        </div>
                        {metaEntries.length > 0 ? (
                          <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
                            {metaEntries.map(([label, val]) => (
                              <div key={label} className="flex items-center gap-1.5">
                                <dt className="font-medium text-foreground">{label}:</dt>
                                <dd className="font-mono">{val}</dd>
                              </div>
                            ))}
                          </dl>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Self-Cancel Confirmation Dialog */}
      <Dialog open={cancelDialogOpen} onOpenChange={setCancelDialogOpen}>
        <DialogContent className="storefront-theme">
          <DialogHeader>
            <DialogTitle>Cancel Order &amp; Release Reserved Stock?</DialogTitle>
            <DialogDescription>
              Cancelling order <span className="font-mono font-medium">{orderNumber}</span>{" "}
              immediately releases your reserved inventory back to available stock.
            </DialogDescription>
          </DialogHeader>

          {cancelError ? (
            <Alert variant="destructive" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertDescription>{cancelError}</AlertDescription>
            </Alert>
          ) : null}

          <div className="space-y-1.5">
            <Label htmlFor="cancel-order-reason">Cancellation reason (optional)</Label>
            <Textarea
              id="cancel-order-reason"
              maxLength={255}
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="e.g., Changed shipping details or ordered by mistake"
              disabled={isCancelling}
            />
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCancelDialogOpen(false)}
              disabled={isCancelling}
            >
              Keep Order
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleConfirmCancel()}
              disabled={isCancelling}
            >
              {isCancelling ? "Cancelling..." : "Confirm Cancellation"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
