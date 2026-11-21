import { SkipLink } from "@/components/shared/skip-link";
import { CartDrawer } from "@/components/storefront/cart-drawer";
import { StorefrontFooter } from "@/components/storefront/footer";
import { StorefrontHeader } from "@/components/storefront/header";
import { MobileBottomNav } from "@/components/storefront/mobile-bottom-nav";
import { CartProvider } from "@/providers/cart-provider";

export default function StorefrontLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <CartProvider>
      <div className="storefront-theme flex min-h-screen flex-col bg-background text-foreground pb-16 lg:pb-0">
        <SkipLink />
        <StorefrontHeader />
        <main id="main-content" className="flex-1">
          {children}
        </main>
        <StorefrontFooter />
        <MobileBottomNav />
        <CartDrawer />
      </div>
    </CartProvider>
  );
}
