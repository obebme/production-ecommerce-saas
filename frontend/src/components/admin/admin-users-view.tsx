"use client";

import * as React from "react";
import { AlertCircle, Search, ShieldAlert, X } from "lucide-react";
import { ApiError, adminUsersApi } from "@/lib/api/client";
import type { AdminUserDTO, PaginatedResponse, UserRole } from "@/lib/api/types";
import { formatUtcTimestamp } from "@/lib/format";
import { useAuth } from "@/providers/auth-provider";
import { resolveErrorMessage } from "@/components/admin/admin-helpers";
import {
  AdminPageHeader,
  AdminRoleGuard,
  DataTableShell,
} from "@/components/admin/admin-primitives";
import { StatusBadge } from "@/components/shared/state-feedback";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
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
import { Switch } from "@/components/ui/switch";
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

type PendingUserMutation =
  | {
      kind: "role";
      target: AdminUserDTO;
      nextRole: UserRole;
    }
  | {
      kind: "status";
      target: AdminUserDTO;
      nextIsActive: boolean;
    };

function AdminUsersContent() {
  const { user: currentUser } = useAuth();
  const { toast } = useToast();

  const [searchInput, setSearchInput] = React.useState("");
  const [appliedQ, setAppliedQ] = React.useState<string | undefined>(undefined);
  const [roleFilter, setRoleFilter] = React.useState<"ALL" | UserRole>("ALL");
  const [activeFilter, setActiveFilter] = React.useState<"ALL" | "true" | "false">("ALL");
  const [page, setPage] = React.useState(1);

  const [data, setData] = React.useState<PaginatedResponse<AdminUserDTO> | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = React.useState(0);

  // Mandatory session-revocation confirmation dialog
  const [pendingMutation, setPendingMutation] = React.useState<PendingUserMutation | null>(null);
  const [mutationError, setMutationError] = React.useState<string | null>(null);
  const [isMutating, setIsMutating] = React.useState(false);

  React.useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    adminUsersApi
      .listUsers(
        {
          q: appliedQ,
          role: roleFilter === "ALL" ? undefined : roleFilter,
          is_active:
            activeFilter === "ALL" ? undefined : activeFilter === "true",
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
        setError(resolveErrorMessage(err, "Unable to load users."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [appliedQ, roleFilter, activeFilter, page, reloadNonce]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setAppliedQ(searchInput.trim().slice(0, 100) || undefined);
    setPage(1);
  };

  const handleClearFilters = () => {
    setSearchInput("");
    setAppliedQ(undefined);
    setRoleFilter("ALL");
    setActiveFilter("ALL");
    setPage(1);
  };

  const handleConfirmMutation = async () => {
    if (!pendingMutation || isMutating) {
      return;
    }
    setMutationError(null);
    setIsMutating(true);

    try {
      const res =
        pendingMutation.kind === "role"
          ? await adminUsersApi.updateRole(pendingMutation.target.id, {
              role: pendingMutation.nextRole,
            })
          : await adminUsersApi.updateStatus(pendingMutation.target.id, {
              is_active: pendingMutation.nextIsActive,
            });

      setData((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((row) => (row.id === res.user.id ? res.user : row)),
            }
          : prev
      );
      setPendingMutation(null);
      toast({
        title: "Session revocation complete",
        description: `${res.user.email} updated (${res.revoked_sessions_count} active session(s) revoked).`,
        variant: "success",
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "CANNOT_DEMOTE_SELF") {
          setMutationError("You cannot demote your own administrator account.");
          return;
        }
        if (err.code === "CANNOT_DEACTIVATE_SELF") {
          setMutationError("You cannot deactivate your own administrator account.");
          return;
        }
        if (err.code === "CANNOT_REMOVE_LAST_ADMIN") {
          setMutationError("Cannot remove or deactivate the last active administrator.");
          return;
        }
        setMutationError(err.message);
      } else {
        setMutationError("Unable to update user. Please try again.");
      }
    } finally {
      setIsMutating(false);
    }
  };

  const hasActiveFilters = Boolean(
    appliedQ || roleFilter !== "ALL" || activeFilter !== "ALL"
  );
  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Users & Roles"
        description="Manage account roles, activation status, and immediate session revocation."
        badge={<Badge variant="accent">Admin Only</Badge>}
      />

      {/* Filter Bar */}
      <form
        onSubmit={handleSearchSubmit}
        className="grid gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-2 lg:grid-cols-12 lg:items-end"
      >
        <div className="space-y-1.5 lg:col-span-5">
          <Label htmlFor="users-search">Search email or full name</Label>
          <div className="flex gap-2">
            <Input
              id="users-search"
              type="search"
              maxLength={100}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="e.g., staff@atelier.test..."
            />
            <Button type="submit" variant="outline">
              <Search className="h-4 w-4" aria-hidden="true" />
              Search
            </Button>
          </div>
        </div>

        <div className="space-y-1.5 lg:col-span-3">
          <Label htmlFor="users-role-filter">Role</Label>
          <Select
            value={roleFilter}
            onValueChange={(val) => {
              setRoleFilter(val as "ALL" | UserRole);
              setPage(1);
            }}
          >
            <SelectTrigger id="users-role-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">All Roles</SelectItem>
              <SelectItem value="customer">Customer</SelectItem>
              <SelectItem value="staff">Staff</SelectItem>
              <SelectItem value="admin">Admin</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5 lg:col-span-3">
          <Label htmlFor="users-active-filter">Account Status</Label>
          <Select
            value={activeFilter}
            onValueChange={(val) => {
              setActiveFilter(val as "ALL" | "true" | "false");
              setPage(1);
            }}
          >
            <SelectTrigger id="users-active-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">All Statuses</SelectItem>
              <SelectItem value="true">Active</SelectItem>
              <SelectItem value="false">Disabled</SelectItem>
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
        title="User Accounts"
        description={data ? `${data.total} matching user(s)` : undefined}
        isLoading={isLoading}
        error={error}
        onRetry={() => setReloadNonce((n) => n + 1)}
        isEmpty={items.length === 0}
        emptyTitle="No matching users"
        emptyDescription="Clear filters to view all registered user accounts."
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
              <TableHead>User</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Account Active</TableHead>
              <TableHead>Created (UTC)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((row) => {
              const isSelf = currentUser?.id === row.id;
              return (
                <TableRow key={row.id}>
                  <TableCell>
                    <div className="space-y-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-foreground">{row.full_name}</span>
                        {isSelf ? <Badge variant="outline">You</Badge> : null}
                      </div>
                      <p className="font-mono text-xs text-muted-foreground">{row.email}</p>
                      {isSelf ? (
                        <p className="text-[11px] text-muted-foreground">
                          You cannot demote or deactivate your own account
                        </p>
                      ) : null}
                    </div>
                  </TableCell>

                  <TableCell>
                    <div className="max-w-[160px]">
                      <Select
                        value={row.role}
                        onValueChange={(nextVal) => {
                          const nextRole = nextVal as UserRole;
                          if (nextRole === row.role || isSelf) {
                            return;
                          }
                          setMutationError(null);
                          setPendingMutation({
                            kind: "role",
                            target: row,
                            nextRole,
                          });
                        }}
                        disabled={isSelf}
                      >
                        <SelectTrigger
                          aria-label={`Role for ${row.email}`}
                          title={
                            isSelf
                              ? "You cannot demote or deactivate your own account"
                              : undefined
                          }
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="customer">customer</SelectItem>
                          <SelectItem value="staff">staff</SelectItem>
                          <SelectItem value="admin">admin</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </TableCell>

                  <TableCell>
                    <div className="flex items-center gap-3">
                      <Switch
                        checked={row.is_active}
                        disabled={isSelf}
                        aria-label={`Active status for ${row.email}`}
                        title={
                          isSelf
                            ? "You cannot demote or deactivate your own account"
                            : undefined
                        }
                        onCheckedChange={(checked) => {
                          if (isSelf || checked === row.is_active) {
                            return;
                          }
                          setMutationError(null);
                          setPendingMutation({
                            kind: "status",
                            target: row,
                            nextIsActive: checked,
                          });
                        }}
                      />
                      <StatusBadge domain="active" value={String(row.is_active)} />
                    </div>
                  </TableCell>

                  <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                    {formatUtcTimestamp(row.created_at)}
                  </TableCell>
                </TableRow>
              );
            })}
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
              itemLabel="users"
            />
          </div>
        ) : null}
      </DataTableShell>

      {/* Session Revocation Confirmation Dialog */}
      <Dialog
        open={Boolean(pendingMutation)}
        onOpenChange={(open) => {
          if (!open && !isMutating) {
            setPendingMutation(null);
            setMutationError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pendingMutation?.kind === "role"
                ? "Confirm User Role Change"
                : "Confirm Account Status Change"}
            </DialogTitle>
            <DialogDescription>
              {pendingMutation ? (
                <span>
                  Target account:{" "}
                  <strong className="text-foreground">{pendingMutation.target.email}</strong>
                </span>
              ) : null}
            </DialogDescription>
          </DialogHeader>

          {pendingMutation ? (
            <div className="space-y-3">
              <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
                {pendingMutation.kind === "role" ? (
                  <p>
                    Change role from{" "}
                    <span className="font-mono font-semibold">{pendingMutation.target.role}</span>{" "}
                    to <span className="font-mono font-semibold">{pendingMutation.nextRole}</span>.
                  </p>
                ) : (
                  <p>
                    {pendingMutation.nextIsActive ? "Activate" : "Deactivate"} account (currently{" "}
                    <span className="font-mono font-semibold">
                      {pendingMutation.target.is_active ? "Active" : "Inactive"}
                    </span>
                    ).
                  </p>
                )}
              </div>

              <Alert variant="warning">
                <ShieldAlert className="h-4 w-4" aria-hidden="true" />
                <AlertDescription>
                  Updating this user&apos;s role or status immediately revokes all their active
                  sessions across devices. Continue?
                </AlertDescription>
              </Alert>

              {mutationError ? (
                <Alert variant="destructive" role="alert">
                  <AlertCircle className="h-4 w-4" aria-hidden="true" />
                  <AlertDescription>{mutationError}</AlertDescription>
                </Alert>
              ) : null}
            </div>
          ) : null}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setPendingMutation(null);
                setMutationError(null);
              }}
              disabled={isMutating}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleConfirmMutation()}
              disabled={isMutating}
            >
              {isMutating ? "Updating..." : "Confirm & Revoke Sessions"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function AdminUsersView() {
  return (
    <AdminRoleGuard requiredRole="admin">
      <AdminUsersContent />
    </AdminRoleGuard>
  );
}
