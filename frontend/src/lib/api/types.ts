/** Exact TypeScript DTOs aligned 1:1 with OpenAPI contracts and backend Pydantic schemas. */

export type UserRole = "customer" | "staff" | "admin";

export interface UserDTO {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

export interface AuthSessionResponse {
  user: UserDTO;
  csrf_token: string;
}

export type AuthResponse = AuthSessionResponse;

export interface UserUpdateResponse {
  user: UserDTO;
}

export interface RegisterRequest {
  email: string;
  password: string;
  full_name: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface UpdateProfileRequest {
  full_name: string;
}

export interface HealthDTO {
  status: "ok";
  database: "ok";
  sandbox_payments: boolean;
}

export interface ValidationFieldError {
  loc: string[];
  msg: string;
  type: string;
}

export interface ErrorDetailPayload {
  code: string;
  message: string;
  details: Record<string, unknown>;
  request_id: string;
}

export interface ErrorEnvelope {
  error: ErrorDetailPayload;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
}

export type ProductStatus = "DRAFT" | "ACTIVE" | "ARCHIVED";
export type ProductSortOrder = "newest" | "price_asc" | "price_desc" | "title_asc";
export type StockStatusFilter = "all" | "in_stock" | "low_stock" | "out_of_stock";

export interface CategoryPublicDTO {
  id: string;
  parent_id: string | null;
  name: string;
  slug: string;
  description: string | null;
  sort_order: number;
}

export interface CategoryListResponse {
  items: CategoryPublicDTO[];
}

export interface CategorySummaryDTO {
  id: string;
  name: string;
  slug: string;
}

export interface ProductImageDTO {
  id: string;
  url: string;
  alt_text: string;
  sort_order: number;
  is_primary: boolean;
}

export interface ProductVariantPublicDTO {
  id: string;
  sku: string;
  title: string;
  attributes: Record<string, unknown>;
  price_cents: number;
  compare_at_price_cents: number | null;
  currency: string;
  is_active: boolean;
  available_quantity: number;
  in_stock: boolean;
  low_stock: boolean;
}

export interface ProductSummaryDTO {
  id: string;
  title: string;
  slug: string;
  category: CategorySummaryDTO;
  primary_image: ProductImageDTO | null;
  min_price_cents: number;
  max_price_cents: number;
  currency: string;
  in_stock: boolean;
}

export interface ProductDetailDTO {
  id: string;
  title: string;
  slug: string;
  description: string;
  status: ProductStatus;
  category: CategorySummaryDTO;
  images: ProductImageDTO[];
  variants: ProductVariantPublicDTO[];
}

export interface ProductListQuery {
  category_slug?: string;
  q?: string;
  min_price_cents?: number;
  max_price_cents?: number;
  in_stock?: boolean;
  sort?: ProductSortOrder;
  page?: number;
  page_size?: number;
}

export interface VariantAvailabilityDTO {
  variant_id: string;
  sku: string;
  is_active: boolean;
  available_quantity: number;
  in_stock: boolean;
  low_stock: boolean;
}

export interface AddCartItemRequest {
  variant_id: string;
  quantity: number;
}

export interface UpdateCartItemRequest {
  quantity: number;
}

export interface CartItemDTO {
  id: string;
  variant_id: string;
  product_id: string;
  product_slug: string;
  product_title: string;
  variant_title: string;
  sku: string;
  image_url: string | null;
  unit_price_cents: number;
  quantity: number;
  line_total_cents: number;
  available_quantity: number;
  in_stock: boolean;
  low_stock: boolean;
  is_available: boolean;
}

export interface CartDTO {
  id: string;
  currency: string;
  items: CartItemDTO[];
  item_count: number;
  subtotal_cents: number;
  estimated_shipping_cents: number;
  estimated_total_cents: number;
}

/* --- Administrative BE-002 DTOs --- */

export interface AdminCategoryDTO {
  id: string;
  parent_id: string | null;
  name: string;
  slug: string;
  description: string | null;
  is_active: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface AdminCategoryListResponse {
  items: AdminCategoryDTO[];
}

export interface AdminVariantDTO {
  id: string;
  product_id: string;
  sku: string;
  title: string;
  attributes: Record<string, unknown>;
  price_cents: number;
  compare_at_price_cents: number | null;
  currency: string;
  is_active: boolean;
  on_hand: number;
  reserved: number;
  available_quantity: number;
  low_stock_threshold: number;
  in_stock: boolean;
  low_stock: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminProductSummaryDTO {
  id: string;
  title: string;
  slug: string;
  status: ProductStatus;
  category: CategorySummaryDTO;
  primary_image: ProductImageDTO | null;
  min_price_cents: number | null;
  max_price_cents: number | null;
  currency: string | null;
  variants_count: number;
  active_variants_count: number;
  total_available_quantity: number;
  in_stock: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminProductDetailDTO {
  id: string;
  title: string;
  slug: string;
  description: string;
  status: ProductStatus;
  category: CategorySummaryDTO;
  images: ProductImageDTO[];
  variants: AdminVariantDTO[];
  created_at: string;
  updated_at: string;
}

export interface AdminInventoryItemDTO {
  variant_id: string;
  product_id: string;
  sku: string;
  product_title: string;
  variant_title: string;
  product_status: ProductStatus;
  is_active: boolean;
  on_hand: number;
  reserved: number;
  available_quantity: number;
  low_stock_threshold: number;
  in_stock: boolean;
  low_stock: boolean;
  updated_at: string;
}
