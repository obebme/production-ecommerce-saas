import type { Metadata } from "next";
import { HomeView } from "@/components/storefront/home-view";

export const metadata: Metadata = {
  title: "Home",
  description: "Explore Atelier seasonal apparel and considered everyday objects.",
};

export default function HomePage() {
  return <HomeView />;
}
