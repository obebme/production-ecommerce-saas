import type { Metadata } from "next";
import { AccountView } from "@/components/storefront/auth-views";

export const metadata: Metadata = {
  title: "My Account",
  description: "Manage your Vellora Store profile settings.",
};

export default function AccountPage() {
  return <AccountView />;
}
