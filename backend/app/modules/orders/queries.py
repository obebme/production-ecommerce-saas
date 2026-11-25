"""Customer order list and detail query services with batch loading."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.pagination import PaginatedResponse, PaginationParams
from app.core.security import RateLimiterProtocol
from app.models.inventory import InventoryReservation
from app.models.order import Order, OrderItem
from app.modules.identity.dependencies import AuthContext
from app.modules.inventory.service import reclaim_expired_reservations_for_variants
from app.modules.orders.authorization import authorize_order_access
from app.modules.orders.common import _load_order_detail_dto
from app.modules.orders.schemas import (
    FulfillmentStatus,
    OrderDetailDTO,
    OrderListItemDTO,
    OrderStatus,
    PaymentStatus,
)


def list_customer_orders(
    db: Session,
    *,
    user_id: uuid.UUID,
    pagination: PaginationParams,
) -> PaginatedResponse[OrderListItemDTO]:
    """List paginated orders owned by user_id (newest first), lazily reclaiming expired stock."""
    total = int(
        db.scalar(
            select(func.count(Order.id)).where(Order.user_id == user_id)
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

    orders = list(
        db.scalars(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.placed_at.desc(), Order.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
    )

    pending_ids = [o.id for o in orders if o.status == "PENDING_PAYMENT"]
    if pending_ids:
        pending_variants = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id.in_(pending_ids),
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if pending_variants and reclaim_expired_reservations_for_variants(
            db, pending_variants
        ) > 0:
            db.commit()
            orders = list(
                db.scalars(
                    select(Order)
                    .where(Order.user_id == user_id)
                    .order_by(Order.placed_at.desc(), Order.id.desc())
                    .offset(pagination.offset)
                    .limit(pagination.page_size)
                    .execution_options(populate_existing=True)
                ).all()
            )

    order_ids = [o.id for o in orders]
    item_counts: dict[uuid.UUID, int] = {oid: 0 for oid in order_ids}
    for oid, cnt in db.execute(
        select(OrderItem.order_id, func.coalesce(func.sum(OrderItem.quantity), 0))
        .where(OrderItem.order_id.in_(order_ids))
        .group_by(OrderItem.order_id)
    ).all():
        item_counts[oid] = int(cnt)

    active_expiries: dict[uuid.UUID, datetime] = {}
    for oid, exp in db.execute(
        select(
            InventoryReservation.order_id,
            func.min(InventoryReservation.expires_at),
        )
        .where(
            InventoryReservation.order_id.in_(order_ids),
            InventoryReservation.status == "ACTIVE",
        )
        .group_by(InventoryReservation.order_id)
    ).all():
        if exp is not None:
            active_expiries[oid] = exp

    items = [
        OrderListItemDTO(
            id=order.id,
            order_number=order.order_number,
            status=cast(OrderStatus, order.status),
            payment_status=cast(PaymentStatus, order.payment_status),
            fulfillment_status=cast(FulfillmentStatus, order.fulfillment_status),
            currency=order.currency,
            subtotal_cents=order.subtotal_cents,
            discount_cents=order.discount_cents,
            shipping_cents=order.shipping_cents,
            tax_cents=order.tax_cents,
            total_cents=order.total_cents,
            item_count=item_counts.get(order.id, 0),
            placed_at=order.placed_at,
            paid_at=order.paid_at,
            reservation_expires_at=active_expiries.get(order.id),
        )
        for order in orders
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )


def get_order_detail(
    db: Session,
    *,
    order_number: str,
    auth: AuthContext | None,
    guest_token_header: str | None,
    guest_orders_cookie: str | None,
    settings: Settings,
    rate_limiter: RateLimiterProtocol,
    client_ip_key: str,
) -> OrderDetailDTO:
    """Fetch full OrderDetailDTO after verifying strict owner/guest access and lazy expiry."""
    order = authorize_order_access(
        db,
        order_number=order_number,
        auth=auth,
        guest_token_header=guest_token_header,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=client_ip_key,
    )

    if order.status == "PENDING_PAYMENT":
        variant_ids = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id == order.id,
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if variant_ids and reclaim_expired_reservations_for_variants(db, variant_ids) > 0:
            db.commit()
            db.refresh(order)

    return _load_order_detail_dto(db, order)
