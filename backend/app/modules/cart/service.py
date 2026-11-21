"""Business logic, single-query DTO projection, and transactional cart mutations for Module 4."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import ts_lt
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.security import generate_session_token, hash_token
from app.models.cart import Cart, CartItem
from app.models.catalog import Category, Product, ProductImage
from app.models.inventory import InventoryStock, ProductVariant
from app.models.order import Order
from app.modules.cart.schemas import AddCartItemRequest, CartDTO, CartItemDTO, UpdateCartItemRequest
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.catalog.service import compute_variant_availability
from app.modules.inventory.service import reclaim_expired_reservations_for_variants

GUEST_CART_TTL = timedelta(days=30)
GUEST_CART_MAX_AGE_SECONDS = int(GUEST_CART_TTL.total_seconds())
MAX_CART_ITEM_QUANTITY = 99


def _new_cart_expiry(now: datetime) -> datetime:
    return now + GUEST_CART_TTL


def _create_user_cart_safe(db: Session, user_id: uuid.UUID, *, now: datetime) -> Cart:
    """Create an ACTIVE cart for user_id, handling concurrent insert races safely."""
    cart = Cart(
        user_id=user_id,
        guest_token_hash=None,
        status="ACTIVE",
        currency="USD",
        expires_at=_new_cart_expiry(now),
    )
    try:
        with db.begin_nested():
            db.add(cart)
            db.flush()
        return cart
    except IntegrityError:
        existing = db.scalar(
            select(Cart).where(Cart.user_id == user_id, Cart.status == "ACTIVE")
        )
        if existing is None:
            raise
        return existing


def _resolve_active_user_cart(
    db: Session,
    user_id: uuid.UUID,
    *,
    now: datetime,
    lock: bool = False,
) -> tuple[Cart, bool]:
    """Return the user's ACTIVE unexpired cart and whether DB state was mutated."""
    stmt = select(Cart).where(Cart.user_id == user_id, Cart.status == "ACTIVE")
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    cart = db.scalar(stmt)
    mutated = False
    if cart is not None and ensure_utc_datetime(cart.expires_at) <= now:
        cart.status = "ABANDONED"
        db.flush()
        mutated = True
        cart = None
    if cart is None:
        cart = _create_user_cart_safe(db, user_id, now=now)
        mutated = True
        if lock:
            locked = db.scalar(
                select(Cart)
                .where(Cart.id == cart.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            assert locked is not None
            return locked, mutated
    return cart, mutated


def _resolve_active_guest_cart(
    db: Session,
    raw_guest_token: str | None,
    *,
    now: datetime,
    lock: bool = False,
) -> tuple[Cart | None, bool]:
    """Resolve an active unexpired guest cart and whether DB state was mutated."""
    if not raw_guest_token:
        return None, False
    token_digest = hash_token(raw_guest_token)
    stmt = select(Cart).where(Cart.guest_token_hash == token_digest)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    existing = db.scalar(stmt)
    if existing is not None and existing.user_id is None and existing.status == "ACTIVE":
        if ensure_utc_datetime(existing.expires_at) > now:
            return existing, False
        existing.status = "ABANDONED"
        db.flush()
        return None, True
    return None, False


def _get_or_create_caller_cart(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    raw_guest_token: str | None,
    now: datetime,
    lock: bool = False,
) -> tuple[Cart, str | None, bool]:
    """Resolve or lazily create an ACTIVE cart, returning (cart, new_guest_token, mutated)."""
    if user_id is not None:
        user_cart, mutated = _resolve_active_user_cart(db, user_id, now=now, lock=lock)
        return user_cart, None, mutated

    existing, mutated = _resolve_active_guest_cart(
        db,
        raw_guest_token,
        now=now,
        lock=lock,
    )
    if existing is not None:
        return existing, None, mutated

    new_raw_token = generate_session_token()
    cart = Cart(
        user_id=None,
        guest_token_hash=hash_token(new_raw_token),
        status="ACTIVE",
        currency="USD",
        expires_at=_new_cart_expiry(now),
    )
    db.add(cart)
    db.flush()
    return cart, new_raw_token, True


def _fetch_cart_item_rows(
    db: Session,
    cart_id: uuid.UUID,
) -> list[tuple[CartItem, ProductVariant, Product, Category, InventoryStock | None, str | None]]:
    """Fetch all cart items with live variant, product, category, stock, and primary image."""
    primary_image_sq = (
        select(ProductImage.url)
        .where(ProductImage.product_id == Product.id)
        .order_by(
            ProductImage.is_primary.desc(),
            ProductImage.sort_order.asc(),
            ProductImage.id.asc(),
        )
        .limit(1)
        .correlate(Product)
        .scalar_subquery()
    )
    rows = db.execute(
        select(
            CartItem,
            ProductVariant,
            Product,
            Category,
            InventoryStock,
            primary_image_sq.label("primary_image_url"),
        )
        .join(ProductVariant, ProductVariant.id == CartItem.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .outerjoin(InventoryStock, InventoryStock.variant_id == ProductVariant.id)
        .where(CartItem.cart_id == cart_id)
        .order_by(CartItem.created_at.asc(), CartItem.id.asc())
        .execution_options(populate_existing=True)
    ).all()
    return [
        (item, variant, product, category, stock, image_url)
        for item, variant, product, category, stock, image_url in rows
    ]


def _estimate_tax_cents(subtotal_cents: int, tax_rate_bps: int) -> int:
    if subtotal_cents <= 0 or tax_rate_bps <= 0:
        return 0
    return (subtotal_cents * tax_rate_bps + 5000) // 10000


def _empty_cart_dto(settings: Settings) -> CartDTO:
    return CartDTO(
        id=uuid.UUID(int=0),
        currency="USD",
        items=[],
        item_count=0,
        subtotal_cents=0,
        estimated_shipping_cents=0,
        tax_rate_bps=settings.tax_rate_bps,
        estimated_tax_cents=0,
        estimated_total_cents=0,
    )


def build_cart_dto(
    db: Session,
    cart: Cart,
    settings: Settings,
    *,
    reclaim_expired: bool = True,
) -> CartDTO:
    """Project CartDTO in a single joined SQL query (reclaiming expired stock if needed)."""
    rows = _fetch_cart_item_rows(db, cart.id)
    if reclaim_expired and rows:
        variant_ids = [variant.id for _, variant, _, _, _, _ in rows]
        if reclaim_expired_reservations_for_variants(db, variant_ids) > 0:
            db.commit()
            rows = _fetch_cart_item_rows(db, cart.id)

    item_dtos: list[CartItemDTO] = []
    item_count = 0
    subtotal_cents = 0

    for cart_item, variant, product, category, stock, primary_image_url in rows:
        _, _, available, _, in_stock, low_stock = compute_variant_availability(stock)
        line_total = variant.price_cents * cart_item.quantity
        is_available = (
            category.is_active
            and product.status == "ACTIVE"
            and variant.is_active
            and variant.currency == cart.currency
            and available >= cart_item.quantity
        )
        item_dtos.append(
            CartItemDTO(
                id=cart_item.id,
                variant_id=variant.id,
                product_id=product.id,
                product_slug=product.slug,
                product_title=product.title,
                variant_title=variant.title,
                sku=variant.sku,
                image_url=primary_image_url,
                unit_price_cents=variant.price_cents,
                quantity=cart_item.quantity,
                line_total_cents=line_total,
                available_quantity=available,
                in_stock=in_stock,
                low_stock=low_stock,
                is_available=is_available,
            )
        )
        item_count += cart_item.quantity
        subtotal_cents += line_total

    estimated_shipping_cents = (
        0
        if not item_dtos or subtotal_cents >= settings.free_shipping_threshold_cents
        else settings.standard_shipping_cents
    )
    estimated_tax_cents = _estimate_tax_cents(subtotal_cents, settings.tax_rate_bps)
    return CartDTO(
        id=cart.id,
        currency=cart.currency,
        items=item_dtos,
        item_count=item_count,
        subtotal_cents=subtotal_cents,
        estimated_shipping_cents=estimated_shipping_cents,
        tax_rate_bps=settings.tax_rate_bps,
        estimated_tax_cents=estimated_tax_cents,
        estimated_total_cents=subtotal_cents + estimated_shipping_cents,
    )


def get_cart(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    raw_guest_token: str | None,
    settings: Settings,
) -> tuple[CartDTO, str | None]:
    """Return the caller's ACTIVE cart without creating a new Cart row on read."""
    now = datetime.now(UTC)
    cart: Cart | None = None
    mutated = False

    if user_id is not None:
        candidate = db.scalar(
            select(Cart).where(Cart.user_id == user_id, Cart.status == "ACTIVE")
        )
        if candidate is not None and ensure_utc_datetime(candidate.expires_at) <= now:
            candidate.status = "ABANDONED"
            mutated = True
        else:
            cart = candidate
    else:
        cart, mutated = _resolve_active_guest_cart(
            db,
            raw_guest_token,
            now=now,
            lock=False,
        )

    if mutated:
        db.commit()

    if cart is None:
        return _empty_cart_dto(settings), None

    return build_cart_dto(db, cart, settings, reclaim_expired=True), None


def add_cart_item(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    raw_guest_token: str | None,
    payload: AddCartItemRequest,
    settings: Settings,
) -> tuple[CartDTO, str | None]:
    """Add quantity of an active variant to the caller's cart with server stock validation."""
    now = datetime.now(UTC)
    if reclaim_expired_reservations_for_variants(db, [payload.variant_id]) > 0:
        db.commit()

    variant_row = db.execute(
        select(ProductVariant, Product, Category)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(ProductVariant.id == payload.variant_id)
    ).first()
    if variant_row is None:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )

    variant, product, category = variant_row
    if not category.is_active or product.status != "ACTIVE" or not variant.is_active:
        raise ConflictError(
            code="VARIANT_UNAVAILABLE",
            message="This product option is currently unavailable.",
        )

    cart, new_guest_token, _ = _get_or_create_caller_cart(
        db,
        user_id=user_id,
        raw_guest_token=raw_guest_token,
        now=now,
        lock=True,
    )

    if variant.currency != cart.currency:
        db.rollback()
        raise ConflictError(
            code="VARIANT_UNAVAILABLE",
            message="Variant currency does not match cart currency.",
        )

    stock = db.scalar(
        select(InventoryStock)
        .where(InventoryStock.variant_id == variant.id)
        .order_by(InventoryStock.variant_id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    _, _, available, _, _, _ = compute_variant_availability(stock)

    existing_item = db.scalar(
        select(CartItem)
        .where(CartItem.cart_id == cart.id, CartItem.variant_id == variant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    existing_qty = existing_item.quantity if existing_item is not None else 0
    resulting_qty = existing_qty + payload.quantity

    if resulting_qty > MAX_CART_ITEM_QUANTITY:
        db.rollback()
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message=f"Quantity per item cannot exceed {MAX_CART_ITEM_QUANTITY}.",
            details={
                "variant_id": str(variant.id),
                "requested": resulting_qty,
                "max_quantity": MAX_CART_ITEM_QUANTITY,
            },
        )

    if resulting_qty > available:
        db.rollback()
        raise ConflictError(
            code="INSUFFICIENT_STOCK",
            message=f"Requested quantity exceeds available stock for {variant.sku}.",
            details={
                "variant_id": str(variant.id),
                "requested": resulting_qty,
                "available": available,
            },
        )

    if existing_item is not None:
        existing_item.quantity = resulting_qty
    else:
        db.add(
            CartItem(
                cart_id=cart.id,
                variant_id=variant.id,
                quantity=resulting_qty,
            )
        )

    cart.expires_at = _new_cart_expiry(now)
    db.commit()
    return build_cart_dto(db, cart, settings, reclaim_expired=False), new_guest_token


def _resolve_owned_cart_for_mutation(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    raw_guest_token: str | None,
    now: datetime,
    lock: bool = True,
) -> Cart | None:
    if user_id is not None:
        stmt = select(Cart).where(Cart.user_id == user_id, Cart.status == "ACTIVE")
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        cart = db.scalar(stmt)
        if cart is not None and ensure_utc_datetime(cart.expires_at) <= now:
            return None
        return cart
    guest_cart, _ = _resolve_active_guest_cart(
        db,
        raw_guest_token,
        now=now,
        lock=lock,
    )
    return guest_cart


def update_cart_item(
    db: Session,
    *,
    item_id: uuid.UUID,
    user_id: uuid.UUID | None,
    raw_guest_token: str | None,
    payload: UpdateCartItemRequest,
    settings: Settings,
) -> CartDTO:
    """Update quantity of a cart line item owned by the caller."""
    now = datetime.now(UTC)
    owned_cart = _resolve_owned_cart_for_mutation(
        db,
        user_id=user_id,
        raw_guest_token=raw_guest_token,
        now=now,
        lock=False,
    )
    if owned_cart is None:
        raise NotFoundError(
            code="CART_ITEM_NOT_FOUND",
            message="Cart item not found.",
        )

    variant_id = db.scalar(
        select(CartItem.variant_id).where(
            CartItem.id == item_id,
            CartItem.cart_id == owned_cart.id,
        )
    )
    if variant_id is None:
        raise NotFoundError(
            code="CART_ITEM_NOT_FOUND",
            message="Cart item not found.",
        )

    if reclaim_expired_reservations_for_variants(db, [variant_id]) > 0:
        db.commit()

    locked_cart = db.scalar(
        select(Cart)
        .where(Cart.id == owned_cart.id, Cart.status == "ACTIVE")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_cart is None:
        raise NotFoundError(
            code="CART_ITEM_NOT_FOUND",
            message="Cart item not found.",
        )

    item = db.scalar(
        select(CartItem)
        .where(CartItem.id == item_id, CartItem.cart_id == locked_cart.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise NotFoundError(
            code="CART_ITEM_NOT_FOUND",
            message="Cart item not found.",
        )

    variant_row = db.execute(
        select(ProductVariant, Product, Category)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(ProductVariant.id == item.variant_id)
    ).first()
    if variant_row is None:
        raise ConflictError(
            code="VARIANT_UNAVAILABLE",
            message="This product option is currently unavailable.",
        )
    variant, product, category = variant_row
    if (
        not category.is_active
        or product.status != "ACTIVE"
        or not variant.is_active
        or variant.currency != locked_cart.currency
    ):
        raise ConflictError(
            code="VARIANT_UNAVAILABLE",
            message="This product option is currently unavailable.",
        )

    stock = db.scalar(
        select(InventoryStock)
        .where(InventoryStock.variant_id == variant.id)
        .order_by(InventoryStock.variant_id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    _, _, available, _, _, _ = compute_variant_availability(stock)
    if payload.quantity > available:
        db.rollback()
        raise ConflictError(
            code="INSUFFICIENT_STOCK",
            message=f"Requested quantity exceeds available stock for {variant.sku}.",
            details={
                "variant_id": str(variant.id),
                "requested": payload.quantity,
                "available": available,
            },
        )

    item.quantity = payload.quantity
    locked_cart.expires_at = _new_cart_expiry(now)
    db.commit()
    return build_cart_dto(db, locked_cart, settings, reclaim_expired=False)


def delete_cart_item(
    db: Session,
    *,
    item_id: uuid.UUID,
    user_id: uuid.UUID | None,
    raw_guest_token: str | None,
    settings: Settings,
) -> CartDTO:
    """Remove a single line item from the caller's active cart."""
    now = datetime.now(UTC)
    cart = _resolve_owned_cart_for_mutation(
        db,
        user_id=user_id,
        raw_guest_token=raw_guest_token,
        now=now,
        lock=True,
    )
    if cart is None:
        raise NotFoundError(
            code="CART_ITEM_NOT_FOUND",
            message="Cart item not found.",
        )

    item = db.scalar(
        select(CartItem)
        .where(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise NotFoundError(
            code="CART_ITEM_NOT_FOUND",
            message="Cart item not found.",
        )

    db.delete(item)
    cart.expires_at = _new_cart_expiry(now)
    db.commit()
    return build_cart_dto(db, cart, settings, reclaim_expired=True)


def clear_cart(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    raw_guest_token: str | None,
    settings: Settings,
) -> tuple[CartDTO, str | None]:
    """Remove all line items from the caller's active cart."""
    now = datetime.now(UTC)
    cart, new_guest_token, _ = _get_or_create_caller_cart(
        db,
        user_id=user_id,
        raw_guest_token=raw_guest_token,
        now=now,
        lock=True,
    )

    db.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
    cart.expires_at = _new_cart_expiry(now)
    db.commit()
    return build_cart_dto(db, cart, settings, reclaim_expired=False), new_guest_token


def merge_guest_cart(
    db: Session,
    *,
    user_id: uuid.UUID,
    raw_guest_token: str | None,
    settings: Settings,
) -> tuple[CartDTO, bool]:
    """Idempotently merge an active guest cart into the authenticated user's cart."""
    now = datetime.now(UTC)
    user_cart, mutated = _resolve_active_user_cart(db, user_id, now=now)
    if mutated:
        db.commit()

    if not raw_guest_token:
        return build_cart_dto(db, user_cart, settings, reclaim_expired=True), False

    guest_token_digest = hash_token(raw_guest_token)
    guest_cart = db.scalar(
        select(Cart)
        .where(Cart.guest_token_hash == guest_token_digest)
        .execution_options(populate_existing=True)
    )
    if guest_cart is None or guest_cart.user_id is not None or guest_cart.status != "ACTIVE":
        return build_cart_dto(db, user_cart, settings, reclaim_expired=True), True

    if ensure_utc_datetime(guest_cart.expires_at) <= now:
        guest_cart.status = "ABANDONED"
        db.commit()
        return build_cart_dto(db, user_cart, settings, reclaim_expired=True), True

    guest_variant_ids = list(
        db.scalars(select(CartItem.variant_id).where(CartItem.cart_id == guest_cart.id)).all()
    )
    if not guest_variant_ids:
        guest_cart.status = "CONVERTED"
        db.commit()
        return build_cart_dto(db, user_cart, settings, reclaim_expired=True), True

    user_variant_ids = list(
        db.scalars(select(CartItem.variant_id).where(CartItem.cart_id == user_cart.id)).all()
    )
    all_variant_ids = sorted(set(guest_variant_ids) | set(user_variant_ids))
    if reclaim_expired_reservations_for_variants(db, all_variant_ids) > 0:
        db.commit()

    # Lock both carts in deterministic UUID ascending order to prevent cross-cart deadlocks.
    cart_ids_sorted = sorted([guest_cart.id, user_cart.id])
    locked_carts = list(
        db.scalars(
            select(Cart)
            .where(Cart.id.in_(cart_ids_sorted))
            .order_by(Cart.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    carts_by_id = {c.id: c for c in locked_carts}
    locked_guest_cart = carts_by_id.get(guest_cart.id)
    locked_user_cart = carts_by_id.get(user_cart.id)
    assert locked_user_cart is not None

    if (
        locked_guest_cart is None
        or locked_guest_cart.status != "ACTIVE"
        or ensure_utc_datetime(locked_guest_cart.expires_at) <= now
    ):
        db.rollback()
        return build_cart_dto(db, locked_user_cart, settings, reclaim_expired=True), True

    guest_rows = db.execute(
        select(CartItem, ProductVariant, Product, Category, InventoryStock)
        .join(ProductVariant, ProductVariant.id == CartItem.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .outerjoin(InventoryStock, InventoryStock.variant_id == ProductVariant.id)
        .where(CartItem.cart_id == locked_guest_cart.id)
        .order_by(CartItem.variant_id.asc())
        .execution_options(populate_existing=True)
    ).all()

    user_items_by_variant = {
        item.variant_id: item
        for item in db.scalars(
            select(CartItem)
            .where(CartItem.cart_id == locked_user_cart.id)
            .order_by(CartItem.variant_id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    }

    for g_item, variant, product, category, stock in guest_rows:
        if (
            not category.is_active
            or product.status != "ACTIVE"
            or not variant.is_active
            or variant.currency != locked_user_cart.currency
        ):
            continue

        _, _, available, _, _, _ = compute_variant_availability(stock)
        if available <= 0:
            continue

        existing_user_item = user_items_by_variant.get(variant.id)
        existing_qty = existing_user_item.quantity if existing_user_item is not None else 0
        clamped_qty = min(MAX_CART_ITEM_QUANTITY, available, existing_qty + g_item.quantity)
        if clamped_qty < 1:
            continue

        if existing_user_item is not None:
            existing_user_item.quantity = clamped_qty
        else:
            new_item = CartItem(
                cart_id=locked_user_cart.id,
                variant_id=variant.id,
                quantity=clamped_qty,
            )
            db.add(new_item)
            user_items_by_variant[variant.id] = new_item

    locked_guest_cart.status = "CONVERTED"
    locked_user_cart.expires_at = _new_cart_expiry(now)
    db.commit()
    return build_cart_dto(db, locked_user_cart, settings, reclaim_expired=False), True


def purge_expired_carts(
    db: Session,
    *,
    retention_days: int = 30,
    batch_size: int = 500,
    max_batches: int = 20,
    now: datetime | None = None,
) -> int:
    """Delete unreferenced expired carts past retention_days in bounded batches."""
    current_time = ensure_utc_datetime(now) if now is not None else datetime.now(UTC)
    cutoff = current_time - timedelta(days=retention_days)
    total_deleted = 0

    for _ in range(max_batches):
        batch_ids = list(
            db.scalars(
                select(Cart.id)
                .where(
                    Cart.status.in_(("ACTIVE", "ABANDONED", "CONVERTED")),
                    ts_lt(db, Cart.expires_at, cutoff),
                    ~exists(select(Order.id).where(Order.cart_id == Cart.id)),
                )
                .order_by(Cart.id.asc())
                .limit(batch_size)
            ).all()
        )
        if not batch_ids:
            break
        db.execute(delete(CartItem).where(CartItem.cart_id.in_(batch_ids)))
        result = db.execute(delete(Cart).where(Cart.id.in_(batch_ids)))
        deleted = int(getattr(result, "rowcount", 0) or 0)
        db.commit()
        total_deleted += deleted
        if deleted == 0:
            break

    return total_deleted
