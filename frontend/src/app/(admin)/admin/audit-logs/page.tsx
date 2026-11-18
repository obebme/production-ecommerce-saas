import type { Metadata } from "next";
import { AdminAuditLogsStarterView } from "@/components/admin/admin-starter-views";

export const metadata: Metadata = {
  title: "Admin Audit Logs",
};

export default function AdminAuditLogsPage() {
  return <AdminAuditLogsStarterView />;
}
