import type { Metadata } from "next";
import { fetchServerCategories, fetchServerProducts } from "@/lib/api/server-catalog";
import { HomeView } from "@/components/storefront/home-view";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Home",
  description: "Explore Vellora Store seasonal apparel and considered everyday objects.",
};

export default async function HomePage() {
  const [categories, productsRes] = await Promise.all([
    fetchServerCategories(),
    fetchServerProducts({ sort: "newest", page: 1, page_size: 8 }),
  ]);

  return (
    <HomeView
      initialCategories={categories ? categories.slice(0, 4) : null}
      initialProducts={productsRes ? productsRes.items : null}
    />
  );
}
