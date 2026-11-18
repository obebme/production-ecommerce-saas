import { SkipLink } from "@/components/shared/skip-link";
import { CartDrawer } from "@/components/storefront/cart-drawer";
import { StorefrontFooter } from "@/components/storefront/footer";
import { StorefrontHeader } from "@/components/storefront/header";

export default function StorefrontLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <SkipLink />
      <StorefrontHeader />
      <main id="main-content" className="flex-1">
        {children}
      </main>
      <StorefrontFooter />
      <CartDrawer />
    </div>
  );
}
