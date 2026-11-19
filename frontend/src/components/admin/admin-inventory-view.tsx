"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertCircle, History, Search, SlidersHorizontal, X } from "lucide-react";
import { ApiError, adminInventoryApi } from "@/lib/api/client";
import type {
  AdminInventoryItemDTO,
  InventoryLedgerEntryDTO,
  PaginatedResponse,
  StockStatusFilter,
} from "@/lib/api/types";
import { formatUtcTimestamp } from "@/lib/format";
import {
  INT32_MAX,
  computeStockBadgeState,
  resolveErrorMessage,
} from "@/components/admin/admin-helpers";
import { AdminPageHeader, DataTableShell } from "@/components/admin/admin-primitives";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "@/components/shared/state-feedback";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Pagination } from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;
const LEDGER_PAGE_SIZE = 15;

const REASON_BADGE_MAP: Record<string, BadgeProps["variant"]> = {
  INITIAL_SEED: "outline",
  MANUAL_ADJUSTMENT: "accentSubtle",
  RESERVE: "warning",
  COMMIT: "success",
  RELEASE: "secondary",
  RETURN_RESTOCK: "default",
};

function formatSignedDelta(delta: number): string {
  if (delta > 0) {
    return `+${delta}`;
  }
  return String(delta);
}

export function AdminInventoryView() {
  const searchParams = useSearchParams();
  const { toast } = useToast();

  const initialSku = searchParams.get("sku")?.trim().slice(0, 100) ?? "";
  const initialQ = initialSku || searchParams.get("q")?.trim().slice(0, 100) || "";

  const [searchInput, setSearchInput] = React.useState(initialQ);
  const [appliedQ, setAppliedQ] = React.useState<string | undefined>(initialQ || undefined);
  const [stockStatus, setStockStatus] = React.useState<StockStatusFilter>("all");
  const [page, setPage] = React.useState(1);

  React.useEffect(() => {
    const paramQ =
      searchParams.get("sku")?.trim().slice(0, 100) ||
      searchParams.get("q")?.trim().slice(0, 100) ||
      "";
    if (paramQ) {
      setSearchInput(paramQ);
      setAppliedQ(paramQ);
      setPage(1);
    }
  }, [searchParams]);

  const [data, setData] = React.useState<PaginatedResponse<AdminInventoryItemDTO> | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  // Stock Adjustment Modal State
  const [adjustTarget, setAdjustTarget] = React.useState<AdminInventoryItemDTO | null>(null);
  const [deltaInput, setDeltaInput] = React.useState("");
  const [noteInput, setNoteInput] = React.useState("");
  const [adjustError, setAdjustError] = React.useState<string | null>(null);
  const [isAdjusting, setIsAdjusting] = React.useState(false);

  // Ledger Drawer State
  const [ledgerTarget, setLedgerTarget] = React.useState<AdminInventoryItemDTO | null>(null);
  const [ledgerPage, setLedgerPage] = React.useState(1);
  const [ledgerData, setLedgerData] =
    React.useState<PaginatedResponse<InventoryLedgerEntryDTO> | null>(null);
  const [ledgerLoading, setLedgerLoading] = React.useState(false);
  const [ledgerError, setLedgerError] = React.useState<string | null>(null);
  const [ledgerReloadNonce, setLedgerReloadNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    adminInventoryApi
      .listInventory(
        {
          q: appliedQ,
          stock_status: stockStatus,
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
        setError(resolveErrorMessage(err, "Unable to load inventory levels."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [appliedQ, stockStatus, page, reloadNonce]);

  React.useEffect(() => {
    if (!ledgerTarget) {
      return;
    }
    const controller = new AbortController();
    setLedgerLoading(true);
    setLedgerError(null);

    adminInventoryApi
      .listLedger(
        ledgerTarget.variant_id,
        { page: ledgerPage, page_size: LEDGER_PAGE_SIZE },
        controller.signal
      )
      .then((res) => setLedgerData(res))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setLedgerError(resolveErrorMessage(err, "Unable to load stock ledger."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLedgerLoading(false);
        }
      });

    return () => controller.abort();
  }, [ledgerTarget, ledgerPage, ledgerReloadNonce]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = searchInput.trim().slice(0, 100);
    setAppliedQ(trimmed || undefined);
    setPage(1);
  };

  const handleClearFilters = () => {
    setSearchInput("");
    setAppliedQ(undefined);
    setStockStatus("all");
    setPage(1);
  };

  const openAdjustModal = (item: AdminInventoryItemDTO) => {
    setAdjustTarget(item);
    setDeltaInput("");
    setNoteInput("");
    setAdjustError(null);
  };

  const openLedgerDrawer = (item: AdminInventoryItemDTO) => {
    setLedgerTarget(item);
    setLedgerPage(1);
    setLedgerData(null);
    setLedgerError(null);
  };

  // Parse signed delta and evaluate invariants
  const deltaTrimmed = deltaInput.trim();
  const isDeltaValidFormat = /^[+-]?\d+$/.test(deltaTrimmed);
  const parsedDelta = isDeltaValidFormat ? Number.parseInt(deltaTrimmed, 10) : null;
  const projectedOnHand =
    adjustTarget && parsedDelta !== null && Number.isSafeInteger(parsedDelta)
      ? adjustTarget.on_hand + parsedDelta
      : null;
  const projectedAvailable =
    adjustTarget && projectedOnHand !== null ? projectedOnHand - adjustTarget.reserved : null;

  let invariantError: string | null = null;
  if (adjustTarget && deltaTrimmed !== "") {
    if (!isDeltaValidFormat || parsedDelta === null || !Number.isSafeInteger(parsedDelta)) {
      invariantError = "Enter a valid signed integer delta (e.g., +10 or -2).";
    } else if (parsedDelta === 0) {
      invariantError = "Stock adjustment delta must be non-zero.";
    } else if (
      projectedOnHand !== null &&
      (projectedOnHand < adjustTarget.reserved || projectedOnHand < 0)
    ) {
      invariantError = `Cannot reduce physical on-hand stock below currently reserved units (${adjustTarget.reserved}).`;
    } else if (projectedOnHand !== null && projectedOnHand > INT32_MAX) {
      invariantError = "Adjusted on-hand quantity exceeds maximum supported limit.";
    }
  }

  const canSubmitAdjustment =
    Boolean(adjustTarget) &&
    parsedDelta !== null &&
    parsedDelta !== 0 &&
    invariantError === null &&
    noteInput.trim().length > 0 &&
    !isAdjusting;

  const handleConfirmAdjustment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!adjustTarget || !canSubmitAdjustment || parsedDelta === null) {
      return;
    }
    setAdjustError(null);
    setIsAdjusting(true);

    try {
      const updated = await adminInventoryApi.adjustStock(adjustTarget.variant_id, {
        delta_on_hand: parsedDelta,
        note: noteInput.trim(),
      });
      toast({
        title: "Stock adjusted",
        description: `${updated.sku}: On Hand is now ${updated.on_hand} (${updated.available_quantity} available).`,
        variant: "success",
      });
      setAdjustTarget(null);
      setReloadNonce((n) => n + 1);
      if (ledgerTarget && ledgerTarget.variant_id === updated.variant_id) {
        setLedgerTarget(updated);
        setLedgerPage(1);
        setLedgerReloadNonce((n) => n + 1);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setAdjustError(err.message);
      } else {
        setAdjustError("Unable to apply stock adjustment. Please try again.");
      }
    } finally {
      setIsAdjusting(false);
    }
  };

  const hasActiveFilters = Boolean(appliedQ || stockStatus !== "all");
  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Inventory Control"
        description="Monitor physical stock, reserved units, and append-only inventory movements."
      />

      {/* Filter Bar */}
      <form
        onSubmit={handleSearchSubmit}
        className="grid gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-2 lg:grid-cols-12 lg:items-end"
      >
        <div className="space-y-1.5 lg:col-span-6">
          <Label htmlFor="inventory-search">Search SKU, product, or variant</Label>
          <div className="flex gap-2">
            <Input
              id="inventory-search"
              type="search"
              maxLength={100}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="e.g., TEE-CHR-M or Merino..."
            />
            <Button type="submit" variant="outline">
              <Search className="h-4 w-4" aria-hidden="true" />
              Search
            </Button>
          </div>
        </div>

        <div className="space-y-1.5 lg:col-span-4">
          <Label htmlFor="inventory-stock-status">Stock Status</Label>
          <Select
            value={stockStatus}
            onValueChange={(value) => {
              setStockStatus(value as StockStatusFilter);
              setPage(1);
            }}
          >
            <SelectTrigger id="inventory-stock-status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="in_stock">In Stock</SelectItem>
              <SelectItem value="low_stock">Low Stock</SelectItem>
              <SelectItem value="out_of_stock">Out of Stock</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex lg:col-span-2 lg:justify-end">
          {hasActiveFilters ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleClearFilters}
              className="w-full lg:w-auto"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              Clear Filters
            </Button>
          ) : null}
        </div>
      </form>

      <DataTableShell
        title="SKU Stock Overview"
        description={data ? `${data.total} tracked SKU(s)` : undefined}
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle={hasActiveFilters ? "No SKUs match your filters" : "No inventory SKUs found"}
        emptyDescription={
          hasActiveFilters
            ? "Clear search or stock-status filters to view all tracked SKUs."
            : "Create products and variants to manage inventory stock."
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
              <TableHead>SKU</TableHead>
              <TableHead>Product / Variant</TableHead>
              <TableHead className="text-right">On Hand</TableHead>
              <TableHead className="text-right">Reserved</TableHead>
              <TableHead className="text-right">Available</TableHead>
              <TableHead className="text-right">Threshold</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const stockState = computeStockBadgeState(item.in_stock, item.low_stock);
              return (
                <TableRow key={item.variant_id}>
                  <TableCell className="font-mono text-xs">{item.sku}</TableCell>
                  <TableCell>
                    <div className="space-y-0.5">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Link
                          href={`/admin/products/${item.product_id}`}
                          className="font-medium text-foreground hover:text-accent hover:underline"
                        >
                          {item.product_title}
                        </Link>
                        <StatusBadge domain="product" value={item.product_status} />
                        {!item.is_active ? (
                          <Badge variant="secondary">Variant Inactive</Badge>
                        ) : null}
                      </div>
                      <p className="text-xs text-muted-foreground">{item.variant_title}</p>
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{item.on_hand}</TableCell>
                  <TableCell className="text-right tabular-nums">{item.reserved}</TableCell>
                  <TableCell className="text-right">
                    <div className="inline-flex items-center justify-end gap-2">
                      <span className="font-semibold tabular-nums">{item.available_quantity}</span>
                      <StatusBadge
                        domain="stock"
                        value={stockState}
                        availableQuantity={item.available_quantity}
                      />
                    </div>
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground tabular-nums">
                    {item.low_stock_threshold}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => openAdjustModal(item)}
                      >
                        <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                        Adjust Stock
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openLedgerDrawer(item)}
                      >
                        <History className="h-3.5 w-3.5" aria-hidden="true" />
                        View Ledger
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
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
              itemLabel="SKUs"
            />
          </div>
        ) : null}
      </DataTableShell>

      {/* Manual Stock Adjustment Modal */}
      <Dialog
        open={Boolean(adjustTarget)}
        onOpenChange={(open) => {
          if (!open && !isAdjusting) {
            setAdjustTarget(null);
            setAdjustError(null);
          }
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Adjust Stock — {adjustTarget?.sku}</DialogTitle>
            <DialogDescription>
              {adjustTarget
                ? `${adjustTarget.product_title} (${adjustTarget.variant_title})`
                : ""}
            </DialogDescription>
          </DialogHeader>

          {adjustTarget ? (
            <form onSubmit={(e) => void handleConfirmAdjustment(e)} className="space-y-4" noValidate>
              {/* Immutable Snapshot Header */}
              <div className="grid grid-cols-3 gap-2 rounded-md border border-border bg-muted/40 p-3 text-center tabular-nums">
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    On Hand
                  </p>
                  <p className="text-lg font-semibold text-foreground">{adjustTarget.on_hand}</p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    Reserved
                  </p>
                  <p className="text-lg font-semibold text-foreground">{adjustTarget.reserved}</p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    Available
                  </p>
                  <p className="text-lg font-semibold text-foreground">
                    {adjustTarget.available_quantity}
                  </p>
                </div>
              </div>

              {adjustError ? (
                <Alert variant="destructive" role="alert">
                  <AlertCircle className="h-4 w-4" aria-hidden="true" />
                  <AlertDescription>{adjustError}</AlertDescription>
                </Alert>
              ) : null}

              <div className="space-y-1.5">
                <Label htmlFor="adjust-delta">Delta On Hand (signed integer)</Label>
                <Input
                  id="adjust-delta"
                  inputMode="numeric"
                  value={deltaInput}
                  onChange={(e) => {
                    setDeltaInput(e.target.value);
                    setAdjustError(null);
                  }}
                  placeholder="e.g., +10 or -2"
                  hasError={Boolean(invariantError)}
                  disabled={isAdjusting}
                  required
                />
                <p className="text-xs text-muted-foreground">
                  Positive adds physical units; negative removes unreserved physical units.
                </p>
              </div>

              {/* Live Projected Preview */}
              {projectedOnHand !== null && projectedAvailable !== null ? (
                <div
                  className={cn(
                    "rounded-md border p-3 text-sm tabular-nums",
                    invariantError
                      ? "border-destructive/40 bg-destructive-subtle text-destructive"
                      : "border-border bg-muted/30 text-foreground"
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span>Projected On Hand</span>
                    <span className="font-semibold">{projectedOnHand}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between">
                    <span>Projected Available</span>
                    <span className="font-semibold">{projectedAvailable}</span>
                  </div>
                </div>
              ) : null}

              {invariantError ? (
                <Alert variant="destructive" role="alert">
                  <AlertCircle className="h-4 w-4" aria-hidden="true" />
                  <AlertDescription>{invariantError}</AlertDescription>
                </Alert>
              ) : null}

              <div className="space-y-1.5">
                <Label htmlFor="adjust-note">Adjustment Note (required)</Label>
                <Textarea
                  id="adjust-note"
                  rows={3}
                  maxLength={255}
                  value={noteInput}
                  onChange={(e) => setNoteInput(e.target.value)}
                  placeholder="e.g., Restock PO-1042 or Damaged units write-off"
                  disabled={isAdjusting}
                  required
                />
                <p className="text-xs text-muted-foreground">
                  Recorded permanently in the append-only inventory ledger.
                </p>
              </div>

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setAdjustTarget(null)}
                  disabled={isAdjusting}
                >
                  Cancel
                </Button>
                <Button type="submit" variant="accent" disabled={!canSubmitAdjustment}>
                  {isAdjusting ? "Applying..." : "Apply Adjustment"}
                </Button>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Append-Only Inventory Ledger Drawer */}
      <Sheet
        open={Boolean(ledgerTarget)}
        onOpenChange={(open) => {
          if (!open) {
            setLedgerTarget(null);
          }
        }}
      >
        <SheetContent side="right" className="max-w-full sm:max-w-3xl">
          <SheetHeader>
            <div className="flex flex-wrap items-center gap-2 pr-8">
              <SheetTitle>Stock Ledger — {ledgerTarget?.sku}</SheetTitle>
              <Badge variant="outline">Append-Only Stock Ledger</Badge>
            </div>
            <SheetDescription>
              {ledgerTarget
                ? `${ledgerTarget.product_title} (${ledgerTarget.variant_title}) — immutable audit history of every stock change.`
                : ""}
            </SheetDescription>
          </SheetHeader>

          <div className="flex-1 overflow-y-auto p-6">
            {ledgerLoading ? (
              <LoadingState label="Loading inventory ledger..." rows={5} />
            ) : ledgerError ? (
              <ErrorState
                title="Unable to load ledger"
                message={ledgerError}
                onRetry={() => setLedgerReloadNonce((n) => n + 1)}
              />
            ) : !ledgerData || ledgerData.items.length === 0 ? (
              <EmptyState
                title="No ledger entries yet"
                description="Stock movements for this SKU will appear here chronologically."
              />
            ) : (
              <div className="space-y-4">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Timestamp (UTC)</TableHead>
                      <TableHead>Reason</TableHead>
                      <TableHead className="text-right">Δ On Hand</TableHead>
                      <TableHead className="text-right">Δ Reserved</TableHead>
                      <TableHead className="text-right">After (OH / Res)</TableHead>
                      <TableHead>Order</TableHead>
                      <TableHead>Actor</TableHead>
                      <TableHead>Note</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {ledgerData.items.map((entry) => (
                      <TableRow key={entry.id}>
                        <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                          {formatUtcTimestamp(entry.created_at)}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={REASON_BADGE_MAP[entry.reason] ?? "outline"}
                            className="font-mono text-[11px]"
                          >
                            {entry.reason}
                          </Badge>
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono text-xs font-semibold tabular-nums",
                            entry.delta_on_hand > 0 && "text-success",
                            entry.delta_on_hand < 0 && "text-destructive"
                          )}
                        >
                          {formatSignedDelta(entry.delta_on_hand)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {formatSignedDelta(entry.delta_reserved)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {entry.on_hand_after} / {entry.reserved_after}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {entry.order_number ? (
                            <Link
                              href={`/admin/orders/${encodeURIComponent(entry.order_number)}`}
                              className="text-accent hover:underline"
                            >
                              {entry.order_number}
                            </Link>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {entry.actor_email ?? "System"}
                        </TableCell>
                        <TableCell className="max-w-[200px] text-xs text-foreground">
                          {entry.note ?? "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>

                <Pagination
                  page={ledgerData.page}
                  pageSize={ledgerData.page_size}
                  total={ledgerData.total}
                  hasNext={ledgerData.has_next}
                  onPageChange={(nextPage) => setLedgerPage(nextPage)}
                  itemLabel="ledger entries"
                />
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
