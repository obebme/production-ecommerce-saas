"use client";

import * as React from "react";
import Link from "next/link";
import { AlertCircle, ArrowRight, Plus, Trash2 } from "lucide-react";
import { ApiError, adminCatalogApi } from "@/lib/api/client";
import type {
  AdminVariantDTO,
  CreateVariantRequest,
  UpdateVariantRequest,
} from "@/lib/api/types";
import { formatCentsForInput, parseDollarsToCents } from "@/lib/format";
import {
  CURRENCY_RE,
  INT32_MAX,
  extractFieldErrorMap,
} from "@/components/admin/admin-helpers";
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
import { Switch } from "@/components/ui/switch";

interface AttributeRow {
  key: string;
  value: string;
}

function attributesToRows(attrs: Record<string, unknown> | undefined): AttributeRow[] {
  if (!attrs) {
    return [{ key: "color", value: "" }];
  }
  const entries = Object.entries(attrs);
  if (entries.length === 0) {
    return [];
  }
  return entries.map(([key, val]) => ({
    key,
    value: typeof val === "string" ? val : JSON.stringify(val),
  }));
}

export interface AdminVariantModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  productId: string;
  variant: AdminVariantDTO | null;
  onSaved: () => void;
  onSuccessToast: (title: string, description: string) => void;
}

export function AdminVariantModal({
  open,
  onOpenChange,
  productId,
  variant,
  onSaved,
  onSuccessToast,
}: AdminVariantModalProps) {
  const isEdit = variant !== null;

  const [sku, setSku] = React.useState("");
  const [title, setTitle] = React.useState("");
  const [attrRows, setAttrRows] = React.useState<AttributeRow[]>([]);
  const [priceInput, setPriceInput] = React.useState("");
  const [compareAtInput, setCompareAtInput] = React.useState("");
  const [currency, setCurrency] = React.useState("USD");
  const [initialOnHand, setInitialOnHand] = React.useState("0");
  const [lowStockThreshold, setLowStockThreshold] = React.useState("5");
  const [isActive, setIsActive] = React.useState(true);

  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [isSaving, setIsSaving] = React.useState(false);

  React.useEffect(() => {
    if (!open) {
      return;
    }
    setFieldErrors({});
    setFormError(null);
    if (variant) {
      setSku(variant.sku);
      setTitle(variant.title);
      setAttrRows(attributesToRows(variant.attributes));
      setPriceInput(formatCentsForInput(variant.price_cents));
      setCompareAtInput(formatCentsForInput(variant.compare_at_price_cents));
      setCurrency(variant.currency);
      setInitialOnHand(String(variant.on_hand));
      setLowStockThreshold(String(variant.low_stock_threshold));
      setIsActive(variant.is_active);
    } else {
      setSku("");
      setTitle("");
      setAttrRows([
        { key: "color", value: "" },
        { key: "size", value: "" },
      ]);
      setPriceInput("");
      setCompareAtInput("");
      setCurrency("USD");
      setInitialOnHand("0");
      setLowStockThreshold("5");
      setIsActive(true);
    }
  }, [open, variant]);

  const clearFieldError = (field: string) => {
    setFieldErrors((prev) => {
      if (!prev[field]) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const nextErrors: Record<string, string> = {};
    const trimmedSku = sku.trim();
    const trimmedTitle = title.trim();
    const normalizedCurrency = currency.trim().toUpperCase();

    if (!trimmedSku) {
      nextErrors.sku = "SKU is required.";
    } else if (trimmedSku.length > 64) {
      nextErrors.sku = "SKU must be at most 64 characters.";
    }

    if (!trimmedTitle) {
      nextErrors.title = "Variant title is required.";
    } else if (trimmedTitle.length > 160) {
      nextErrors.title = "Variant title must be at most 160 characters.";
    }

    const parsedPriceCents = parseDollarsToCents(priceInput);
    if (parsedPriceCents === null) {
      nextErrors.price_cents = "Enter a valid non-negative price (e.g., 45 or 45.00).";
    }

    let parsedCompareCents: number | null = null;
    if (compareAtInput.trim() !== "") {
      parsedCompareCents = parseDollarsToCents(compareAtInput);
      if (parsedCompareCents === null) {
        nextErrors.compare_at_price_cents = "Enter a valid compare-at price (e.g., 60.00).";
      } else if (parsedPriceCents !== null && parsedCompareCents < parsedPriceCents) {
        nextErrors.compare_at_price_cents =
          "Compare-at price must be greater than or equal to price.";
      }
    }

    if (!CURRENCY_RE.test(normalizedCurrency)) {
      nextErrors.currency = "Currency must be a 3-letter uppercase ISO-4217 code (e.g., USD).";
    }

    const parsedThreshold = Number.parseInt(lowStockThreshold.trim(), 10);
    if (
      !/^\d+$/.test(lowStockThreshold.trim()) ||
      !Number.isSafeInteger(parsedThreshold) ||
      parsedThreshold < 0 ||
      parsedThreshold > INT32_MAX
    ) {
      nextErrors.low_stock_threshold = "Low-stock threshold must be a non-negative integer.";
    }

    let parsedInitialOnHand = 0;
    if (!isEdit) {
      parsedInitialOnHand = Number.parseInt(initialOnHand.trim(), 10);
      if (
        !/^\d+$/.test(initialOnHand.trim()) ||
        !Number.isSafeInteger(parsedInitialOnHand) ||
        parsedInitialOnHand < 0 ||
        parsedInitialOnHand > INT32_MAX
      ) {
        nextErrors.initial_on_hand = "Initial stock must be a non-negative integer.";
      }
    }

    const builtAttributes: Record<string, string> = {};
    for (const row of attrRows) {
      const k = row.key.trim();
      const v = row.value.trim();
      if (!k && !v) {
        continue;
      }
      if (!k) {
        nextErrors.attributes = "Each attribute row with a value must have a non-empty key.";
        break;
      }
      if (k.length > 64) {
        nextErrors.attributes = "Attribute keys must be at most 64 characters.";
        break;
      }
      if (Object.prototype.hasOwnProperty.call(builtAttributes, k)) {
        nextErrors.attributes = `Duplicate attribute key "${k}".`;
        break;
      }
      builtAttributes[k] = v;
    }

    if (Object.keys(nextErrors).length > 0 || parsedPriceCents === null) {
      setFieldErrors(nextErrors);
      return;
    }

    setFieldErrors({});
    setIsSaving(true);

    try {
      if (isEdit && variant) {
        const payload: UpdateVariantRequest = {
          sku: trimmedSku,
          title: trimmedTitle,
          attributes: builtAttributes,
          price_cents: parsedPriceCents,
          compare_at_price_cents: parsedCompareCents,
          currency: normalizedCurrency,
          low_stock_threshold: parsedThreshold,
          is_active: isActive,
        };
        await adminCatalogApi.updateVariant(variant.id, payload);
        onSuccessToast("Variant updated", `Saved changes for SKU ${trimmedSku}.`);
      } else {
        const payload: CreateVariantRequest = {
          sku: trimmedSku,
          title: trimmedTitle,
          attributes: builtAttributes,
          price_cents: parsedPriceCents,
          compare_at_price_cents: parsedCompareCents,
          currency: normalizedCurrency,
          initial_on_hand: parsedInitialOnHand,
          low_stock_threshold: parsedThreshold,
          is_active: isActive,
        };
        await adminCatalogApi.createVariant(productId, payload);
        onSuccessToast(
          "Variant created",
          `Created SKU ${trimmedSku} with initial stock ${parsedInitialOnHand}.`
        );
      }
      onOpenChange(false);
      onSaved();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409 && err.code === "SKU_CONFLICT") {
          setFieldErrors({ sku: err.message || "A variant with this SKU already exists." });
          return;
        }
        if (err.code === "PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT") {
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
        setFormError("Unable to save variant. Please try again.");
      }
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? `Edit Variant: ${variant?.sku}` : "Add Variant"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update SKU, pricing, attributes, and low-stock threshold. Physical stock adjustments are recorded through Inventory."
              : "Define SKU, pricing, option attributes, and initial on-hand stock."}
          </DialogDescription>
        </DialogHeader>

        {formError ? (
          <Alert variant="destructive" role="alert">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        {isEdit && variant ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-muted/40 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-4 tabular-nums">
              <div>
                <span className="text-xs text-muted-foreground">On Hand: </span>
                <span className="font-semibold text-foreground">{variant.on_hand}</span>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Reserved: </span>
                <span className="font-semibold text-foreground">{variant.reserved}</span>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Available: </span>
                <span className="font-semibold text-foreground">
                  {variant.available_quantity}
                </span>
              </div>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link href={`/admin/inventory?sku=${encodeURIComponent(variant.sku)}`}>
                Adjust stock in Inventory
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            </Button>
          </div>
        ) : null}

        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="var-sku">SKU</Label>
              <Input
                id="var-sku"
                className="font-mono"
                value={sku}
                onChange={(e) => {
                  setSku(e.target.value);
                  clearFieldError("sku");
                }}
                maxLength={64}
                placeholder="e.g., MERINO-CHR-M"
                hasError={Boolean(fieldErrors.sku)}
                disabled={isSaving}
                required
              />
              {fieldErrors.sku ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.sku}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="var-title">Variant Title</Label>
              <Input
                id="var-title"
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  clearFieldError("title");
                }}
                maxLength={160}
                placeholder="e.g., Charcoal / Medium"
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
          </div>

          {/* Pricing */}
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="var-price">Price ($)</Label>
              <Input
                id="var-price"
                inputMode="decimal"
                value={priceInput}
                onChange={(e) => {
                  setPriceInput(e.target.value);
                  clearFieldError("price_cents");
                }}
                placeholder="45.00"
                hasError={Boolean(fieldErrors.price_cents)}
                disabled={isSaving}
                required
              />
              {fieldErrors.price_cents ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.price_cents}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="var-compare-price">Compare-at Price ($)</Label>
              <Input
                id="var-compare-price"
                inputMode="decimal"
                value={compareAtInput}
                onChange={(e) => {
                  setCompareAtInput(e.target.value);
                  clearFieldError("compare_at_price_cents");
                }}
                placeholder="Optional (e.g., 60.00)"
                hasError={Boolean(fieldErrors.compare_at_price_cents)}
                disabled={isSaving}
              />
              {fieldErrors.compare_at_price_cents ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.compare_at_price_cents}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="var-currency">Currency (ISO-4217)</Label>
              <Input
                id="var-currency"
                className="font-mono uppercase"
                value={currency}
                onChange={(e) => {
                  setCurrency(e.target.value.toUpperCase());
                  clearFieldError("currency");
                }}
                maxLength={3}
                placeholder="USD"
                hasError={Boolean(fieldErrors.currency)}
                disabled={isSaving}
                required
              />
              {fieldErrors.currency ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.currency}
                </p>
              ) : null}
            </div>
          </div>

          {/* Stock fields */}
          <div className="grid gap-4 sm:grid-cols-2">
            {!isEdit ? (
              <div className="space-y-1.5">
                <Label htmlFor="var-initial-stock">Initial On-Hand Stock</Label>
                <Input
                  id="var-initial-stock"
                  type="number"
                  min={0}
                  step={1}
                  value={initialOnHand}
                  onChange={(e) => {
                    setInitialOnHand(e.target.value);
                    clearFieldError("initial_on_hand");
                  }}
                  hasError={Boolean(fieldErrors.initial_on_hand)}
                  disabled={isSaving}
                />
                {fieldErrors.initial_on_hand ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.initial_on_hand}
                  </p>
                ) : null}
              </div>
            ) : null}

            <div className="space-y-1.5">
              <Label htmlFor="var-threshold">Low-Stock Threshold</Label>
              <Input
                id="var-threshold"
                type="number"
                min={0}
                step={1}
                value={lowStockThreshold}
                onChange={(e) => {
                  setLowStockThreshold(e.target.value);
                  clearFieldError("low_stock_threshold");
                }}
                hasError={Boolean(fieldErrors.low_stock_threshold)}
                disabled={isSaving}
              />
              {fieldErrors.low_stock_threshold ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.low_stock_threshold}
                </p>
              ) : null}
            </div>
          </div>

          {/* Attributes key-value builder */}
          <div className="space-y-2 rounded-md border border-border p-3">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-sm font-medium text-foreground">Option Attributes</p>
                <p className="text-xs text-muted-foreground">
                  Key-value pairs displayed on the storefront option selector (e.g. color, size).
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setAttrRows((prev) => [...prev, { key: "", value: "" }])}
                disabled={isSaving}
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Add Attribute
              </Button>
            </div>

            {attrRows.length === 0 ? (
              <p className="text-xs text-muted-foreground">No attributes added.</p>
            ) : (
              <div className="space-y-2">
                {attrRows.map((row, idx) => (
                  <div key={idx} className="grid grid-cols-12 items-center gap-2">
                    <div className="col-span-5">
                      <Input
                        aria-label={`Attribute key ${idx + 1}`}
                        placeholder="Key (e.g., color)"
                        value={row.key}
                        onChange={(e) => {
                          const val = e.target.value;
                          setAttrRows((prev) =>
                            prev.map((item, i) => (i === idx ? { ...item, key: val } : item))
                          );
                          clearFieldError("attributes");
                        }}
                        maxLength={64}
                        disabled={isSaving}
                      />
                    </div>
                    <div className="col-span-6">
                      <Input
                        aria-label={`Attribute value ${idx + 1}`}
                        placeholder="Value (e.g., Charcoal)"
                        value={row.value}
                        onChange={(e) => {
                          const val = e.target.value;
                          setAttrRows((prev) =>
                            prev.map((item, i) => (i === idx ? { ...item, value: val } : item))
                          );
                          clearFieldError("attributes");
                        }}
                        disabled={isSaving}
                      />
                    </div>
                    <div className="col-span-1 flex justify-end">
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-9 w-9 text-muted-foreground hover:text-destructive"
                        aria-label={`Remove attribute row ${idx + 1}`}
                        onClick={() =>
                          setAttrRows((prev) => prev.filter((_, i) => i !== idx))
                        }
                        disabled={isSaving}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {fieldErrors.attributes ? (
              <p className="text-xs text-destructive" role="alert">
                {fieldErrors.attributes}
              </p>
            ) : null}
          </div>

          <div className="flex items-center justify-between rounded-md border border-border bg-muted/40 px-3 py-2.5">
            <div className="space-y-0.5">
              <Label htmlFor="var-active" className="cursor-pointer">
                Active Variant
              </Label>
              <p className="text-xs text-muted-foreground">
                Active variants are available for storefront purchase when product status is ACTIVE.
              </p>
            </div>
            <Switch
              id="var-active"
              checked={isActive}
              onCheckedChange={setIsActive}
              disabled={isSaving}
            />
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isSaving}
            >
              Cancel
            </Button>
            <Button type="submit" variant="accent" disabled={isSaving}>
              {isSaving ? "Saving..." : isEdit ? "Save Variant" : "Create Variant"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
