import * as React from "react";
import type { Metadata } from "next";
import { LoginView } from "@/components/storefront/auth-views";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = {
  title: "Sign In",
  description: "Sign in to your Atelier account.",
};

export default function LoginPage() {
  return (
    <React.Suspense
      fallback={
        <div className="mx-auto max-w-md px-4 py-14">
          <Skeleton className="h-80 w-full" />
        </div>
      }
    >
      <LoginView />
    </React.Suspense>
  );
}
