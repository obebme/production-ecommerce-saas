"use client";

import * as React from "react";
import Link from "next/link";
import { adminCatalogApi, adminInventoryApi } from "@/lib/api/client";
import type { AdminInventoryItemDTO, AdminProductSummaryDTO } from "@/lib/api/types";
import { formatCents } from "@/lib/format";
import { StatusBadge } from "@/components/shared/state-feedback";
import {
  AdminPageHeader,
  AdminStarterNotice,
  DataTableShell,
  KpiCard,
} from "@/components/admin/admin-primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function AdminDashboardView() {
  const [productsTotal, setProductsTotal] = React.useState<number | null>(null);
  const [categoriesTotal, setCategoriesTotal] = React.useState<number | null>(null);
  const [inventoryItems, setInventoryItems] = React.useState<AdminInventoryItemDTO[]>([]);
  const [inventoryTotal, setInventoryTotal] = React.useState<number | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    Promise.all([
      adminCatalogApi.listProducts({ page: 1, page_size: 5 }, controller.signal),
      adminCatalogApi.listCategories(controller.signal),
      adminInventoryApi.listInventory({ page: 1, page_size: 8 }, controller.signal),
    ])
      .then(([productsRes, categoriesRes, inventoryRes]) => {
        setProductsTotal(productsRes.total);
        setCategoriesTotal(categoriesRes.items.length);
        setInventoryTotal(inventoryRes.total);
        setInventoryItems(inventoryRes.items);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load admin summary.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [reloadNonce]);

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Operations Dashboard"
        description="Live BE-002 catalog and inventory metrics with analytics starter layout."
        badge={<Badge variant="outline">UTC Timezone</Badge>}
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link href="/admin/inventory">View Inventory</Link>
          </Button>
        }
      />

      {/* 4-Col KPI Starter Grid (Real BE-002 counts + honest BE-004 placeholders) */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          title="Catalog Products"
          value={productsTotal ?? "—"}
          subtitle={
            categoriesTotal !== null
              ? `Across ${categoriesTotal} categories`
              : "Live count from /api/v1/admin/products"
          }
          isLoading={isLoading}
        />
        <KpiCard
          title="Tracked SKUs"
          value={inventoryTotal ?? "—"}
          subtitle="Live count from /api/v1/admin/inventory"
          isLoading={isLoading}
        />
        <KpiCard
          title="Net Revenue (UTC)"
          value="Pending BE-004"
          subtitle="Sales analytics API connects in BE-004 / FE-003"
          badge={<Badge variant="secondary">FE-003</Badge>}
        />
        <KpiCard
          title="Paid Orders (UTC)"
          value="Pending BE-004"
          subtitle="Time-series & order breakdown connect in FE-003"
          badge={<Badge variant="secondary">FE-003</Badge>}
        />
      </div>

      {/* Live Inventory Snapshot Table */}
      <DataTableShell
        title="Inventory Snapshot"
        description="Live server-derived SKU availability from BE-002."
        headerActions={
          <Button variant="outline" size="sm" asChild>
            <Link href="/admin/inventory">All SKUs</Link>
          </Button>
        }
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={inventoryItems.length === 0}
        emptyTitle="No inventory SKUs found"
        emptyDescription="Create products and variants in FE-003 or seed catalog data to inspect live stock levels."
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>SKU</TableHead>
              <TableHead>Product / Variant</TableHead>
              <TableHead className="text-right">On Hand</TableHead>
              <TableHead className="text-right">Reserved</TableHead>
              <TableHead className="text-right">Available</TableHead>
              <TableHead>Stock Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {inventoryItems.map((item) => {
              const stockState = !item.in_stock
                ? "out_of_stock"
                : item.low_stock
                  ? "low_stock"
                  : "in_stock";
              return (
                <TableRow key={item.variant_id}>
                  <TableCell className="font-mono text-xs">{item.sku}</TableCell>
                  <TableCell>
                    <p className="font-medium text-foreground">{item.product_title}</p>
                    <p className="text-xs text-muted-foreground">{item.variant_title}</p>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{item.on_hand}</TableCell>
                  <TableCell className="text-right tabular-nums">{item.reserved}</TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {item.available_quantity}
                  </TableCell>
                  <TableCell>
                    <StatusBadge
                      domain="stock"
                      value={stockState}
                      availableQuantity={item.available_quantity}
                    />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </DataTableShell>
    </div>
  );
}

export function AdminProductsStarterView() {
  const [items, setItems] = React.useState<AdminProductSummaryDTO[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    adminCatalogApi
      .listProducts({ page: 1, page_size: 20 }, controller.signal)
      .then((res) => setItems(res.items))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load admin products.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });
    return () => controller.abort();
  }, [reloadNonce]);

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Products"
        description="Live catalog inventory overview across DRAFT, ACTIVE, and ARCHIVED statuses."
      />
      <AdminStarterNotice
        moduleTitle="Catalog Management"
        milestoneNote="Live listing connected to GET /api/v1/admin/products. Create, edit, image manager, and archive workflows arrive in FE-003."
      />
      <DataTableShell
        title="All Products"
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle="No products created yet"
        emptyDescription="Products created via the admin API will appear here."
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Price Range</TableHead>
              <TableHead className="text-right">Variants</TableHead>
              <TableHead className="text-right">Available</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <p className="font-medium text-foreground">{item.title}</p>
                  <p className="font-mono text-xs text-muted-foreground">{item.slug}</p>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {item.category.name}
                </TableCell>
                <TableCell>
                  <StatusBadge domain="product" value={item.status} />
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {item.min_price_cents != null
                    ? item.min_price_cents === item.max_price_cents
                      ? formatCents(item.min_price_cents, item.currency ?? "USD")
                      : `${formatCents(item.min_price_cents, item.currency ?? "USD")} – ${formatCents(item.max_price_cents ?? item.min_price_cents, item.currency ?? "USD")}`
                    : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {item.active_variants_count} / {item.variants_count}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {item.total_available_quantity}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataTableShell>
    </div>
  );
}
