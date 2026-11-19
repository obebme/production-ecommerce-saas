import * as React from "react";
import type { Metadata } from "next";
import { AdminOrdersView } from "@/components/admin/admin-orders-view";
import { LoadingState } from "@/components/shared/state-feedback";

export const metadata: Metadata = {
  title: "Admin Orders",
};

export default function AdminOrdersPage() {
  return (
    <React.Suspense fallback={<LoadingState label="Loading orders workbench..." rows={5} />}>
      <AdminOrdersView />
    </React.Suspense>
  );
}
