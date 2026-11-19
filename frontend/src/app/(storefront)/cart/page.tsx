import type { Metadata } from "next";
import { CartPageView } from "@/components/storefront/cart-page-view";

export const metadata: Metadata = {
  title: "Shopping Bag",
  description: "Review your shopping bag items, live stock availability, and estimated totals.",
};

export default function CartPage() {
  return <CartPageView />;
}
