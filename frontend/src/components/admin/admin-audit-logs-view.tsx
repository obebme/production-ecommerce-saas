"use client";

import * as React from "react";
import { Eye, Search, X } from "lucide-react";
import { adminAuditApi } from "@/lib/api/client";
import type { AuditLogEntryDTO, PaginatedResponse } from "@/lib/api/types";
import { formatUtcTimestamp } from "@/lib/format";
import {
  UUID_RE,
  resolveErrorMessage,
  toUtcRangeEndIso,
  toUtcRangeStartIso,
} from "@/components/admin/admin-helpers";
import {
  AdminPageHeader,
  AdminRoleGuard,
  DataTableShell,
} from "@/components/admin/admin-primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

const RESOURCE_TYPES = [
  "user",
  "category",
  "product",
  "product_variant",
  "inventory_stock",
  "order",
];

function computeChangedKeys(
  beforeState: Record<string, unknown> | null,
  afterState: Record<string, unknown> | null
): Set<string> {
  const changed = new Set<string>();
  const keys = new Set<string>([
    ...Object.keys(beforeState ?? {}),
    ...Object.keys(afterState ?? {}),
  ]);
  for (const key of keys) {
    const beforeVal = beforeState ? beforeState[key] : undefined;
    const afterVal = afterState ? afterState[key] : undefined;
    if (JSON.stringify(beforeVal) !== JSON.stringify(afterVal)) {
      changed.add(key);
    }
  }
  return changed;
}

function StateDiffBlock({
  title,
  state,
  changedKeys,
  tone,
}: {
  title: string;
  state: Record<string, unknown> | null;
  changedKeys: Set<string>;
  tone: "before" | "after";
}) {
  if (state === null) {
    return (
      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
        <div className="flex min-h-[120px] items-center justify-center rounded-md border border-border bg-muted/30 p-4 font-mono text-sm text-muted-foreground">
          —
        </div>
      </div>
    );
  }

  const entries = Object.entries(state);

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <div className="overflow-x-auto rounded-md border border-border bg-muted/20 p-3 font-mono text-xs">
        {entries.length === 0 ? (
          <span className="text-muted-foreground">{"{}"}</span>
        ) : (
          <div className="space-y-1">
            {entries.map(([key, value]) => {
              const isChanged = changedKeys.has(key);
              const serializedValue = JSON.stringify(value, null, 2);
              return (
                <div
                  key={key}
                  className={cn(
                    "rounded px-2 py-1",
                    isChanged &&
                      tone === "before" &&
                      "bg-destructive-subtle text-destructive",
                    isChanged &&
                      tone === "after" &&
                      "bg-success-subtle text-success"
                  )}
                >
                  <span className="font-semibold">{key}:</span>{" "}
                  <span className="whitespace-pre-wrap break-all">{serializedValue}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function AdminAuditLogsContent() {
  const [resourceType, setResourceType] = React.useState<string>("ALL");
  const [actionInput, setActionInput] = React.useState("");
  const [actorIdInput, setActorIdInput] = React.useState("");
  const [fromDateInput, setFromDateInput] = React.useState("");
  const [toDateInput, setToDateInput] = React.useState("");

  const [appliedAction, setAppliedAction] = React.useState<string | undefined>(undefined);
  const [appliedActorId, setAppliedActorId] = React.useState<string | undefined>(undefined);
  const [appliedFromTs, setAppliedFromTs] = React.useState<string | undefined>(undefined);
  const [appliedToTs, setAppliedToTs] = React.useState<string | undefined>(undefined);
  const [filterError, setFilterError] = React.useState<string | null>(null);
  const [page, setPage] = React.useState(1);

  const [data, setData] = React.useState<PaginatedResponse<AuditLogEntryDTO> | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  const [inspectedEntry, setInspectedEntry] = React.useState<AuditLogEntryDTO | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    adminAuditApi
      .listAuditLogs(
        {
          resource_type: resourceType === "ALL" ? undefined : resourceType,
          action: appliedAction,
          actor_user_id: appliedActorId,
          from_ts: appliedFromTs,
          to_ts: appliedToTs,
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
        setError(resolveErrorMessage(err, "Unable to load audit logs."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [resourceType, appliedAction, appliedActorId, appliedFromTs, appliedToTs, page, reloadNonce]);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedActor = actorIdInput.trim();
    if (trimmedActor && !UUID_RE.test(trimmedActor)) {
      setFilterError("Actor User ID must be a valid UUID.");
      return;
    }
    if (fromDateInput && toDateInput && fromDateInput > toDateInput) {
      setFilterError("Start date must be before or equal to end date.");
      return;
    }
    setFilterError(null);
    setAppliedAction(actionInput.trim().slice(0, 64) || undefined);
    setAppliedActorId(trimmedActor || undefined);
    setAppliedFromTs(toUtcRangeStartIso(fromDateInput));
    setAppliedToTs(toUtcRangeEndIso(toDateInput));
    setPage(1);
  };

  const handleClearFilters = () => {
    setResourceType("ALL");
    setActionInput("");
    setActorIdInput("");
    setFromDateInput("");
    setToDateInput("");
    setAppliedAction(undefined);
    setAppliedActorId(undefined);
    setAppliedFromTs(undefined);
    setAppliedToTs(undefined);
    setFilterError(null);
    setPage(1);
  };

  const hasActiveFilters = Boolean(
    resourceType !== "ALL" || appliedAction || appliedActorId || appliedFromTs || appliedToTs
  );
  const items = data?.items ?? [];

  const inspectedChangedKeys = React.useMemo(
    () =>
      inspectedEntry
        ? computeChangedKeys(inspectedEntry.before_state, inspectedEntry.after_state)
        : new Set<string>(),
    [inspectedEntry]
  );

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Audit Logs"
        description="Immutable security and operational audit trail with before/after JSON diffs."
        badge={<Badge variant="accent">Admin Only</Badge>}
      />

      {/* Filter Bar */}
      <form
        onSubmit={handleApplyFilters}
        className="space-y-3 rounded-lg border border-border bg-surface p-4"
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-12">
          <div className="space-y-1.5 lg:col-span-3">
            <Label htmlFor="audit-resource">Resource Type</Label>
            <Select
              value={resourceType}
              onValueChange={(val) => {
                setResourceType(val);
                setPage(1);
              }}
            >
              <SelectTrigger id="audit-resource">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All Resources</SelectItem>
                {RESOURCE_TYPES.map((rt) => (
                  <SelectItem key={rt} value={rt}>
                    {rt}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5 lg:col-span-3">
            <Label htmlFor="audit-action">Action</Label>
            <Input
              id="audit-action"
              className="font-mono"
              maxLength={64}
              value={actionInput}
              onChange={(e) => setActionInput(e.target.value)}
              placeholder="e.g., ORDER_REFUNDED"
            />
          </div>

          <div className="space-y-1.5 lg:col-span-3">
            <Label htmlFor="audit-actor">Actor User ID (UUID)</Label>
            <Input
              id="audit-actor"
              className="font-mono"
              value={actorIdInput}
              onChange={(e) => setActorIdInput(e.target.value)}
              placeholder="Optional UUID"
            />
          </div>

          <div className="grid grid-cols-2 gap-2 lg:col-span-3">
            <div className="space-y-1.5">
              <Label htmlFor="audit-from">From (UTC)</Label>
              <Input
                id="audit-from"
                type="date"
                value={fromDateInput}
                onChange={(e) => setFromDateInput(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="audit-to">To (UTC)</Label>
              <Input
                id="audit-to"
                type="date"
                value={toDateInput}
                onChange={(e) => setToDateInput(e.target.value)}
              />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
          <Button type="submit" variant="outline">
            <Search className="h-4 w-4" aria-hidden="true" />
            Apply Filters
          </Button>
          {hasActiveFilters ? (
            <Button type="button" variant="ghost" onClick={handleClearFilters}>
              <X className="h-4 w-4" aria-hidden="true" />
              Clear Filters
            </Button>
          ) : null}
          {filterError ? (
            <p className="text-xs text-destructive" role="alert">
              {filterError}
            </p>
          ) : null}
        </div>
      </form>

      <DataTableShell
        title="Audit Events"
        description={data ? `${data.total} recorded event(s)` : undefined}
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle="No matching audit events"
        emptyDescription="Clear filters to inspect all recorded administrative and security events."
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
              <TableHead>Timestamp (UTC)</TableHead>
              <TableHead>Actor</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Resource</TableHead>
              <TableHead>IP Address</TableHead>
              <TableHead className="text-right">Inspect</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((entry) => (
              <TableRow key={entry.id}>
                <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                  {formatUtcTimestamp(entry.created_at)}
                </TableCell>
                <TableCell>
                  {entry.actor_email || entry.actor_full_name ? (
                    <div>
                      <p className="text-sm font-medium text-foreground">
                        {entry.actor_full_name || entry.actor_email}
                      </p>
                      {entry.actor_email ? (
                        <p className="font-mono text-xs text-muted-foreground">
                          {entry.actor_email}
                        </p>
                      ) : null}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {entry.actor_user_id ?? "System"}
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className="font-mono text-xs">
                    {entry.action}
                  </Badge>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs font-medium text-foreground">
                    {entry.resource_type}
                  </span>
                  <p className="max-w-[220px] truncate font-mono text-[11px] text-muted-foreground">
                    {entry.resource_id}
                  </p>
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  {entry.ip_address ?? "—"}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setInspectedEntry(entry)}
                    aria-label={`Inspect audit event ${entry.action} on ${entry.resource_id}`}
                  >
                    <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                    Inspect
                  </Button>
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
              itemLabel="audit events"
            />
          </div>
        ) : null}
      </DataTableShell>

      {/* Before / After JSON Diff Drawer */}
      <Sheet
        open={Boolean(inspectedEntry)}
        onOpenChange={(open) => {
          if (!open) {
            setInspectedEntry(null);
          }
        }}
      >
        <SheetContent side="right" className="max-w-full sm:max-w-3xl">
          <SheetHeader>
            <div className="flex flex-wrap items-center gap-2 pr-8">
              <SheetTitle className="font-mono text-base">
                {inspectedEntry?.action ?? "Audit Diff"}
              </SheetTitle>
              {inspectedEntry ? (
                <Badge variant="outline" className="font-mono text-xs">
                  {inspectedEntry.resource_type}
                </Badge>
              ) : null}
            </div>
            <SheetDescription>
              {inspectedEntry
                ? `Resource ID: ${inspectedEntry.resource_id} · ${formatUtcTimestamp(
                    inspectedEntry.created_at
                  )}`
                : ""}
            </SheetDescription>
          </SheetHeader>

          {inspectedEntry ? (
            <div className="flex-1 space-y-5 overflow-y-auto p-6">
              <div className="grid gap-3 rounded-md border border-border bg-muted/30 p-3 text-xs sm:grid-cols-2">
                <div>
                  <span className="font-semibold text-muted-foreground">Actor: </span>
                  <span className="text-foreground">
                    {inspectedEntry.actor_full_name ||
                      inspectedEntry.actor_email ||
                      inspectedEntry.actor_user_id ||
                      "System"}
                  </span>
                </div>
                <div>
                  <span className="font-semibold text-muted-foreground">IP Address: </span>
                  <span className="font-mono text-foreground">
                    {inspectedEntry.ip_address ?? "—"}
                  </span>
                </div>
                <div className="sm:col-span-2">
                  <span className="font-semibold text-muted-foreground">Resource: </span>
                  <span className="font-mono text-foreground">
                    {inspectedEntry.resource_type} / {inspectedEntry.resource_id}
                  </span>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <StateDiffBlock
                  title="Before State"
                  state={inspectedEntry.before_state}
                  changedKeys={inspectedChangedKeys}
                  tone="before"
                />
                <StateDiffBlock
                  title="After State"
                  state={inspectedEntry.after_state}
                  changedKeys={inspectedChangedKeys}
                  tone="after"
                />
              </div>
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

export function AdminAuditLogsView() {
  return (
    <AdminRoleGuard requiredRole="admin">
      <AdminAuditLogsContent />
    </AdminRoleGuard>
  );
}
