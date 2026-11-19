import type { Metadata } from "next";
import { AdminAuditLogsView } from "@/components/admin/admin-audit-logs-view";

export const metadata: Metadata = {
  title: "Admin Audit Logs",
};

export default function AdminAuditLogsPage() {
  return <AdminAuditLogsView />;
}
