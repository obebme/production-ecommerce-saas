import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AdminProductEditorView } from "@/components/admin/admin-product-editor-view";

export const metadata: Metadata = {
  title: "Edit Product",
};

export default async function AdminEditProductPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: rawId } = await params;
  let decodedId: string;
  try {
    decodedId = decodeURIComponent(rawId);
  } catch {
    notFound();
  }
  return <AdminProductEditorView mode="edit" productId={decodedId} />;
}
