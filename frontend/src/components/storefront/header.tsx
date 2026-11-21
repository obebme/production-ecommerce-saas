"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  LayoutDashboard,
  LogOut,
  Menu,
  Package,
  Search,
  ShoppingBag,
  User as UserIcon,
  X,
} from "lucide-react";
import { catalogApi } from "@/lib/api/client";
import type { CategoryPublicDTO } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";
import { useCart } from "@/providers/cart-provider";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { VelloraLogo } from "@/components/storefront/vellora-logo";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

interface GroupedCategories {
  topLevel: CategoryPublicDTO[];
  childrenByParent: Map<string, CategoryPublicDTO[]>;
}

function groupCategories(categories: CategoryPublicDTO[]): GroupedCategories {
  const byId = new Map(categories.map((c) => [c.id, c]));
  const topLevel: CategoryPublicDTO[] = [];
  const childrenByParent = new Map<string, CategoryPublicDTO[]>();

  for (const category of categories) {
    if (category.parent_id && byId.has(category.parent_id)) {
      const existing = childrenByParent.get(category.parent_id) ?? [];
      existing.push(category);
      childrenByParent.set(category.parent_id, existing);
    } else {
      topLevel.push(category);
    }
  }
  return { topLevel, childrenByParent };
}

export function StorefrontHeader() {
  const router = useRouter();
  const { user, isLoading: authLoading, logout } = useAuth();
  const { cart, openDrawer } = useCart();

  const [categories, setCategories] = React.useState<CategoryPublicDTO[]>([]);
  const [searchValue, setSearchValue] = React.useState("");
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);
  const [mobileSearchOpen, setMobileSearchOpen] = React.useState(false);
  const mobileSearchInputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    catalogApi
      .listCategories(controller.signal)
      .then((res) => setCategories(res.items))
      .catch(() => {
        // Keep header functional even if categories endpoint is temporarily unavailable
      });
    return () => controller.abort();
  }, []);

  React.useEffect(() => {
    if (mobileSearchOpen) {
      mobileSearchInputRef.current?.focus();
    }
  }, [mobileSearchOpen]);

  const grouped = React.useMemo(() => groupCategories(categories), [categories]);
  const itemCount = cart?.item_count ?? 0;
  const isStaffOrAdmin = user?.role === "staff" || user?.role === "admin";

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = searchValue.trim().slice(0, 100);
    setMobileSearchOpen(false);
    setMobileMenuOpen(false);
    if (trimmed) {
      router.push(`/products?q=${encodeURIComponent(trimmed)}`);
    } else {
      router.push("/products");
    }
  };

  const handleLogout = async () => {
    setMobileMenuOpen(false);
    await logout();
    router.push("/");
  };

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/90 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
        {/* Left: Mobile Menu + Vellora Logo + Primary Desktop Nav */}
        <div className="flex items-center gap-2 lg:gap-8">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            aria-label="Open navigation menu"
            onClick={() => setMobileMenuOpen(true)}
          >
            <Menu className="h-5 w-5" aria-hidden="true" />
          </Button>

          <Link
            href="/"
            className="rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <VelloraLogo />
          </Link>

          <nav aria-label="Primary" className="hidden lg:flex lg:items-center lg:gap-6">
            <Link
              href="/products"
              className="text-sm font-medium text-foreground/90 transition-colors hover:text-accent"
            >
              Catalog
            </Link>

            {categories.length > 0 ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-sm text-sm font-medium text-foreground/90 transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Categories
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  className="storefront-theme max-h-96 w-60 overflow-y-auto"
                >
                  <DropdownMenuItem asChild>
                    <Link href="/products" className="font-medium">
                      All Categories
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  {grouped.topLevel.map((parent) => {
                    const children = grouped.childrenByParent.get(parent.id) ?? [];
                    return (
                      <React.Fragment key={parent.id}>
                        <DropdownMenuItem asChild>
                          <Link
                            href={`/products?category_slug=${encodeURIComponent(parent.slug)}`}
                            className="font-medium"
                          >
                            {parent.name}
                          </Link>
                        </DropdownMenuItem>
                        {children.map((child) => (
                          <DropdownMenuItem key={child.id} asChild inset>
                            <Link
                              href={`/products?category_slug=${encodeURIComponent(child.slug)}`}
                              className="text-muted-foreground"
                            >
                              {child.name}
                            </Link>
                          </DropdownMenuItem>
                        ))}
                      </React.Fragment>
                    );
                  })}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}

            <Link
              href="/products?sort=newest"
              className="text-sm font-medium text-foreground/90 transition-colors hover:text-accent"
            >
              New Arrivals
            </Link>

            <Link
              href="/products?in_stock=true"
              className="text-sm font-medium text-foreground/90 transition-colors hover:text-accent"
            >
              In Stock
            </Link>
          </nav>
        </div>

        {/* Center/Right: Search + Theme Pill + Account + Cart */}
        <div className="flex items-center gap-1.5 sm:gap-2.5">
          <form
            onSubmit={handleSearchSubmit}
            role="search"
            aria-label="Search products"
            className="relative hidden md:block md:w-52 lg:w-64 xl:w-72"
          >
            <Search
              className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              type="search"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              maxLength={100}
              placeholder="Search goods..."
              aria-label="Search products"
              className="h-9 rounded-full border-border bg-surface pl-9 pr-8 text-sm"
            />
            {searchValue ? (
              <button
                type="button"
                onClick={() => setSearchValue("")}
                aria-label="Clear search query"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ) : null}
          </form>

          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label={mobileSearchOpen ? "Close search" : "Open search"}
            aria-expanded={mobileSearchOpen}
            onClick={() => setMobileSearchOpen((prev) => !prev)}
          >
            <Search className="h-5 w-5" aria-hidden="true" />
          </Button>

          <div className="hidden sm:block">
            <ThemeToggle variant="pill" />
          </div>

          {/* Account Controls */}
          {authLoading ? (
            <Skeleton className="hidden h-9 w-24 rounded-full sm:block" />
          ) : user ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="hidden gap-1.5 sm:inline-flex">
                  <UserIcon className="h-4 w-4" aria-hidden="true" />
                  <span className="max-w-[120px] truncate">{user.full_name}</span>
                  <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="storefront-theme w-56">
                <DropdownMenuLabel className="font-normal">
                  <div className="flex flex-col space-y-0.5">
                    <span className="truncate text-sm font-semibold text-foreground">
                      {user.full_name}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">{user.email}</span>
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href="/account">
                    <UserIcon className="h-4 w-4" aria-hidden="true" />
                    My Account
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/orders">
                    <Package className="h-4 w-4" aria-hidden="true" />
                    My Orders
                  </Link>
                </DropdownMenuItem>
                {isStaffOrAdmin ? (
                  <DropdownMenuItem asChild>
                    <Link href="/admin">
                      <LayoutDashboard className="h-4 w-4 text-accent" aria-hidden="true" />
                      Admin Console
                    </Link>
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onSelect={() => void handleLogout()}
                  className="text-destructive focus:text-destructive"
                >
                  <LogOut className="h-4 w-4" aria-hidden="true" />
                  Sign Out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <div className="hidden items-center gap-1.5 sm:flex">
              <Button variant="ghost" size="sm" asChild>
                <Link href="/login">Sign In</Link>
              </Button>
              <Button variant="outline" size="sm" asChild className="rounded-full">
                <Link href="/register">Create Account</Link>
              </Button>
            </div>
          )}

          {/* Cart Drawer Trigger */}
          <Button
            variant="ghost"
            size="default"
            onClick={openDrawer}
            aria-label={`Open cart, ${itemCount} ${itemCount === 1 ? "item" : "items"}`}
            className="relative gap-2 rounded-full px-2.5 sm:px-3"
          >
            <ShoppingBag className="h-5 w-5" aria-hidden="true" />
            <span
              aria-live="polite"
              className="inline-flex min-w-[1.35rem] items-center justify-center rounded-full bg-accent px-1.5 py-0.5 text-xs font-semibold text-accent-foreground tabular-nums"
            >
              {itemCount}
            </span>
          </Button>
        </div>
      </div>

      {/* Mobile Collapsible Search Bar */}
      {mobileSearchOpen ? (
        <div className="border-t border-border bg-surface px-4 py-3 md:hidden">
          <form onSubmit={handleSearchSubmit} role="search" className="flex items-center gap-2">
            <Input
              ref={mobileSearchInputRef}
              type="search"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              maxLength={100}
              placeholder="Search goods..."
              aria-label="Search products"
              className="flex-1 rounded-full"
            />
            <Button type="submit" size="default">
              Search
            </Button>
          </form>
        </div>
      ) : null}

      {/* Mobile Navigation Drawer */}
      <Sheet open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
        <SheetContent side="left" className="storefront-theme p-0">
          <SheetHeader>
            <SheetTitle>
              <VelloraLogo />
            </SheetTitle>
          </SheetHeader>
          <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
            <nav aria-label="Mobile navigation" className="space-y-3">
              <Link
                href="/products"
                onClick={() => setMobileMenuOpen(false)}
                className="block py-1.5 text-base font-medium text-foreground hover:text-accent"
              >
                Catalog
              </Link>
              <Link
                href="/products?sort=newest"
                onClick={() => setMobileMenuOpen(false)}
                className="block py-1.5 text-base font-medium text-foreground hover:text-accent"
              >
                New Arrivals
              </Link>
              <Link
                href="/products?in_stock=true"
                onClick={() => setMobileMenuOpen(false)}
                className="block py-1.5 text-base font-medium text-foreground hover:text-accent"
              >
                In Stock
              </Link>

              {categories.length > 0 ? (
                <div className="space-y-2 border-t border-border pt-4">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Categories
                  </p>
                  <ul className="space-y-1.5">
                    {grouped.topLevel.map((parent) => {
                      const children = grouped.childrenByParent.get(parent.id) ?? [];
                      return (
                        <li key={parent.id} className="space-y-1">
                          <Link
                            href={`/products?category_slug=${encodeURIComponent(parent.slug)}`}
                            onClick={() => setMobileMenuOpen(false)}
                            className="block py-1 text-sm font-medium text-foreground hover:text-accent"
                          >
                            {parent.name}
                          </Link>
                          {children.length > 0 ? (
                            <ul className="space-y-1 pl-3">
                              {children.map((child) => (
                                <li key={child.id}>
                                  <Link
                                    href={`/products?category_slug=${encodeURIComponent(child.slug)}`}
                                    onClick={() => setMobileMenuOpen(false)}
                                    className="block py-1 text-sm text-muted-foreground hover:text-accent"
                                  >
                                    {child.name}
                                  </Link>
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}
            </nav>

            <div className="space-y-2 border-t border-border pt-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Account
              </p>
              {user ? (
                <div className="space-y-1">
                  <p className="text-sm font-semibold text-foreground">{user.full_name}</p>
                  <p className="text-xs text-muted-foreground">{user.email}</p>
                  <div className="space-y-1 pt-2">
                    <Link
                      href="/account"
                      onClick={() => setMobileMenuOpen(false)}
                      className="block py-1.5 text-sm font-medium text-foreground hover:text-accent"
                    >
                      My Account
                    </Link>
                    <Link
                      href="/orders"
                      onClick={() => setMobileMenuOpen(false)}
                      className="block py-1.5 text-sm font-medium text-foreground hover:text-accent"
                    >
                      My Orders
                    </Link>
                    {isStaffOrAdmin ? (
                      <Link
                        href="/admin"
                        onClick={() => setMobileMenuOpen(false)}
                        className="block py-1.5 text-sm font-medium text-accent hover:underline"
                      >
                        Admin Console
                      </Link>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => void handleLogout()}
                      className="block w-full py-1.5 text-left text-sm font-medium text-destructive hover:underline"
                    >
                      Sign Out
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-2 pt-1">
                  <Button asChild onClick={() => setMobileMenuOpen(false)}>
                    <Link href="/login">Sign In</Link>
                  </Button>
                  <Button variant="outline" asChild onClick={() => setMobileMenuOpen(false)}>
                    <Link href="/register">Create Account</Link>
                  </Button>
                </div>
              )}
            </div>

            <div className="border-t border-border pt-4">
              <ThemeToggle showLabel className="w-full justify-start" />
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </header>
  );
}
