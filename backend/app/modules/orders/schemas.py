"""Pydantic schemas and strict address/checkout validators for Module 5 (Orders)."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.modules.catalog.schemas import UTCDatetime, strip_single_line
from app.modules.payments.schemas import PaymentIntentDTO

OrderStatus = Literal[
    "PENDING_PAYMENT",
    "CONFIRMED",
    "PROCESSING",
    "SHIPPED",
    "COMPLETED",
    "CANCELLED",
    "REFUNDED",
]
PaymentStatus = Literal[
    "REQUIRES_PAYMENT",
    "PAID",
    "FAILED",
    "REFUND_PENDING",
    "REFUNDED",
    "CANCELLED",
]
FulfillmentStatus = Literal[
    "UNFULFILLED",
    "ALLOCATED",
    "SHIPPED",
    "DELIVERED",
    "CANCELLED",
    "RETURNED",
]

_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")


def _normalize_optional_text(value: object, *, field_name: str) -> object:
    if value is None:
        return None
    stripped = strip_single_line(value, field_name=field_name)
    if isinstance(stripped, str) and not stripped:
        return None
    return stripped


class AddressDTO(BaseModel):
    """Strict shipping/billing address DTO rejecting unknown fields and control chars."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=32)
    country: str = Field(min_length=2, max_length=2)
    phone: str = Field(min_length=3, max_length=32)

    @field_validator("full_name", mode="before")
    @classmethod
    def _validate_full_name(cls, value: object) -> object:
        return strip_single_line(value, field_name="full_name")

    @field_validator("line1", mode="before")
    @classmethod
    def _validate_line1(cls, value: object) -> object:
        return strip_single_line(value, field_name="line1")

    @field_validator("line2", mode="before")
    @classmethod
    def _validate_line2(cls, value: object) -> object:
        return _normalize_optional_text(value, field_name="line2")

    @field_validator("city", mode="before")
    @classmethod
    def _validate_city(cls, value: object) -> object:
        return strip_single_line(value, field_name="city")

    @field_validator("state", mode="before")
    @classmethod
    def _validate_state(cls, value: object) -> object:
        return strip_single_line(value, field_name="state")

    @field_validator("postal_code", mode="before")
    @classmethod
    def _validate_postal_code(cls, value: object) -> object:
        return strip_single_line(value, field_name="postal_code")

    @field_validator("country", mode="before")
    @classmethod
    def _validate_country(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = strip_single_line(value, field_name="country")
        assert isinstance(stripped, str)
        normalized = stripped.upper()
        if not _COUNTRY_CODE_RE.fullmatch(normalized):
            raise ValueError("Country must be a 2-letter uppercase ISO-3166 alpha-2 code.")
        return normalized

    @field_validator("phone", mode="before")
    @classmethod
    def _validate_phone(cls, value: object) -> object:
        return strip_single_line(value, field_name="phone")


class CreateCheckoutOrderRequest(BaseModel):
    """Request payload for POST /api/v1/checkout/orders (no client prices/totals)."""

    model_config = ConfigDict(extra="forbid")

    customer_email: EmailStr
    shipping_address: AddressDTO
    billing_same_as_shipping: bool = True
    billing_address: AddressDTO | None = None

    @field_validator("customer_email", mode="after")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        normalized = str(value).strip().lower()
        if len(normalized) > 320:
            raise ValueError("Customer email exceeds maximum length of 320 characters.")
        return normalized

    @model_validator(mode="after")
    def _validate_billing_address(self) -> CreateCheckoutOrderRequest:
        if not self.billing_same_as_shipping and self.billing_address is None:
            raise ValueError(
                "billing_address is required when billing_same_as_shipping is false."
            )
        return self

    def effective_billing_address(self) -> AddressDTO:
        """Resolve canonical billing address according to billing_same_as_shipping."""
        if self.billing_same_as_shipping or self.billing_address is None:
            return self.shipping_address
        return self.billing_address


class CancelOrderRequest(BaseModel):
    """Request payload for POST /api/v1/orders/{order_number}/cancel."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=255)

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> object:
        return _normalize_optional_text(value, field_name="reason")


class OrderItemDTO(BaseModel):
    """Immutable order line-item snapshot DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    variant_id: uuid.UUID
    sku_snapshot: str
    product_title_snapshot: str
    variant_title_snapshot: str
    attributes_snapshot: dict[str, Any]
    unit_price_cents: int
    quantity: int
    line_total_cents: int


class OrderEventDTO(BaseModel):
    """Append-only order status/audit timeline event DTO."""

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    event_type: str
    from_status: str | None
    to_status: str
    metadata: dict[str, Any]
    created_at: UTCDatetime


class OrderCheckoutSummaryDTO(BaseModel):
    """Order summary returned by POST /api/v1/checkout/orders."""

    id: uuid.UUID
    order_number: str
    status: OrderStatus
    payment_status: PaymentStatus
    fulfillment_status: FulfillmentStatus
    customer_email: str
    currency: str
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    items: list[OrderItemDTO]
    placed_at: UTCDatetime
    paid_at: UTCDatetime | None


class CheckoutOrderResponseDTO(BaseModel):
    """Response payload for POST /api/v1/checkout/orders."""

    order: OrderCheckoutSummaryDTO
    payment_intent: PaymentIntentDTO
    reservation_expires_at: UTCDatetime
    guest_access_token: str | None


class OrderListItemDTO(BaseModel):
    """Paginated customer order list entry for GET /api/v1/orders."""

    id: uuid.UUID
    order_number: str
    status: OrderStatus
    payment_status: PaymentStatus
    fulfillment_status: FulfillmentStatus
    currency: str
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    item_count: int
    placed_at: UTCDatetime
    paid_at: UTCDatetime | None
    reservation_expires_at: UTCDatetime | None


class OrderDetailDTO(BaseModel):
    """Detailed customer/guest order DTO for GET /orders/{order_number} and /cancel."""

    id: uuid.UUID
    order_number: str
    user_id: uuid.UUID | None
    status: OrderStatus
    payment_status: PaymentStatus
    fulfillment_status: FulfillmentStatus
    customer_email: str
    currency: str
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    shipping_address: AddressDTO
    billing_address: AddressDTO
    items: list[OrderItemDTO]
    payment_intent: PaymentIntentDTO | None
    reservation_expires_at: UTCDatetime | None
    events: list[OrderEventDTO]
    placed_at: UTCDatetime
    paid_at: UTCDatetime | None
    shipped_at: UTCDatetime | None
    completed_at: UTCDatetime | None
    cancelled_at: UTCDatetime | None
    cancellation_reason: str | None
