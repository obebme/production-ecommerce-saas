import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ProductDetailView } from "@/components/storefront/product-detail-view";

const PRODUCT_SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/i;

export const metadata: Metadata = {
  title: "Product Details",
  description: "Select product variants and check live stock availability.",
};

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug: rawSlug } = await params;
  let decodedSlug: string;
  try {
    decodedSlug = decodeURIComponent(rawSlug);
  } catch {
    notFound();
  }
  if (!PRODUCT_SLUG_RE.test(decodedSlug)) {
    notFound();
  }
  return <ProductDetailView slug={decodedSlug} />;
}
