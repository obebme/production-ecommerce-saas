import type { Metadata } from "next";
import { AdminProductsStarterView } from "@/components/admin/admin-dashboard-view";

export const metadata: Metadata = {
  title: "Admin Products",
};

export default function AdminProductsPage() {
  return <AdminProductsStarterView />;
}
