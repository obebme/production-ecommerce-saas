import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AdminOrderDetailView } from "@/components/admin/admin-order-detail-view";

const ORDER_NUMBER_RE = /^[A-Za-z0-9_-]{1,32}$/;

export const metadata: Metadata = {
  title: "Order Workbench",
};

export default async function AdminOrderDetailPage({
  params,
}: {
  params: Promise<{ orderNumber: string }>;
}) {
  const { orderNumber: rawOrderNumber } = await params;
  let decodedOrderNumber: string;
  try {
    decodedOrderNumber = decodeURIComponent(rawOrderNumber);
  } catch {
    notFound();
  }
  if (!ORDER_NUMBER_RE.test(decodedOrderNumber)) {
    notFound();
  }
  return <AdminOrderDetailView orderNumber={decodedOrderNumber} />;
}
