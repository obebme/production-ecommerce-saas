"use client";

import * as React from "react";
import { adminCatalogApi, adminInventoryApi } from "@/lib/api/client";
import type { AdminCategoryDTO, AdminInventoryItemDTO } from "@/lib/api/types";
import { StatusBadge } from "@/components/shared/state-feedback";
import {
  AdminPageHeader,
  AdminRoleGuard,
  AdminStarterNotice,
  DataTableShell,
} from "@/components/admin/admin-primitives";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function AdminCategoriesStarterView() {
  const [items, setItems] = React.useState<AdminCategoryDTO[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    adminCatalogApi
      .listCategories(controller.signal)
      .then((res) => setItems(res.items))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load categories.");
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
        title="Categories"
        description="Live active and inactive catalog categories."
      />
      <AdminStarterNotice
        moduleTitle="Category Hierarchy"
        milestoneNote="Live listing connected to GET /api/v1/admin/categories. Category creation, editing, and cycle-safe parenting arrive in FE-003."
      />
      <DataTableShell
        title="All Categories"
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle="No categories found"
        emptyDescription="Create categories once FE-003 management dialogs are enabled."
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead className="text-right">Sort Order</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((cat) => (
              <TableRow key={cat.id}>
                <TableCell className="font-medium text-foreground">{cat.name}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{cat.slug}</TableCell>
                <TableCell className="text-right tabular-nums">{cat.sort_order}</TableCell>
                <TableCell>
                  <StatusBadge domain="active" value={String(cat.is_active)} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataTableShell>
    </div>
  );
}

export function AdminInventoryStarterView() {
  const [items, setItems] = React.useState<AdminInventoryItemDTO[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    adminInventoryApi
      .listInventory({ page: 1, page_size: 20 }, controller.signal)
      .then((res) => setItems(res.items))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load inventory.");
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
        title="Inventory Control"
        description="Live on-hand, reserved, and sellable stock counts per SKU."
      />
      <AdminStarterNotice
        moduleTitle="Inventory & Append-Only Ledger"
        milestoneNote="Live overview connected to GET /api/v1/admin/inventory. Manual stock adjustments and the append-only ledger drawer arrive in FE-003."
      />
      <DataTableShell
        title="SKU Stock Levels"
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle="No SKUs tracked yet"
        emptyDescription="Add variants to products to view physical and reserved stock levels."
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
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
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
                  <TableCell className="text-right text-muted-foreground tabular-nums">
                    {item.low_stock_threshold}
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

export function AdminOrdersStarterView() {
  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Orders & Fulfillment"
        description="Operational order workbench with distinct order, payment, and fulfillment states."
        badge={<Badge variant="secondary">FE-003</Badge>}
      />
      <AdminStarterNotice
        moduleTitle="Order Fulfillment Workbench"
        milestoneNote="RBAC-gated route shell ready. Live order search, fulfillment transitions, unpaid cancellations, and admin-only refunds connect with BE-004 / FE-003."
      />
    </div>
  );
}

export function AdminUsersStarterView() {
  return (
    <AdminRoleGuard requiredRole="admin">
      <div className="space-y-6">
        <AdminPageHeader
          title="Users & Roles"
          description="Administrator-only role assignment and session revocation controls."
          badge={<Badge variant="accent">Admin Only</Badge>}
        />
        <AdminStarterNotice
          moduleTitle="User & Role Management"
          milestoneNote="Strict administrator guard active. User filtering, role changes, and active-status session revocation connect with BE-004 / FE-003."
        />
      </div>
    </AdminRoleGuard>
  );
}

export function AdminAuditLogsStarterView() {
  return (
    <AdminRoleGuard requiredRole="admin">
      <div className="space-y-6">
        <AdminPageHeader
          title="Audit Logs"
          description="Administrator-only immutable audit log and before/after state diff viewer."
          badge={<Badge variant="accent">Admin Only</Badge>}
        />
        <AdminStarterNotice
          moduleTitle="Security & Operations Audit Logs"
          milestoneNote="Strict administrator guard active. Paginated audit filtering and JSON diff inspection drawer connect with BE-004 / FE-003."
        />
      </div>
    </AdminRoleGuard>
  );
}
