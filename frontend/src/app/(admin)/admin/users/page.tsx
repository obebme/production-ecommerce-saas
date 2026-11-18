import type { Metadata } from "next";
import { AdminUsersStarterView } from "@/components/admin/admin-starter-views";

export const metadata: Metadata = {
  title: "Admin Users & Roles",
};

export default function AdminUsersPage() {
  return <AdminUsersStarterView />;
}
