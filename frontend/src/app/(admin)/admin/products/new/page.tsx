import type { Metadata } from "next";
import { AdminProductEditorView } from "@/components/admin/admin-product-editor-view";

export const metadata: Metadata = {
  title: "New Product",
};

export default function AdminNewProductPage() {
  return <AdminProductEditorView mode="create" />;
}
