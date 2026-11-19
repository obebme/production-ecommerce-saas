"""Pydantic schemas and strict input validators for Module 7 (Admin Console APIs)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.catalog.schemas import UTCDatetime, strip_single_line
from app.modules.identity.schemas import UserRole
from app.modules.orders.schemas import (
    FulfillmentStatus,
    OrderDetailDTO,
    OrderStatus,
    PaymentStatus,
)
from app.modules.payments.schemas import PaymentIntentDTO

ReservationStatus = Literal["ACTIVE", "COMMITTED", "RELEASED", "EXPIRED"]
FulfillOrderAction = Literal["START_PROCESSING", "SHIP", "COMPLETE"]


def _normalize_optional_single_line(value: object, *, field_name: str) -> object:
    if value is None:
        return None
    stripped = strip_single_line(value, field_name=field_name)
    if isinstance(stripped, str) and not stripped:
        return None
    return stripped


class AdminOrderListItemDTO(BaseModel):
    """Paginated administrative order list item DTO for GET /api/v1/admin/orders."""

    id: uuid.UUID
    order_number: str
    user_id: uuid.UUID | None
    customer_email: str
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
    shipped_at: UTCDatetime | None
    completed_at: UTCDatetime | None
    cancelled_at: UTCDatetime | None


class AdminOrderReservationDTO(BaseModel):
    """Inventory reservation record embedded in AdminOrderDetailDTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    variant_id: uuid.UUID
    quantity: int
    status: ReservationStatus
    expires_at: UTCDatetime
    created_at: UTCDatetime
    updated_at: UTCDatetime


class AdminOrderDetailDTO(OrderDetailDTO):
    """Extended administrative order detail DTO with full intents and reservations."""

    payment_intents: list[PaymentIntentDTO]
    reservations: list[AdminOrderReservationDTO]
    updated_at: UTCDatetime


class FulfillOrderRequest(BaseModel):
    """Request payload for POST /api/v1/admin/orders/{order_number}/fulfill."""

    model_config = ConfigDict(extra="forbid")

    action: FulfillOrderAction
    tracking_number: str | None = Field(default=None, max_length=120)
    carrier: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=255)

    @field_validator("tracking_number", mode="before")
    @classmethod
    def _validate_tracking_number(cls, value: object) -> object:
        return _normalize_optional_single_line(value, field_name="tracking_number")

    @field_validator("carrier", mode="before")
    @classmethod
    def _validate_carrier(cls, value: object) -> object:
        return _normalize_optional_single_line(value, field_name="carrier")

    @field_validator("note", mode="before")
    @classmethod
    def _validate_note(cls, value: object) -> object:
        return _normalize_optional_single_line(value, field_name="note")


class RefundOrderRequest(BaseModel):
    """Request payload for POST /api/v1/admin/orders/{order_number}/refund."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=255)
    restock: bool = True

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> object:
        return _normalize_optional_single_line(value, field_name="reason")


class AdminUserDTO(BaseModel):
    """Administrative user summary DTO (never exposes password_hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: UTCDatetime
    updated_at: UTCDatetime


class UpdateUserRoleRequest(BaseModel):
    """Request payload for PATCH /api/v1/admin/users/{user_id}/role."""

    model_config = ConfigDict(extra="forbid")

    role: UserRole


class UpdateUserStatusRequest(BaseModel):
    """Request payload for PATCH /api/v1/admin/users/{user_id}/status."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool


class AdminUserMutationResponse(BaseModel):
    """Response payload for admin user role/status updates with session revocation count."""

    user: AdminUserDTO
    revoked_sessions_count: int


class AuditLogEntryDTO(BaseModel):
    """Paginated append-only audit log entry DTO for GET /api/v1/admin/audit-logs."""

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    actor_full_name: str | None
    action: str
    resource_type: str
    resource_id: str
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    ip_address: str | None
    created_at: UTCDatetime
