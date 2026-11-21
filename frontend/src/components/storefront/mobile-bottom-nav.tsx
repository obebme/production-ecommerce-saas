"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, LayoutGrid, ShoppingBag, User as UserIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { useCart } from "@/providers/cart-provider";

const HIDDEN_ROUTES = ["/checkout", "/cart", "/login", "/register"] as const;

function isBottomNavHiddenRoute(pathname: string | null): boolean {
  if (!pathname) {
    return false;
  }
  if (/^\/products\/[^/]+/.test(pathname)) {
    return true;
  }
  return HIDDEN_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );
}

export function MobileBottomNav() {
  const pathname = usePathname();
  const { user } = useAuth();
  const { cart } = useCart();
  const [isKeyboardOpen, setIsKeyboardOpen] = React.useState(false);

  React.useEffect(() => {
    if (typeof window === "undefined" || !window.visualViewport) {
      return;
    }

    const viewport = window.visualViewport;
    const updateKeyboardState = () => {
      setIsKeyboardOpen(viewport.height < window.innerHeight * 0.78);
    };

    updateKeyboardState();
    viewport.addEventListener("resize", updateKeyboardState);
    viewport.addEventListener("scroll", updateKeyboardState);

    return () => {
      viewport.removeEventListener("resize", updateKeyboardState);
      viewport.removeEventListener("scroll", updateKeyboardState);
    };
  }, []);

  if (isBottomNavHiddenRoute(pathname) || isKeyboardOpen) {
    return null;
  }

  const itemCount = cart?.item_count ?? 0;
  const accountHref = user ? "/account" : "/login";

  const items = [
    {
      label: "Home",
      href: "/",
      icon: Home,
      isActive: pathname === "/",
    },
    {
      label: "Catalog",
      href: "/products",
      icon: LayoutGrid,
      isActive: pathname.startsWith("/products"),
    },
    {
      label: "Cart",
      href: "/cart",
      icon: ShoppingBag,
      isActive: pathname.startsWith("/cart") || pathname.startsWith("/checkout"),
      badge: itemCount,
    },
    {
      label: "Account",
      href: accountHref,
      icon: UserIcon,
      isActive:
        pathname.startsWith("/account") ||
        pathname.startsWith("/orders") ||
        pathname.startsWith("/login") ||
        pathname.startsWith("/register"),
    },
  ];

  return (
    <>
      <div
        aria-hidden="true"
        className="h-[calc(4rem+env(safe-area-inset-bottom))] shrink-0 lg:hidden"
      />
      <nav
        aria-label="Mobile bottom navigation"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md lg:hidden"
      >
        <ul className="mx-auto grid max-w-md grid-cols-4 px-2 py-1">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.label}>
                <Link
                  href={item.href}
                  aria-current={item.isActive ? "page" : undefined}
                  className={cn(
                    "relative flex min-h-[44px] flex-col items-center justify-center gap-0.5 rounded-lg px-2 py-1.5 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    item.isActive
                      ? "text-accent"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  <span className="relative inline-flex">
                    <Icon className="h-5 w-5" aria-hidden="true" />
                    {typeof item.badge === "number" && item.badge > 0 ? (
                      <span className="absolute -right-2.5 -top-1.5 inline-flex min-w-[1.1rem] items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold leading-tight text-accent-foreground tabular-nums">
                        {item.badge}
                      </span>
                    ) : null}
                  </span>
                  <span>{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
