"use client";

import * as React from "react";
import { AlertCircle, CornerDownRight, Pencil, Plus } from "lucide-react";
import { ApiError, adminCatalogApi } from "@/lib/api/client";
import type {
  AdminCategoryDTO,
  CreateCategoryRequest,
  UpdateCategoryRequest,
} from "@/lib/api/types";
import {
  SLUG_RE,
  extractFieldErrorMap,
  resolveErrorMessage,
  slugify,
} from "@/components/admin/admin-helpers";
import { AdminPageHeader, DataTableShell } from "@/components/admin/admin-primitives";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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

interface HierarchicalCategoryRow {
  category: AdminCategoryDTO;
  depth: number;
  parentName: string | null;
}

function buildHierarchicalCategories(items: AdminCategoryDTO[]): HierarchicalCategoryRow[] {
  const byId = new Map<string, AdminCategoryDTO>();
  for (const cat of items) {
    byId.set(cat.id, cat);
  }

  const childrenMap = new Map<string | null, AdminCategoryDTO[]>();
  for (const cat of items) {
    const effectiveParentId = cat.parent_id && byId.has(cat.parent_id) ? cat.parent_id : null;
    const list = childrenMap.get(effectiveParentId) ?? [];
    list.push(cat);
    childrenMap.set(effectiveParentId, list);
  }

  for (const list of childrenMap.values()) {
    list.sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));
  }

  const rows: HierarchicalCategoryRow[] = [];
  const visited = new Set<string>();

  function walk(parentId: string | null, depth: number) {
    const children = childrenMap.get(parentId) ?? [];
    for (const child of children) {
      if (visited.has(child.id)) {
        continue;
      }
      visited.add(child.id);
      const parent = child.parent_id ? byId.get(child.parent_id) ?? null : null;
      rows.push({
        category: child,
        depth,
        parentName: parent ? parent.name : null,
      });
      walk(child.id, depth + 1);
    }
  }

  walk(null, 0);

  // Append any unvisited nodes safely in case backend data ever had a disconnected cycle
  for (const cat of items) {
    if (!visited.has(cat.id)) {
      const parent = cat.parent_id ? byId.get(cat.parent_id) ?? null : null;
      rows.push({ category: cat, depth: 0, parentName: parent ? parent.name : null });
    }
  }

  return rows;
}

interface CategoryFormState {
  name: string;
  slug: string;
  parentId: string;
  description: string;
  sortOrder: string;
  isActive: boolean;
}

const EMPTY_FORM: CategoryFormState = {
  name: "",
  slug: "",
  parentId: "__none__",
  description: "",
  sortOrder: "0",
  isActive: true,
};

export function AdminCategoriesView() {
  const { toast } = useToast();

  const [items, setItems] = React.useState<AdminCategoryDTO[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  // Dialog state
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editingCategory, setEditingCategory] = React.useState<AdminCategoryDTO | null>(null);
  const [form, setForm] = React.useState<CategoryFormState>(EMPTY_FORM);
  const [slugManuallyEdited, setSlugManuallyEdited] = React.useState(false);
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [isSaving, setIsSaving] = React.useState(false);

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
        setError(resolveErrorMessage(err, "Unable to load categories."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });
    return () => controller.abort();
  }, [reloadNonce]);

  const hierarchicalRows = React.useMemo(() => buildHierarchicalCategories(items), [items]);

  const openCreateDialog = () => {
    setEditingCategory(null);
    setForm(EMPTY_FORM);
    setSlugManuallyEdited(false);
    setFieldErrors({});
    setFormError(null);
    setDialogOpen(true);
  };

  const openEditDialog = (cat: AdminCategoryDTO) => {
    setEditingCategory(cat);
    setForm({
      name: cat.name,
      slug: cat.slug,
      parentId: cat.parent_id ?? "__none__",
      description: cat.description ?? "",
      sortOrder: String(cat.sort_order),
      isActive: cat.is_active,
    });
    setSlugManuallyEdited(true);
    setFieldErrors({});
    setFormError(null);
    setDialogOpen(true);
  };

  const handleNameChange = (value: string) => {
    setForm((prev) => ({
      ...prev,
      name: value,
      slug: !slugManuallyEdited && !editingCategory ? slugify(value) : prev.slug,
    }));
    setFieldErrors((prev) => {
      if (!prev.name && !prev.slug) {
        return prev;
      }
      const next = { ...prev };
      delete next.name;
      if (!slugManuallyEdited && !editingCategory) {
        delete next.slug;
      }
      return next;
    });
  };

  const handleSlugChange = (value: string) => {
    setSlugManuallyEdited(true);
    setForm((prev) => ({ ...prev, slug: value }));
    setFieldErrors((prev) => {
      if (!prev.slug) {
        return prev;
      }
      const next = { ...prev };
      delete next.slug;
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const nextErrors: Record<string, string> = {};
    const trimmedName = form.name.trim();
    const normalizedSlug = form.slug.trim().toLowerCase();
    const parsedSortOrder = Number.parseInt(form.sortOrder.trim() || "0", 10);

    if (!trimmedName) {
      nextErrors.name = "Category name is required.";
    } else if (trimmedName.length > 120) {
      nextErrors.name = "Category name must be at most 120 characters.";
    }

    if (!normalizedSlug) {
      nextErrors.slug = "Slug is required.";
    } else if (!SLUG_RE.test(normalizedSlug)) {
      nextErrors.slug =
        "Slug must be lowercase kebab-case alphanumeric segments separated by hyphens.";
    } else if (normalizedSlug.length > 140) {
      nextErrors.slug = "Slug must be at most 140 characters.";
    }

    if (
      form.sortOrder.trim() !== "" &&
      (!/^-?\d+$/.test(form.sortOrder.trim()) ||
        !Number.isSafeInteger(parsedSortOrder) ||
        parsedSortOrder < -2_147_483_648 ||
        parsedSortOrder > 2_147_483_647)
    ) {
      nextErrors.sort_order = "Sort order must be a valid 32-bit integer.";
    }

    if (Object.keys(nextErrors).length > 0) {
      setFieldErrors(nextErrors);
      return;
    }

    setFieldErrors({});
    setIsSaving(true);

    const parentId = form.parentId === "__none__" ? null : form.parentId;
    const description = form.description.trim() ? form.description : null;
    const sortOrder = Number.isSafeInteger(parsedSortOrder) ? parsedSortOrder : 0;

    try {
      if (editingCategory) {
        const payload: UpdateCategoryRequest = {
          name: trimmedName,
          slug: normalizedSlug,
          parent_id: parentId,
          description,
          sort_order: sortOrder,
          is_active: form.isActive,
        };
        await adminCatalogApi.updateCategory(editingCategory.id, payload);
        toast({
          title: "Category updated",
          description: `Saved changes to "${trimmedName}".`,
          variant: "success",
        });
      } else {
        const payload: CreateCategoryRequest = {
          name: trimmedName,
          slug: normalizedSlug,
          parent_id: parentId,
          description,
          sort_order: sortOrder,
          is_active: form.isActive,
        };
        await adminCatalogApi.createCategory(payload);
        toast({
          title: "Category created",
          description: `Created category "${trimmedName}".`,
          variant: "success",
        });
      }
      setDialogOpen(false);
      setReloadNonce((n) => n + 1);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409 && err.code === "SLUG_CONFLICT") {
          setFieldErrors({ slug: err.message || "A category with this slug already exists." });
          return;
        }
        if (err.code === "CATEGORY_CYCLE_DETECTED") {
          setFieldErrors({
            parent_id: "Selected parent would create a category cycle.",
          });
          return;
        }
        if (err.status === 422) {
          const mapped = extractFieldErrorMap(err);
          if (Object.keys(mapped).length > 0) {
            setFieldErrors(mapped);
            return;
          }
        }
        setFormError(err.message);
      } else {
        setFormError("Unable to save category. Please try again.");
      }
    } finally {
      setIsSaving(false);
    }
  };

  const parentCandidates = React.useMemo(
    () =>
      hierarchicalRows.filter((row) =>
        editingCategory ? row.category.id !== editingCategory.id : true
      ),
    [hierarchicalRows, editingCategory]
  );

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Categories"
        description="Manage hierarchical storefront and catalog categories."
        actions={
          <Button onClick={openCreateDialog}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            New Category
          </Button>
        }
      />

      <DataTableShell
        title="Category Hierarchy"
        description="Ordered by sort order and name within each parent branch."
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={hierarchicalRows.length === 0}
        emptyTitle="No categories found"
        emptyDescription="Create your first category to organize storefront products."
        emptyAction={
          <Button onClick={openCreateDialog}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Create First Category
          </Button>
        }
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead>Parent Category</TableHead>
              <TableHead className="text-right">Sort Order</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {hierarchicalRows.map(({ category, depth, parentName }) => (
              <TableRow key={category.id}>
                <TableCell>
                  <div
                    className="flex items-center gap-1.5 font-medium text-foreground"
                    style={{ paddingLeft: `${depth * 20}px` }}
                  >
                    {depth > 0 ? (
                      <CornerDownRight
                        className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                    ) : null}
                    <span>{category.name}</span>
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  {category.slug}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {parentName ?? "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">{category.sort_order}</TableCell>
                <TableCell>
                  <StatusBadge domain="active" value={String(category.is_active)} />
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => openEditDialog(category)}
                    aria-label={`Edit category ${category.name}`}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                    Edit
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataTableShell>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editingCategory ? `Edit Category: ${editingCategory.name}` : "New Category"}
            </DialogTitle>
            <DialogDescription>
              {editingCategory
                ? "Update category hierarchy, slug, and visibility."
                : "Create a new catalog category with a unique lowercase kebab-case slug."}
            </DialogDescription>
          </DialogHeader>

          {formError ? (
            <Alert variant="destructive" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}

          <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="cat-name">Name</Label>
              <Input
                id="cat-name"
                value={form.name}
                onChange={(e) => handleNameChange(e.target.value)}
                maxLength={120}
                required
                hasError={Boolean(fieldErrors.name)}
                placeholder="e.g., Knitwear"
                disabled={isSaving}
              />
              {fieldErrors.name ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.name}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="cat-slug">Slug</Label>
              <Input
                id="cat-slug"
                className="font-mono"
                value={form.slug}
                onChange={(e) => handleSlugChange(e.target.value)}
                maxLength={140}
                required
                hasError={Boolean(fieldErrors.slug)}
                placeholder="e.g., knitwear"
                disabled={isSaving}
              />
              <p className="text-xs text-muted-foreground">
                Lowercase alphanumeric segments separated by hyphens.
              </p>
              {fieldErrors.slug ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.slug}
                </p>
              ) : null}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="cat-parent">Parent Category</Label>
                <Select
                  value={form.parentId}
                  onValueChange={(value) => {
                    setForm((prev) => ({ ...prev, parentId: value }));
                    setFieldErrors((prev) => {
                      if (!prev.parent_id) return prev;
                      const next = { ...prev };
                      delete next.parent_id;
                      return next;
                    });
                  }}
                  disabled={isSaving}
                >
                  <SelectTrigger
                    id="cat-parent"
                    aria-invalid={Boolean(fieldErrors.parent_id) || undefined}
                  >
                    <SelectValue placeholder="None (Top-level)" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">None (Top-level)</SelectItem>
                    {parentCandidates.map((row) => (
                      <SelectItem key={row.category.id} value={row.category.id}>
                        {row.depth > 0 ? `${"— ".repeat(row.depth)}${row.category.name}` : row.category.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {fieldErrors.parent_id ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.parent_id}
                  </p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="cat-sort-order">Sort Order</Label>
                <Input
                  id="cat-sort-order"
                  type="number"
                  step={1}
                  value={form.sortOrder}
                  onChange={(e) => setForm((prev) => ({ ...prev, sortOrder: e.target.value }))}
                  hasError={Boolean(fieldErrors.sort_order)}
                  disabled={isSaving}
                />
                {fieldErrors.sort_order ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.sort_order}
                  </p>
                ) : null}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="cat-description">Description</Label>
              <Textarea
                id="cat-description"
                value={form.description}
                onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
                maxLength={5000}
                rows={3}
                placeholder="Optional category description..."
                disabled={isSaving}
                hasError={Boolean(fieldErrors.description)}
              />
              {fieldErrors.description ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.description}
                </p>
              ) : null}
            </div>

            <div className="flex items-center justify-between rounded-md border border-border bg-muted/40 px-3 py-2.5">
              <div className="space-y-0.5">
                <Label htmlFor="cat-active" className="cursor-pointer">
                  Active Category
                </Label>
                <p className="text-xs text-muted-foreground">
                  Inactive categories and their products are hidden from the storefront.
                </p>
              </div>
              <Switch
                id="cat-active"
                checked={form.isActive}
                onCheckedChange={(checked) => setForm((prev) => ({ ...prev, isActive: checked }))}
                disabled={isSaving}
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
                disabled={isSaving}
              >
                Cancel
              </Button>
              <Button type="submit" variant="accent" disabled={isSaving}>
                {isSaving
                  ? "Saving..."
                  : editingCategory
                    ? "Save Changes"
                    : "Create Category"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
