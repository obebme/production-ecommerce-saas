import type { Metadata } from "next";
import { AdminInventoryStarterView } from "@/components/admin/admin-starter-views";

export const metadata: Metadata = {
  title: "Admin Inventory",
};

export default function AdminInventoryPage() {
  return <AdminInventoryStarterView />;
}
