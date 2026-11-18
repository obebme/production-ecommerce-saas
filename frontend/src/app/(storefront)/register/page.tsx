import * as React from "react";
import type { Metadata } from "next";
import { RegisterView } from "@/components/storefront/auth-views";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = {
  title: "Create Account",
  description: "Create a customer account to manage your profile and shopping bag.",
};

export default function RegisterPage() {
  return (
    <React.Suspense
      fallback={
        <div className="mx-auto max-w-md px-4 py-14">
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <RegisterView />
    </React.Suspense>
  );
}
