import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { fetchServerProductBySlug } from "@/lib/api/server-catalog";
import { ProductDetailView } from "@/components/storefront/product-detail-view";

export const dynamic = "force-dynamic";

const PRODUCT_SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/i;

function decodeAndValidateSlug(rawSlug: string): string | null {
  let decodedSlug: string;
  try {
    decodedSlug = decodeURIComponent(rawSlug);
  } catch {
    return null;
  }
  if (!PRODUCT_SLUG_RE.test(decodedSlug)) {
    return null;
  }
  return decodedSlug;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug: rawSlug } = await params;
  const decodedSlug = decodeAndValidateSlug(rawSlug);
  if (!decodedSlug) {
    return {
      title: "Product Not Found",
      description: "The requested product could not be found.",
    };
  }

  const result = await fetchServerProductBySlug(decodedSlug);
  if (result.status === "not_found") {
    return {
      title: "Product Not Found",
      description: "The requested product could not be found.",
    };
  }

  if (result.status === "ok") {
    const { product } = result;
    const sortedImages = [...product.images].sort((a, b) => a.sort_order - b.sort_order);
    const primaryImage = sortedImages.find((img) => img.is_primary) ?? sortedImages[0] ?? null;
    const description =
      product.description || `${product.title} — ${product.category.name} at Atelier.`;

    return {
      title: product.title,
      description,
      openGraph: {
        title: product.title,
        description,
        images: primaryImage
          ? [{ url: primaryImage.url, alt: primaryImage.alt_text || product.title }]
          : undefined,
      },
    };
  }

  return {
    title: "Product Details",
    description: "Select product variants and check live stock availability.",
  };
}

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug: rawSlug } = await params;
  const decodedSlug = decodeAndValidateSlug(rawSlug);
  if (!decodedSlug) {
    notFound();
  }

  const result = await fetchServerProductBySlug(decodedSlug);
  if (result.status === "not_found") {
    notFound();
  }

  if (result.status === "ok") {
    return <ProductDetailView slug={decodedSlug} initialProduct={result.product} />;
  }

  return <ProductDetailView slug={decodedSlug} initialError={result.message} />;
}
