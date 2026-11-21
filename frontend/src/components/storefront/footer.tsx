"use client";

import * as React from "react";
import Link from "next/link";
import { catalogApi, healthApi } from "@/lib/api/client";
import type { CategoryPublicDTO, HealthDTO } from "@/lib/api/types";
import { VelloraLogo } from "@/components/storefront/vellora-logo";
import { Badge } from "@/components/ui/badge";

export function StorefrontFooter() {
  const [categories, setCategories] = React.useState<CategoryPublicDTO[]>([]);
  const [health, setHealth] = React.useState<HealthDTO | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    catalogApi
      .listCategories(controller.signal)
      .then((res) => setCategories(res.items.slice(0, 4)))
      .catch(() => {});

    healthApi
      .getHealth(controller.signal)
      .then((res) => setHealth(res))
      .catch(() => {});

    return () => controller.abort();
  }, []);

  return (
    <footer className="border-t border-border bg-surface text-foreground dark:bg-[#14171B]">
      <div className="mx-auto grid max-w-[1440px] grid-cols-1 gap-6 px-4 py-8 sm:px-6 md:grid-cols-4 lg:gap-8 lg:px-8 lg:py-10">
        <div className="space-y-2.5">
          <Link
            href="/"
            className="inline-flex rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <VelloraLogo />
          </Link>
          <p className="max-w-xs text-xs leading-relaxed text-muted-foreground">
            Considered everyday objects, natural textiles, and home essentials backed by live
            studio inventory.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-6 md:col-span-2 lg:gap-8">
          <div className="space-y-2.5">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Shop
            </h2>
            <ul className="space-y-1.5 text-sm">
              <li>
                <Link href="/products" className="text-foreground transition-colors hover:text-accent">
                  All Products
                </Link>
              </li>
              {categories.map((category) => (
                <li key={category.id}>
                  <Link
                    href={`/products?category_slug=${encodeURIComponent(category.slug)}`}
                    className="text-muted-foreground transition-colors hover:text-accent"
                  >
                    {category.name}
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          <div className="space-y-2.5">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Customer
            </h2>
            <ul className="space-y-1.5 text-sm">
              <li>
                <Link href="/account" className="text-foreground transition-colors hover:text-accent">
                  My Account
                </Link>
              </li>
              <li>
                <Link href="/orders" className="text-foreground transition-colors hover:text-accent">
                  My Orders
                </Link>
              </li>
              <li>
                <Link href="/cart" className="text-muted-foreground transition-colors hover:text-accent">
                  Shopping Bag
                </Link>
              </li>
              <li>
                <Link href="/login" className="text-muted-foreground transition-colors hover:text-accent">
                  Sign In
                </Link>
              </li>
              <li>
                <Link
                  href="/register"
                  className="text-muted-foreground transition-colors hover:text-accent"
                >
                  Create Account
                </Link>
              </li>
              <li className="pt-0.5 text-xs text-muted-foreground">
                Complimentary standard shipping on orders over $100.00.
              </li>
            </ul>
          </div>
        </div>

        <div className="space-y-2.5">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Environment Notice
          </h2>
          {health?.sandbox_payments ? (
            <div className="space-y-2">
              <Badge variant="warning">Sandbox Simulation Mode</Badge>
              <p className="text-xs leading-relaxed text-muted-foreground">
                This environment operates in Sandbox Simulation Mode. No real payment cards are
                collected, stored, or charged.
              </p>
            </div>
          ) : health ? (
            <p className="text-xs leading-relaxed text-muted-foreground">
              Storefront API connected ({health.status}). Server-authoritative inventory and pricing
              enforced on every request.
            </p>
          ) : (
            <p className="text-xs leading-relaxed text-muted-foreground">
              Vellora Store Modular Commerce Platform. Server-authoritative pricing and inventory
              validation.
            </p>
          )}
        </div>
      </div>
      <div className="border-t border-border px-4 py-3.5 text-center text-xs text-muted-foreground sm:px-6 lg:px-8">
        © {new Date().getFullYear()} Vellora Store. All prices in USD.
      </div>
    </footer>
  );
}
