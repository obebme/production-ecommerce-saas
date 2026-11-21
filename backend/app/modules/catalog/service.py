"""Business logic and query services for Module 2 (Product Catalog & Categories)."""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import ColumnElement, case, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.audit import record_audit_log
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.pagination import PaginatedResponse, PaginationParams, escape_like_query
from app.models.catalog import Category, Product, ProductImage
from app.models.inventory import InventoryStock, ProductVariant
from app.modules.catalog.schemas import (
    SLUG_RE,
    AdminCategoryDTO,
    AdminProductDetailDTO,
    AdminProductSummaryDTO,
    AdminVariantDTO,
    CategoryPublicDTO,
    CategorySummaryDTO,
    CreateCategoryRequest,
    CreateProductRequest,
    ProductDetailDTO,
    ProductImageDTO,
    ProductSortOrder,
    ProductStatus,
    ProductSummaryDTO,
    ProductVariantPublicDTO,
    UpdateCategoryRequest,
    UpdateProductRequest,
    ensure_utc_datetime,
)


def _normalize_valid_slug_or_none(raw_slug: str | None, *, max_length: int = 220) -> str | None:
    """Validate and normalize a route/query slug before querying the database."""
    if raw_slug is None or len(raw_slug) > max_length:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_slug):
        return None
    normalized = raw_slug.strip().lower()
    if not normalized or len(normalized) > max_length or not SLUG_RE.fullmatch(normalized):
        return None
    return normalized


def normalize_search_query(raw_q: str | None) -> str | None:
    """Validate and trim optional search string q, returning None when blank."""
    if raw_q is None:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_q):
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="Search query must not contain control characters.",
        )
    cleaned = raw_q.strip()
    return cleaned if cleaned else None


def compute_variant_availability(
    stock: InventoryStock | None,
) -> tuple[int, int, int, int, bool, bool]:
    """Compute (on_hand, reserved, available, threshold, in_stock, low_stock) server-side."""
    on_hand = stock.on_hand if stock is not None else 0
    reserved = stock.reserved if stock is not None else 0
    threshold = stock.low_stock_threshold if stock is not None else 5
    available = max(0, on_hand - reserved)
    in_stock = available > 0
    low_stock = available > 0 and available <= threshold
    return on_hand, reserved, available, threshold, in_stock, low_stock


def to_admin_variant_dto(variant: ProductVariant) -> AdminVariantDTO:
    """Map a ProductVariant (+ optional InventoryStock) to AdminVariantDTO."""
    on_hand, reserved, available, threshold, in_stock, low_stock = compute_variant_availability(
        variant.stock
    )
    return AdminVariantDTO(
        id=variant.id,
        product_id=variant.product_id,
        sku=variant.sku,
        title=variant.title,
        attributes=dict(variant.attributes_json),
        price_cents=variant.price_cents,
        compare_at_price_cents=variant.compare_at_price_cents,
        currency=variant.currency,
        is_active=variant.is_active,
        on_hand=on_hand,
        reserved=reserved,
        available_quantity=available,
        low_stock_threshold=threshold,
        in_stock=in_stock,
        low_stock=low_stock,
        created_at=variant.created_at,
        updated_at=variant.updated_at,
    )


def _select_primary_image_dto(images: Sequence[ProductImage]) -> ProductImageDTO | None:
    """Select the primary image deterministically (is_primary first, then sort_order ASC)."""
    if not images:
        return None
    ordered = sorted(
        images,
        key=lambda img: (not img.is_primary, img.sort_order, str(img.id)),
    )
    return ProductImageDTO.model_validate(ordered[0])


def _ordered_image_dtos(images: Sequence[ProductImage]) -> list[ProductImageDTO]:
    ordered = sorted(images, key=lambda img: (img.sort_order, str(img.id)))
    return [ProductImageDTO.model_validate(img) for img in ordered]


def _category_snapshot(category: Category) -> dict[str, Any]:
    return {
        "id": str(category.id),
        "parent_id": str(category.parent_id) if category.parent_id is not None else None,
        "name": category.name,
        "slug": category.slug,
        "description": category.description,
        "is_active": category.is_active,
        "sort_order": category.sort_order,
    }


def _product_snapshot(product: Product) -> dict[str, Any]:
    ordered_images = sorted(product.images, key=lambda img: (img.sort_order, str(img.id)))
    return {
        "id": str(product.id),
        "category_id": str(product.category_id),
        "title": product.title,
        "slug": product.slug,
        "description": product.description,
        "status": product.status,
        "images": [
            {
                "url": img.url,
                "alt_text": img.alt_text,
                "sort_order": img.sort_order,
                "is_primary": img.is_primary,
            }
            for img in ordered_images
        ],
    }


def _to_admin_product_detail_dto(product: Product) -> AdminProductDetailDTO:
    ordered_variants = sorted(
        product.variants,
        key=lambda variant: (
            ensure_utc_datetime(variant.created_at),
            variant.sku,
            str(variant.id),
        ),
    )
    return AdminProductDetailDTO(
        id=product.id,
        title=product.title,
        slug=product.slug,
        description=product.description,
        status=cast(ProductStatus, product.status),
        category=CategorySummaryDTO.model_validate(product.category),
        images=_ordered_image_dtos(product.images),
        variants=[to_admin_variant_dto(variant) for variant in ordered_variants],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _load_product_with_relations(
    db: Session,
    product_id: uuid.UUID,
    *,
    populate_existing: bool = False,
) -> Product | None:
    stmt = (
        select(Product)
        .where(Product.id == product_id)
        .options(
            joinedload(Product.category),
            joinedload(Product.images),
            joinedload(Product.variants).joinedload(ProductVariant.stock),
        )
    )
    if populate_existing:
        stmt = stmt.execution_options(populate_existing=True)
    return db.execute(stmt).unique().scalar_one_or_none()


def _resolve_active_category_subtree_ids(
    db: Session, category_slug: str
) -> list[uuid.UUID]:
    """Return the active category ID matching slug plus all active descendant category IDs."""
    normalized_slug = category_slug.strip().lower()
    if not normalized_slug:
        return []

    rows = db.execute(
        select(Category.id, Category.parent_id, Category.slug, Category.is_active)
    ).all()

    target_id: uuid.UUID | None = None
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]] = {}
    for cat_id, parent_id, slug, is_active in rows:
        if not is_active:
            continue
        if slug == normalized_slug:
            target_id = cat_id
        if parent_id is not None:
            children_by_parent.setdefault(parent_id, []).append(cat_id)

    if target_id is None:
        return []

    visited: set[uuid.UUID] = set()
    queue: deque[uuid.UUID] = deque([target_id])
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for child_id in children_by_parent.get(current, ()):
            if child_id not in visited:
                queue.append(child_id)
    return list(visited)


def list_public_categories(db: Session) -> list[CategoryPublicDTO]:
    """List active storefront categories ordered by sort_order ASC, name ASC."""
    rows = db.scalars(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order.asc(), Category.name.asc(), Category.id.asc())
    ).all()
    return [CategoryPublicDTO.model_validate(row) for row in rows]


def list_public_products(
    db: Session,
    *,
    category_slug: str | None = None,
    q: str | None = None,
    min_price_cents: int | None = None,
    max_price_cents: int | None = None,
    in_stock: bool | None = None,
    sort: ProductSortOrder = "newest",
    pagination: PaginationParams,
) -> PaginatedResponse[ProductSummaryDTO]:
    """List paginated ACTIVE products in active categories with server-derived price/stock."""
    from app.modules.inventory.service import reclaim_expired_if_any

    cleaned_q = normalize_search_query(q)
    reclaim_expired_if_any(db)

    filters: list[ColumnElement[bool]] = [
        Product.status == "ACTIVE",
        Category.is_active.is_(True),
    ]

    if category_slug is not None and category_slug.strip():
        normalized_cat_slug = _normalize_valid_slug_or_none(category_slug, max_length=140)
        if normalized_cat_slug is None:
            return PaginatedResponse(
                items=[],
                total=0,
                page=pagination.page,
                page_size=pagination.page_size,
                has_next=False,
            )
        category_ids = _resolve_active_category_subtree_ids(db, normalized_cat_slug)
        if not category_ids:
            return PaginatedResponse(
                items=[],
                total=0,
                page=pagination.page,
                page_size=pagination.page_size,
                has_next=False,
            )
        filters.append(Product.category_id.in_(category_ids))

    if cleaned_q is not None:
        pattern = f"%{escape_like_query(cleaned_q)}%"
        sku_match = exists(
            select(ProductVariant.id).where(
                ProductVariant.product_id == Product.id,
                ProductVariant.is_active.is_(True),
                ProductVariant.sku.ilike(pattern, escape="\\"),
            )
        )
        filters.append(
            or_(
                Product.title.ilike(pattern, escape="\\"),
                Product.description.ilike(pattern, escape="\\"),
                sku_match,
            )
        )

    if min_price_cents is not None or max_price_cents is not None:
        price_conds: list[ColumnElement[bool]] = [
            ProductVariant.product_id == Product.id,
            ProductVariant.is_active.is_(True),
        ]
        if min_price_cents is not None:
            price_conds.append(ProductVariant.price_cents >= min_price_cents)
        if max_price_cents is not None:
            price_conds.append(ProductVariant.price_cents <= max_price_cents)
        filters.append(exists(select(ProductVariant.id).where(*price_conds)))

    avail_expr = case(
        (
            func.coalesce(InventoryStock.on_hand, 0)
            - func.coalesce(InventoryStock.reserved, 0)
            > 0,
            func.coalesce(InventoryStock.on_hand, 0)
            - func.coalesce(InventoryStock.reserved, 0),
        ),
        else_=0,
    )
    variant_agg = (
        select(
            ProductVariant.product_id.label("product_id"),
            func.min(ProductVariant.price_cents).label("min_price_cents"),
            func.max(ProductVariant.price_cents).label("max_price_cents"),
            func.min(ProductVariant.currency).label("currency"),
            func.max(case((avail_expr > 0, 1), else_=0)).label("has_stock"),
        )
        .outerjoin(InventoryStock, InventoryStock.variant_id == ProductVariant.id)
        .where(ProductVariant.is_active.is_(True))
        .group_by(ProductVariant.product_id)
        .subquery()
    )

    if in_stock is True:
        filters.append(variant_agg.c.has_stock == 1)
    elif in_stock is False:
        filters.append(variant_agg.c.has_stock == 0)

    total = int(
        db.scalar(
            select(func.count(Product.id))
            .select_from(Product)
            .join(Category, Category.id == Product.category_id)
            .join(variant_agg, variant_agg.c.product_id == Product.id)
            .where(*filters)
        )
        or 0
    )

    if total == 0 or pagination.offset >= total:
        return PaginatedResponse(
            items=[],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            has_next=False,
        )

    sort_clauses: tuple[Any, ...]
    if sort == "price_asc":
        sort_clauses = (
            variant_agg.c.min_price_cents.asc(),
            Product.created_at.desc(),
            Product.id.asc(),
        )
    elif sort == "price_desc":
        sort_clauses = (
            variant_agg.c.min_price_cents.desc(),
            Product.created_at.desc(),
            Product.id.asc(),
        )
    elif sort == "title_asc":
        sort_clauses = (
            func.lower(Product.title).asc(),
            Product.title.asc(),
            Product.id.asc(),
        )
    else:
        sort_clauses = (Product.created_at.desc(), Product.id.asc())

    rows = db.execute(
        select(
            Product,
            variant_agg.c.min_price_cents,
            variant_agg.c.max_price_cents,
            variant_agg.c.currency,
            variant_agg.c.has_stock,
        )
        .join(Category, Category.id == Product.category_id)
        .join(variant_agg, variant_agg.c.product_id == Product.id)
        .options(
            joinedload(Product.category),
            selectinload(Product.images),
        )
        .where(*filters)
        .order_by(*sort_clauses)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    items: list[ProductSummaryDTO] = []
    for product, min_price, max_price, currency, has_stock in rows:
        items.append(
            ProductSummaryDTO(
                id=product.id,
                title=product.title,
                slug=product.slug,
                category=CategorySummaryDTO.model_validate(product.category),
                primary_image=_select_primary_image_dto(product.images),
                min_price_cents=int(min_price),
                max_price_cents=int(max_price),
                currency=str(currency),
                in_stock=bool(has_stock),
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )


def get_public_product_by_slug(db: Session, slug: str) -> ProductDetailDTO:
    """Fetch an ACTIVE storefront product by slug with active variants and availability."""
    normalized_slug = _normalize_valid_slug_or_none(slug, max_length=220)
    if normalized_slug is None:
        raise NotFoundError(
            code="PRODUCT_NOT_FOUND",
            message="Product not found.",
        )

    product = (
        db.execute(
            select(Product)
            .join(Category, Category.id == Product.category_id)
            .where(
                Product.slug == normalized_slug,
                Product.status == "ACTIVE",
                Category.is_active.is_(True),
            )
            .options(
                joinedload(Product.category),
                joinedload(Product.images),
                joinedload(Product.variants).joinedload(ProductVariant.stock),
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    if product is None:
        raise NotFoundError(
            code="PRODUCT_NOT_FOUND",
            message="Product not found.",
        )

    active_variants = sorted(
        (variant for variant in product.variants if variant.is_active),
        key=lambda variant: (
            ensure_utc_datetime(variant.created_at),
            variant.sku,
            str(variant.id),
        ),
    )
    if not active_variants:
        raise NotFoundError(
            code="PRODUCT_NOT_FOUND",
            message="Product not found.",
        )

    from app.modules.inventory.service import reclaim_expired_reservations_for_variants

    active_variant_ids = [variant.id for variant in active_variants]
    if reclaim_expired_reservations_for_variants(db, active_variant_ids) > 0:
        db.commit()
        product = (
            db.execute(
                select(Product)
                .join(Category, Category.id == Product.category_id)
                .where(
                    Product.slug == normalized_slug,
                    Product.status == "ACTIVE",
                    Category.is_active.is_(True),
                )
                .options(
                    joinedload(Product.category),
                    joinedload(Product.images),
                    joinedload(Product.variants).joinedload(ProductVariant.stock),
                )
                .execution_options(populate_existing=True)
            )
            .unique()
            .scalar_one_or_none()
        )
        if product is None:
            raise NotFoundError(
                code="PRODUCT_NOT_FOUND",
                message="Product not found.",
            )
        active_variants = sorted(
            (variant for variant in product.variants if variant.is_active),
            key=lambda variant: (
                ensure_utc_datetime(variant.created_at),
                variant.sku,
                str(variant.id),
            ),
        )
        if not active_variants:
            raise NotFoundError(
                code="PRODUCT_NOT_FOUND",
                message="Product not found.",
            )

    variant_dtos: list[ProductVariantPublicDTO] = []
    for variant in active_variants:
        _, _, available, _, in_stock, low_stock = compute_variant_availability(variant.stock)
        variant_dtos.append(
            ProductVariantPublicDTO(
                id=variant.id,
                sku=variant.sku,
                title=variant.title,
                attributes=dict(variant.attributes_json),
                price_cents=variant.price_cents,
                compare_at_price_cents=variant.compare_at_price_cents,
                currency=variant.currency,
                is_active=variant.is_active,
                available_quantity=available,
                in_stock=in_stock,
                low_stock=low_stock,
            )
        )

    return ProductDetailDTO(
        id=product.id,
        title=product.title,
        slug=product.slug,
        description=product.description,
        status=cast(ProductStatus, product.status),
        category=CategorySummaryDTO.model_validate(product.category),
        images=_ordered_image_dtos(product.images),
        variants=variant_dtos,
    )


def list_admin_categories(db: Session) -> list[AdminCategoryDTO]:
    """List all categories (active and inactive) ordered by sort_order ASC, name ASC."""
    rows = db.scalars(
        select(Category).order_by(Category.sort_order.asc(), Category.name.asc(), Category.id.asc())
    ).all()
    return [AdminCategoryDTO.model_validate(row) for row in rows]


def create_category(
    db: Session,
    payload: CreateCategoryRequest,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminCategoryDTO:
    """Create a category with unique slug validation and audit logging."""
    if payload.parent_id is not None:
        parent = db.scalar(select(Category.id).where(Category.id == payload.parent_id))
        if parent is None:
            raise NotFoundError(
                code="CATEGORY_NOT_FOUND",
                message="Parent category not found.",
            )

    existing_slug = db.scalar(select(Category.id).where(Category.slug == payload.slug))
    if existing_slug is not None:
        raise ConflictError(
            code="SLUG_CONFLICT",
            message="A category with this slug already exists.",
        )

    category = Category(
        parent_id=payload.parent_id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(category)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            code="SLUG_CONFLICT",
            message="A category with this slug already exists.",
        ) from exc

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="CATEGORY_CREATED",
        resource_type="category",
        resource_id=str(category.id),
        before_state=None,
        after_state=_category_snapshot(category),
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(category)
    return AdminCategoryDTO.model_validate(category)


def update_category(
    db: Session,
    category_id: uuid.UUID,
    payload: UpdateCategoryRequest,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminCategoryDTO:
    """Update a category while enforcing cycle detection, slug uniqueness, and audit logging."""
    fields = payload.model_fields_set
    if "parent_id" in fields and payload.parent_id is not None:
        if payload.parent_id == category_id:
            raise ValidationAppError(
                code="CATEGORY_CYCLE_DETECTED",
                message="A category cannot be its own parent.",
            )
        # Lock categories in ID order so concurrent reparents cannot form 2-cycles.
        locked_categories = list(
            db.scalars(
                select(Category)
                .order_by(Category.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        categories_by_id = {cat.id: cat for cat in locked_categories}
        category = categories_by_id.get(category_id)
        if category is None:
            raise NotFoundError(
                code="CATEGORY_NOT_FOUND",
                message="Category not found.",
            )
        if payload.parent_id not in categories_by_id:
            raise NotFoundError(
                code="CATEGORY_NOT_FOUND",
                message="Parent category not found.",
            )
        parent_map: dict[uuid.UUID, uuid.UUID | None] = {
            cid: cat.parent_id for cid, cat in categories_by_id.items()
        }
        cursor: uuid.UUID | None = payload.parent_id
        visited: set[uuid.UUID] = set()
        while cursor is not None:
            if cursor == category_id:
                raise ValidationAppError(
                    code="CATEGORY_CYCLE_DETECTED",
                    message="Selected parent would create a category cycle.",
                )
            if cursor in visited:
                break
            visited.add(cursor)
            cursor = parent_map.get(cursor)
    else:
        category = db.scalar(
            select(Category)
            .where(Category.id == category_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if category is None:
            raise NotFoundError(
                code="CATEGORY_NOT_FOUND",
                message="Category not found.",
            )

    before_state = _category_snapshot(category)

    if "slug" in fields and payload.slug is not None and payload.slug != category.slug:
        conflict_id = db.scalar(
            select(Category.id).where(
                Category.slug == payload.slug,
                Category.id != category_id,
            )
        )
        if conflict_id is not None:
            raise ConflictError(
                code="SLUG_CONFLICT",
                message="A category with this slug already exists.",
            )
        category.slug = payload.slug

    if "parent_id" in fields:
        category.parent_id = payload.parent_id
    if "name" in fields and payload.name is not None:
        category.name = payload.name
    if "description" in fields:
        category.description = payload.description
    if "is_active" in fields and payload.is_active is not None:
        category.is_active = payload.is_active
    if "sort_order" in fields and payload.sort_order is not None:
        category.sort_order = payload.sort_order

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            code="SLUG_CONFLICT",
            message="A category with this slug already exists.",
        ) from exc

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="CATEGORY_UPDATED",
        resource_type="category",
        resource_id=str(category.id),
        before_state=before_state,
        after_state=_category_snapshot(category),
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(category)
    return AdminCategoryDTO.model_validate(category)


def list_admin_products(
    db: Session,
    *,
    q: str | None = None,
    status: ProductStatus | None = None,
    category_id: uuid.UUID | None = None,
    pagination: PaginationParams,
) -> PaginatedResponse[AdminProductSummaryDTO]:
    """List paginated admin products across DRAFT, ACTIVE, and ARCHIVED statuses."""
    cleaned_q = normalize_search_query(q)

    filters: list[ColumnElement[bool]] = []
    if status is not None:
        filters.append(Product.status == status)
    if category_id is not None:
        filters.append(Product.category_id == category_id)
    if cleaned_q is not None:
        pattern = f"%{escape_like_query(cleaned_q)}%"
        sku_match = exists(
            select(ProductVariant.id).where(
                ProductVariant.product_id == Product.id,
                ProductVariant.sku.ilike(pattern, escape="\\"),
            )
        )
        filters.append(
            or_(
                Product.title.ilike(pattern, escape="\\"),
                Product.slug.ilike(pattern, escape="\\"),
                Product.description.ilike(pattern, escape="\\"),
                sku_match,
            )
        )

    total = int(db.scalar(select(func.count(Product.id)).where(*filters)) or 0)
    if total == 0 or pagination.offset >= total:
        return PaginatedResponse(
            items=[],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            has_next=False,
        )

    avail_expr = case(
        (
            func.coalesce(InventoryStock.on_hand, 0)
            - func.coalesce(InventoryStock.reserved, 0)
            > 0,
            func.coalesce(InventoryStock.on_hand, 0)
            - func.coalesce(InventoryStock.reserved, 0),
        ),
        else_=0,
    )
    active_avail_expr = case((ProductVariant.is_active.is_(True), avail_expr), else_=0)

    variant_agg = (
        select(
            ProductVariant.product_id.label("product_id"),
            func.count(ProductVariant.id).label("variants_count"),
            func.coalesce(
                func.sum(case((ProductVariant.is_active.is_(True), 1), else_=0)), 0
            ).label("active_variants_count"),
            func.min(ProductVariant.price_cents).label("min_price_cents"),
            func.max(ProductVariant.price_cents).label("max_price_cents"),
            func.min(ProductVariant.currency).label("currency"),
            func.coalesce(func.sum(active_avail_expr), 0).label("total_available_quantity"),
        )
        .outerjoin(InventoryStock, InventoryStock.variant_id == ProductVariant.id)
        .group_by(ProductVariant.product_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Product,
            variant_agg.c.variants_count,
            variant_agg.c.active_variants_count,
            variant_agg.c.min_price_cents,
            variant_agg.c.max_price_cents,
            variant_agg.c.currency,
            variant_agg.c.total_available_quantity,
        )
        .outerjoin(variant_agg, variant_agg.c.product_id == Product.id)
        .options(
            joinedload(Product.category),
            selectinload(Product.images),
        )
        .where(*filters)
        .order_by(Product.created_at.desc(), Product.id.asc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    items: list[AdminProductSummaryDTO] = []
    for (
        product,
        variants_count,
        active_variants_count,
        min_price,
        max_price,
        currency,
        total_avail,
    ) in rows:
        avail_int = int(total_avail or 0)
        items.append(
            AdminProductSummaryDTO(
                id=product.id,
                title=product.title,
                slug=product.slug,
                status=cast(ProductStatus, product.status),
                category=CategorySummaryDTO.model_validate(product.category),
                primary_image=_select_primary_image_dto(product.images),
                min_price_cents=int(min_price) if min_price is not None else None,
                max_price_cents=int(max_price) if max_price is not None else None,
                currency=str(currency) if currency is not None else None,
                variants_count=int(variants_count or 0),
                active_variants_count=int(active_variants_count or 0),
                total_available_quantity=avail_int,
                in_stock=avail_int > 0,
                created_at=product.created_at,
                updated_at=product.updated_at,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )


def get_admin_product(db: Session, product_id: uuid.UUID) -> AdminProductDetailDTO:
    """Fetch a single product across any status for admin editing."""
    product = _load_product_with_relations(db, product_id)
    if product is None:
        raise NotFoundError(
            code="PRODUCT_NOT_FOUND",
            message="Product not found.",
        )
    return _to_admin_product_detail_dto(product)


def create_admin_product(
    db: Session,
    payload: CreateProductRequest,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminProductDetailDTO:
    """Create a product (default DRAFT; rejects ACTIVE without active variants) + images."""
    category_exists = db.scalar(select(Category.id).where(Category.id == payload.category_id))
    if category_exists is None:
        raise NotFoundError(
            code="CATEGORY_NOT_FOUND",
            message="Category not found.",
        )

    if payload.status == "ACTIVE":
        raise ValidationAppError(
            code="PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT",
            message="A product must have at least one active variant before activation.",
        )

    slug_exists = db.scalar(select(Product.id).where(Product.slug == payload.slug))
    if slug_exists is not None:
        raise ConflictError(
            code="SLUG_CONFLICT",
            message="A product with this slug already exists.",
        )

    product = Product(
        category_id=payload.category_id,
        title=payload.title,
        slug=payload.slug,
        description=payload.description,
        status=payload.status,
    )
    db.add(product)
    try:
        db.flush()
        for img_input in payload.images:
            product.images.append(
                ProductImage(
                    product_id=product.id,
                    url=img_input.url,
                    alt_text=img_input.alt_text,
                    sort_order=img_input.sort_order,
                    is_primary=img_input.is_primary,
                )
            )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            code="SLUG_CONFLICT",
            message="A product with this slug already exists.",
        ) from exc

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="PRODUCT_CREATED",
        resource_type="product",
        resource_id=str(product.id),
        before_state=None,
        after_state=_product_snapshot(product),
        ip_address=ip_address,
    )
    db.commit()

    created = _load_product_with_relations(db, product.id)
    assert created is not None
    return _to_admin_product_detail_dto(created)


def update_admin_product(
    db: Session,
    product_id: uuid.UUID,
    payload: UpdateProductRequest,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminProductDetailDTO:
    """Update product fields/images/status while enforcing active-variant invariant and auditing."""
    locked_product = db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_product is None:
        raise NotFoundError(
            code="PRODUCT_NOT_FOUND",
            message="Product not found.",
        )

    product = _load_product_with_relations(db, product_id, populate_existing=True)
    assert product is not None

    before_state = _product_snapshot(product)
    fields = payload.model_fields_set

    if (
        "category_id" in fields
        and payload.category_id is not None
        and payload.category_id != product.category_id
    ):
        cat_exists = db.scalar(select(Category.id).where(Category.id == payload.category_id))
        if cat_exists is None:
            raise NotFoundError(
                code="CATEGORY_NOT_FOUND",
                message="Category not found.",
            )
        product.category_id = payload.category_id

    if "slug" in fields and payload.slug is not None and payload.slug != product.slug:
        conflict_id = db.scalar(
            select(Product.id).where(
                Product.slug == payload.slug,
                Product.id != product_id,
            )
        )
        if conflict_id is not None:
            raise ConflictError(
                code="SLUG_CONFLICT",
                message="A product with this slug already exists.",
            )
        product.slug = payload.slug

    target_status = (
        payload.status
        if "status" in fields and payload.status is not None
        else product.status
    )
    if target_status == "ACTIVE" and not any(variant.is_active for variant in product.variants):
        raise ValidationAppError(
            code="PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT",
            message="A product must have at least one active variant before activation.",
        )

    if "title" in fields and payload.title is not None:
        product.title = payload.title
    if "description" in fields and payload.description is not None:
        product.description = payload.description
    if "status" in fields and payload.status is not None:
        product.status = payload.status

    if "images" in fields and payload.images is not None:
        # Flush deletion of old images first so uq_product_primary_image does not collide mid-flush.
        product.images.clear()
        db.flush()
        for img_input in payload.images:
            product.images.append(
                ProductImage(
                    product_id=product.id,
                    url=img_input.url,
                    alt_text=img_input.alt_text,
                    sort_order=img_input.sort_order,
                    is_primary=img_input.is_primary,
                )
            )

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            code="SLUG_CONFLICT",
            message="A product with this slug already exists.",
        ) from exc

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="PRODUCT_UPDATED",
        resource_type="product",
        resource_id=str(product.id),
        before_state=before_state,
        after_state=_product_snapshot(product),
        ip_address=ip_address,
    )
    db.commit()

    updated = _load_product_with_relations(db, product_id)
    assert updated is not None
    return _to_admin_product_detail_dto(updated)


def archive_admin_product(
    db: Session,
    product_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminProductDetailDTO:
    """Soft-archive a product by setting status = 'ARCHIVED' and recording an audit log."""
    locked_product = db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_product is None:
        raise NotFoundError(
            code="PRODUCT_NOT_FOUND",
            message="Product not found.",
        )

    product = _load_product_with_relations(db, product_id, populate_existing=True)
    assert product is not None

    before_state = _product_snapshot(product)
    product.status = "ARCHIVED"
    db.flush()

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="PRODUCT_ARCHIVED",
        resource_type="product",
        resource_id=str(product.id),
        before_state=before_state,
        after_state=_product_snapshot(product),
        ip_address=ip_address,
    )
    db.commit()

    archived = _load_product_with_relations(db, product_id)
    assert archived is not None
    return _to_admin_product_detail_dto(archived)
