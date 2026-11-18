"""Pydantic schemas for Module 4 (Shopping Cart)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class AddCartItemRequest(BaseModel):
    """Request payload for POST /api/v1/cart/items."""

    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)


class UpdateCartItemRequest(BaseModel):
    """Request payload for PATCH /api/v1/cart/items/{item_id}."""

    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(ge=1, le=99)


class CartItemDTO(BaseModel):
    """Server-reconciled cart line item projected from live catalog and inventory rows."""

    id: uuid.UUID
    variant_id: uuid.UUID
    product_id: uuid.UUID
    product_slug: str
    product_title: str
    variant_title: str
    sku: str
    image_url: str | None
    unit_price_cents: int
    quantity: int
    line_total_cents: int
    available_quantity: int
    in_stock: bool
    low_stock: bool
    is_available: bool


class CartDTO(BaseModel):
    """Full server-authoritative shopping cart representation."""

    id: uuid.UUID
    currency: str
    items: list[CartItemDTO]
    item_count: int
    subtotal_cents: int
    estimated_shipping_cents: int
    estimated_total_cents: int
