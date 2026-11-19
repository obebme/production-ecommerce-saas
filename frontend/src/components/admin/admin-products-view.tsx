"use client";

import * as React from "react";
import Link from "next/link";
import { Archive, Pencil, Plus, Search, X } from "lucide-react";
import { ApiError, adminCatalogApi } from "@/lib/api/client";
import type {
  AdminCategoryDTO,
  AdminProductSummaryDTO,
  PaginatedResponse,
  ProductStatus,
} from "@/lib/api/types";
import { formatCents } from "@/lib/format";
import { resolveErrorMessage } from "@/components/admin/admin-helpers";
import { AdminPageHeader, DataTableShell } from "@/components/admin/admin-primitives";
import { ProductImage } from "@/components/shared/product-image";
import { StatusBadge } from "@/components/shared/state-feedback";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

const PAGE_SIZE = 20;

export function AdminProductsView() {
  const { toast } = useToast();

  const [categories, setCategories] = React.useState<AdminCategoryDTO[]>([]);
  const [searchInput, setSearchInput] = React.useState("");
  const [appliedQ, setAppliedQ] = React.useState<string | undefined>(undefined);
  const [statusFilter, setStatusFilter] = React.useState<"ALL" | ProductStatus>("ALL");
  const [categoryFilter, setCategoryFilter] = React.useState<string>("ALL");
  const [page, setPage] = React.useState(1);

  const [data, setData] = React.useState<PaginatedResponse<AdminProductSummaryDTO> | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  // Archive modal state
  const [archiveTarget, setArchiveTarget] = React.useState<AdminProductSummaryDTO | null>(null);
  const [archiveError, setArchiveError] = React.useState<string | null>(null);
  const [isArchiving, setIsArchiving] = React.useState(false);

  React.useEffect(() => {
    const controller = new AbortController();
    adminCatalogApi
      .listCategories(controller.signal)
      .then((res) => setCategories(res.items))
      .catch(() => {});
    return () => controller.abort();
  }, []);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    adminCatalogApi
      .listProducts(
        {
          q: appliedQ,
          status: statusFilter === "ALL" ? undefined : statusFilter,
          category_id: categoryFilter === "ALL" ? undefined : categoryFilter,
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
        setError(resolveErrorMessage(err, "Unable to load admin products."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [appliedQ, statusFilter, categoryFilter, page, reloadNonce]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = searchInput.trim().slice(0, 100);
    setAppliedQ(trimmed || undefined);
    setPage(1);
  };

  const handleClearFilters = () => {
    setSearchInput("");
    setAppliedQ(undefined);
    setStatusFilter("ALL");
    setCategoryFilter("ALL");
    setPage(1);
  };

  const handleConfirmArchive = async () => {
    if (!archiveTarget || isArchiving) {
      return;
    }
    setIsArchiving(true);
    setArchiveError(null);
    try {
      await adminCatalogApi.archiveProduct(archiveTarget.id);
      toast({
        title: "Product archived",
        description: `"${archiveTarget.title}" is now ARCHIVED and hidden from the storefront.`,
        variant: "success",
      });
      setArchiveTarget(null);
      setReloadNonce((n) => n + 1);
    } catch (err) {
      setArchiveError(
        err instanceof ApiError
          ? err.message
          : "Unable to archive product. Please try again."
      );
    } finally {
      setIsArchiving(false);
    }
  };

  const hasActiveFilters = Boolean(
    appliedQ || statusFilter !== "ALL" || categoryFilter !== "ALL"
  );
  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Products"
        description="Manage catalog products, images, variants, and publication statuses."
        actions={
          <Button asChild>
            <Link href="/admin/products/new">
              <Plus className="h-4 w-4" aria-hidden="true" />
              New Product
            </Link>
          </Button>
        }
      />

      {/* Filter Bar */}
      <form
        onSubmit={handleSearchSubmit}
        className="grid gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-2 lg:grid-cols-12 lg:items-end"
      >
        <div className="space-y-1.5 lg:col-span-5">
          <Label htmlFor="products-search">Search title, slug, or SKU</Label>
          <div className="flex gap-2">
            <Input
              id="products-search"
              type="search"
              maxLength={100}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search products..."
            />
            <Button type="submit" variant="outline">
              <Search className="h-4 w-4" aria-hidden="true" />
              Search
            </Button>
          </div>
        </div>

        <div className="space-y-1.5 lg:col-span-3">
          <Label htmlFor="products-status-filter">Status</Label>
          <Select
            value={statusFilter}
            onValueChange={(value) => {
              setStatusFilter(value as "ALL" | ProductStatus);
              setPage(1);
            }}
          >
            <SelectTrigger id="products-status-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">All Statuses</SelectItem>
              <SelectItem value="DRAFT">Draft</SelectItem>
              <SelectItem value="ACTIVE">Active</SelectItem>
              <SelectItem value="ARCHIVED">Archived</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5 lg:col-span-3">
          <Label htmlFor="products-category-filter">Category</Label>
          <Select
            value={categoryFilter}
            onValueChange={(value) => {
              setCategoryFilter(value);
              setPage(1);
            }}
          >
            <SelectTrigger id="products-category-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">All Categories</SelectItem>
              {categories.map((cat) => (
                <SelectItem key={cat.id} value={cat.id}>
                  {cat.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex lg:col-span-1 lg:justify-end">
          {hasActiveFilters ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleClearFilters}
              className="w-full lg:w-auto"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              Clear
            </Button>
          ) : null}
        </div>
      </form>

      <DataTableShell
        title="Catalog Products"
        description={data ? `${data.total} total products` : undefined}
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle={hasActiveFilters ? "No products match your filters" : "No products created yet"}
        emptyDescription={
          hasActiveFilters
            ? "Reset filters to view all products across DRAFT, ACTIVE, and ARCHIVED."
            : "Create your first product and add variants to publish to the storefront."
        }
        emptyAction={
          hasActiveFilters ? (
            <Button variant="outline" onClick={handleClearFilters}>
              Clear Filters
            </Button>
          ) : (
            <Button asChild>
              <Link href="/admin/products/new">
                <Plus className="h-4 w-4" aria-hidden="true" />
                Create First Product
              </Link>
            </Button>
          )
        }
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Price Range</TableHead>
              <TableHead className="text-right">Variants (Active/Total)</TableHead>
              <TableHead className="text-right">Available</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <div className="flex items-center gap-3">
                    <div className="h-11 w-11 shrink-0 overflow-hidden rounded-md border border-border bg-muted">
                      <ProductImage
                        src={item.primary_image?.url}
                        alt={item.primary_image?.alt_text || item.title}
                        fallbackLabel={item.title}
                        sizes="44px"
                      />
                    </div>
                    <div className="min-w-0">
                      <Link
                        href={`/admin/products/${item.id}`}
                        className="font-medium text-foreground hover:text-accent hover:underline"
                      >
                        {item.title}
                      </Link>
                      <p className="truncate font-mono text-xs text-muted-foreground">
                        {item.slug}
                      </p>
                    </div>
                  </div>
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
                      : `${formatCents(item.min_price_cents, item.currency ?? "USD")} – ${formatCents(
                          item.max_price_cents ?? item.min_price_cents,
                          item.currency ?? "USD"
                        )}`
                    : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {item.active_variants_count} / {item.variants_count}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {item.total_available_quantity}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-2">
                    <Button variant="outline" size="sm" asChild>
                      <Link href={`/admin/products/${item.id}`}>
                        <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                        Edit
                      </Link>
                    </Button>
                    {item.status !== "ARCHIVED" ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:bg-destructive-subtle hover:text-destructive"
                        onClick={() => {
                          setArchiveError(null);
                          setArchiveTarget(item);
                        }}
                        aria-label={`Archive ${item.title}`}
                      >
                        <Archive className="h-3.5 w-3.5" aria-hidden="true" />
                        Archive
                      </Button>
                    ) : null}
                  </div>
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
              itemLabel="products"
            />
          </div>
        ) : null}
      </DataTableShell>

      <Dialog
        open={Boolean(archiveTarget)}
        onOpenChange={(open) => {
          if (!open && !isArchiving) {
            setArchiveTarget(null);
            setArchiveError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Archive Product</DialogTitle>
            <DialogDescription>
              Archiving sets status to ARCHIVED and hides the product from the storefront while
              preserving historical order snapshots.
            </DialogDescription>
          </DialogHeader>

          {archiveTarget ? (
            <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
              <p className="font-medium text-foreground">{archiveTarget.title}</p>
              <p className="font-mono text-xs text-muted-foreground">{archiveTarget.slug}</p>
            </div>
          ) : null}

          {archiveError ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{archiveError}</AlertDescription>
            </Alert>
          ) : null}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setArchiveTarget(null)}
              disabled={isArchiving}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleConfirmArchive()}
              disabled={isArchiving}
            >
              {isArchiving ? "Archiving..." : "Archive Product"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
