"""Pydantic schemas and strict payload validators for Module 6 (Payments & Webhooks)."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.catalog.schemas import UTCDatetime, strip_single_line

PaymentIntentStatus = Literal[
    "REQUIRES_ACTION",
    "PROCESSING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "REFUNDED",
]
WebhookEventType = Literal[
    "payment_intent.succeeded",
    "payment_intent.failed",
    "charge.refunded",
]
SandboxSimulateOutcome = Literal["succeeded", "failed"]

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_INT32_MAX = 2_147_483_647


def _normalize_optional_single_line(value: object, *, field_name: str) -> object:
    if value is None:
        return None
    stripped = strip_single_line(value, field_name=field_name)
    if isinstance(stripped, str) and not stripped:
        return None
    return stripped


def _normalize_currency(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = strip_single_line(value, field_name="currency")
    assert isinstance(stripped, str)
    normalized = stripped.upper()
    if not _CURRENCY_RE.fullmatch(normalized):
        raise ValueError("Currency must be a 3-letter ISO-4217 uppercase code.")
    return normalized


class PaymentIntentDTO(BaseModel):
    """Payment intent status DTO embedded in checkout/order/payment responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    provider_intent_id: str
    amount_cents: int
    currency: str
    status: PaymentIntentStatus
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: UTCDatetime


class CreatePaymentIntentRequest(BaseModel):
    """Request payload for POST /api/v1/payments/intents."""

    model_config = ConfigDict(extra="forbid")

    order_number: str = Field(min_length=1, max_length=32)

    @field_validator("order_number", mode="before")
    @classmethod
    def _validate_order_number(cls, value: object) -> object:
        return strip_single_line(value, field_name="order_number")


class PaymentIntentResponse(BaseModel):
    """Response envelope for POST /api/v1/payments/intents."""

    payment_intent: PaymentIntentDTO


class SimulateSandboxPaymentRequest(BaseModel):
    """Request payload for POST /api/v1/payments/sandbox/{provider_intent_id}/simulate."""

    model_config = ConfigDict(extra="forbid")

    outcome: SandboxSimulateOutcome
    failure_code: str | None = Field(default=None, max_length=64)

    @field_validator("failure_code", mode="before")
    @classmethod
    def _validate_failure_code(cls, value: object) -> object:
        return _normalize_optional_single_line(value, field_name="failure_code")


class SimulateSandboxPaymentResponse(BaseModel):
    """Response envelope for sandbox payment simulation."""

    event_id: str
    webhook_status: str
    order_status: str
    payment_status: str


class WebhookEventData(BaseModel):
    """Nested data object inside a verified payment webhook payload."""

    model_config = ConfigDict(extra="forbid")

    provider_intent_id: str = Field(min_length=1, max_length=128)
    order_number: str = Field(min_length=1, max_length=32)
    amount_cents: int = Field(ge=0, le=_INT32_MAX)
    currency: str = Field(min_length=3, max_length=3)
    failure_code: str | None = Field(default=None, max_length=64)
    failure_message: str | None = Field(default=None, max_length=255)

    @field_validator("provider_intent_id", mode="before")
    @classmethod
    def _validate_intent_id(cls, value: object) -> object:
        return strip_single_line(value, field_name="provider_intent_id")

    @field_validator("order_number", mode="before")
    @classmethod
    def _validate_order_number(cls, value: object) -> object:
        return strip_single_line(value, field_name="order_number")

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, value: object) -> object:
        return _normalize_currency(value)

    @field_validator("failure_code", mode="before")
    @classmethod
    def _validate_failure_code(cls, value: object) -> object:
        return _normalize_optional_single_line(value, field_name="failure_code")

    @field_validator("failure_message", mode="before")
    @classmethod
    def _validate_failure_message(cls, value: object) -> object:
        return _normalize_optional_single_line(value, field_name="failure_message")


class WebhookPayload(BaseModel):
    """Verified provider webhook event body."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: WebhookEventType
    data: WebhookEventData

    @field_validator("event_id", mode="before")
    @classmethod
    def _validate_event_id(cls, value: object) -> object:
        return strip_single_line(value, field_name="event_id")


class WebhookResponse(BaseModel):
    """Response envelope for POST /api/v1/webhooks/payments/{provider}."""

    received: bool
    status: Literal["PROCESSED", "IGNORED_DUPLICATE"]
