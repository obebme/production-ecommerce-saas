import type { Metadata } from "next";
import { AdminOrdersStarterView } from "@/components/admin/admin-starter-views";

export const metadata: Metadata = {
  title: "Admin Orders",
};

export default function AdminOrdersPage() {
  return <AdminOrdersStarterView />;
}
