import type { Metadata } from "next";
import { CheckoutView } from "@/components/storefront/checkout-view";

export const metadata: Metadata = {
  title: "Checkout",
  description: "Provide shipping and billing details to reserve stock and place your order.",
};

export default function CheckoutPage() {
  return <CheckoutView />;
}
