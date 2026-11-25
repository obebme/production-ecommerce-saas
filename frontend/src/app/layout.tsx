import type { Metadata } from "next";
import { headers } from "next/headers";
import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource-variable/newsreader";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./globals.css";
import { ThemeProvider } from "@/components/shared/theme-provider";
import { ToastProvider } from "@/components/ui/toast";
import { resolveSiteUrl } from "@/lib/site-url";
import { AuthProvider } from "@/providers/auth-provider";

export const metadata: Metadata = {
  metadataBase: resolveSiteUrl(),
  title: {
    default: "Vellora Store — Considered Everyday Goods",
    template: "%s | Vellora Store",
  },
  description:
    "Curated apparel and everyday objects crafted with transparent materials and live inventory precision.",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Opt into per-request dynamic rendering so Next.js reads the middleware CSP
  // nonce from request headers and stamps it onto inline runtime scripts.
  const headerList = await headers();
  void headerList.get("x-nonce");

  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans text-foreground antialiased">
        <ThemeProvider>
          <ToastProvider>
            <AuthProvider>{children}</AuthProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
