"""FastAPI routes for Module 8 (/api/v1/admin/analytics/*)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import MAX_PAGINATION_PAGE, PaginationParams
from app.models.identity import User
from app.modules.analytics import service
from app.modules.analytics.schemas import (
    AnalyticsSummaryResponse,
    AnalyticsTimeseriesResponse,
    InventoryHealthResponse,
    TopProductSortBy,
    TopProductsResponse,
)
from app.modules.identity.dependencies import require_roles

router = APIRouter(prefix="/admin/analytics", tags=["analytics"])


@router.get(
    "/summary",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsSummaryResponse,
)
def get_analytics_summary(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    from_param: Annotated[str | None, Query(alias="from")] = None,
    to_param: Annotated[str | None, Query(alias="to")] = None,
) -> AnalyticsSummaryResponse:
    """Return UTC sales KPI summary and order status breakdown."""
    return service.get_analytics_summary(
        db,
        from_raw=from_param,
        to_raw=to_param,
    )


@router.get(
    "/timeseries",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsTimeseriesResponse,
)
def get_analytics_timeseries(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    from_param: Annotated[str | None, Query(alias="from")] = None,
    to_param: Annotated[str | None, Query(alias="to")] = None,
) -> AnalyticsTimeseriesResponse:
    """Return continuous zero-filled daily UTC net revenue and paid order counts."""
    return service.get_analytics_timeseries(
        db,
        from_raw=from_param,
        to_raw=to_param,
    )


@router.get(
    "/top-products",
    status_code=status.HTTP_200_OK,
    response_model=TopProductsResponse,
)
def get_analytics_top_products(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    from_param: Annotated[str | None, Query(alias="from")] = None,
    to_param: Annotated[str | None, Query(alias="to")] = None,
    sort_by: Annotated[TopProductSortBy, Query()] = "revenue",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> TopProductsResponse:
    """Return top-performing product variants ranked by revenue or units sold."""
    return service.get_analytics_top_products(
        db,
        from_raw=from_param,
        to_raw=to_param,
        sort_by=sort_by,
        limit=limit,
    )


@router.get(
    "/inventory-health",
    status_code=status.HTTP_200_OK,
    response_model=InventoryHealthResponse,
)
def get_inventory_health(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> InventoryHealthResponse:
    """Return active SKU inventory health summary and paginated low/out-of-stock alerts."""
    return service.get_inventory_health(
        db,
        pagination=PaginationParams(page=page, page_size=page_size),
    )
