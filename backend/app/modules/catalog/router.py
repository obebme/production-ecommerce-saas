"""FastAPI routes for Module 2 (Storefront & Admin Product Catalog)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import MAX_PAGINATION_PAGE, PaginatedResponse, PaginationParams
from app.core.security import parse_client_ip
from app.models.identity import User
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    AdminCategoryDTO,
    AdminCategoryListResponse,
    AdminProductDetailDTO,
    AdminProductSummaryDTO,
    CategoryListResponse,
    CreateCategoryRequest,
    CreateProductRequest,
    ProductDetailDTO,
    ProductSortOrder,
    ProductStatus,
    ProductSummaryDTO,
    UpdateCategoryRequest,
    UpdateProductRequest,
)
from app.modules.identity.dependencies import (
    AuthContext,
    require_roles,
    require_roles_with_csrf,
)

router = APIRouter(tags=["catalog"])

_INT32_MAX = 2_147_483_647


def _client_ip(request: Request) -> str | None:
    return parse_client_ip(request.client.host if request.client else None)


@router.get(
    "/categories",
    status_code=status.HTTP_200_OK,
    response_model=CategoryListResponse,
)
def list_public_categories(
    db: Annotated[Session, Depends(get_db)],
) -> CategoryListResponse:
    """List active storefront categories ordered by sort_order ASC, name ASC."""
    return CategoryListResponse(items=service.list_public_categories(db))


@router.get(
    "/products",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[ProductSummaryDTO],
)
def list_public_products(
    db: Annotated[Session, Depends(get_db)],
    category_slug: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    min_price_cents: Annotated[int | None, Query(ge=0, le=_INT32_MAX)] = None,
    max_price_cents: Annotated[int | None, Query(ge=0, le=_INT32_MAX)] = None,
    in_stock: Annotated[bool | None, Query()] = None,
    sort: Annotated[ProductSortOrder, Query()] = "newest",
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[ProductSummaryDTO]:
    """List paginated active products for storefront browsing and search."""
    return service.list_public_products(
        db,
        category_slug=category_slug,
        q=q,
        min_price_cents=min_price_cents,
        max_price_cents=max_price_cents,
        in_stock=in_stock,
        sort=sort,
        pagination=PaginationParams(page=page, page_size=page_size),
    )


@router.get(
    "/products/{slug}",
    status_code=status.HTTP_200_OK,
    response_model=ProductDetailDTO,
)
def get_public_product(
    slug: Annotated[str, Path(min_length=1)],
    db: Annotated[Session, Depends(get_db)],
) -> ProductDetailDTO:
    """Retrieve a single ACTIVE storefront product by slug."""
    return service.get_public_product_by_slug(db, slug)


@router.get(
    "/admin/categories",
    status_code=status.HTTP_200_OK,
    response_model=AdminCategoryListResponse,
)
def list_admin_categories(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminCategoryListResponse:
    """List all categories (active and inactive) for staff/admin management."""
    return AdminCategoryListResponse(items=service.list_admin_categories(db))


@router.post(
    "/admin/categories",
    status_code=status.HTTP_201_CREATED,
    response_model=AdminCategoryDTO,
)
def create_admin_category(
    payload: CreateCategoryRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminCategoryDTO:
    """Create a product category with slug validation and audit logging."""
    return service.create_category(
        db,
        payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.patch(
    "/admin/categories/{category_id}",
    status_code=status.HTTP_200_OK,
    response_model=AdminCategoryDTO,
)
def update_admin_category(
    category_id: uuid.UUID,
    payload: UpdateCategoryRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminCategoryDTO:
    """Update a category while enforcing cycle prevention and audit logging."""
    return service.update_category(
        db,
        category_id,
        payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.get(
    "/admin/products",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[AdminProductSummaryDTO],
)
def list_admin_products(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[ProductStatus | None, Query(alias="status")] = None,
    category_id: Annotated[uuid.UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[AdminProductSummaryDTO]:
    """List paginated products across DRAFT, ACTIVE, and ARCHIVED statuses."""
    return service.list_admin_products(
        db,
        q=q,
        status=status_filter,
        category_id=category_id,
        pagination=PaginationParams(page=page, page_size=page_size),
    )


@router.get(
    "/admin/products/{product_id}",
    status_code=status.HTTP_200_OK,
    response_model=AdminProductDetailDTO,
)
def get_admin_product(
    product_id: uuid.UUID,
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminProductDetailDTO:
    """Fetch full admin product editor state across any lifecycle status."""
    return service.get_admin_product(db, product_id)


@router.post(
    "/admin/products",
    status_code=status.HTTP_201_CREATED,
    response_model=AdminProductDetailDTO,
)
def create_admin_product(
    payload: CreateProductRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminProductDetailDTO:
    """Create a new product (default DRAFT) with validated image URLs and audit logging."""
    return service.create_admin_product(
        db,
        payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.patch(
    "/admin/products/{product_id}",
    status_code=status.HTTP_200_OK,
    response_model=AdminProductDetailDTO,
)
def update_admin_product(
    product_id: uuid.UUID,
    payload: UpdateProductRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminProductDetailDTO:
    """Update product fields/images/status with active-variant invariant and audit logging."""
    return service.update_admin_product(
        db,
        product_id,
        payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.delete(
    "/admin/products/{product_id}",
    status_code=status.HTTP_200_OK,
    response_model=AdminProductDetailDTO,
)
def archive_admin_product(
    product_id: uuid.UUID,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminProductDetailDTO:
    """Soft-archive a product by setting status = ARCHIVED and recording an audit log."""
    return service.archive_admin_product(
        db,
        product_id,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )
