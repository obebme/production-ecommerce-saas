import type { Metadata } from "next";
import { AdminCategoriesStarterView } from "@/components/admin/admin-starter-views";

export const metadata: Metadata = {
  title: "Admin Categories",
};

export default function AdminCategoriesPage() {
  return <AdminCategoriesStarterView />;
}
