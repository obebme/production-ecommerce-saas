"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertCircle, ChevronDown, ChevronUp, Lock, ShoppingBag } from "lucide-react";
import { ApiError, checkoutApi, extractValidationErrors } from "@/lib/api/client";
import type { AddressDTO, CartDTO, CreateCheckoutOrderRequest } from "@/lib/api/types";
import { formatCents } from "@/lib/format";
import { saveGuestOrderToken } from "@/lib/guest-order-memory";
import { generateUuidV4 } from "@/lib/uuid";
import { useAuth } from "@/providers/auth-provider";
import { useCart } from "@/providers/cart-provider";
import { ProductImage } from "@/components/shared/product-image";
import { EmptyState, ErrorState } from "@/components/shared/state-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const BASELINE_TAX_RATE_BPS = 800;

const ISO_COUNTRY_OPTIONS = [
  { code: "US", label: "United States (US)" },
  { code: "CA", label: "Canada (CA)" },
  { code: "GB", label: "United Kingdom (GB)" },
  { code: "AU", label: "Australia (AU)" },
  { code: "DE", label: "Germany (DE)" },
  { code: "FR", label: "France (FR)" },
  { code: "IT", label: "Italy (IT)" },
  { code: "ES", label: "Spain (ES)" },
  { code: "NL", label: "Netherlands (NL)" },
  { code: "SE", label: "Sweden (SE)" },
  { code: "JP", label: "Japan (JP)" },
  { code: "SG", label: "Singapore (SG)" },
] as const;

interface AddressFormState {
  full_name: string;
  line1: string;
  line2: string;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  phone: string;
}

const EMPTY_ADDRESS: AddressFormState = {
  full_name: "",
  line1: "",
  line2: "",
  city: "",
  state: "",
  postal_code: "",
  country: "US",
  phone: "",
};

function computePreviewTaxCents(
  subtotalCents: number,
  taxRateBps: number = BASELINE_TAX_RATE_BPS
): number {
  if (subtotalCents <= 0 || taxRateBps <= 0) {
    return 0;
  }
  return Math.floor((subtotalCents * taxRateBps + 5000) / 10000);
}

function resolveCartPreviewTotals(cart: CartDTO): {
  taxRateBps: number;
  previewTaxCents: number;
  previewTotalCents: number;
} {
  const taxRateBps = cart.tax_rate_bps ?? BASELINE_TAX_RATE_BPS;
  const previewTaxCents =
    cart.estimated_tax_cents ?? computePreviewTaxCents(cart.subtotal_cents, taxRateBps);
  const previewTotalCents = cart.subtotal_cents + cart.estimated_shipping_cents + previewTaxCents;
  return { taxRateBps, previewTaxCents, previewTotalCents };
}

function normalizeAddressDto(form: AddressFormState): AddressDTO {
  const trimmedLine2 = form.line2.trim();
  return {
    full_name: form.full_name.trim(),
    line1: form.line1.trim(),
    line2: trimmedLine2 ? trimmedLine2 : null,
    city: form.city.trim(),
    state: form.state.trim(),
    postal_code: form.postal_code.trim(),
    country: form.country.trim().toUpperCase(),
    phone: form.phone.trim(),
  };
}

function validateAddressForm(
  prefix: "shipping" | "billing",
  form: AddressFormState
): Record<string, string> {
  const errors: Record<string, string> = {};
  const fullName = form.full_name.trim();
  const line1 = form.line1.trim();
  const line2 = form.line2.trim();
  const city = form.city.trim();
  const state = form.state.trim();
  const postalCode = form.postal_code.trim();
  const country = form.country.trim().toUpperCase();
  const phone = form.phone.trim();

  if (fullName.length < 1 || fullName.length > 120) {
    errors[`${prefix}.full_name`] = "Full name is required (1–120 characters).";
  }
  if (line1.length < 1 || line1.length > 200) {
    errors[`${prefix}.line1`] = "Address line 1 is required.";
  }
  if (line2.length > 200) {
    errors[`${prefix}.line2`] = "Address line 2 cannot exceed 200 characters.";
  }
  if (city.length < 1 || city.length > 100) {
    errors[`${prefix}.city`] = "City is required.";
  }
  if (state.length < 1 || state.length > 100) {
    errors[`${prefix}.state`] = "State or province is required.";
  }
  if (postalCode.length < 1 || postalCode.length > 32) {
    errors[`${prefix}.postal_code`] = "Postal code is required.";
  }
  if (!/^[A-Z]{2}$/.test(country)) {
    errors[`${prefix}.country`] = "Select a valid 2-letter ISO country code.";
  }
  if (phone.length < 3 || phone.length > 32) {
    errors[`${prefix}.phone`] = "Phone number is required (3–32 characters).";
  }
  return errors;
}

function computeCheckoutClientFingerprint(
  cart: CartDTO,
  payload: CreateCheckoutOrderRequest
): string {
  const sortedItems = [...cart.items]
    .map((item) => [item.variant_id, item.quantity] as const)
    .sort((a, b) => a[0].localeCompare(b[0]));

  return JSON.stringify({
    cart_id: cart.id,
    items: sortedItems,
    customer_email: payload.customer_email,
    shipping_address: payload.shipping_address,
    billing_same_as_shipping: payload.billing_same_as_shipping,
    billing_address: payload.billing_same_as_shipping ? null : payload.billing_address ?? null,
  });
}

function mapServerValidationLoc(loc: string[]): string | null {
  const parts = loc[0] === "body" ? loc.slice(1) : loc;
  if (parts.length === 0) {
    return null;
  }
  if (parts[0] === "customer_email") {
    return "customer_email";
  }
  if (parts[0] === "shipping_address" && parts[1]) {
    return `shipping.${parts[1]}`;
  }
  if (parts[0] === "billing_address" && parts[1]) {
    return `billing.${parts[1]}`;
  }
  return parts.join(".");
}

function AddressFields({
  prefix,
  legend,
  value,
  onChange,
  fieldErrors,
  disabled,
  fieldRefs,
}: {
  prefix: "shipping" | "billing";
  legend: string;
  value: AddressFormState;
  onChange: (field: keyof AddressFormState, next: string) => void;
  fieldErrors: Record<string, string>;
  disabled: boolean;
  fieldRefs: React.MutableRefObject<Record<string, HTMLElement | null>>;
}) {
  const autoCompletePrefix = prefix === "shipping" ? "shipping" : "billing";

  const registerRef = (key: string) => (el: HTMLElement | null) => {
    fieldRefs.current[`${prefix}.${key}`] = el;
  };

  const getError = (key: keyof AddressFormState) => fieldErrors[`${prefix}.${key}`];

  return (
    <fieldset className="space-y-4">
      <legend className="text-base font-semibold text-foreground">{legend}</legend>

      <div className="space-y-1.5">
        <Label htmlFor={`${prefix}-full_name`}>Full name</Label>
        <Input
          ref={registerRef("full_name")}
          id={`${prefix}-full_name`}
          type="text"
          required
          maxLength={120}
          autoComplete={`${autoCompletePrefix} name`}
          value={value.full_name}
          onChange={(e) => onChange("full_name", e.target.value)}
          hasError={Boolean(getError("full_name"))}
          aria-describedby={getError("full_name") ? `${prefix}-full_name-error` : undefined}
          disabled={disabled}
        />
        {getError("full_name") ? (
          <p id={`${prefix}-full_name-error`} className="text-xs text-destructive">
            {getError("full_name")}
          </p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={`${prefix}-line1`}>Address line 1</Label>
        <Input
          ref={registerRef("line1")}
          id={`${prefix}-line1`}
          type="text"
          required
          maxLength={200}
          autoComplete={`${autoCompletePrefix} address-line1`}
          value={value.line1}
          onChange={(e) => onChange("line1", e.target.value)}
          hasError={Boolean(getError("line1"))}
          aria-describedby={getError("line1") ? `${prefix}-line1-error` : undefined}
          disabled={disabled}
        />
        {getError("line1") ? (
          <p id={`${prefix}-line1-error`} className="text-xs text-destructive">
            {getError("line1")}
          </p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={`${prefix}-line2`}>Apartment, suite, etc. (optional)</Label>
        <Input
          ref={registerRef("line2")}
          id={`${prefix}-line2`}
          type="text"
          maxLength={200}
          autoComplete={`${autoCompletePrefix} address-line2`}
          value={value.line2}
          onChange={(e) => onChange("line2", e.target.value)}
          hasError={Boolean(getError("line2"))}
          aria-describedby={getError("line2") ? `${prefix}-line2-error` : undefined}
          disabled={disabled}
        />
        {getError("line2") ? (
          <p id={`${prefix}-line2-error`} className="text-xs text-destructive">
            {getError("line2")}
          </p>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-city`}>City</Label>
          <Input
            ref={registerRef("city")}
            id={`${prefix}-city`}
            type="text"
            required
            maxLength={100}
            autoComplete={`${autoCompletePrefix} address-level2`}
            value={value.city}
            onChange={(e) => onChange("city", e.target.value)}
            hasError={Boolean(getError("city"))}
            aria-describedby={getError("city") ? `${prefix}-city-error` : undefined}
            disabled={disabled}
          />
          {getError("city") ? (
            <p id={`${prefix}-city-error`} className="text-xs text-destructive">
              {getError("city")}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-state`}>State / Province</Label>
          <Input
            ref={registerRef("state")}
            id={`${prefix}-state`}
            type="text"
            required
            maxLength={100}
            autoComplete={`${autoCompletePrefix} address-level1`}
            value={value.state}
            onChange={(e) => onChange("state", e.target.value)}
            hasError={Boolean(getError("state"))}
            aria-describedby={getError("state") ? `${prefix}-state-error` : undefined}
            disabled={disabled}
          />
          {getError("state") ? (
            <p id={`${prefix}-state-error`} className="text-xs text-destructive">
              {getError("state")}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-postal_code`}>Postal code</Label>
          <Input
            ref={registerRef("postal_code")}
            id={`${prefix}-postal_code`}
            type="text"
            required
            maxLength={32}
            autoComplete={`${autoCompletePrefix} postal-code`}
            value={value.postal_code}
            onChange={(e) => onChange("postal_code", e.target.value)}
            hasError={Boolean(getError("postal_code"))}
            aria-describedby={getError("postal_code") ? `${prefix}-postal_code-error` : undefined}
            disabled={disabled}
          />
          {getError("postal_code") ? (
            <p id={`${prefix}-postal_code-error`} className="text-xs text-destructive">
              {getError("postal_code")}
            </p>
          ) : null}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-country`}>Country (ISO-2)</Label>
          <Select
            value={value.country}
            onValueChange={(next) => onChange("country", next)}
            disabled={disabled}
          >
            <SelectTrigger
              ref={registerRef("country") as React.Ref<HTMLButtonElement>}
              id={`${prefix}-country`}
              aria-invalid={getError("country") ? true : undefined}
              aria-describedby={getError("country") ? `${prefix}-country-error` : undefined}
            >
              <SelectValue placeholder="Select country" />
            </SelectTrigger>
            <SelectContent>
              {ISO_COUNTRY_OPTIONS.map((opt) => (
                <SelectItem key={opt.code} value={opt.code}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {getError("country") ? (
            <p id={`${prefix}-country-error`} className="text-xs text-destructive">
              {getError("country")}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-phone`}>Phone</Label>
          <Input
            ref={registerRef("phone")}
            id={`${prefix}-phone`}
            type="tel"
            required
            minLength={3}
            maxLength={32}
            autoComplete={`${autoCompletePrefix} tel`}
            value={value.phone}
            onChange={(e) => onChange("phone", e.target.value)}
            hasError={Boolean(getError("phone"))}
            aria-describedby={getError("phone") ? `${prefix}-phone-error` : undefined}
            disabled={disabled}
            placeholder="+14155550101"
          />
          {getError("phone") ? (
            <p id={`${prefix}-phone-error`} className="text-xs text-destructive">
              {getError("phone")}
            </p>
          ) : null}
        </div>
      </div>
    </fieldset>
  );
}

function CheckoutOrderSummaryCard({ cart }: { cart: CartDTO }) {
  const currency = cart.currency || "USD";
  const { taxRateBps, previewTaxCents, previewTotalCents } = resolveCartPreviewTotals(cart);

  return (
    <Card className="shadow-sm">
      <CardHeader className="border-b border-border pb-4">
        <CardTitle className="flex items-center justify-between text-base">
          <span>Order Summary</span>
          <span className="text-xs font-normal text-muted-foreground tabular-nums">
            {cart.item_count} {cart.item_count === 1 ? "item" : "items"}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 pt-5">
        <ul className="max-h-72 space-y-3 overflow-y-auto pr-1">
          {cart.items.map((item) => (
            <li key={item.id} className="flex items-center gap-3 text-sm">
              <div className="aspect-[4/5] w-12 shrink-0 overflow-hidden rounded-sm border border-border bg-muted">
                <ProductImage
                  src={item.image_url}
                  alt={item.product_title}
                  fallbackLabel={item.product_title}
                  sizes="48px"
                />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-foreground">{item.product_title}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {item.variant_title} · Qty{" "}
                  <span className="tabular-nums">{item.quantity}</span>
                </p>
              </div>
              <p className="shrink-0 font-medium text-foreground tabular-nums">
                {formatCents(item.line_total_cents, currency)}
              </p>
            </li>
          ))}
        </ul>

        <div className="space-y-2 border-t border-border pt-4 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Subtotal</span>
            <span className="font-medium tabular-nums">
              {formatCents(cart.subtotal_cents, currency)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Estimated Shipping</span>
            <span className="font-medium tabular-nums">
              {cart.estimated_shipping_cents === 0
                ? `${formatCents(0, currency)} (Free)`
                : formatCents(cart.estimated_shipping_cents, currency)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">
              Estimated Tax ({(taxRateBps / 100).toFixed(2)}% preview)
            </span>
            <span className="font-medium tabular-nums">
              {formatCents(previewTaxCents, currency)}
            </span>
          </div>
          <div className="flex justify-between border-t border-border pt-3 text-base font-semibold">
            <span>Estimated Total</span>
            <span className="tabular-nums">{formatCents(previewTotalCents, currency)}</span>
          </div>
        </div>

        <p className="rounded-md bg-muted/60 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          Final totals and stock availability are verified by the server when you place your order.
        </p>
      </CardContent>
    </Card>
  );
}

function CheckoutSkeleton() {
  return (
    <div className="grid gap-8 lg:grid-cols-12" role="status" aria-label="Loading checkout">
      <span className="sr-only">Loading checkout...</span>
      <div className="space-y-6 lg:col-span-7">
        <Skeleton className="h-32 w-full rounded-lg" />
        <Skeleton className="h-96 w-full rounded-lg" />
      </div>
      <div className="lg:col-span-5">
        <Skeleton className="h-80 w-full rounded-lg" />
      </div>
    </div>
  );
}

export function CheckoutView() {
  const router = useRouter();
  const { user, isLoading: authLoading, sessionExpired, accountDisabled } = useAuth();
  const { cart, isLoading: cartLoading, error: cartError, refreshCart } = useCart();

  const [customerEmail, setCustomerEmail] = React.useState("");
  const emailTouchedRef = React.useRef(false);
  const [shippingAddress, setShippingAddress] = React.useState<AddressFormState>(EMPTY_ADDRESS);
  const [billingSameAsShipping, setBillingSameAsShipping] = React.useState(true);
  const [billingAddress, setBillingAddress] = React.useState<AddressFormState>(EMPTY_ADDRESS);
  const [mobileSummaryOpen, setMobileSummaryOpen] = React.useState(false);

  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const submittingRef = React.useRef(false);
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [submitAlert, setSubmitAlert] = React.useState<{
    message: string;
    actionHref?: string;
    actionLabel?: string;
  } | null>(null);

  const idempotencyRef = React.useRef<{ fingerprint: string; key: string } | null>(null);
  const fieldRefs = React.useRef<Record<string, HTMLElement | null>>({});
  const alertRef = React.useRef<HTMLDivElement>(null);

  // Reconcile fresh cart stock/prices when navigating to /checkout with an already-hydrated cart.
  const mountedWithCartRef = React.useRef(Boolean(cart));
  React.useEffect(() => {
    if (
      mountedWithCartRef.current &&
      !authLoading &&
      !sessionExpired &&
      !accountDisabled
    ) {
      mountedWithCartRef.current = false;
      void refreshCart();
    }
  }, [authLoading, sessionExpired, accountDisabled, refreshCart]);

  // Pre-fill contact email when signed in unless user already typed a custom email.
  React.useEffect(() => {
    if (user?.email && !emailTouchedRef.current) {
      setCustomerEmail(user.email);
    }
  }, [user]);

  const handleShippingChange = (field: keyof AddressFormState, next: string) => {
    setShippingAddress((prev) => ({ ...prev, [field]: next }));
    setFieldErrors((prev) => {
      const key = `shipping.${field}`;
      if (!prev[key]) return prev;
      const copy = { ...prev };
      delete copy[key];
      return copy;
    });
  };

  const handleBillingChange = (field: keyof AddressFormState, next: string) => {
    setBillingAddress((prev) => ({ ...prev, [field]: next }));
    setFieldErrors((prev) => {
      const key = `billing.${field}`;
      if (!prev[key]) return prev;
      const copy = { ...prev };
      delete copy[key];
      return copy;
    });
  };

  const focusFirstError = (errors: Record<string, string>) => {
    const firstKey = Object.keys(errors)[0];
    if (firstKey && fieldRefs.current[firstKey]) {
      fieldRefs.current[firstKey]?.focus();
      return;
    }
    alertRef.current?.focus();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submittingRef.current || !cart || cart.items.length === 0) {
      return;
    }

    setSubmitAlert(null);
    const trimmedEmail = customerEmail.trim().toLowerCase();
    const nextErrors: Record<string, string> = {};

    if (!trimmedEmail || !trimmedEmail.includes("@") || trimmedEmail.length > 320) {
      nextErrors.customer_email = "Enter a valid email address.";
    }
    Object.assign(nextErrors, validateAddressForm("shipping", shippingAddress));
    if (!billingSameAsShipping) {
      Object.assign(nextErrors, validateAddressForm("billing", billingAddress));
    }

    if (Object.keys(nextErrors).length > 0) {
      setFieldErrors(nextErrors);
      setSubmitAlert({
        message: "Please review the highlighted contact and address fields before placing your order.",
      });
      requestAnimationFrame(() => focusFirstError(nextErrors));
      return;
    }

    setFieldErrors({});

    const payload: CreateCheckoutOrderRequest = {
      customer_email: trimmedEmail,
      shipping_address: normalizeAddressDto(shippingAddress),
      billing_same_as_shipping: billingSameAsShipping,
      billing_address: billingSameAsShipping ? null : normalizeAddressDto(billingAddress),
    };

    const fingerprint = computeCheckoutClientFingerprint(cart, payload);
    if (!idempotencyRef.current || idempotencyRef.current.fingerprint !== fingerprint) {
      idempotencyRef.current = {
        fingerprint,
        key: generateUuidV4(),
      };
    }

    submittingRef.current = true;
    setIsSubmitting(true);
    try {
      const res = await checkoutApi.createOrder(payload, idempotencyRef.current.key);
      if (res.guest_access_token) {
        saveGuestOrderToken(res.order.order_number, res.guest_access_token);
      }
      await refreshCart();
      router.push(`/orders/${encodeURIComponent(res.order.order_number)}`);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422) {
          const parsed = extractValidationErrors(err);
          const mapped: Record<string, string> = {};
          for (const item of parsed) {
            const key = mapServerValidationLoc(item.loc);
            if (key) {
              mapped[key] = item.msg;
            }
          }
          if (Object.keys(mapped).length > 0) {
            setFieldErrors(mapped);
            setSubmitAlert({ message: "Please correct the highlighted fields." });
            requestAnimationFrame(() => focusFirstError(mapped));
          } else {
            setSubmitAlert({ message: err.message });
          }
          return;
        }

        if (err.status === 409 && err.code === "IDEMPOTENCY_KEY_CONFLICT") {
          idempotencyRef.current = {
            fingerprint,
            key: generateUuidV4(),
          };
          setSubmitAlert({
            message:
              "Checkout details changed since last attempt. Please review and submit again.",
          });
          return;
        }

        if (err.status === 409 && err.code === "INSUFFICIENT_STOCK") {
          const available = err.details.available;
          const requested = err.details.requested;
          const detailSuffix =
            typeof available === "number" && typeof requested === "number"
              ? ` (requested ${requested}, available ${available})`
              : "";
          setSubmitAlert({
            message: `${err.message}${detailSuffix}`,
            actionHref: "/cart",
            actionLabel: "Update Cart",
          });
          void refreshCart();
          return;
        }

        if (err.status === 409 && err.code === "VARIANT_UNAVAILABLE") {
          setSubmitAlert({
            message: err.message,
            actionHref: "/cart",
            actionLabel: "Update Cart",
          });
          void refreshCart();
          return;
        }

        if (err.status === 409 && err.code === "CART_EMPTY") {
          setSubmitAlert({
            message: "Your cart is empty.",
            actionHref: "/products",
            actionLabel: "Browse Catalog",
          });
          void refreshCart();
          return;
        }

        if (err.status === 429 && err.code === "RATE_LIMIT_EXCEEDED") {
          setSubmitAlert({
            message:
              "Too many checkout attempts or active unpaid reservations (max 3 active pending orders per guest session). Please complete or cancel an existing pending order.",
          });
          return;
        }

        if (err.status === 401 && err.code === "AUTH_SESSION_EXPIRED") {
          setSubmitAlert({
            message: "Your session has expired. Please sign in again to place your order.",
            actionHref: "/login?expired=1&returnTo=%2Fcheckout",
            actionLabel: "Sign In",
          });
          return;
        }

        setSubmitAlert({ message: err.message });
      } else {
        setSubmitAlert({
          message:
            "Unable to place your order due to a connection error. You can safely retry with the same details.",
        });
      }
      requestAnimationFrame(() => alertRef.current?.focus());
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  const hasUnavailableItems = Boolean(cart?.items.some((item) => !item.is_available));

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-10 sm:px-6 lg:px-8">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="font-display text-3xl font-medium tracking-[-0.02em] text-foreground">
            Checkout
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Reserve stock for 15 minutes and complete sandbox payment on the next step.
          </p>
        </div>

        <Link
          href="/cart"
          className="text-sm font-medium text-accent underline-offset-4 hover:underline"
        >
          Return to Bag
        </Link>
      </div>

      {sessionExpired ? (
        <Alert variant="warning" role="alert" className="mb-6">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Session expired</AlertTitle>
          <AlertDescription className="mt-1 space-y-3">
            <p>Your session has expired. Please sign in again to continue checkout.</p>
            <Button size="sm" asChild>
              <Link href="/login?expired=1&returnTo=%2Fcheckout">Sign In to Continue</Link>
            </Button>
          </AlertDescription>
        </Alert>
      ) : accountDisabled ? (
        <Alert variant="destructive" role="alert" className="mb-6">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Account disabled</AlertTitle>
          <AlertDescription>
            Your account has been deactivated. Please contact an administrator.
          </AlertDescription>
        </Alert>
      ) : (authLoading || cartLoading) && !cart ? (
        <CheckoutSkeleton />
      ) : cartError && !cart ? (
        <ErrorState
          title="Unable to verify your shopping bag"
          message={cartError}
          onRetry={() => void refreshCart()}
        />
      ) : !cart || cart.item_count === 0 || cart.items.length === 0 ? (
        <EmptyState
          title="Your cart is empty"
          description="Add items from the catalog before proceeding to checkout."
          icon={<ShoppingBag className="h-5 w-5" aria-hidden="true" />}
          action={
            <Button size="lg" asChild>
              <Link href="/products">Browse Catalog</Link>
            </Button>
          }
        />
      ) : (
        <div className="space-y-6">
          {/* Mobile Collapsible Order Summary */}
          <div className="lg:hidden">
            <div className="rounded-lg border border-border bg-surface">
              <button
                type="button"
                aria-expanded={mobileSummaryOpen}
                onClick={() => setMobileSummaryOpen((prev) => !prev)}
                className="flex w-full items-center justify-between px-4 py-3.5 text-left text-sm font-semibold text-foreground"
              >
                <span className="flex items-center gap-2">
                  <span>Order Summary ({cart.item_count})</span>
                  {mobileSummaryOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  )}
                </span>
                <span className="tabular-nums">
                  {formatCents(resolveCartPreviewTotals(cart).previewTotalCents, cart.currency)}
                </span>
              </button>
              {mobileSummaryOpen ? (
                <div className="border-t border-border p-4">
                  <CheckoutOrderSummaryCard cart={cart} />
                </div>
              ) : null}
            </div>
          </div>

          <div className="grid gap-8 lg:grid-cols-12">
            <div className="space-y-6 lg:col-span-7">
              {hasUnavailableItems ? (
                <Alert variant="destructive" role="alert">
                  <AlertCircle className="h-4 w-4" aria-hidden="true" />
                  <AlertTitle>Stock or availability changed</AlertTitle>
                  <AlertDescription className="mt-1 space-y-3">
                    <p>
                      One or more items in your bag are out of stock, deactivated, or exceed
                      current stock. Please update your bag before placing your order.
                    </p>
                    <Button size="sm" variant="outline" asChild>
                      <Link href="/cart">Update Cart</Link>
                    </Button>
                  </AlertDescription>
                </Alert>
              ) : null}

              {submitAlert ? (
                <div ref={alertRef} tabIndex={-1} className="focus:outline-none">
                  <Alert variant="destructive" role="alert">
                    <AlertCircle className="h-4 w-4" aria-hidden="true" />
                    <AlertDescription className="space-y-3">
                      <p>{submitAlert.message}</p>
                      {submitAlert.actionHref && submitAlert.actionLabel ? (
                        <Button size="sm" variant="outline" asChild>
                          <Link href={submitAlert.actionHref}>{submitAlert.actionLabel}</Link>
                        </Button>
                      ) : null}
                    </AlertDescription>
                  </Alert>
                </div>
              ) : null}

              <form onSubmit={(e) => void handleSubmit(e)} className="space-y-6" noValidate>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">1. Contact Information</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="checkout-email">Email address</Label>
                      <Input
                        ref={(el) => {
                          fieldRefs.current.customer_email = el;
                        }}
                        id="checkout-email"
                        type="email"
                        required
                        maxLength={320}
                        autoComplete="email"
                        value={customerEmail}
                        onChange={(e) => {
                          emailTouchedRef.current = true;
                          setCustomerEmail(e.target.value);
                          setFieldErrors((prev) => {
                            if (!prev.customer_email) return prev;
                            const copy = { ...prev };
                            delete copy.customer_email;
                            return copy;
                          });
                        }}
                        placeholder="you@example.com"
                        hasError={Boolean(fieldErrors.customer_email)}
                        aria-describedby={
                          fieldErrors.customer_email ? "checkout-email-error" : "checkout-email-hint"
                        }
                        disabled={isSubmitting}
                      />
                      {!user ? (
                        <p id="checkout-email-hint" className="text-xs text-muted-foreground">
                          We&apos;ll send your order reference and guest receipt access here.
                        </p>
                      ) : null}
                      {fieldErrors.customer_email ? (
                        <p id="checkout-email-error" className="text-xs text-destructive">
                          {fieldErrors.customer_email}
                        </p>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">2. Shipping Address</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <AddressFields
                      prefix="shipping"
                      legend="Shipping recipient and destination"
                      value={shippingAddress}
                      onChange={handleShippingChange}
                      fieldErrors={fieldErrors}
                      disabled={isSubmitting}
                      fieldRefs={fieldRefs}
                    />
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">3. Billing Address</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-5">
                    <div className="flex items-center gap-3">
                      <Checkbox
                        id="billing-same-as-shipping"
                        checked={billingSameAsShipping}
                        onCheckedChange={(checked) => {
                          setBillingSameAsShipping(checked === true);
                        }}
                        disabled={isSubmitting}
                      />
                      <Label
                        htmlFor="billing-same-as-shipping"
                        className="cursor-pointer text-sm font-medium"
                      >
                        Billing address same as shipping
                      </Label>
                    </div>

                    {!billingSameAsShipping ? (
                      <div className="border-t border-border pt-5">
                        <AddressFields
                          prefix="billing"
                          legend="Billing recipient and address"
                          value={billingAddress}
                          onChange={handleBillingChange}
                          fieldErrors={fieldErrors}
                          disabled={isSubmitting}
                          fieldRefs={fieldRefs}
                        />
                      </div>
                    ) : null}
                  </CardContent>
                </Card>

                <div className="space-y-3">
                  <Button
                    type="submit"
                    size="lg"
                    className="w-full"
                    disabled={isSubmitting || hasUnavailableItems}
                  >
                    <Lock className="h-4 w-4" aria-hidden="true" />
                    {isSubmitting ? "Reserving stock..." : "Place Order & Reserve Stock"}
                  </Button>
                  <p className="text-center text-xs text-muted-foreground">
                    Placing your order reserves inventory for 15 minutes while you complete sandbox
                    payment on the next screen.
                  </p>
                </div>
              </form>
            </div>

            <aside aria-label="Order summary" className="hidden lg:block lg:col-span-5">
              <div className="lg:sticky lg:top-24">
                <CheckoutOrderSummaryCard cart={cart} />
              </div>
            </aside>
          </div>
        </div>
      )}
    </div>
  );
}
