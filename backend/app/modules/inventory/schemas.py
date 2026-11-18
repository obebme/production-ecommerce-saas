"""Pydantic schemas and strict input validators for Module 3 (Variants & Inventory)."""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.catalog.schemas import ProductStatus, UTCDatetime, strip_single_line

StockStatusFilter = Literal["all", "in_stock", "low_stock", "out_of_stock"]

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_INT32_MAX = 2_147_483_647
_INT32_MIN = -2_147_483_648
_MAX_ATTR_DEPTH = 4
_MAX_ATTR_ENTRIES = 50
_MAX_ATTR_STR_LEN = 1000
_MAX_ATTR_NODES = 250
_MAX_ATTR_BYTES = 16_384


def _validate_and_normalize_json_value(
    value: Any,
    *,
    depth: int,
    counter: list[int],
) -> Any:
    counter[0] += 1
    if counter[0] > _MAX_ATTR_NODES:
        raise ValueError(
            f"Variant attributes exceed maximum node count ({_MAX_ATTR_NODES})."
        )
    if depth > _MAX_ATTR_DEPTH:
        raise ValueError("Variant attributes exceed maximum nesting depth.")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Variant attributes must not contain non-finite numbers.")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_ATTR_STR_LEN:
            raise ValueError(
                f"Variant attribute string values must be at most {_MAX_ATTR_STR_LEN} characters."
            )
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError(
                "Variant attribute string values must not contain control characters."
            )
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_ATTR_ENTRIES:
            raise ValueError("Variant attributes mapping is too large.")
        normalized_dict: dict[str, Any] = {}
        for raw_key, item_val in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("Variant attribute keys must be strings.")
            if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_key):
                raise ValueError("Variant attribute keys must not contain control characters.")
            stripped_key = raw_key.strip()
            if not stripped_key or len(stripped_key) > 64:
                raise ValueError(
                    "Variant attribute keys must be non-empty strings (<= 64 chars)."
                )
            if stripped_key in normalized_dict:
                raise ValueError("Duplicate variant attribute key after trimming whitespace.")
            normalized_dict[stripped_key] = _validate_and_normalize_json_value(
                item_val,
                depth=depth + 1,
                counter=counter,
            )
        return normalized_dict
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        if len(value) > _MAX_ATTR_ENTRIES:
            raise ValueError("Variant attribute lists exceed maximum length.")
        return [
            _validate_and_normalize_json_value(item, depth=depth + 1, counter=counter)
            for item in value
        ]
    raise ValueError("Variant attributes contain unsupported value types.")


def _validate_attributes(value: object) -> object:
    if not isinstance(value, dict):
        return value
    if len(value) > _MAX_ATTR_ENTRIES:
        raise ValueError(
            f"Variant attributes must contain at most {_MAX_ATTR_ENTRIES} entries."
        )
    normalized = _validate_and_normalize_json_value(value, depth=0, counter=[0])
    encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > _MAX_ATTR_BYTES:
        raise ValueError(
            f"Variant attributes exceed maximum serialized size ({_MAX_ATTR_BYTES} bytes)."
        )
    return normalized


def _normalize_currency(value: object) -> object:
    if not isinstance(value, str):
        return value
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError("Currency must not contain control characters.")
    normalized = value.strip().upper()
    if not _CURRENCY_RE.fullmatch(normalized):
        raise ValueError("Currency must be a 3-letter ISO-4217 uppercase code.")
    return normalized


class VariantAvailabilityDTO(BaseModel):
    """Public stock availability response for GET /api/v1/variants/{variant_id}/availability."""

    variant_id: uuid.UUID
    sku: str
    is_active: bool
    available_quantity: int
    in_stock: bool
    low_stock: bool


class CreateVariantRequest(BaseModel):
    """Request payload for POST /api/v1/admin/products/{product_id}/variants."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=160)
    attributes: dict[str, Any] = Field(default_factory=dict)
    price_cents: int = Field(ge=0, le=_INT32_MAX)
    compare_at_price_cents: int | None = Field(default=None, ge=0, le=_INT32_MAX)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    is_active: bool = True
    initial_on_hand: int = Field(default=0, ge=0, le=_INT32_MAX)
    low_stock_threshold: int = Field(default=5, ge=0, le=_INT32_MAX)

    @field_validator("sku", mode="before")
    @classmethod
    def _validate_sku(cls, value: object) -> object:
        return strip_single_line(value, field_name="SKU")

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: object) -> object:
        return strip_single_line(value, field_name="Variant title")

    @field_validator("attributes", mode="before")
    @classmethod
    def _validate_attrs(cls, value: object) -> object:
        return _validate_attributes(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, value: object) -> object:
        return _normalize_currency(value)

    @model_validator(mode="after")
    def _validate_compare_price(self) -> CreateVariantRequest:
        if (
            self.compare_at_price_cents is not None
            and self.compare_at_price_cents < self.price_cents
        ):
            raise ValueError(
                "compare_at_price_cents must be greater than or equal to price_cents."
            )
        return self


class UpdateVariantRequest(BaseModel):
    """Request payload for PATCH /api/v1/admin/variants/{variant_id}."""

    model_config = ConfigDict(extra="forbid")

    sku: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    attributes: dict[str, Any] | None = None
    price_cents: int | None = Field(default=None, ge=0, le=_INT32_MAX)
    compare_at_price_cents: int | None = Field(default=None, ge=0, le=_INT32_MAX)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool | None = None
    low_stock_threshold: int | None = Field(default=None, ge=0, le=_INT32_MAX)

    @field_validator("sku", mode="before")
    @classmethod
    def _validate_sku(cls, value: object) -> object:
        return strip_single_line(value, field_name="SKU")

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: object) -> object:
        return strip_single_line(value, field_name="Variant title")

    @field_validator("attributes", mode="before")
    @classmethod
    def _validate_attrs(cls, value: object) -> object:
        return _validate_attributes(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, value: object) -> object:
        return _normalize_currency(value)

    @model_validator(mode="after")
    def _reject_null_required_fields(self) -> UpdateVariantRequest:
        for field_name in (
            "sku",
            "title",
            "attributes",
            "price_cents",
            "currency",
            "is_active",
            "low_stock_threshold",
        ):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"Field '{field_name}' cannot be null.")
        return self


class AdminInventoryItemDTO(BaseModel):
    """Administrative inventory overview row DTO."""

    variant_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_title: str
    variant_title: str
    product_status: ProductStatus
    is_active: bool
    on_hand: int
    reserved: int
    available_quantity: int
    low_stock_threshold: int
    in_stock: bool
    low_stock: bool
    updated_at: UTCDatetime


class StockAdjustmentRequest(BaseModel):
    """Request payload for POST /api/v1/admin/inventory/{variant_id}/adjust."""

    model_config = ConfigDict(extra="forbid")

    delta_on_hand: int = Field(ge=_INT32_MIN, le=_INT32_MAX)
    note: str = Field(min_length=1, max_length=255)

    @field_validator("note", mode="before")
    @classmethod
    def _validate_note(cls, value: object) -> object:
        return strip_single_line(value, field_name="Adjustment note")


class InventoryLedgerEntryDTO(BaseModel):
    """Append-only inventory stock movement ledger entry DTO."""

    id: uuid.UUID
    variant_id: uuid.UUID
    order_id: uuid.UUID | None
    order_number: str | None
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    reason: str
    delta_on_hand: int
    delta_reserved: int
    on_hand_after: int
    reserved_after: int
    note: str | None
    created_at: UTCDatetime
