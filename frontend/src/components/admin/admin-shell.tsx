"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  ChevronRight,
  ExternalLink,
  FolderTree,
  LayoutDashboard,
  LogOut,
  Menu,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  ShieldAlert,
  ShoppingBag,
  Users,
  Warehouse,
} from "lucide-react";
import { healthApi } from "@/lib/api/client";
import type { HealthDTO } from "@/lib/api/types";
import { sanitizeReturnTo } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { SkipLink } from "@/components/shared/skip-link";
import { StatusBadge } from "@/components/shared/state-feedback";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

interface AdminNavItem {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  adminOnly?: boolean;
}

const OPERATIONS_NAV: AdminNavItem[] = [
  { label: "Dashboard", href: "/admin", icon: LayoutDashboard },
  { label: "Products", href: "/admin/products", icon: Package },
  { label: "Categories", href: "/admin/categories", icon: FolderTree },
  { label: "Inventory", href: "/admin/inventory", icon: Warehouse },
  { label: "Orders", href: "/admin/orders", icon: ShoppingBag },
];

const ADMINISTRATION_NAV: AdminNavItem[] = [
  { label: "Users & Roles", href: "/admin/users", icon: Users, adminOnly: true },
  { label: "Audit Logs", href: "/admin/audit-logs", icon: ScrollText, adminOnly: true },
];

const SEGMENT_LABELS: Record<string, string> = {
  admin: "Admin",
  products: "Products",
  new: "New Product",
  categories: "Categories",
  inventory: "Inventory",
  orders: "Orders",
  users: "Users & Roles",
  "audit-logs": "Audit Logs",
};

function buildBreadcrumbs(pathname: string): { label: string; href: string }[] {
  const parts = pathname.split("/").filter(Boolean);
  const crumbs: { label: string; href: string }[] = [];
  let acc = "";
  for (const part of parts) {
    acc += `/${part}`;
    crumbs.push({
      label: SEGMENT_LABELS[part] ?? part,
      href: acc,
    });
  }
  return crumbs.length > 0 ? crumbs : [{ label: "Admin", href: "/admin" }];
}

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, isLoading: authLoading, sessionExpired, logout } = useAuth();

  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const [health, setHealth] = React.useState<HealthDTO | null>(null);

  const isStaffOrAdmin = user?.role === "staff" || user?.role === "admin";
  const isAdmin = user?.role === "admin";

  React.useEffect(() => {
    if (!authLoading && !user) {
      const safeReturnTo = sanitizeReturnTo(pathname, "/admin");
      const expiredFlag = sessionExpired ? "&expired=1" : "";
      router.replace(`/login?returnTo=${encodeURIComponent(safeReturnTo)}${expiredFlag}`);
    }
  }, [authLoading, user, pathname, sessionExpired, router]);

  React.useEffect(() => {
    if (!isStaffOrAdmin) {
      return;
    }
    const controller = new AbortController();
    healthApi
      .getHealth(controller.signal)
      .then((res) => setHealth(res))
      .catch(() => {});
    return () => controller.abort();
  }, [isStaffOrAdmin]);

  // 1. Auth Loading -> clean skeleton shell (zero admin nav leak!)
  if (authLoading || !user) {
    return (
      <div className="min-h-screen bg-background text-foreground">
        <div className="flex h-14 items-center justify-between border-b border-border bg-surface px-4 lg:px-6">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-8 w-32" />
        </div>
        <div className="mx-auto max-w-[1600px] p-6 space-y-4">
          <Skeleton className="h-8 w-64" />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[1, 2, 3, 4].map((idx) => (
              <Skeleton key={idx} className="h-28 w-full" />
            ))}
          </div>
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  // 2. Customer -> explicit 403 Access Denied screen with zero admin navigation or API calls
  if (!isStaffOrAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12 text-foreground">
        <div className="w-full max-w-lg">
          <Alert variant="destructive" role="alert">
            <ShieldAlert className="h-4 w-4" aria-hidden="true" />
            <AlertTitle>403 Access Denied</AlertTitle>
            <AlertDescription className="mt-2 space-y-4">
              <p>
                Signed in as <span className="font-medium">{user.email}</span> ({user.role}).
                Staff or administrator privileges are required to access the Admin Console.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button asChild>
                  <Link href="/">Return to Storefront</Link>
                </Button>
                <Button variant="outline" asChild>
                  <Link href="/account">My Account</Link>
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        </div>
      </div>
    );
  }

  const breadcrumbs = buildBreadcrumbs(pathname);

  const handleSignOut = async () => {
    setMobileNavOpen(false);
    await logout();
    router.push("/login");
  };

  const renderNavLinks = (collapsed: boolean, onNavigate?: () => void) => (
    <div className="flex flex-col gap-6 px-2 py-4">
      <div className="space-y-1">
        {!collapsed ? (
          <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Operations
          </p>
        ) : null}
        {OPERATIONS_NAV.map((item) => {
          const Icon = item.icon;
          const isActive =
            item.href === "/admin"
              ? pathname === "/admin"
              : pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              title={collapsed ? item.label : undefined}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isActive
                  ? "bg-accent-subtle text-accent"
                  : "text-foreground hover:bg-muted",
                collapsed && "justify-center px-2"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {!collapsed ? <span>{item.label}</span> : <span className="sr-only">{item.label}</span>}
            </Link>
          );
        })}
      </div>

      {/* Strictly Admin-only section — completely hidden from staff */}
      {isAdmin ? (
        <div className="space-y-1 border-t border-border pt-4">
          {!collapsed ? (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Administration
            </p>
          ) : null}
          {ADMINISTRATION_NAV.map((item) => {
            const Icon = item.icon;
            const isActive =
              pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onNavigate}
                title={collapsed ? item.label : undefined}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive
                    ? "bg-accent-subtle text-accent"
                    : "text-foreground hover:bg-muted",
                  collapsed && "justify-center px-2"
                )}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                {!collapsed ? (
                  <span>{item.label}</span>
                ) : (
                  <span className="sr-only">{item.label}</span>
                )}
              </Link>
            );
          })}
        </div>
      ) : null}
    </div>
  );

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <SkipLink />

      {/* Desktop Persistent Collapsible Left Sidebar */}
      <aside
        aria-label="Admin Sidebar"
        className={cn(
          "hidden shrink-0 flex-col border-r border-border bg-surface transition-[width] lg:flex",
          sidebarCollapsed ? "w-16" : "w-[240px]"
        )}
      >
        <div
          className={cn(
            "flex h-14 items-center border-b border-border px-3",
            sidebarCollapsed ? "justify-center" : "justify-between"
          )}
        >
          {!sidebarCollapsed ? (
            <Link href="/admin" className="text-sm font-semibold tracking-tight text-foreground">
              Atelier Admin
            </Link>
          ) : null}
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setSidebarCollapsed((c) => !c)}
            aria-label={sidebarCollapsed ? "Expand admin sidebar" : "Collapse admin sidebar"}
          >
            {sidebarCollapsed ? (
              <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
            ) : (
              <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
            )}
          </Button>
        </div>

        <nav aria-label="Admin navigation" className="flex-1 overflow-y-auto">
          {renderNavLinks(sidebarCollapsed)}
        </nav>
      </aside>

      {/* Main Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 56px Top Utility Bar */}
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-surface px-4 lg:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden"
              aria-label="Open admin navigation"
              onClick={() => setMobileNavOpen(true)}
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </Button>

            <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 text-sm">
              {breadcrumbs.map((crumb, idx) => {
                const isLast = idx === breadcrumbs.length - 1;
                return (
                  <React.Fragment key={crumb.href}>
                    {idx > 0 ? (
                      <ChevronRight
                        className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                    ) : null}
                    {isLast ? (
                      <span
                        aria-current="page"
                        className="truncate font-semibold text-foreground"
                      >
                        {crumb.label}
                      </span>
                    ) : (
                      <Link
                        href={crumb.href}
                        className="truncate text-muted-foreground hover:text-foreground"
                      >
                        {crumb.label}
                      </Link>
                    )}
                  </React.Fragment>
                );
              })}
            </nav>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            {health?.sandbox_payments ? (
              <Badge variant="warning" className="hidden sm:inline-flex">
                Sandbox Mode
              </Badge>
            ) : null}

            <ThemeToggle />

            <div className="hidden items-center gap-2 rounded-md border border-border bg-muted/50 px-2.5 py-1 md:flex">
              <span className="max-w-[130px] truncate text-xs font-medium text-foreground">
                {user.full_name}
              </span>
              <StatusBadge domain="role" value={user.role} />
            </div>

            <Button variant="outline" size="sm" asChild className="hidden sm:inline-flex">
              <Link href="/">
                <span>View Storefront</span>
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            </Button>

            <Button
              variant="ghost"
              size="sm"
              onClick={() => void handleSignOut()}
              aria-label="Sign out"
              className="text-muted-foreground hover:text-destructive"
            >
              <LogOut className="h-4 w-4" aria-hidden="true" />
              <span className="hidden sm:inline">Sign Out</span>
            </Button>
          </div>
        </header>

        <main id="main-content" className="mx-auto w-full max-w-[1600px] flex-1 p-4 sm:p-6">
          {children}
        </main>
      </div>

      {/* Mobile Admin Navigation Drawer */}
      <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <SheetContent side="left" className="p-0">
          <SheetHeader>
            <SheetTitle>Atelier Admin</SheetTitle>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto">
            <div className="border-b border-border px-5 py-3 flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium text-foreground">{user.full_name}</span>
              <StatusBadge domain="role" value={user.role} />
            </div>
            <nav aria-label="Mobile admin navigation">
              {renderNavLinks(false, () => setMobileNavOpen(false))}
            </nav>
            <div className="border-t border-border p-4 space-y-2">
              <Button variant="outline" className="w-full justify-start" asChild>
                <Link href="/" onClick={() => setMobileNavOpen(false)}>
                  <ExternalLink className="h-4 w-4" aria-hidden="true" />
                  View Storefront
                </Link>
              </Button>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
