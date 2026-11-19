"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { PackageCheck } from "lucide-react";
import { ordersApi } from "@/lib/api/client";
import type { OrderListItemDTO, PaginatedResponse } from "@/lib/api/types";
import { formatCents, formatUtcTimestamp } from "@/lib/format";
import { useAuth } from "@/providers/auth-provider";
import { EmptyState, ErrorState, StatusBadge } from "@/components/shared/state-feedback";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PAGE_SIZE = 20;

function OrderHistorySkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading order history">
      <span className="sr-only">Loading order history...</span>
      {[1, 2, 3].map((idx) => (
        <Skeleton key={idx} className="h-20 w-full rounded-lg" />
      ))}
    </div>
  );
}

export function OrderHistoryView() {
  const router = useRouter();
  const { user, isLoading: authLoading, sessionExpired } = useAuth();

  const [page, setPage] = React.useState(1);
  const [data, setData] = React.useState<PaginatedResponse<OrderListItemDTO> | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  React.useEffect(() => {
    if (!authLoading && !user) {
      const target = sessionExpired
        ? "/login?expired=1&returnTo=%2Forders"
        : "/login?returnTo=%2Forders";
      router.replace(target);
    }
  }, [authLoading, user, sessionExpired, router]);

  React.useEffect(() => {
    if (authLoading || !user) {
      return;
    }

    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    ordersApi
      .listOrders({ page, page_size: PAGE_SIZE }, controller.signal)
      .then((res) => {
        setData(res);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load your orders.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [authLoading, user, page, reloadNonce]);

  return (
    <div className="mx-auto max-w-[1440px] space-y-8 px-4 py-10 sm:px-6 lg:px-8">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="font-display text-3xl font-medium tracking-[-0.02em] text-foreground">
            My Orders
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Track payment confirmations, stock reservations, and fulfillment updates.
          </p>
        </div>

        <Button variant="outline" size="sm" asChild>
          <Link href="/account">Account Profile</Link>
        </Button>
      </div>

      {authLoading || !user ? (
        <OrderHistorySkeleton />
      ) : isLoading && !data ? (
        <OrderHistorySkeleton />
      ) : error ? (
        <ErrorState
          title="Unable to load order history"
          message={error}
          onRetry={() => setReloadNonce((n) => n + 1)}
        />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="No orders yet"
          description="You haven't placed any orders yet. Explore the catalog to begin."
          icon={<PackageCheck className="h-5 w-5" aria-hidden="true" />}
          action={
            <Button size="lg" asChild>
              <Link href="/products">Start Shopping</Link>
            </Button>
          }
        />
      ) : (
        <div className="space-y-6">
          {/* Mobile Cards (< md) */}
          <div className="space-y-4 md:hidden">
            {data.items.map((order) => {
              const orderHref = `/orders/${encodeURIComponent(order.order_number)}`;
              return (
                <Card key={order.id}>
                  <CardContent className="space-y-4 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <Link
                          href={orderHref}
                          className="font-mono text-sm font-semibold text-foreground hover:text-accent"
                        >
                          {order.order_number}
                        </Link>
                        <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                          {formatUtcTimestamp(order.placed_at)}
                        </p>
                      </div>
                      <p className="text-base font-semibold text-foreground tabular-nums">
                        {formatCents(order.total_cents, order.currency)}
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-1.5">
                      <StatusBadge domain="order" value={order.status} />
                      <StatusBadge domain="payment" value={order.payment_status} />
                      <StatusBadge domain="fulfillment" value={order.fulfillment_status} />
                    </div>

                    <div className="flex items-center justify-between border-t border-border pt-3 text-xs text-muted-foreground">
                      <span className="tabular-nums">
                        {order.item_count} {order.item_count === 1 ? "item" : "items"}
                      </span>
                      <Button variant="outline" size="sm" asChild>
                        <Link href={orderHref}>View Order</Link>
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {/* Desktop Table (md+) */}
          <div className="hidden rounded-lg border border-border bg-surface md:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Order</TableHead>
                  <TableHead>Placed</TableHead>
                  <TableHead>Items</TableHead>
                  <TableHead>Total</TableHead>
                  <TableHead>Order Status</TableHead>
                  <TableHead>Payment</TableHead>
                  <TableHead>Fulfillment</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((order) => {
                  const orderHref = `/orders/${encodeURIComponent(order.order_number)}`;
                  return (
                    <TableRow key={order.id}>
                      <TableCell>
                        <Link
                          href={orderHref}
                          className="font-mono text-xs font-semibold text-foreground hover:text-accent"
                        >
                          {order.order_number}
                        </Link>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {formatUtcTimestamp(order.placed_at)}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {order.item_count}
                      </TableCell>
                      <TableCell className="font-medium tabular-nums">
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
                          <Link href={orderHref}>View Order</Link>
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>

          <Pagination
            page={data.page}
            pageSize={data.page_size}
            total={data.total}
            hasNext={data.has_next}
            onPageChange={(next) => setPage(next)}
            itemLabel="orders"
          />
        </div>
      )}
    </div>
  );
}
