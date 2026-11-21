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

/* --- Module 5 & 6: Checkout, Orders & Payments DTOs (BE-003) --- */

export type OrderStatus =
  | "PENDING_PAYMENT"
  | "CONFIRMED"
  | "PROCESSING"
  | "SHIPPED"
  | "COMPLETED"
  | "CANCELLED"
  | "REFUNDED";

export type PaymentStatus =
  | "REQUIRES_PAYMENT"
  | "PAID"
  | "FAILED"
  | "REFUND_PENDING"
  | "REFUNDED"
  | "CANCELLED";

export type FulfillmentStatus =
  | "UNFULFILLED"
  | "ALLOCATED"
  | "SHIPPED"
  | "DELIVERED"
  | "CANCELLED"
  | "RETURNED";

export type PaymentIntentStatus =
  | "REQUIRES_ACTION"
  | "PROCESSING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED"
  | "REFUND_PENDING"
  | "REFUNDED";

export type SandboxSimulateOutcome = "succeeded" | "failed";

export interface AddressDTO {
  full_name: string;
  line1: string;
  line2?: string | null;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  phone: string;
}

export interface CreateCheckoutOrderRequest {
  customer_email: string;
  shipping_address: AddressDTO;
  billing_same_as_shipping: boolean;
  billing_address?: AddressDTO | null;
}

export interface CancelOrderRequest {
  reason?: string | null;
}

export interface CreatePaymentIntentRequest {
  order_number: string;
}

export interface SimulateSandboxPaymentRequest {
  outcome: SandboxSimulateOutcome;
  failure_code?: string | null;
}

export interface OrderItemDTO {
  id: string;
  product_id: string;
  variant_id: string;
  sku_snapshot: string;
  product_title_snapshot: string;
  variant_title_snapshot: string;
  attributes_snapshot: Record<string, unknown>;
  unit_price_cents: number;
  quantity: number;
  line_total_cents: number;
}

export interface OrderEventDTO {
  id: string;
  actor_user_id: string | null;
  event_type: string;
  from_status: string | null;
  to_status: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface PaymentIntentDTO {
  id: string;
  provider: string;
  provider_intent_id: string;
  amount_cents: number;
  currency: string;
  status: PaymentIntentStatus;
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
}

export interface OrderCheckoutSummaryDTO {
  id: string;
  order_number: string;
  status: OrderStatus;
  payment_status: PaymentStatus;
  fulfillment_status: FulfillmentStatus;
  customer_email: string;
  currency: string;
  subtotal_cents: number;
  discount_cents: number;
  shipping_cents: number;
  tax_cents: number;
  total_cents: number;
  items: OrderItemDTO[];
  placed_at: string;
  paid_at: string | null;
}

export interface CheckoutOrderResponseDTO {
  order: OrderCheckoutSummaryDTO;
  payment_intent: PaymentIntentDTO;
  reservation_expires_at: string;
  guest_access_token: string | null;
}

export interface OrderListItemDTO {
  id: string;
  order_number: string;
  status: OrderStatus;
  payment_status: PaymentStatus;
  fulfillment_status: FulfillmentStatus;
  currency: string;
  subtotal_cents: number;
  discount_cents: number;
  shipping_cents: number;
  tax_cents: number;
  total_cents: number;
  item_count: number;
  placed_at: string;
  paid_at: string | null;
  reservation_expires_at: string | null;
}

export interface OrderDetailDTO {
  id: string;
  order_number: string;
  user_id: string | null;
  status: OrderStatus;
  payment_status: PaymentStatus;
  fulfillment_status: FulfillmentStatus;
  customer_email: string;
  currency: string;
  subtotal_cents: number;
  discount_cents: number;
  shipping_cents: number;
  tax_cents: number;
  total_cents: number;
  shipping_address: AddressDTO;
  billing_address: AddressDTO;
  items: OrderItemDTO[];
  payment_intent: PaymentIntentDTO | null;
  reservation_expires_at: string | null;
  events: OrderEventDTO[];
  placed_at: string;
  paid_at: string | null;
  shipped_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
}

export interface PaymentIntentResponse {
  payment_intent: PaymentIntentDTO;
}

export interface SimulateSandboxPaymentResponse {
  event_id: string;
  webhook_status: string;
  order_status: string;
  payment_status: string;
}

/* --- FE-003 Administrative Catalog, Inventory, Orders, Users, Audit & Analytics DTOs --- */

export interface CreateCategoryRequest {
  parent_id?: string | null;
  name: string;
  slug: string;
  description?: string | null;
  is_active?: boolean;
  sort_order?: number;
}

export interface UpdateCategoryRequest {
  parent_id?: string | null;
  name?: string;
  slug?: string;
  description?: string | null;
  is_active?: boolean;
  sort_order?: number;
}

export interface ProductImageInput {
  url: string;
  alt_text?: string;
  sort_order?: number;
  is_primary?: boolean;
}

export interface CreateProductRequest {
  category_id: string;
  title: string;
  slug: string;
  description?: string;
  status?: ProductStatus;
  images?: ProductImageInput[];
}

export interface UpdateProductRequest {
  category_id?: string;
  title?: string;
  slug?: string;
  description?: string;
  status?: ProductStatus;
  images?: ProductImageInput[];
}

export interface CreateVariantRequest {
  sku: string;
  title: string;
  attributes?: Record<string, unknown>;
  price_cents: number;
  compare_at_price_cents?: number | null;
  currency?: string;
  is_active?: boolean;
  initial_on_hand?: number;
  low_stock_threshold?: number;
}

export interface UpdateVariantRequest {
  sku?: string;
  title?: string;
  attributes?: Record<string, unknown>;
  price_cents?: number;
  compare_at_price_cents?: number | null;
  currency?: string;
  is_active?: boolean;
  low_stock_threshold?: number;
}

export interface StockAdjustmentRequest {
  delta_on_hand: number;
  note: string;
}

export interface InventoryLedgerEntryDTO {
  id: string;
  variant_id: string;
  order_id: string | null;
  order_number: string | null;
  actor_user_id: string | null;
  actor_email: string | null;
  reason: string;
  delta_on_hand: number;
  delta_reserved: number;
  on_hand_after: number;
  reserved_after: number;
  note: string | null;
  created_at: string;
}

export type ReservationStatus = "ACTIVE" | "COMMITTED" | "RELEASED" | "EXPIRED";
export type FulfillOrderAction = "START_PROCESSING" | "SHIP" | "COMPLETE";

export interface AdminOrderListItemDTO {
  id: string;
  order_number: string;
  user_id: string | null;
  customer_email: string;
  status: OrderStatus;
  payment_status: PaymentStatus;
  fulfillment_status: FulfillmentStatus;
  currency: string;
  subtotal_cents: number;
  discount_cents: number;
  shipping_cents: number;
  tax_cents: number;
  total_cents: number;
  item_count: number;
  placed_at: string;
  paid_at: string | null;
  shipped_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
}

export interface AdminOrderReservationDTO {
  id: string;
  order_id: string;
  variant_id: string;
  quantity: number;
  status: ReservationStatus;
  expires_at: string;
  created_at: string;
  updated_at: string;
}

export interface AdminOrderDetailDTO extends OrderDetailDTO {
  payment_intents: PaymentIntentDTO[];
  reservations: AdminOrderReservationDTO[];
  updated_at: string;
}

export interface FulfillOrderRequest {
  action: FulfillOrderAction;
  tracking_number?: string | null;
  carrier?: string | null;
  note?: string | null;
}

export interface RefundOrderRequest {
  reason?: string | null;
  restock?: boolean;
}

export interface AdminUserDTO {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UpdateUserRoleRequest {
  role: UserRole;
}

export interface UpdateUserStatusRequest {
  is_active: boolean;
}

export interface AdminUserMutationResponse {
  user: AdminUserDTO;
  revoked_sessions_count: number;
}

export interface AuditLogEntryDTO {
  id: string;
  actor_user_id: string | null;
  actor_email: string | null;
  actor_full_name: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

export type TopProductSortBy = "revenue" | "units";

export interface AnalyticsSummaryResponse {
  from_date: string;
  to_date: string;
  gross_revenue_cents: number;
  refunded_revenue_cents: number;
  net_revenue_cents: number;
  paid_orders_count: number;
  average_order_value_cents: number;
  orders_by_status: Record<OrderStatus, number>;
}

export interface TimeseriesPointDTO {
  date: string;
  net_revenue_cents: number;
  paid_orders_count: number;
}

export interface AnalyticsTimeseriesResponse {
  from_date: string;
  to_date: string;
  points: TimeseriesPointDTO[];
}

export interface TopProductDTO {
  variant_id: string;
  product_id: string;
  sku_snapshot: string;
  product_title_snapshot: string;
  variant_title_snapshot: string;
  units_sold: number;
  revenue_cents: number;
}

export interface TopProductsResponse {
  from_date: string;
  to_date: string;
  items: TopProductDTO[];
}

export interface InventoryHealthSummaryDTO {
  active_skus_count: number;
  out_of_stock_count: number;
  low_stock_count: number;
  total_reserved_units: number;
}

export interface InventoryHealthAlertDTO {
  variant_id: string;
  product_id: string;
  sku: string;
  product_title: string;
  variant_title: string;
  on_hand: number;
  reserved: number;
  available_quantity: number;
  low_stock_threshold: number;
  in_stock: boolean;
  low_stock: boolean;
}

export interface InventoryHealthResponse {
  summary: InventoryHealthSummaryDTO;
  alerts: PaginatedResponse<InventoryHealthAlertDTO>;
}
