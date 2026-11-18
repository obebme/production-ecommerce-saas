import type { Metadata } from "next";
import { AccountView } from "@/components/storefront/auth-views";

export const metadata: Metadata = {
  title: "My Account",
  description: "Manage your Atelier profile settings.",
};

export default function AccountPage() {
  return <AccountView />;
}
