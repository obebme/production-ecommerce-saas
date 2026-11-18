"""Business logic, lock-ordered stock mutations, and lazy reservation cleanup for Module 3."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.audit import record_audit_log
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.pagination import PaginatedResponse, PaginationParams, escape_like_query
from app.models.catalog import Category, Product
from app.models.identity import User
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderEvent
from app.models.payment import PaymentIntent
from app.modules.catalog.schemas import AdminVariantDTO, ProductStatus, ensure_utc_datetime
from app.modules.catalog.service import (
    compute_variant_availability,
    normalize_search_query,
    to_admin_variant_dto,
)
from app.modules.inventory.schemas import (
    AdminInventoryItemDTO,
    CreateVariantRequest,
    InventoryLedgerEntryDTO,
    StockAdjustmentRequest,
    StockStatusFilter,
    UpdateVariantRequest,
    VariantAvailabilityDTO,
)

_INT32_MAX = 2_147_483_647


def _variant_snapshot(variant: ProductVariant, stock: InventoryStock | None) -> dict[str, Any]:
    return {
        "id": str(variant.id),
        "product_id": str(variant.product_id),
        "sku": variant.sku,
        "title": variant.title,
        "attributes": dict(variant.attributes_json),
        "price_cents": variant.price_cents,
        "compare_at_price_cents": variant.compare_at_price_cents,
        "currency": variant.currency,
        "is_active": variant.is_active,
        "on_hand": stock.on_hand if stock is not None else 0,
        "reserved": stock.reserved if stock is not None else 0,
        "low_stock_threshold": stock.low_stock_threshold if stock is not None else 5,
    }


def _stock_snapshot(stock: InventoryStock) -> dict[str, Any]:
    return {
        "variant_id": str(stock.variant_id),
        "on_hand": stock.on_hand,
        "reserved": stock.reserved,
        "available_quantity": max(0, stock.on_hand - stock.reserved),
        "low_stock_threshold": stock.low_stock_threshold,
        "version": stock.version,
    }


def reclaim_expired_reservations_for_variants(
    db: Session,
    variant_ids: Sequence[uuid.UUID],
    *,
    now: datetime | None = None,
) -> int:
    """Atomically expire overdue PENDING_PAYMENT orders touching any of variant_ids.

    Strictly obeys the global canonical lock hierarchy across the entire batch:
      1. payment_intents (ORDER BY id ASC FOR UPDATE SKIP LOCKED)
      2. orders (ORDER BY id ASC FOR UPDATE SKIP LOCKED)
      3. inventory_reservations (ORDER BY id ASC FOR UPDATE)
      4. inventory_stocks (ORDER BY variant_id ASC FOR UPDATE)
    """
    unique_variant_ids = sorted(set(variant_ids))
    if not unique_variant_ids:
        return 0

    current_time = ensure_utc_datetime(now) if now is not None else datetime.now(UTC)

    # 1. Select expired candidate order IDs without taking row locks.
    candidate_order_ids = list(
        db.scalars(
            select(InventoryReservation.order_id)
            .join(Order, Order.id == InventoryReservation.order_id)
            .where(
                InventoryReservation.variant_id.in_(unique_variant_ids),
                InventoryReservation.status == "ACTIVE",
                InventoryReservation.expires_at < current_time,
                Order.status == "PENDING_PAYMENT",
            )
            .distinct()
            .order_by(InventoryReservation.order_id.asc())
            .limit(100)
        ).all()
    )
    if not candidate_order_ids:
        return 0

    # 2. Compute expected intent counts and lock ALL payment_intents for candidates in one query.
    expected_intent_counts: dict[uuid.UUID, int] = {oid: 0 for oid in candidate_order_ids}
    for oid, cnt in db.execute(
        select(PaymentIntent.order_id, func.count(PaymentIntent.id))
        .where(PaymentIntent.order_id.in_(candidate_order_ids))
        .group_by(PaymentIntent.order_id)
    ).all():
        expected_intent_counts[oid] = int(cnt)

    locked_intents = list(
        db.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.order_id.in_(candidate_order_ids))
            .order_by(PaymentIntent.id.asc())
            .with_for_update(skip_locked=True)
        ).all()
    )
    intents_by_order: dict[uuid.UUID, list[PaymentIntent]] = {
        oid: [] for oid in candidate_order_ids
    }
    for intent in locked_intents:
        intents_by_order[intent.order_id].append(intent)

    surviving_order_ids: list[uuid.UUID] = []
    for oid in candidate_order_ids:
        order_intents = intents_by_order[oid]
        if len(order_intents) < expected_intent_counts[oid]:
            continue
        if any(intent.status == "SUCCEEDED" for intent in order_intents):
            continue
        surviving_order_ids.append(oid)

    if not surviving_order_ids:
        return 0

    # 3. Lock ALL surviving PENDING_PAYMENT candidate orders in one query.
    locked_orders = list(
        db.scalars(
            select(Order)
            .where(
                Order.id.in_(surviving_order_ids),
                Order.status == "PENDING_PAYMENT",
            )
            .order_by(Order.id.asc())
            .with_for_update(skip_locked=True)
        ).all()
    )
    eligible_orders: dict[uuid.UUID, Order] = {
        order.id: order
        for order in locked_orders
        if order.payment_status != "PAID"
    }
    if not eligible_orders:
        return 0

    # 4. Lock ALL active reservations for the locked orders in one query.
    all_active_reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(
                InventoryReservation.order_id.in_(sorted(eligible_orders.keys())),
                InventoryReservation.status == "ACTIVE",
            )
            .order_by(InventoryReservation.id.asc())
            .with_for_update()
        ).all()
    )
    reservations_by_order: dict[uuid.UUID, list[InventoryReservation]] = {}
    for reservation in all_active_reservations:
        reservations_by_order.setdefault(reservation.order_id, []).append(reservation)

    expired_order_ids: list[uuid.UUID] = []
    for oid in sorted(eligible_orders.keys()):
        order_reservations = reservations_by_order.get(oid, [])
        if not order_reservations:
            continue
        if any(
            ensure_utc_datetime(res.expires_at) < current_time
            for res in order_reservations
        ):
            expired_order_ids.append(oid)

    if not expired_order_ids:
        return 0

    # 5. Lock ALL InventoryStock rows for the sorted global union of variant IDs in one query.
    global_variant_ids = sorted(
        {
            res.variant_id
            for oid in expired_order_ids
            for res in reservations_by_order[oid]
        }
    )
    stocks = list(
        db.scalars(
            select(InventoryStock)
            .where(InventoryStock.variant_id.in_(global_variant_ids))
            .order_by(InventoryStock.variant_id.asc())
            .with_for_update()
        ).all()
    )
    stocks_by_variant = {stock.variant_id: stock for stock in stocks}

    # 6. Apply all order, payment intent, reservation, stock, and ledger transitions.
    reclaimed_orders = 0
    for oid in expired_order_ids:
        order = eligible_orders[oid]
        for intent in intents_by_order.get(oid, ()):
            if intent.status in ("REQUIRES_ACTION", "PROCESSING", "FAILED"):
                intent.status = "CANCELLED"

        order.status = "CANCELLED"
        order.payment_status = "CANCELLED"
        order.fulfillment_status = "CANCELLED"
        order.cancelled_at = current_time
        order.cancellation_reason = "RESERVATION_EXPIRED"

        db.add(
            OrderEvent(
                order_id=order.id,
                actor_user_id=None,
                event_type="ORDER_CANCELLED",
                from_status="PENDING_PAYMENT",
                to_status="CANCELLED",
                metadata_json={"reason": "RESERVATION_EXPIRED"},
            )
        )

        order_reservations_sorted = sorted(
            reservations_by_order[oid],
            key=lambda res: (res.variant_id, str(res.id)),
        )
        for reservation in order_reservations_sorted:
            reservation.status = "EXPIRED"
            stock = stocks_by_variant.get(reservation.variant_id)
            if stock is None:
                continue
            actual_release = min(reservation.quantity, max(0, stock.reserved))
            stock.reserved -= actual_release
            stock.version += 1
            db.add(
                InventoryLedgerEntry(
                    variant_id=reservation.variant_id,
                    order_id=order.id,
                    actor_user_id=None,
                    reason="RELEASE",
                    delta_on_hand=0,
                    delta_reserved=-actual_release,
                    on_hand_after=stock.on_hand,
                    reserved_after=stock.reserved,
                    note="RESERVATION_EXPIRED",
                )
            )

        reclaimed_orders += 1

    db.flush()
    return reclaimed_orders


def get_variant_availability(db: Session, variant_id: uuid.UUID) -> VariantAvailabilityDTO:
    """Reclaim expired reservations for variant_id and return current public availability."""
    row = db.execute(
        select(ProductVariant, Product, Category)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(ProductVariant.id == variant_id)
    ).first()
    if row is None:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )

    variant, product, category = row
    if product.status != "ACTIVE" or not category.is_active:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )

    reclaimed = reclaim_expired_reservations_for_variants(db, [variant_id])
    if reclaimed > 0:
        db.commit()

    stock = db.scalar(select(InventoryStock).where(InventoryStock.variant_id == variant_id))
    if not variant.is_active:
        return VariantAvailabilityDTO(
            variant_id=variant.id,
            sku=variant.sku,
            is_active=False,
            available_quantity=0,
            in_stock=False,
            low_stock=False,
        )

    _, _, available, _, in_stock, low_stock = compute_variant_availability(stock)
    return VariantAvailabilityDTO(
        variant_id=variant.id,
        sku=variant.sku,
        is_active=True,
        available_quantity=available,
        in_stock=in_stock,
        low_stock=low_stock,
    )


def create_variant(
    db: Session,
    product_id: uuid.UUID,
    payload: CreateVariantRequest,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminVariantDTO:
    """Atomically create a ProductVariant, its InventoryStock, INITIAL_SEED ledger, and AuditLog."""
    product_exists = db.scalar(
        select(Product.id).where(Product.id == product_id).with_for_update()
    )
    if product_exists is None:
        raise NotFoundError(
            code="PRODUCT_NOT_FOUND",
            message="Product not found.",
        )

    existing_sku = db.scalar(
        select(ProductVariant.id).where(ProductVariant.sku == payload.sku)
    )
    if existing_sku is not None:
        raise ConflictError(
            code="SKU_CONFLICT",
            message="A variant with this SKU already exists.",
        )

    variant = ProductVariant(
        product_id=product_id,
        sku=payload.sku,
        title=payload.title,
        attributes_json=payload.attributes,
        price_cents=payload.price_cents,
        compare_at_price_cents=payload.compare_at_price_cents,
        currency=payload.currency,
        is_active=payload.is_active,
    )
    db.add(variant)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            code="SKU_CONFLICT",
            message="A variant with this SKU already exists.",
        ) from exc

    stock = InventoryStock(
        variant_id=variant.id,
        on_hand=payload.initial_on_hand,
        reserved=0,
        low_stock_threshold=payload.low_stock_threshold,
        version=1,
    )
    db.add(stock)

    db.add(
        InventoryLedgerEntry(
            variant_id=variant.id,
            order_id=None,
            actor_user_id=actor_user_id,
            reason="INITIAL_SEED",
            delta_on_hand=payload.initial_on_hand,
            delta_reserved=0,
            on_hand_after=payload.initial_on_hand,
            reserved_after=0,
            note="Initial variant stock seed",
        )
    )
    db.flush()

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="VARIANT_CREATED",
        resource_type="product_variant",
        resource_id=str(variant.id),
        before_state=None,
        after_state=_variant_snapshot(variant, stock),
        ip_address=ip_address,
    )
    db.commit()

    created = db.scalar(
        select(ProductVariant)
        .where(ProductVariant.id == variant.id)
        .options(joinedload(ProductVariant.stock))
    )
    assert created is not None
    return to_admin_variant_dto(created)


def update_variant(
    db: Session,
    variant_id: uuid.UUID,
    payload: UpdateVariantRequest,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminVariantDTO:
    """Update variant details and low_stock_threshold while guarding active-product invariant."""
    variant_product_id = db.scalar(
        select(ProductVariant.product_id).where(ProductVariant.id == variant_id)
    )
    if variant_product_id is None:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )

    # Lock parent Product before ProductVariant -> InventoryStock to prevent TOCTOU races.
    parent_product = db.scalar(
        select(Product).where(Product.id == variant_product_id).with_for_update()
    )
    variant = db.scalar(
        select(ProductVariant)
        .where(ProductVariant.id == variant_id)
        .with_for_update()
    )
    if parent_product is None or variant is None:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )

    stock = db.scalar(
        select(InventoryStock)
        .where(InventoryStock.variant_id == variant_id)
        .order_by(InventoryStock.variant_id.asc())
        .with_for_update()
    )
    before_state = _variant_snapshot(variant, stock)
    fields = payload.model_fields_set

    if "sku" in fields and payload.sku is not None and payload.sku != variant.sku:
        sku_conflict = db.scalar(
            select(ProductVariant.id).where(
                ProductVariant.sku == payload.sku,
                ProductVariant.id != variant_id,
            )
        )
        if sku_conflict is not None:
            raise ConflictError(
                code="SKU_CONFLICT",
                message="A variant with this SKU already exists.",
            )
        variant.sku = payload.sku

    new_price = (
        payload.price_cents
        if "price_cents" in fields and payload.price_cents is not None
        else variant.price_cents
    )
    new_compare = (
        payload.compare_at_price_cents
        if "compare_at_price_cents" in fields
        else variant.compare_at_price_cents
    )
    if new_compare is not None and new_compare < new_price:
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="compare_at_price_cents must be greater than or equal to price_cents.",
        )

    if (
        "is_active" in fields
        and payload.is_active is False
        and variant.is_active is True
        and parent_product.status == "ACTIVE"
    ):
        other_active = int(
            db.scalar(
                select(func.count(ProductVariant.id)).where(
                    ProductVariant.product_id == variant.product_id,
                    ProductVariant.id != variant.id,
                    ProductVariant.is_active.is_(True),
                )
            )
            or 0
        )
        if other_active == 0:
            raise ValidationAppError(
                code="PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT",
                message="Cannot deactivate the last active variant of an active product.",
            )

    if "title" in fields and payload.title is not None:
        variant.title = payload.title
    if "attributes" in fields and payload.attributes is not None:
        variant.attributes_json = payload.attributes
    if "price_cents" in fields and payload.price_cents is not None:
        variant.price_cents = payload.price_cents
    if "compare_at_price_cents" in fields:
        variant.compare_at_price_cents = payload.compare_at_price_cents
    if "currency" in fields and payload.currency is not None:
        variant.currency = payload.currency
    if "is_active" in fields and payload.is_active is not None:
        variant.is_active = payload.is_active

    if "low_stock_threshold" in fields and payload.low_stock_threshold is not None:
        if stock is None:
            stock = InventoryStock(
                variant_id=variant.id,
                on_hand=0,
                reserved=0,
                low_stock_threshold=payload.low_stock_threshold,
                version=1,
            )
            db.add(stock)
        else:
            stock.low_stock_threshold = payload.low_stock_threshold

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            code="SKU_CONFLICT",
            message="A variant with this SKU already exists.",
        ) from exc

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="VARIANT_UPDATED",
        resource_type="product_variant",
        resource_id=str(variant.id),
        before_state=before_state,
        after_state=_variant_snapshot(variant, stock),
        ip_address=ip_address,
    )
    db.commit()

    updated = db.scalar(
        select(ProductVariant)
        .where(ProductVariant.id == variant_id)
        .options(joinedload(ProductVariant.stock))
    )
    assert updated is not None
    return to_admin_variant_dto(updated)


def list_admin_inventory(
    db: Session,
    *,
    q: str | None = None,
    stock_status: StockStatusFilter = "all",
    pagination: PaginationParams,
) -> PaginatedResponse[AdminInventoryItemDTO]:
    """List paginated inventory rows via a single joined query (zero N+1)."""
    cleaned_q = normalize_search_query(q)
    available_expr = case(
        (
            InventoryStock.on_hand - InventoryStock.reserved > 0,
            InventoryStock.on_hand - InventoryStock.reserved,
        ),
        else_=0,
    )

    filters: list[ColumnElement[bool]] = []
    if cleaned_q is not None:
        pattern = f"%{escape_like_query(cleaned_q)}%"
        filters.append(
            or_(
                ProductVariant.sku.ilike(pattern, escape="\\"),
                Product.title.ilike(pattern, escape="\\"),
                ProductVariant.title.ilike(pattern, escape="\\"),
            )
        )

    if stock_status == "out_of_stock":
        filters.append(available_expr <= 0)
    elif stock_status == "low_stock":
        filters.append(
            (available_expr > 0) & (available_expr <= InventoryStock.low_stock_threshold)
        )
    elif stock_status == "in_stock":
        filters.append(available_expr > 0)

    total = int(
        db.scalar(
            select(func.count(InventoryStock.variant_id))
            .select_from(InventoryStock)
            .join(ProductVariant, ProductVariant.id == InventoryStock.variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
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

    rows = db.execute(
        select(InventoryStock, ProductVariant, Product)
        .join(ProductVariant, ProductVariant.id == InventoryStock.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(*filters)
        .order_by(ProductVariant.sku.asc(), InventoryStock.variant_id.asc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    items: list[AdminInventoryItemDTO] = []
    for stock, variant, product in rows:
        on_hand, reserved, available, threshold, in_stock, low_stock = (
            compute_variant_availability(stock)
        )
        items.append(
            AdminInventoryItemDTO(
                variant_id=variant.id,
                product_id=product.id,
                sku=variant.sku,
                product_title=product.title,
                variant_title=variant.title,
                product_status=cast(ProductStatus, product.status),
                is_active=variant.is_active,
                on_hand=on_hand,
                reserved=reserved,
                available_quantity=available,
                low_stock_threshold=threshold,
                in_stock=in_stock,
                low_stock=low_stock,
                updated_at=stock.updated_at,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )


def adjust_inventory_stock(
    db: Session,
    variant_id: uuid.UUID,
    payload: StockAdjustmentRequest,
    *,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminInventoryItemDTO:
    """Lock InventoryStock, validate invariants, apply delta, append ledger and audit log."""
    if payload.delta_on_hand == 0:
        raise ValidationAppError(
            code="INVALID_STOCK_ADJUSTMENT",
            message="Stock adjustment delta_on_hand must be non-zero.",
            details={"variant_id": str(variant_id), "delta_on_hand": 0},
        )

    stock = db.scalar(
        select(InventoryStock)
        .where(InventoryStock.variant_id == variant_id)
        .order_by(InventoryStock.variant_id.asc())
        .with_for_update()
    )
    if stock is None:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )

    variant_row = db.execute(
        select(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.id == variant_id)
    ).first()
    if variant_row is None:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )
    variant, product = variant_row

    new_on_hand = stock.on_hand + payload.delta_on_hand
    if new_on_hand > _INT32_MAX or new_on_hand < 0 or new_on_hand < stock.reserved:
        raise ValidationAppError(
            code="INVALID_STOCK_ADJUSTMENT",
            message="Adjusted on_hand cannot be negative, exceed limits, or fall below reserved.",
            details={
                "variant_id": str(variant_id),
                "on_hand": stock.on_hand,
                "reserved": stock.reserved,
                "delta_on_hand": payload.delta_on_hand,
            },
        )

    before_state = _stock_snapshot(stock)
    stock.on_hand = new_on_hand
    stock.version += 1

    db.add(
        InventoryLedgerEntry(
            variant_id=variant_id,
            order_id=None,
            actor_user_id=actor_user_id,
            reason="MANUAL_ADJUSTMENT",
            delta_on_hand=payload.delta_on_hand,
            delta_reserved=0,
            on_hand_after=new_on_hand,
            reserved_after=stock.reserved,
            note=payload.note,
        )
    )
    db.flush()

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="STOCK_ADJUSTED",
        resource_type="inventory_stock",
        resource_id=str(variant_id),
        before_state=before_state,
        after_state=_stock_snapshot(stock),
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(stock)

    on_hand, reserved, available, threshold, in_stock, low_stock = compute_variant_availability(
        stock
    )
    return AdminInventoryItemDTO(
        variant_id=variant.id,
        product_id=product.id,
        sku=variant.sku,
        product_title=product.title,
        variant_title=variant.title,
        product_status=cast(ProductStatus, product.status),
        is_active=variant.is_active,
        on_hand=on_hand,
        reserved=reserved,
        available_quantity=available,
        low_stock_threshold=threshold,
        in_stock=in_stock,
        low_stock=low_stock,
        updated_at=stock.updated_at,
    )


def list_variant_ledger(
    db: Session,
    variant_id: uuid.UUID,
    *,
    pagination: PaginationParams,
) -> PaginatedResponse[InventoryLedgerEntryDTO]:
    """List paginated append-only inventory ledger entries ordered newest first."""
    variant_exists = db.scalar(select(ProductVariant.id).where(ProductVariant.id == variant_id))
    if variant_exists is None:
        raise NotFoundError(
            code="VARIANT_NOT_FOUND",
            message="Variant not found.",
        )

    total = int(
        db.scalar(
            select(func.count(InventoryLedgerEntry.id)).where(
                InventoryLedgerEntry.variant_id == variant_id
            )
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

    rows = db.execute(
        select(InventoryLedgerEntry, Order.order_number, User.email)
        .outerjoin(Order, Order.id == InventoryLedgerEntry.order_id)
        .outerjoin(User, User.id == InventoryLedgerEntry.actor_user_id)
        .where(InventoryLedgerEntry.variant_id == variant_id)
        .order_by(InventoryLedgerEntry.created_at.desc(), InventoryLedgerEntry.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    items = [
        InventoryLedgerEntryDTO(
            id=entry.id,
            variant_id=entry.variant_id,
            order_id=entry.order_id,
            order_number=order_number,
            actor_user_id=entry.actor_user_id,
            actor_email=actor_email,
            reason=entry.reason,
            delta_on_hand=entry.delta_on_hand,
            delta_reserved=entry.delta_reserved,
            on_hand_after=entry.on_hand_after,
            reserved_after=entry.reserved_after,
            note=entry.note,
            created_at=entry.created_at,
        )
        for entry, order_number, actor_email in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )
