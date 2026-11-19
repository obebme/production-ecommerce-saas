"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  PackageCheck,
  RotateCcw,
  Truck,
  XCircle,
} from "lucide-react";
import { ApiError, adminOrdersApi } from "@/lib/api/client";
import type {
  AddressDTO,
  AdminOrderDetailDTO,
  FulfillOrderAction,
  PaymentIntentStatus,
  ReservationStatus,
} from "@/lib/api/types";
import { formatCents, formatUtcTimestamp } from "@/lib/format";
import { useAuth } from "@/providers/auth-provider";
import { resolveErrorMessage } from "@/components/admin/admin-helpers";
import { AdminPageHeader } from "@/components/admin/admin-primitives";
import { EmptyState, LoadingState, StatusBadge } from "@/components/shared/state-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
  REFUNDED: { label: "Refunded", variant: "destructive" },
};

const RESERVATION_STATUS_BADGE: Record<ReservationStatus, BadgeProps["variant"]> = {
  ACTIVE: "warning",
  COMMITTED: "success",
  RELEASED: "secondary",
  EXPIRED: "outline",
};

function formatEventTitle(eventType: string): string {
  return eventType
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
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

export function AdminOrderDetailView({ orderNumber }: { orderNumber: string }) {
  const { user } = useAuth();
  const { toast } = useToast();

  const isAdmin = user?.role === "admin";

  const [order, setOrder] = React.useState<AdminOrderDetailDTO | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [notFoundError, setNotFoundError] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [actionAlert, setActionAlert] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  // Fulfill Modal State
  const [fulfillAction, setFulfillAction] = React.useState<FulfillOrderAction | null>(null);
  const [carrier, setCarrier] = React.useState("");
  const [trackingNumber, setTrackingNumber] = React.useState("");
  const [fulfillNote, setFulfillNote] = React.useState("");
  const [fulfillError, setFulfillError] = React.useState<string | null>(null);
  const [isFulfilling, setIsFulfilling] = React.useState(false);

  // Cancel Modal State
  const [cancelOpen, setCancelOpen] = React.useState(false);
  const [cancelReason, setCancelReason] = React.useState("");
  const [cancelError, setCancelError] = React.useState<string | null>(null);
  const [isCancelling, setIsCancelling] = React.useState(false);

  // Admin-Only Refund Modal State
  const [refundOpen, setRefundOpen] = React.useState(false);
  const [refundReason, setRefundReason] = React.useState("");
  const [refundRestock, setRefundRestock] = React.useState(true);
  const [refundError, setRefundError] = React.useState<string | null>(null);
  const [isRefunding, setIsRefunding] = React.useState(false);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setNotFoundError(false);
    setLoadError(null);

    adminOrdersApi
      .getOrder(orderNumber, controller.signal)
      .then((res) => setOrder(res))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFoundError(true);
          return;
        }
        setLoadError(resolveErrorMessage(err, "Unable to load order details."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [orderNumber, reloadNonce]);

  const refreshOrderAfterConflict = async (conflictMessage: string) => {
    setActionAlert(conflictMessage);
    try {
      const fresh = await adminOrdersApi.getOrder(orderNumber);
      setOrder(fresh);
    } catch {
      // Keep conflict banner visible even if background refresh fails
    }
  };

  const openFulfillModal = (action: FulfillOrderAction) => {
    setFulfillAction(action);
    setCarrier("");
    setTrackingNumber("");
    setFulfillNote("");
    setFulfillError(null);
  };

  const handleConfirmFulfill = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!order || !fulfillAction || isFulfilling) {
      return;
    }
    setFulfillError(null);
    setActionAlert(null);
    setIsFulfilling(true);

    try {
      const updated = await adminOrdersApi.fulfillOrder(order.order_number, {
        action: fulfillAction,
        carrier: fulfillAction === "SHIP" ? carrier.trim() || null : undefined,
        tracking_number: fulfillAction === "SHIP" ? trackingNumber.trim() || null : undefined,
        note: fulfillNote.trim() || null,
      });
      setOrder(updated);
      setFulfillAction(null);
      toast({
        title: "Order fulfillment updated",
        description: `Order ${updated.order_number} is now ${updated.status}.`,
        variant: "success",
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setFulfillAction(null);
          await refreshOrderAfterConflict(err.message);
          return;
        }
        setFulfillError(err.message);
      } else {
        setFulfillError("Unable to update order fulfillment.");
      }
    } finally {
      setIsFulfilling(false);
    }
  };

  const handleConfirmCancel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!order || isCancelling) {
      return;
    }
    setCancelError(null);
    setActionAlert(null);
    setIsCancelling(true);

    try {
      const updated = await adminOrdersApi.cancelOrder(order.order_number, {
        reason: cancelReason.trim() || null,
      });
      setOrder(updated);
      setCancelOpen(false);
      toast({
        title: "Order cancelled",
        description: `Unpaid order ${updated.order_number} was cancelled and reservations were released.`,
        variant: "success",
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setCancelOpen(false);
          await refreshOrderAfterConflict(err.message);
          return;
        }
        setCancelError(err.message);
      } else {
        setCancelError("Unable to cancel order.");
      }
    } finally {
      setIsCancelling(false);
    }
  };

  const openRefundDialog = () => {
    if (!order) return;
    setRefundReason("");
    setRefundRestock(order.status === "CONFIRMED" || order.status === "PROCESSING");
    setRefundError(null);
    setRefundOpen(true);
  };

  const handleConfirmRefund = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!order || !isAdmin || isRefunding) {
      return;
    }
    setRefundError(null);
    setActionAlert(null);
    setIsRefunding(true);

    try {
      const updated = await adminOrdersApi.refundOrder(order.order_number, {
        reason: refundReason.trim() || null,
        restock: refundRestock,
      });
      setOrder(updated);
      setRefundOpen(false);
      toast({
        title: "Refund processed",
        description: `Issued full refund for ${updated.order_number}${
          refundRestock ? " and restocked returned items." : "."
        }`,
        variant: "success",
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setRefundOpen(false);
          await refreshOrderAfterConflict(err.message);
          return;
        }
        setRefundError(err.message);
      } else {
        setRefundError("Unable to issue refund.");
      }
    } finally {
      setIsRefunding(false);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <AdminPageHeader title={`Order ${orderNumber}`} />
        <LoadingState label="Loading order workbench..." rows={6} />
      </div>
    );
  }

  if (notFoundError) {
    return (
      <div className="space-y-6">
        <AdminPageHeader
          title="Order Not Found"
          actions={
            <Button variant="outline" asChild>
              <Link href="/admin/orders">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                Back to Orders
              </Link>
            </Button>
          }
        />
        <EmptyState
          title="Order not found"
          description={`No order matched order number "${orderNumber}".`}
          action={
            <Button asChild>
              <Link href="/admin/orders">Return to Orders</Link>
            </Button>
          }
        />
      </div>
    );
  }

  if (loadError || !order) {
    return (
      <div className="space-y-6">
        <AdminPageHeader
          title={`Order ${orderNumber}`}
          actions={
            <Button variant="outline" asChild>
              <Link href="/admin/orders">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                Back to Orders
              </Link>
            </Button>
          }
        />
        <Alert variant="destructive" role="alert">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Unable to load order</AlertTitle>
          <AlertDescription className="mt-2 space-y-3">
            <p>{loadError ?? "Unexpected error loading order."}</p>
            <Button variant="outline" size="sm" onClick={() => setReloadNonce((n) => n + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const isPaidRefundable =
    order.payment_status === "PAID" &&
    (order.status === "CONFIRMED" ||
      order.status === "PROCESSING" ||
      order.status === "SHIPPED" ||
      order.status === "COMPLETED");

  const variantLabelById = new Map<string, { sku: string; title: string }>();
  for (const item of order.items) {
    variantLabelById.set(item.variant_id, {
      sku: item.sku_snapshot,
      title: `${item.product_title_snapshot} — ${item.variant_title_snapshot}`,
    });
  }

  const resultingRefundFulfillment =
    order.status === "SHIPPED" || order.status === "COMPLETED" ? "RETURNED" : "CANCELLED";

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title={`Order ${order.order_number}`}
        description={`Customer: ${order.customer_email} · Placed ${formatUtcTimestamp(order.placed_at)}`}
        actions={
          <Button variant="outline" asChild>
            <Link href="/admin/orders">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              All Orders
            </Link>
          </Button>
        }
      />

      {/* Distinct 3-Axis Status Strip */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface p-4">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Order:
          </span>
          <StatusBadge domain="order" value={order.status} />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Payment:
          </span>
          <StatusBadge domain="payment" value={order.payment_status} />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Fulfillment:
          </span>
          <StatusBadge domain="fulfillment" value={order.fulfillment_status} />
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
          {order.paid_at ? <span>Paid: {formatUtcTimestamp(order.paid_at)}</span> : null}
          {order.shipped_at ? <span>Shipped: {formatUtcTimestamp(order.shipped_at)}</span> : null}
          {order.completed_at ? (
            <span>Completed: {formatUtcTimestamp(order.completed_at)}</span>
          ) : null}
          {order.cancelled_at ? (
            <span>Cancelled: {formatUtcTimestamp(order.cancelled_at)}</span>
          ) : null}
        </div>
      </div>

      {actionAlert ? (
        <Alert variant="warning" role="alert">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Order state refreshed</AlertTitle>
          <AlertDescription>{actionAlert}</AlertDescription>
        </Alert>
      ) : null}

      {/* Context-Aware Action Bar (§5.2) */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Fulfillment & Order Actions</CardTitle>
          <CardDescription>
            Actions are strictly governed by the order&apos;s current payment and fulfillment state.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            {order.status === "PENDING_PAYMENT" ? (
              <>
                <Button
                  variant="destructive"
                  onClick={() => {
                    setCancelReason("");
                    setCancelError(null);
                    setCancelOpen(true);
                  }}
                >
                  <XCircle className="h-4 w-4" aria-hidden="true" />
                  Cancel Unpaid Order
                </Button>
                <Button variant="outline" disabled>
                  <PackageCheck className="h-4 w-4" aria-hidden="true" />
                  Start Processing
                </Button>
                <Button variant="outline" disabled>
                  <Truck className="h-4 w-4" aria-hidden="true" />
                  Mark Shipped
                </Button>
              </>
            ) : null}

            {order.status === "CONFIRMED" && order.payment_status === "PAID" ? (
              <>
                <Button variant="accent" onClick={() => openFulfillModal("START_PROCESSING")}>
                  <PackageCheck className="h-4 w-4" aria-hidden="true" />
                  Start Processing
                </Button>
                <Button variant="outline" onClick={() => openFulfillModal("SHIP")}>
                  <Truck className="h-4 w-4" aria-hidden="true" />
                  Mark Shipped
                </Button>
                <Button variant="ghost" disabled>
                  <XCircle className="h-4 w-4" aria-hidden="true" />
                  Cancel Unpaid Order
                </Button>
              </>
            ) : null}

            {order.status === "PROCESSING" && order.payment_status === "PAID" ? (
              <Button variant="accent" onClick={() => openFulfillModal("SHIP")}>
                <Truck className="h-4 w-4" aria-hidden="true" />
                Mark Shipped
              </Button>
            ) : null}

            {order.status === "SHIPPED" && order.payment_status === "PAID" ? (
              <Button variant="accent" onClick={() => openFulfillModal("COMPLETE")}>
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                Mark Completed
              </Button>
            ) : null}

            {/* Strictly admin-only Issue Refund CTA — never rendered for staff */}
            {isAdmin && isPaidRefundable ? (
              <Button
                variant="destructive"
                onClick={openRefundDialog}
                className="sm:ml-auto"
              >
                <RotateCcw className="h-4 w-4" aria-hidden="true" />
                Issue Refund
              </Button>
            ) : null}
          </div>

          {/* Guard / Terminal Helper Copy */}
          {order.status === "PENDING_PAYMENT" ? (
            <p className="text-xs text-muted-foreground">
              Payment must succeed before fulfillment can begin.
            </p>
          ) : null}
          {order.status === "CONFIRMED" && order.payment_status === "PAID" ? (
            <p className="text-xs text-muted-foreground">
              Paid orders must be refunded, not cancelled.
            </p>
          ) : null}
          {order.status === "COMPLETED" ? (
            <p className="text-xs font-medium text-success">Order fulfilled and delivered.</p>
          ) : null}
          {order.status === "CANCELLED" || order.status === "REFUNDED" ? (
            <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              <span>
                This order is in a terminal <strong className="text-foreground">{order.status}</strong>{" "}
                state.
              </span>
              {order.cancellation_reason ? (
                <span className="ml-2">
                  Reason: <span className="font-mono text-foreground">{order.cancellation_reason}</span>
                </span>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Line Items Snapshot & Totals */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Line Items Snapshot</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>SKU</TableHead>
                <TableHead>Item</TableHead>
                <TableHead className="text-right">Unit Price</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Line Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {order.items.map((item) => {
                const attrs = Object.entries(item.attributes_snapshot ?? {});
                return (
                  <TableRow key={item.id}>
                    <TableCell className="font-mono text-xs">{item.sku_snapshot}</TableCell>
                    <TableCell>
                      <p className="font-medium text-foreground">{item.product_title_snapshot}</p>
                      <p className="text-xs text-muted-foreground">{item.variant_title_snapshot}</p>
                      {attrs.length > 0 ? (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {attrs.map(([k, v]) => (
                            <Badge key={k} variant="outline" className="text-[11px]">
                              {k}: {String(v)}
                            </Badge>
                          ))}
                        </div>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCents(item.unit_price_cents, order.currency)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{item.quantity}</TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatCents(item.line_total_cents, order.currency)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          <div className="ml-auto max-w-xs space-y-1.5 border-t border-border pt-3 text-sm tabular-nums">
            <div className="flex justify-between text-muted-foreground">
              <span>Subtotal</span>
              <span>{formatCents(order.subtotal_cents, order.currency)}</span>
            </div>
            {order.discount_cents > 0 ? (
              <div className="flex justify-between text-muted-foreground">
                <span>Discount</span>
                <span>-{formatCents(order.discount_cents, order.currency)}</span>
              </div>
            ) : null}
            <div className="flex justify-between text-muted-foreground">
              <span>Shipping</span>
              <span>{formatCents(order.shipping_cents, order.currency)}</span>
            </div>
            <div className="flex justify-between text-muted-foreground">
              <span>Tax</span>
              <span>{formatCents(order.tax_cents, order.currency)}</span>
            </div>
            <div className="flex justify-between border-t border-border pt-2 text-base font-semibold text-foreground">
              <span>Total</span>
              <span>{formatCents(order.total_cents, order.currency)}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Payment Intents & Inventory Reservations */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Payment Intents</CardTitle>
          </CardHeader>
          <CardContent>
            {order.payment_intents.length === 0 ? (
              <p className="text-sm text-muted-foreground">No payment intents recorded.</p>
            ) : (
              <div className="space-y-3">
                {order.payment_intents.map((intent) => {
                  const badgeCfg = INTENT_STATUS_BADGE[intent.status] ?? {
                    label: intent.status,
                    variant: "outline" as const,
                  };
                  return (
                    <div
                      key={intent.id}
                      className="rounded-md border border-border bg-muted/30 p-3 text-sm"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-mono text-xs font-medium text-foreground">
                          {intent.provider_intent_id}
                        </span>
                        <Badge variant={badgeCfg.variant}>{badgeCfg.label}</Badge>
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                        <span>
                          Provider: <span className="font-mono">{intent.provider}</span> ·{" "}
                          {formatCents(intent.amount_cents, intent.currency)}
                        </span>
                        <span className="font-mono">{formatUtcTimestamp(intent.created_at)}</span>
                      </div>
                      {intent.failure_code || intent.failure_message ? (
                        <p className="mt-1.5 text-xs text-destructive">
                          {intent.failure_code ? `[${intent.failure_code}] ` : ""}
                          {intent.failure_message}
                        </p>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Inventory Reservations</CardTitle>
          </CardHeader>
          <CardContent>
            {order.reservations.length === 0 ? (
              <p className="text-sm text-muted-foreground">No stock reservations recorded.</p>
            ) : (
              <div className="space-y-3">
                {order.reservations.map((res) => {
                  const itemInfo = variantLabelById.get(res.variant_id);
                  return (
                    <div
                      key={res.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted/30 p-3 text-sm"
                    >
                      <div className="min-w-0 space-y-0.5">
                        <p className="font-mono text-xs font-medium text-foreground">
                          {itemInfo ? itemInfo.sku : res.variant_id}
                        </p>
                        {itemInfo ? (
                          <p className="truncate text-xs text-muted-foreground">{itemInfo.title}</p>
                        ) : null}
                        <p className="text-xs text-muted-foreground">
                          Expires: <span className="font-mono">{formatUtcTimestamp(res.expires_at)}</span>
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold tabular-nums">
                          Qty {res.quantity}
                        </span>
                        <Badge variant={RESERVATION_STATUS_BADGE[res.status] ?? "outline"}>
                          {res.status}
                        </Badge>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Shipping & Billing Addresses */}
      <div className="grid gap-6 md:grid-cols-2">
        <AddressCard title="Shipping Address" address={order.shipping_address} />
        <AddressCard title="Billing Address" address={order.billing_address} />
      </div>

      {/* Order Events Audit Timeline */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Order Events Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          {order.events.length === 0 ? (
            <p className="text-sm text-muted-foreground">No events recorded.</p>
          ) : (
            <ol className="space-y-3">
              {order.events.map((evt) => {
                const metaEntries = Object.entries(evt.metadata ?? {}).filter(
                  ([, val]) => val !== null && val !== undefined && String(val).trim() !== ""
                );
                return (
                  <li
                    key={evt.id}
                    className="rounded-md border border-border bg-muted/20 px-4 py-3 text-sm"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-foreground">
                          {formatEventTitle(evt.event_type)}
                        </span>
                        {evt.from_status ? (
                          <Badge variant="outline" className="font-mono text-[11px]">
                            {evt.from_status} → {evt.to_status}
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="font-mono text-[11px]">
                            {evt.to_status}
                          </Badge>
                        )}
                      </div>
                      <span className="font-mono text-xs text-muted-foreground">
                        {formatUtcTimestamp(evt.created_at)}
                      </span>
                    </div>
                    {metaEntries.length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        {metaEntries.map(([key, val]) => (
                          <span key={key}>
                            <span className="font-medium text-foreground">{key}:</span>{" "}
                            <span className="font-mono">
                              {Array.isArray(val) ? val.join(", ") : String(val)}
                            </span>
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
        </CardContent>
      </Card>

      {/* Fulfill Modal (START_PROCESSING / SHIP / COMPLETE) */}
      <Dialog
        open={Boolean(fulfillAction)}
        onOpenChange={(open) => {
          if (!open && !isFulfilling) {
            setFulfillAction(null);
            setFulfillError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {fulfillAction === "START_PROCESSING"
                ? "Start Order Processing"
                : fulfillAction === "SHIP"
                  ? "Mark Order Shipped"
                  : "Mark Order Completed"}
            </DialogTitle>
            <DialogDescription>
              {fulfillAction === "START_PROCESSING"
                ? `Move order ${order.order_number} from CONFIRMED to PROCESSING.`
                : fulfillAction === "SHIP"
                  ? `Record shipment carrier and tracking details for ${order.order_number}.`
                  : `Mark shipped order ${order.order_number} as COMPLETED / DELIVERED.`}
            </DialogDescription>
          </DialogHeader>

          {fulfillError ? (
            <Alert variant="destructive" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertDescription>{fulfillError}</AlertDescription>
            </Alert>
          ) : null}

          <form onSubmit={(e) => void handleConfirmFulfill(e)} className="space-y-4">
            {fulfillAction === "SHIP" ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="ship-carrier">Carrier</Label>
                  <Input
                    id="ship-carrier"
                    value={carrier}
                    onChange={(e) => setCarrier(e.target.value)}
                    maxLength={80}
                    placeholder="e.g., UPS, FedEx, USPS"
                    disabled={isFulfilling}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="ship-tracking">Tracking Number</Label>
                  <Input
                    id="ship-tracking"
                    className="font-mono"
                    value={trackingNumber}
                    onChange={(e) => setTrackingNumber(e.target.value)}
                    maxLength={120}
                    placeholder="e.g., 1Z999AA10123456784"
                    disabled={isFulfilling}
                  />
                </div>
              </div>
            ) : null}

            <div className="space-y-1.5">
              <Label htmlFor="fulfill-note">Fulfillment Note (optional)</Label>
              <Textarea
                id="fulfill-note"
                rows={3}
                maxLength={255}
                value={fulfillNote}
                onChange={(e) => setFulfillNote(e.target.value)}
                placeholder="Optional packing or handoff note..."
                disabled={isFulfilling}
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setFulfillAction(null)}
                disabled={isFulfilling}
              >
                Cancel
              </Button>
              <Button type="submit" variant="accent" disabled={isFulfilling}>
                {isFulfilling
                  ? "Updating..."
                  : fulfillAction === "SHIP"
                    ? "Mark Shipped"
                    : fulfillAction === "START_PROCESSING"
                      ? "Start Processing"
                      : "Mark Completed"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Unpaid Cancel Modal */}
      <Dialog
        open={cancelOpen}
        onOpenChange={(open) => {
          if (!open && !isCancelling) {
            setCancelOpen(false);
            setCancelError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel Unpaid Order</DialogTitle>
            <DialogDescription>
              Releases ACTIVE inventory reservations back to sellable stock.
            </DialogDescription>
          </DialogHeader>

          {cancelError ? (
            <Alert variant="destructive" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertDescription>{cancelError}</AlertDescription>
            </Alert>
          ) : null}

          <form onSubmit={(e) => void handleConfirmCancel(e)} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="admin-cancel-reason">Cancellation Reason (optional)</Label>
              <Input
                id="admin-cancel-reason"
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                maxLength={255}
                placeholder="e.g., Customer requested cancellation before payment"
                disabled={isCancelling}
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCancelOpen(false)}
                disabled={isCancelling}
              >
                Keep Order
              </Button>
              <Button type="submit" variant="destructive" disabled={isCancelling}>
                {isCancelling ? "Cancelling..." : "Cancel Unpaid Order"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Strictly Admin-Only Issue Refund Dialog */}
      {isAdmin ? (
        <Dialog
          open={refundOpen}
          onOpenChange={(open) => {
            if (!open && !isRefunding) {
              setRefundOpen(false);
              setRefundError(null);
            }
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Issue Full Refund — {order.order_number}</DialogTitle>
              <DialogDescription>
                Refunds the full paid amount and updates order, payment, and inventory ledger records.
              </DialogDescription>
            </DialogHeader>

            <Alert variant="warning">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>
                Full Refund Amount: {formatCents(order.total_cents, order.currency)}
              </AlertTitle>
              <AlertDescription>
                This action is recorded permanently in the audit log and cannot be undone.
              </AlertDescription>
            </Alert>

            {refundError ? (
              <Alert variant="destructive" role="alert">
                <AlertCircle className="h-4 w-4" aria-hidden="true" />
                <AlertDescription>{refundError}</AlertDescription>
              </Alert>
            ) : null}

            <form onSubmit={(e) => void handleConfirmRefund(e)} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="refund-reason">Refund Reason (optional)</Label>
                <Input
                  id="refund-reason"
                  value={refundReason}
                  onChange={(e) => setRefundReason(e.target.value)}
                  maxLength={255}
                  placeholder="e.g., Customer return or order cancellation"
                  disabled={isRefunding}
                />
              </div>

              <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-muted/40 p-3 text-sm">
                <Checkbox
                  id="refund-restock"
                  checked={refundRestock}
                  onCheckedChange={(checked) => setRefundRestock(Boolean(checked))}
                  disabled={isRefunding}
                />
                <div className="space-y-0.5">
                  <span className="font-medium text-foreground">
                    Restock returned items back to on-hand inventory (RETURN_RESTOCK)
                  </span>
                  <p className="text-xs text-muted-foreground">
                    Adds line-item quantities back to physical on-hand stock with a ledger entry.
                  </p>
                </div>
              </label>

              <div className="rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
                <p className="font-semibold text-foreground">Resulting State Preview</p>
                <p className="mt-1 font-mono">
                  status → REFUNDED · payment_status → REFUNDED · fulfillment_status →{" "}
                  {resultingRefundFulfillment}
                </p>
              </div>

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setRefundOpen(false)}
                  disabled={isRefunding}
                >
                  Cancel
                </Button>
                <Button type="submit" variant="destructive" disabled={isRefunding}>
                  {isRefunding ? "Processing Refund..." : "Confirm Full Refund"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}
