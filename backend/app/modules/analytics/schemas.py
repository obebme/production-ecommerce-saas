"""Pydantic schemas for Module 8 (Admin Sales & Inventory Health Analytics)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel

from app.core.pagination import PaginatedResponse
from app.modules.orders.schemas import OrderStatus

TopProductSortBy = Literal["revenue", "units"]


class AnalyticsSummaryResponse(BaseModel):
    """Response payload for GET /api/v1/admin/analytics/summary."""

    from_date: str
    to_date: str
    gross_revenue_cents: int
    refunded_revenue_cents: int
    net_revenue_cents: int
    paid_orders_count: int
    average_order_value_cents: int
    orders_by_status: dict[OrderStatus, int]


class TimeseriesPointDTO(BaseModel):
    """Single UTC calendar day metric point."""

    date: str
    net_revenue_cents: int
    paid_orders_count: int


class AnalyticsTimeseriesResponse(BaseModel):
    """Response payload for GET /api/v1/admin/analytics/timeseries."""

    from_date: str
    to_date: str
    points: list[TimeseriesPointDTO]


class TopProductDTO(BaseModel):
    """Top-selling variant snapshot aggregate."""

    variant_id: uuid.UUID
    product_id: uuid.UUID
    sku_snapshot: str
    product_title_snapshot: str
    variant_title_snapshot: str
    units_sold: int
    revenue_cents: int


class TopProductsResponse(BaseModel):
    """Response payload for GET /api/v1/admin/analytics/top-products."""

    from_date: str
    to_date: str
    items: list[TopProductDTO]


class InventoryHealthSummaryDTO(BaseModel):
    """Global active-SKU inventory health summary counts."""

    active_skus_count: int
    out_of_stock_count: int
    low_stock_count: int
    total_reserved_units: int


class InventoryHealthAlertDTO(BaseModel):
    """Low-stock or out-of-stock active SKU alert item."""

    variant_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_title: str
    variant_title: str
    on_hand: int
    reserved: int
    available_quantity: int
    low_stock_threshold: int
    in_stock: bool
    low_stock: bool


class InventoryHealthResponse(BaseModel):
    """Response payload for GET /api/v1/admin/analytics/inventory-health."""

    summary: InventoryHealthSummaryDTO
    alerts: PaginatedResponse[InventoryHealthAlertDTO]
