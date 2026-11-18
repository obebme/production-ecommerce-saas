import * as React from "react";
import { AlertCircle, PackageOpen, RefreshCw } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { ProductStatus, UserRole } from "@/lib/api/types";

export function LoadingState({
  label = "Loading content...",
  rows = 3,
  className,
}: {
  label?: string;
  rows?: number;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={label}
      className={cn("space-y-3", className)}
    >
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, idx) => (
        <Skeleton key={idx} className="h-12 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
}: {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface px-6 py-12 text-center",
        className
      )}
    >
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
        {icon ?? <PackageOpen className="h-5 w-5" aria-hidden="true" />}
      </div>
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      {description ? (
        <p className="mt-1.5 max-w-md text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-5 flex flex-wrap items-center justify-center gap-3">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  retryLabel = "Try again",
  action,
  className,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <Alert variant="destructive" role="alert" className={cn("my-2", className)}>
      <AlertCircle className="h-4 w-4" aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="mt-1 space-y-3">
        <p>{message}</p>
        {onRetry || action ? (
          <div className="flex flex-wrap items-center gap-2">
            {onRetry ? (
              <Button variant="outline" size="sm" onClick={onRetry}>
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                {retryLabel}
              </Button>
            ) : null}
            {action}
          </div>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

export type StatusBadgeDomain =
  | "product"
  | "stock"
  | "role"
  | "order"
  | "payment"
  | "fulfillment"
  | "active";

const PRODUCT_STATUS_MAP: Record<ProductStatus, { label: string; variant: BadgeProps["variant"] }> =
  {
    DRAFT: { label: "Draft", variant: "outline" },
    ACTIVE: { label: "Active", variant: "success" },
    ARCHIVED: { label: "Archived", variant: "secondary" },
  };

const ROLE_STATUS_MAP: Record<UserRole, { label: string; variant: BadgeProps["variant"] }> = {
  customer: { label: "Customer", variant: "secondary" },
  staff: { label: "Staff", variant: "accentSubtle" },
  admin: { label: "Admin", variant: "accent" },
};

const ORDER_STATUS_MAP: Record<string, { label: string; variant: BadgeProps["variant"] }> = {
  PENDING_PAYMENT: { label: "Pending Payment", variant: "warning" },
  CONFIRMED: { label: "Confirmed", variant: "accentSubtle" },
  PROCESSING: { label: "Processing", variant: "accentSubtle" },
  SHIPPED: { label: "Shipped", variant: "default" },
  COMPLETED: { label: "Completed", variant: "success" },
  CANCELLED: { label: "Cancelled", variant: "secondary" },
  REFUNDED: { label: "Refunded", variant: "destructive" },
};

const PAYMENT_STATUS_MAP: Record<string, { label: string; variant: BadgeProps["variant"] }> = {
  REQUIRES_PAYMENT: { label: "Requires Payment", variant: "warning" },
  PAID: { label: "Paid", variant: "success" },
  FAILED: { label: "Failed", variant: "destructive" },
  REFUNDED: { label: "Refunded", variant: "destructive" },
  CANCELLED: { label: "Cancelled", variant: "secondary" },
};

const FULFILLMENT_STATUS_MAP: Record<string, { label: string; variant: BadgeProps["variant"] }> = {
  UNFULFILLED: { label: "Unfulfilled", variant: "outline" },
  ALLOCATED: { label: "Allocated", variant: "accentSubtle" },
  SHIPPED: { label: "Shipped", variant: "default" },
  DELIVERED: { label: "Delivered", variant: "success" },
  CANCELLED: { label: "Cancelled", variant: "secondary" },
  RETURNED: { label: "Returned", variant: "warning" },
};

export function StatusBadge({
  domain,
  value,
  availableQuantity,
  className,
}: {
  domain: StatusBadgeDomain;
  value: string;
  availableQuantity?: number;
  className?: string;
}) {
  if (domain === "product") {
    const cfg = PRODUCT_STATUS_MAP[value as ProductStatus] ?? {
      label: value,
      variant: "outline" as const,
    };
    return (
      <Badge variant={cfg.variant} className={className}>
        {cfg.label}
      </Badge>
    );
  }

  if (domain === "role") {
    const cfg = ROLE_STATUS_MAP[value as UserRole] ?? {
      label: value,
      variant: "secondary" as const,
    };
    return (
      <Badge variant={cfg.variant} className={className}>
        {cfg.label}
      </Badge>
    );
  }

  if (domain === "stock") {
    if (value === "out_of_stock") {
      return (
        <Badge variant="destructive" className={className}>
          Out of stock
        </Badge>
      );
    }
    if (value === "low_stock") {
      return (
        <Badge variant="warning" className={cn("tabular-nums", className)}>
          {availableQuantity != null
            ? `Low stock — only ${availableQuantity} left`
            : "Low stock"}
        </Badge>
      );
    }
    return (
      <Badge variant="success" className={className}>
        In stock
      </Badge>
    );
  }

  if (domain === "active") {
    return value === "true" ? (
      <Badge variant="success" className={className}>
        Active
      </Badge>
    ) : (
      <Badge variant="secondary" className={className}>
        Inactive
      </Badge>
    );
  }

  const lookup =
    domain === "order"
      ? ORDER_STATUS_MAP
      : domain === "payment"
        ? PAYMENT_STATUS_MAP
        : FULFILLMENT_STATUS_MAP;
  const cfg = lookup[value] ?? { label: value, variant: "outline" as const };
  return (
    <Badge variant={cfg.variant} className={className}>
      {cfg.label}
    </Badge>
  );
}
