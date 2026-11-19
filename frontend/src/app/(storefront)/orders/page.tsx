import type { Metadata } from "next";
import { OrderHistoryView } from "@/components/storefront/order-history-view";

export const metadata: Metadata = {
  title: "My Orders",
  description: "View your order history, payment confirmation status, and fulfillment progress.",
};

export default function OrdersPage() {
  return <OrderHistoryView />;
}
