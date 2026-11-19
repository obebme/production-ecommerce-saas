import type { Metadata } from "next";
import { AdminProductsView } from "@/components/admin/admin-products-view";

export const metadata: Metadata = {
  title: "Admin Products",
};

export default function AdminProductsPage() {
  return <AdminProductsView />;
}
