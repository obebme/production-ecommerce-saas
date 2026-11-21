import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function StorefrontNotFoundPage() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center bg-background px-4 py-16 text-center text-foreground">
      <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">404</p>
      <h1 className="mt-2 font-display text-3xl font-medium tracking-[-0.02em]">
        Page not found
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        The requested page could not be found or is no longer available.
      </p>
      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <Button asChild>
          <Link href="/">Return Home</Link>
        </Button>
        <Button variant="outline" asChild>
          <Link href="/products">Browse Catalog</Link>
        </Button>
      </div>
    </div>
  );
}
