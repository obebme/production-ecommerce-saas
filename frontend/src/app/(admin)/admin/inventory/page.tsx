import * as React from "react";
import type { Metadata } from "next";
import { AdminInventoryView } from "@/components/admin/admin-inventory-view";
import { LoadingState } from "@/components/shared/state-feedback";

export const metadata: Metadata = {
  title: "Admin Inventory",
};

export default function AdminInventoryPage() {
  return (
    <React.Suspense fallback={<LoadingState label="Loading inventory control..." rows={5} />}>
      <AdminInventoryView />
    </React.Suspense>
  );
}
