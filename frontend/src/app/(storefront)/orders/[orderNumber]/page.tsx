import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { OrderDetailView } from "@/components/storefront/order-detail-view";

const ORDER_NUMBER_RE = /^[A-Za-z0-9_-]{1,32}$/;

export const metadata: Metadata = {
  title: "Order Details",
  description: "Review your order summary, stock reservation status, and sandbox payment.",
};

export default async function OrderDetailPage({
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
  return <OrderDetailView orderNumber={decodedOrderNumber} />;
}
