"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowRight, Search, X } from "lucide-react";
import { adminOrdersApi } from "@/lib/api/client";
import type {
  AdminOrderListItemDTO,
  FulfillmentStatus,
  OrderStatus,
  PaginatedResponse,
  PaymentStatus,
} from "@/lib/api/types";
import { formatCents, formatUtcTimestamp } from "@/lib/format";
import {
  resolveErrorMessage,
  toDateInputValue,
  toUtcRangeEndIso,
  toUtcRangeStartIso,
} from "@/components/admin/admin-helpers";
import { AdminPageHeader, DataTableShell } from "@/components/admin/admin-primitives";
import { StatusBadge } from "@/components/shared/state-feedback";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PAGE_SIZE = 20;

const ORDER_STATUSES: OrderStatus[] = [
  "PENDING_PAYMENT",
  "CONFIRMED",
  "PROCESSING",
  "SHIPPED",
  "COMPLETED",
  "CANCELLED",
  "REFUNDED",
];

const PAYMENT_STATUSES: PaymentStatus[] = [
  "REQUIRES_PAYMENT",
  "PAID",
  "FAILED",
  "REFUNDED",
  "CANCELLED",
];

const FULFILLMENT_STATUSES: FulfillmentStatus[] = [
  "UNFULFILLED",
  "ALLOCATED",
  "SHIPPED",
  "DELIVERED",
  "CANCELLED",
  "RETURNED",
];

function isValidOrderStatus(val: string | null): val is OrderStatus {
  return Boolean(val && ORDER_STATUSES.includes(val as OrderStatus));
}

function isValidPaymentStatus(val: string | null): val is PaymentStatus {
  return Boolean(val && PAYMENT_STATUSES.includes(val as PaymentStatus));
}

function isValidFulfillmentStatus(val: string | null): val is FulfillmentStatus {
  return Boolean(val && FULFILLMENT_STATUSES.includes(val as FulfillmentStatus));
}

export function AdminOrdersView() {
  const searchParams = useSearchParams();

  const paramStatus = searchParams.get("status");
  const paramPayment = searchParams.get("payment_status");
  const paramFulfillment = searchParams.get("fulfillment_status");
  const paramQ = searchParams.get("q")?.trim().slice(0, 100) ?? "";
  const paramFromTs = searchParams.get("from_ts")?.trim() ?? "";
  const paramToTs = searchParams.get("to_ts")?.trim() ?? "";

  const [searchInput, setSearchInput] = React.useState(paramQ);
  const [appliedQ, setAppliedQ] = React.useState<string | undefined>(paramQ || undefined);
  const [statusFilter, setStatusFilter] = React.useState<"ALL" | OrderStatus>(
    isValidOrderStatus(paramStatus) ? paramStatus : "ALL"
  );
  const [paymentFilter, setPaymentFilter] = React.useState<"ALL" | PaymentStatus>(
    isValidPaymentStatus(paramPayment) ? paramPayment : "ALL"
  );
  const [fulfillmentFilter, setFulfillmentFilter] = React.useState<"ALL" | FulfillmentStatus>(
    isValidFulfillmentStatus(paramFulfillment) ? paramFulfillment : "ALL"
  );

  const [fromDateInput, setFromDateInput] = React.useState(toDateInputValue(paramFromTs));
  const [toDateInput, setToDateInput] = React.useState(toDateInputValue(paramToTs));
  const [appliedFromTs, setAppliedFromTs] = React.useState<string | undefined>(
    paramFromTs || undefined
  );
  const [appliedToTs, setAppliedToTs] = React.useState<string | undefined>(
    paramToTs || undefined
  );
  const [dateRangeError, setDateRangeError] = React.useState<string | null>(null);
  const [page, setPage] = React.useState(1);

  React.useEffect(() => {
    const nextStatus = searchParams.get("status");
    const nextPayment = searchParams.get("payment_status");
    const nextFulfillment = searchParams.get("fulfillment_status");
    const nextQ = searchParams.get("q")?.trim().slice(0, 100) ?? "";
    const nextFrom = searchParams.get("from_ts")?.trim() ?? "";
    const nextTo = searchParams.get("to_ts")?.trim() ?? "";

    if (isValidOrderStatus(nextStatus)) {
      setStatusFilter(nextStatus);
    }
    if (isValidPaymentStatus(nextPayment)) {
      setPaymentFilter(nextPayment);
    }
    if (isValidFulfillmentStatus(nextFulfillment)) {
      setFulfillmentFilter(nextFulfillment);
    }
    if (nextQ) {
      setSearchInput(nextQ);
      setAppliedQ(nextQ);
    }
    if (nextFrom) {
      setFromDateInput(toDateInputValue(nextFrom));
      setAppliedFromTs(nextFrom);
    }
    if (nextTo) {
      setToDateInput(toDateInputValue(nextTo));
      setAppliedToTs(nextTo);
    }
  }, [searchParams]);

  const [data, setData] = React.useState<PaginatedResponse<AdminOrderListItemDTO> | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  React.useEffect(() => {
    if (appliedFromTs && appliedToTs && Date.parse(appliedFromTs) > Date.parse(appliedToTs)) {
      setDateRangeError("Start date must be before or equal to end date.");
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    setDateRangeError(null);

    adminOrdersApi
      .listOrders(
        {
          status: statusFilter === "ALL" ? undefined : statusFilter,
          payment_status: paymentFilter === "ALL" ? undefined : paymentFilter,
          fulfillment_status: fulfillmentFilter === "ALL" ? undefined : fulfillmentFilter,
          q: appliedQ,
          from_ts: appliedFromTs,
          to_ts: appliedToTs,
          page,
          page_size: PAGE_SIZE,
        },
        controller.signal
      )
      .then((res) => setData(res))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setError(resolveErrorMessage(err, "Unable to load orders."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [
    statusFilter,
    paymentFilter,
    fulfillmentFilter,
    appliedQ,
    appliedFromTs,
    appliedToTs,
    page,
    reloadNonce,
  ]);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    if (fromDateInput && toDateInput && fromDateInput > toDateInput) {
      setDateRangeError("Start date must be before or equal to end date.");
      return;
    }
    setDateRangeError(null);
    setAppliedQ(searchInput.trim().slice(0, 100) || undefined);
    setAppliedFromTs(toUtcRangeStartIso(fromDateInput));
    setAppliedToTs(toUtcRangeEndIso(toDateInput));
    setPage(1);
  };

  const handleClearFilters = () => {
    setSearchInput("");
    setAppliedQ(undefined);
    setStatusFilter("ALL");
    setPaymentFilter("ALL");
    setFulfillmentFilter("ALL");
    setFromDateInput("");
    setToDateInput("");
    setAppliedFromTs(undefined);
    setAppliedToTs(undefined);
    setDateRangeError(null);
    setPage(1);
  };

  const hasActiveFilters = Boolean(
    appliedQ ||
      statusFilter !== "ALL" ||
      paymentFilter !== "ALL" ||
      fulfillmentFilter !== "ALL" ||
      appliedFromTs ||
      appliedToTs
  );
  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Orders & Fulfillment"
        description="Triage customer and guest orders with explicit order, payment, and fulfillment states."
        badge={<Badge variant="outline">UTC Timestamps</Badge>}
      />

      {/* Filter Bar */}
      <form
        onSubmit={handleApplyFilters}
        className="space-y-3 rounded-lg border border-border bg-surface p-4"
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-12">
          <div className="space-y-1.5 lg:col-span-4">
            <Label htmlFor="orders-search">Order # or Customer Email</Label>
            <Input
              id="orders-search"
              type="search"
              maxLength={100}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="e.g., ORD-2026... or buyer@..."
            />
          </div>

          <div className="space-y-1.5 lg:col-span-2">
            <Label htmlFor="orders-status">Order Status</Label>
            <Select
              value={statusFilter}
              onValueChange={(val) => {
                setStatusFilter(val as "ALL" | OrderStatus);
                setPage(1);
              }}
            >
              <SelectTrigger id="orders-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All Orders</SelectItem>
                {ORDER_STATUSES.map((st) => (
                  <SelectItem key={st} value={st}>
                    {st}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5 lg:col-span-3">
            <Label htmlFor="orders-payment-status">Payment Status</Label>
            <Select
              value={paymentFilter}
              onValueChange={(val) => {
                setPaymentFilter(val as "ALL" | PaymentStatus);
                setPage(1);
              }}
            >
              <SelectTrigger id="orders-payment-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All Payments</SelectItem>
                {PAYMENT_STATUSES.map((st) => (
                  <SelectItem key={st} value={st}>
                    {st}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5 lg:col-span-3">
            <Label htmlFor="orders-fulfillment-status">Fulfillment Status</Label>
            <Select
              value={fulfillmentFilter}
              onValueChange={(val) => {
                setFulfillmentFilter(val as "ALL" | FulfillmentStatus);
                setPage(1);
              }}
            >
              <SelectTrigger id="orders-fulfillment-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All Fulfillment</SelectItem>
                {FULFILLMENT_STATUSES.map((st) => (
                  <SelectItem key={st} value={st}>
                    {st}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-3 border-t border-border pt-3">
          <div className="space-y-1">
            <Label htmlFor="orders-from-date" className="text-xs">
              Placed From (UTC)
            </Label>
            <Input
              id="orders-from-date"
              type="date"
              value={fromDateInput}
              onChange={(e) => setFromDateInput(e.target.value)}
              hasError={Boolean(dateRangeError)}
              className="w-40"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="orders-to-date" className="text-xs">
              Placed To (UTC)
            </Label>
            <Input
              id="orders-to-date"
              type="date"
              value={toDateInput}
              onChange={(e) => setToDateInput(e.target.value)}
              hasError={Boolean(dateRangeError)}
              className="w-40"
            />
          </div>
          <Button type="submit" variant="outline">
            <Search className="h-4 w-4" aria-hidden="true" />
            Apply Filters
          </Button>
          {hasActiveFilters ? (
            <Button type="button" variant="ghost" onClick={handleClearFilters}>
              <X className="h-4 w-4" aria-hidden="true" />
              Clear Filters
            </Button>
          ) : null}
          {dateRangeError ? (
            <p className="text-xs text-destructive" role="alert">
              {dateRangeError}
            </p>
          ) : null}
        </div>
      </form>

      <DataTableShell
        title="Orders"
        description={data ? `${data.total} matching order(s)` : undefined}
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle="No matching orders"
        emptyDescription={
          hasActiveFilters
            ? "No orders match the current search, status, or UTC date filters."
            : "Orders placed by customers or guests will appear here."
        }
        emptyAction={
          hasActiveFilters ? (
            <Button variant="outline" onClick={handleClearFilters}>
              Clear Filters
            </Button>
          ) : undefined
        }
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Order #</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Placed (UTC)</TableHead>
              <TableHead className="text-right">Items</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Order Status</TableHead>
              <TableHead>Payment Status</TableHead>
              <TableHead>Fulfillment Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((order) => (
              <TableRow key={order.id}>
                <TableCell className="whitespace-nowrap font-mono text-xs font-semibold">
                  <Link
                    href={`/admin/orders/${encodeURIComponent(order.order_number)}`}
                    className="text-foreground hover:text-accent hover:underline"
                  >
                    {order.order_number}
                  </Link>
                </TableCell>
                <TableCell className="max-w-[200px] truncate text-sm">
                  {order.customer_email}
                </TableCell>
                <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                  {formatUtcTimestamp(order.placed_at)}
                </TableCell>
                <TableCell className="text-right tabular-nums">{order.item_count}</TableCell>
                <TableCell className="text-right font-semibold tabular-nums">
                  {formatCents(order.total_cents, order.currency)}
                </TableCell>
                <TableCell>
                  <StatusBadge domain="order" value={order.status} />
                </TableCell>
                <TableCell>
                  <StatusBadge domain="payment" value={order.payment_status} />
                </TableCell>
                <TableCell>
                  <StatusBadge domain="fulfillment" value={order.fulfillment_status} />
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="outline" size="sm" asChild>
                    <Link href={`/admin/orders/${encodeURIComponent(order.order_number)}`}>
                      Manage
                      <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </Link>
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {data && data.total > 0 ? (
          <div className="px-4 pb-4">
            <Pagination
              page={data.page}
              pageSize={data.page_size}
              total={data.total}
              hasNext={data.has_next}
              onPageChange={(nextPage) => setPage(nextPage)}
              itemLabel="orders"
            />
          </div>
        ) : null}
      </DataTableShell>
    </div>
  );
}
