import type { Metadata } from "next";
import { AdminCategoriesView } from "@/components/admin/admin-categories-view";

export const metadata: Metadata = {
  title: "Admin Categories",
};

export default function AdminCategoriesPage() {
  return <AdminCategoriesView />;
}
