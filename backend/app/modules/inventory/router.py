"""FastAPI routes for Module 3 (Storefront Availability & Admin Variant/Inventory Management)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import MAX_PAGINATION_PAGE, PaginatedResponse, PaginationParams
from app.core.security import resolve_client_ip
from app.models.identity import User
from app.modules.catalog.schemas import AdminVariantDTO
from app.modules.identity.dependencies import (
    AuthContext,
    require_roles,
    require_roles_with_csrf,
)
from app.modules.inventory import service
from app.modules.inventory.schemas import (
    AdminInventoryItemDTO,
    CreateVariantRequest,
    InventoryLedgerEntryDTO,
    StockAdjustmentRequest,
    StockStatusFilter,
    UpdateVariantRequest,
    VariantAvailabilityDTO,
)

router = APIRouter(tags=["inventory"])


def _client_ip(request: Request) -> str | None:
    return resolve_client_ip(request)


@router.get(
    "/variants/{variant_id}/availability",
    status_code=status.HTTP_200_OK,
    response_model=VariantAvailabilityDTO,
)
def get_variant_availability(
    variant_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> VariantAvailabilityDTO:
    """Reclaim expired reservations and return live server-derived variant availability."""
    return service.get_variant_availability(db, variant_id)


@router.post(
    "/admin/products/{product_id}/variants",
    status_code=status.HTTP_201_CREATED,
    response_model=AdminVariantDTO,
)
def create_admin_variant(
    product_id: uuid.UUID,
    payload: CreateVariantRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminVariantDTO:
    """Create a product variant, its 1:1 inventory stock row, INITIAL_SEED ledger, and audit log."""
    return service.create_variant(
        db,
        product_id,
        payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.patch(
    "/admin/variants/{variant_id}",
    status_code=status.HTTP_200_OK,
    response_model=AdminVariantDTO,
)
def update_admin_variant(
    variant_id: uuid.UUID,
    payload: UpdateVariantRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminVariantDTO:
    """Update variant attributes, pricing, active status, and low-stock threshold."""
    return service.update_variant(
        db,
        variant_id,
        payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.get(
    "/admin/inventory",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[AdminInventoryItemDTO],
)
def list_admin_inventory(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    stock_status: Annotated[StockStatusFilter, Query()] = "all",
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[AdminInventoryItemDTO]:
    """List paginated inventory overview rows with search and stock-status filters."""
    return service.list_admin_inventory(
        db,
        q=q,
        stock_status=stock_status,
        pagination=PaginationParams(page=page, page_size=page_size),
    )


@router.post(
    "/admin/inventory/{variant_id}/adjust",
    status_code=status.HTTP_200_OK,
    response_model=AdminInventoryItemDTO,
)
def adjust_admin_inventory(
    variant_id: uuid.UUID,
    payload: StockAdjustmentRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminInventoryItemDTO:
    """Apply a manual stock adjustment with row locking, ledger append, and audit logging."""
    return service.adjust_inventory_stock(
        db,
        variant_id,
        payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.get(
    "/admin/inventory/{variant_id}/ledger",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[InventoryLedgerEntryDTO],
)
def list_admin_inventory_ledger(
    variant_id: uuid.UUID,
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[InventoryLedgerEntryDTO]:
    """List paginated append-only inventory ledger entries ordered newest first."""
    return service.list_variant_ledger(
        db,
        variant_id,
        pagination=PaginationParams(page=page, page_size=page_size),
    )
