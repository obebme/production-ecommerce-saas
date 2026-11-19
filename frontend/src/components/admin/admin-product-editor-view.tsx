"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  ImageIcon,
  Pencil,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { ApiError, adminCatalogApi } from "@/lib/api/client";
import type {
  AdminCategoryDTO,
  AdminProductDetailDTO,
  AdminVariantDTO,
  CreateProductRequest,
  ProductImageInput,
  ProductStatus,
  UpdateProductRequest,
} from "@/lib/api/types";
import { formatCents, sanitizeMediaUrl } from "@/lib/format";
import {
  SLUG_RE,
  UUID_RE,
  computeStockBadgeState,
  extractFieldErrorMap,
  resolveErrorMessage,
  slugify,
} from "@/components/admin/admin-helpers";
import { AdminPageHeader } from "@/components/admin/admin-primitives";
import { AdminVariantModal } from "@/components/admin/admin-variant-modal";
import { EmptyState, LoadingState, StatusBadge } from "@/components/shared/state-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";

interface EditableImageRow {
  localId: string;
  url: string;
  alt_text: string;
  sort_order: string;
  is_primary: boolean;
}

function makeLocalId(): string {
  return `img_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function AdminProductEditorView({
  mode,
  productId,
}: {
  mode: "create" | "edit";
  productId?: string;
}) {
  const router = useRouter();
  const { toast } = useToast();

  const isInvalidId = mode === "edit" && (!productId || !UUID_RE.test(productId));

  const [categories, setCategories] = React.useState<AdminCategoryDTO[]>([]);
  const [product, setProduct] = React.useState<AdminProductDetailDTO | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [notFoundError, setNotFoundError] = React.useState(isInvalidId);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  // Core Details form
  const [title, setTitle] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugManuallyEdited, setSlugManuallyEdited] = React.useState(false);
  const [categoryId, setCategoryId] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [status, setStatus] = React.useState<ProductStatus>("DRAFT");

  // Images form
  const [images, setImages] = React.useState<EditableImageRow[]>([]);
  const [imageErrors, setImageErrors] = React.useState<Record<string, string>>({});

  // Validation & Save state
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [isSaving, setIsSaving] = React.useState(false);

  // Variant modal state
  const [variantModalOpen, setVariantModalOpen] = React.useState(false);
  const [editingVariant, setEditingVariant] = React.useState<AdminVariantDTO | null>(null);

  const hydrateFromProduct = React.useCallback((dto: AdminProductDetailDTO) => {
    setProduct(dto);
    setTitle(dto.title);
    setSlug(dto.slug);
    setSlugManuallyEdited(true);
    setCategoryId(dto.category.id);
    setDescription(dto.description);
    setStatus(dto.status);
    setImages(
      dto.images.map((img) => ({
        localId: img.id || makeLocalId(),
        url: img.url,
        alt_text: img.alt_text,
        sort_order: String(img.sort_order),
        is_primary: img.is_primary,
      }))
    );
    setImageErrors({});
  }, []);

  React.useEffect(() => {
    if (isInvalidId) {
      setNotFoundError(true);
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    setIsLoading(true);
    setLoadError(null);
    setNotFoundError(false);

    if (mode === "create") {
      adminCatalogApi
        .listCategories(controller.signal)
        .then((catRes) => {
          setCategories(catRes.items);
          if (catRes.items.length > 0 && !categoryId) {
            setCategoryId(catRes.items[0].id);
          }
        })
        .catch((err) => {
          if (err instanceof DOMException && err.name === "AbortError") {
            return;
          }
          setLoadError(resolveErrorMessage(err, "Unable to load categories."));
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setIsLoading(false);
          }
        });
    } else if (productId) {
      Promise.all([
        adminCatalogApi.getProduct(productId, controller.signal),
        adminCatalogApi.listCategories(controller.signal),
      ])
        .then(([prodRes, catRes]) => {
          setCategories(catRes.items);
          hydrateFromProduct(prodRes);
        })
        .catch((err) => {
          if (err instanceof DOMException && err.name === "AbortError") {
            return;
          }
          if (err instanceof ApiError && err.status === 404) {
            setNotFoundError(true);
            return;
          }
          setLoadError(resolveErrorMessage(err, "Unable to load product details."));
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setIsLoading(false);
          }
        });
    }

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, productId, isInvalidId, reloadNonce, hydrateFromProduct]);

  const activeVariantsCount = React.useMemo(
    () => (product ? product.variants.filter((v) => v.is_active).length : 0),
    [product]
  );

  const activationBlocked = status === "ACTIVE" && activeVariantsCount === 0;

  const handleTitleChange = (val: string) => {
    setTitle(val);
    if (mode === "create" && !slugManuallyEdited) {
      setSlug(slugify(val));
    }
    setFieldErrors((prev) => {
      if (!prev.title && !prev.slug) return prev;
      const next = { ...prev };
      delete next.title;
      if (mode === "create" && !slugManuallyEdited) delete next.slug;
      return next;
    });
  };

  const handleAddImage = () => {
    setImages((prev) => [
      ...prev,
      {
        localId: makeLocalId(),
        url: "",
        alt_text: "",
        sort_order: String(prev.length),
        is_primary: prev.length === 0,
      },
    ]);
  };

  const handleRemoveImage = (localId: string) => {
    setImages((prev) => {
      const remaining = prev.filter((img) => img.localId !== localId);
      if (remaining.length > 0 && !remaining.some((img) => img.is_primary)) {
        remaining[0] = { ...remaining[0], is_primary: true };
      }
      return remaining;
    });
    setImageErrors((prev) => {
      if (!prev[localId]) return prev;
      const next = { ...prev };
      delete next[localId];
      return next;
    });
  };

  const handlePrimaryChange = (localId: string) => {
    setImages((prev) =>
      prev.map((img) => ({
        ...img,
        is_primary: img.localId === localId,
      }))
    );
  };

  const validateAndBuildImages = (): ProductImageInput[] | null => {
    const nextImageErrors: Record<string, string> = {};
    const built: ProductImageInput[] = [];

    for (const img of images) {
      const trimmedUrl = img.url.trim();
      const safeUrl = sanitizeMediaUrl(trimmedUrl);
      if (!trimmedUrl) {
        nextImageErrors[img.localId] =
          "Image URL is required. Allowed formats: https://... or /assets/...";
        continue;
      }
      if (!safeUrl) {
        nextImageErrors[img.localId] =
          "Invalid URL. Only https://... or /assets/... URLs without whitespace or traversal are allowed.";
        continue;
      }
      const parsedOrder = Number.parseInt(img.sort_order.trim() || "0", 10);
      if (
        img.sort_order.trim() !== "" &&
        (!/^-?\d+$/.test(img.sort_order.trim()) || !Number.isSafeInteger(parsedOrder))
      ) {
        nextImageErrors[img.localId] = "Sort order must be a valid integer.";
        continue;
      }

      built.push({
        url: safeUrl,
        alt_text: img.alt_text.trim().slice(0, 200),
        sort_order: Number.isSafeInteger(parsedOrder) ? parsedOrder : 0,
        is_primary: img.is_primary,
      });
    }

    setImageErrors(nextImageErrors);
    return Object.keys(nextImageErrors).length === 0 ? built : null;
  };

  const handleSaveProduct = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const nextFieldErrors: Record<string, string> = {};
    const trimmedTitle = title.trim();
    const normalizedSlug = slug.trim().toLowerCase();

    if (!trimmedTitle) {
      nextFieldErrors.title = "Product title is required.";
    } else if (trimmedTitle.length > 200) {
      nextFieldErrors.title = "Product title must be at most 200 characters.";
    }

    if (!normalizedSlug) {
      nextFieldErrors.slug = "Slug is required.";
    } else if (!SLUG_RE.test(normalizedSlug)) {
      nextFieldErrors.slug =
        "Slug must be lowercase kebab-case alphanumeric segments separated by hyphens.";
    } else if (normalizedSlug.length > 220) {
      nextFieldErrors.slug = "Slug must be at most 220 characters.";
    }

    if (!categoryId) {
      nextFieldErrors.category_id = "Category is required.";
    }

    if (activationBlocked) {
      nextFieldErrors.status =
        "Add and activate at least 1 variant before setting status to ACTIVE.";
    }

    const builtImages = validateAndBuildImages();
    if (Object.keys(nextFieldErrors).length > 0 || builtImages === null) {
      setFieldErrors(nextFieldErrors);
      return;
    }

    setFieldErrors({});
    setIsSaving(true);

    try {
      if (mode === "create") {
        const payload: CreateProductRequest = {
          category_id: categoryId,
          title: trimmedTitle,
          slug: normalizedSlug,
          description,
          status,
          images: builtImages,
        };
        const created = await adminCatalogApi.createProduct(payload);
        toast({
          title: "Product created",
          description: `"${created.title}" created as ${created.status}. Add variants below.`,
          variant: "success",
        });
        router.push(`/admin/products/${created.id}`);
      } else if (productId) {
        const payload: UpdateProductRequest = {
          category_id: categoryId,
          title: trimmedTitle,
          slug: normalizedSlug,
          description,
          status,
          images: builtImages,
        };
        const updated = await adminCatalogApi.updateProduct(productId, payload);
        hydrateFromProduct(updated);
        toast({
          title: "Product updated",
          description: `Saved changes to "${updated.title}".`,
          variant: "success",
        });
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409 && err.code === "SLUG_CONFLICT") {
          setFieldErrors({ slug: err.message || "A product with this slug already exists." });
          return;
        }
        if (err.code === "PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT") {
          setFieldErrors({
            status: "Add and activate at least 1 variant before setting status to ACTIVE",
          });
          setFormError(err.message);
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
        setFormError("Unable to save product. Please try again.");
      }
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <AdminPageHeader title={mode === "create" ? "New Product" : "Product Editor"} />
        <LoadingState label="Loading product editor..." rows={5} />
      </div>
    );
  }

  if (notFoundError) {
    return (
      <div className="space-y-6">
        <AdminPageHeader
          title="Product Not Found"
          actions={
            <Button variant="outline" asChild>
              <Link href="/admin/products">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                Back to Products
              </Link>
            </Button>
          }
        />
        <EmptyState
          title="Product not found"
          description="This product may not exist or the identifier is invalid."
          action={
            <Button asChild>
              <Link href="/admin/products">Return to Products</Link>
            </Button>
          }
        />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="space-y-6">
        <AdminPageHeader
          title={mode === "create" ? "New Product" : "Product Editor"}
          actions={
            <Button variant="outline" asChild>
              <Link href="/admin/products">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                Back to Products
              </Link>
            </Button>
          }
        />
        <Alert variant="destructive" role="alert">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Unable to load product</AlertTitle>
          <AlertDescription className="mt-2 space-y-3">
            <p>{loadError}</p>
            <Button variant="outline" size="sm" onClick={() => setReloadNonce((n) => n + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title={mode === "create" ? "New Product" : product?.title ?? "Edit Product"}
        description={
          mode === "create"
            ? "Create a new product draft, configure images, and add SKUs after saving."
            : `Manage core details, images, and SKU variants for ${product?.slug ?? "product"}.`
        }
        badge={
          product ? <StatusBadge domain="product" value={product.status} /> : <Badge variant="outline">Draft</Badge>
        }
        actions={
          <Button variant="outline" asChild>
            <Link href="/admin/products">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Back to Products
            </Link>
          </Button>
        }
      />

      {mode === "create" && categories.length === 0 ? (
        <Alert variant="warning">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>No categories available</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center gap-3">
            <span>Create at least one category before creating products.</span>
            <Button variant="outline" size="sm" asChild>
              <Link href="/admin/categories">Go to Categories</Link>
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      <form onSubmit={(e) => void handleSaveProduct(e)} className="space-y-6" noValidate>
        {formError ? (
          <Alert variant="destructive" role="alert">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        {/* Section 1: Core Details */}
        <Card>
          <CardHeader>
            <CardTitle>Core Details</CardTitle>
            <CardDescription>
              Title, URL slug, category placement, description, and publication lifecycle status.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="prod-title">Title</Label>
                <Input
                  id="prod-title"
                  value={title}
                  onChange={(e) => handleTitleChange(e.target.value)}
                  maxLength={200}
                  placeholder="e.g., Merino Wool Crewneck"
                  hasError={Boolean(fieldErrors.title)}
                  disabled={isSaving}
                  required
                />
                {fieldErrors.title ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.title}
                  </p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="prod-slug">Slug</Label>
                <Input
                  id="prod-slug"
                  className="font-mono"
                  value={slug}
                  onChange={(e) => {
                    setSlugManuallyEdited(true);
                    setSlug(e.target.value);
                    setFieldErrors((prev) => {
                      if (!prev.slug) return prev;
                      const next = { ...prev };
                      delete next.slug;
                      return next;
                    });
                  }}
                  maxLength={220}
                  placeholder="e.g., merino-wool-crewneck"
                  hasError={Boolean(fieldErrors.slug)}
                  disabled={isSaving}
                  required
                />
                {fieldErrors.slug ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.slug}
                  </p>
                ) : null}
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="prod-category">Category</Label>
                <Select
                  value={categoryId}
                  onValueChange={(val) => {
                    setCategoryId(val);
                    setFieldErrors((prev) => {
                      if (!prev.category_id) return prev;
                      const next = { ...prev };
                      delete next.category_id;
                      return next;
                    });
                  }}
                  disabled={isSaving || categories.length === 0}
                >
                  <SelectTrigger
                    id="prod-category"
                    aria-invalid={Boolean(fieldErrors.category_id) || undefined}
                  >
                    <SelectValue placeholder="Select a category" />
                  </SelectTrigger>
                  <SelectContent>
                    {categories.map((cat) => (
                      <SelectItem key={cat.id} value={cat.id}>
                        {cat.name}
                        {!cat.is_active ? " (Inactive)" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {fieldErrors.category_id ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.category_id}
                  </p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="prod-status">Status</Label>
                <Select
                  value={status}
                  onValueChange={(val) => {
                    setStatus(val as ProductStatus);
                    setFieldErrors((prev) => {
                      if (!prev.status) return prev;
                      const next = { ...prev };
                      delete next.status;
                      return next;
                    });
                  }}
                  disabled={isSaving}
                >
                  <SelectTrigger
                    id="prod-status"
                    aria-invalid={Boolean(fieldErrors.status) || undefined}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="DRAFT">Draft</SelectItem>
                    <SelectItem value="ACTIVE">Active</SelectItem>
                    <SelectItem value="ARCHIVED">Archived</SelectItem>
                  </SelectContent>
                </Select>
                {fieldErrors.status ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.status}
                  </p>
                ) : null}
              </div>
            </div>

            {activationBlocked ? (
              <Alert variant="warning" role="alert">
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                <AlertTitle>Active Variant Required</AlertTitle>
                <AlertDescription>
                  Add and activate at least 1 variant before setting status to ACTIVE
                </AlertDescription>
              </Alert>
            ) : null}

            <div className="space-y-1.5">
              <Label htmlFor="prod-description">Description</Label>
              <Textarea
                id="prod-description"
                rows={4}
                maxLength={20000}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Material composition, fit notes, and care instructions..."
                disabled={isSaving}
              />
            </div>
          </CardContent>
        </Card>

        {/* Section 2: Images Manager */}
        <Card>
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Product Images</CardTitle>
              <CardDescription>
                Allowed formats: <span className="font-mono">https://...</span> or{" "}
                <span className="font-mono">/assets/...</span>
              </CardDescription>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleAddImage}
              disabled={isSaving || images.length >= 20}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add Image
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {images.length === 0 ? (
              <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                No product images configured. Add an image URL starting with{" "}
                <span className="font-mono">https://</span> or{" "}
                <span className="font-mono">/assets/</span>.
              </p>
            ) : (
              <div className="space-y-3">
                {images.map((img, idx) => {
                  const previewUrl = sanitizeMediaUrl(img.url);
                  const rowError = imageErrors[img.localId];
                  const missingAlt = img.alt_text.trim().length === 0;
                  return (
                    <div
                      key={img.localId}
                      className="grid gap-3 rounded-lg border border-border bg-muted/20 p-3 md:grid-cols-12 md:items-start"
                    >
                      {/* Thumbnail Preview */}
                      <div className="flex items-center gap-3 md:col-span-2">
                        <div className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-muted">
                          {previewUrl ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={previewUrl}
                              alt={img.alt_text || `Image ${idx + 1} preview`}
                              className="h-full w-full object-cover"
                            />
                          ) : (
                            <ImageIcon
                              className="h-5 w-5 text-muted-foreground"
                              aria-hidden="true"
                            />
                          )}
                        </div>
                        <label className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-foreground">
                          <input
                            type="radio"
                            name="primary-product-image"
                            checked={img.is_primary}
                            onChange={() => handlePrimaryChange(img.localId)}
                            disabled={isSaving}
                            className="h-4 w-4 accent-accent"
                          />
                          Primary
                        </label>
                      </div>

                      {/* URL */}
                      <div className="space-y-1 md:col-span-5">
                        <Label htmlFor={`img-url-${img.localId}`} className="text-xs">
                          Image URL
                        </Label>
                        <Input
                          id={`img-url-${img.localId}`}
                          className="font-mono text-xs"
                          value={img.url}
                          onChange={(e) => {
                            const val = e.target.value;
                            setImages((prev) =>
                              prev.map((row) =>
                                row.localId === img.localId ? { ...row, url: val } : row
                              )
                            );
                            setImageErrors((prev) => {
                              if (!prev[img.localId]) return prev;
                              const next = { ...prev };
                              delete next[img.localId];
                              return next;
                            });
                          }}
                          placeholder="https://... or /assets/..."
                          hasError={Boolean(rowError)}
                          disabled={isSaving}
                        />
                        {rowError ? (
                          <p className="text-xs text-destructive" role="alert">
                            {rowError}
                          </p>
                        ) : null}
                      </div>

                      {/* Alt Text */}
                      <div className="space-y-1 md:col-span-3">
                        <Label htmlFor={`img-alt-${img.localId}`} className="text-xs">
                          Alt Text
                        </Label>
                        <Input
                          id={`img-alt-${img.localId}`}
                          value={img.alt_text}
                          maxLength={200}
                          onChange={(e) => {
                            const val = e.target.value;
                            setImages((prev) =>
                              prev.map((row) =>
                                row.localId === img.localId ? { ...row, alt_text: val } : row
                              )
                            );
                          }}
                          placeholder="Front view in charcoal"
                          disabled={isSaving}
                        />
                        {missingAlt ? (
                          <p className="text-[11px] text-muted-foreground">
                            Describe the image for screen-reader accessibility
                          </p>
                        ) : null}
                      </div>

                      {/* Sort Order & Remove */}
                      <div className="flex items-end gap-2 md:col-span-2">
                        <div className="flex-1 space-y-1">
                          <Label htmlFor={`img-order-${img.localId}`} className="text-xs">
                            Order
                          </Label>
                          <Input
                            id={`img-order-${img.localId}`}
                            type="number"
                            step={1}
                            value={img.sort_order}
                            onChange={(e) => {
                              const val = e.target.value;
                              setImages((prev) =>
                                prev.map((row) =>
                                  row.localId === img.localId
                                    ? { ...row, sort_order: val }
                                    : row
                                )
                              );
                            }}
                            disabled={isSaving}
                          />
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="text-muted-foreground hover:text-destructive"
                          onClick={() => handleRemoveImage(img.localId)}
                          aria-label={`Remove image ${idx + 1}`}
                          disabled={isSaving}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-wrap items-center justify-end gap-3">
          <Button variant="outline" asChild>
            <Link href="/admin/products">Cancel</Link>
          </Button>
          <Button
            type="submit"
            variant="accent"
            disabled={isSaving || (mode === "create" && categories.length === 0)}
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            {isSaving
              ? "Saving..."
              : mode === "create"
                ? "Create Product"
                : "Save Product Changes"}
          </Button>
        </div>
      </form>

      {/* Section 3: Variants & SKUs */}
      {mode === "create" ? (
        <Alert variant="info">
          <AlertTitle>Variants & SKUs</AlertTitle>
          <AlertDescription>
            Save the product first to add SKU variants, prices, and initial stock quantities.
          </AlertDescription>
        </Alert>
      ) : product ? (
        <Card>
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Variants & SKUs</CardTitle>
              <CardDescription>
                {product.variants.length} variant(s) ({activeVariantsCount} active). Edit variant
                details or adjust physical stock in Inventory.
              </CardDescription>
            </div>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setEditingVariant(null);
                setVariantModalOpen(true);
              }}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add Variant
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            {product.variants.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  title="No variants added yet"
                  description="Add at least one active variant before activating this product."
                  action={
                    <Button
                      onClick={() => {
                        setEditingVariant(null);
                        setVariantModalOpen(true);
                      }}
                    >
                      <Plus className="h-4 w-4" aria-hidden="true" />
                      Add First Variant
                    </Button>
                  }
                  className="border-none py-6"
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>SKU</TableHead>
                    <TableHead>Title & Attributes</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead>Active</TableHead>
                    <TableHead className="text-right">On Hand</TableHead>
                    <TableHead className="text-right">Reserved</TableHead>
                    <TableHead className="text-right">Available</TableHead>
                    <TableHead>Stock Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {product.variants.map((variant) => {
                    const stockState = computeStockBadgeState(
                      variant.in_stock,
                      variant.low_stock
                    );
                    const attrEntries = Object.entries(variant.attributes);
                    return (
                      <TableRow key={variant.id}>
                        <TableCell className="font-mono text-xs">{variant.sku}</TableCell>
                        <TableCell>
                          <p className="font-medium text-foreground">{variant.title}</p>
                          {attrEntries.length > 0 ? (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {attrEntries.map(([k, v]) => (
                                <Badge key={k} variant="outline" className="text-[11px]">
                                  {k}: {String(v)}
                                </Badge>
                              ))}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          <span className="font-medium">
                            {formatCents(variant.price_cents, variant.currency)}
                          </span>
                          {variant.compare_at_price_cents != null &&
                          variant.compare_at_price_cents > variant.price_cents ? (
                            <span className="ml-1.5 text-xs text-muted-foreground line-through">
                              {formatCents(variant.compare_at_price_cents, variant.currency)}
                            </span>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <StatusBadge domain="active" value={String(variant.is_active)} />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{variant.on_hand}</TableCell>
                        <TableCell className="text-right tabular-nums">{variant.reserved}</TableCell>
                        <TableCell className="text-right font-semibold tabular-nums">
                          {variant.available_quantity}
                        </TableCell>
                        <TableCell>
                          <StatusBadge
                            domain="stock"
                            value={stockState}
                            availableQuantity={variant.available_quantity}
                          />
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setEditingVariant(variant);
                              setVariantModalOpen(true);
                            }}
                          >
                            <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                            Edit
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      ) : null}

      {mode === "edit" && product ? (
        <AdminVariantModal
          open={variantModalOpen}
          onOpenChange={setVariantModalOpen}
          productId={product.id}
          variant={editingVariant}
          onSaved={() => setReloadNonce((n) => n + 1)}
          onSuccessToast={(t, desc) => toast({ title: t, description: desc, variant: "success" })}
        />
      ) : null}
    </div>
  );
}
