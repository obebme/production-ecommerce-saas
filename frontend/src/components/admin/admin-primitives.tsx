"use client";

import * as React from "react";
import Link from "next/link";
import { Info, ShieldAlert } from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { EmptyState, ErrorState } from "@/components/shared/state-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function AdminPageHeader({
  title,
  description,
  badge,
  actions,
}: {
  title: string;
  description?: string;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col justify-between gap-4 border-b border-border pb-4 sm:flex-row sm:items-center">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-2xl font-semibold tracking-[-0.015em] text-foreground">{title}</h1>
          {badge}
        </div>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function KpiCard({
  title,
  value,
  subtitle,
  isLoading = false,
  badge,
  className,
}: {
  title: string;
  value: React.ReactNode;
  subtitle?: string;
  isLoading?: boolean;
  badge?: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("shadow-xs", className)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 p-4 pb-2">
        <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </CardTitle>
        {badge}
      </CardHeader>
      <CardContent className="p-4 pt-0">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-7 w-24" />
            <Skeleton className="h-3.5 w-36" />
          </div>
        ) : (
          <>
            <div className="text-2xl font-semibold text-foreground tabular-nums">{value}</div>
            {subtitle ? <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p> : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function DataTableShell({
  title,
  description,
  headerActions,
  isLoading,
  error,
  onRetry,
  isEmpty,
  emptyTitle = "No records found",
  emptyDescription,
  emptyAction,
  children,
}: {
  title: string;
  description?: string;
  headerActions?: React.ReactNode;
  isLoading: boolean;
  error: string | null;
  onRetry?: () => void;
  isEmpty: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="overflow-hidden shadow-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3.5">
        <div>
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
        </div>
        {headerActions ? <div className="flex items-center gap-2">{headerActions}</div> : null}
      </div>

      {isLoading ? (
        <div className="space-y-2.5 p-4" role="status" aria-label={`Loading ${title}`}>
          {[1, 2, 3, 4].map((row) => (
            <Skeleton key={row} className="h-10 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="p-4">
          <ErrorState title={`Unable to load ${title.toLowerCase()}`} message={error} onRetry={onRetry} />
        </div>
      ) : isEmpty ? (
        <div className="p-4">
          <EmptyState
            title={emptyTitle}
            description={emptyDescription}
            action={emptyAction}
            className="border-none py-8"
          />
        </div>
      ) : (
        <div>{children}</div>
      )}
    </Card>
  );
}

export function AdminRoleGuard({
  requiredRole,
  children,
}: {
  requiredRole: "admin";
  children: React.ReactNode;
}) {
  const { user } = useAuth();

  if (!user || user.role !== requiredRole) {
    return (
      <div className="mx-auto max-w-xl py-10">
        <Alert variant="destructive" role="alert">
          <ShieldAlert className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>403 Restricted to Administrators</AlertTitle>
          <AlertDescription className="mt-2 space-y-4">
            <p>
              This section requires an administrator account. Staff operators do not have access to
              role management or audit logs.
            </p>
            <Button variant="outline" size="sm" asChild>
              <Link href="/admin">Return to Dashboard</Link>
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <>{children}</>;
}

export function AdminStarterNotice({
  moduleTitle,
  milestoneNote,
}: {
  moduleTitle: string;
  milestoneNote: string;
}) {
  return (
    <Alert variant="info">
      <Info className="h-4 w-4" aria-hidden="true" />
      <AlertTitle>{moduleTitle} — Foundation Shell</AlertTitle>
      <AlertDescription>{milestoneNote}</AlertDescription>
    </Alert>
  );
}
