"""Business logic for Admin Orders, Fulfillment, Refunds, Users RBAC, and Audit Logs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_log
from app.core.database import ts_gte, ts_lte
from app.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ValidationAppError,
)
from app.core.order_events import append_order_event
from app.core.pagination import PaginatedResponse, PaginationParams, escape_like_query
from app.models.identity import AuditLog, User
from app.models.inventory import InventoryLedgerEntry, InventoryReservation, InventoryStock
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import PaymentIntent, PaymentRefundRequest
from app.modules.admin.schemas import (
    AdminOrderDetailDTO,
    AdminOrderListItemDTO,
    AdminOrderReservationDTO,
    AdminUserDTO,
    AdminUserMutationResponse,
    AuditLogEntryDTO,
    FulfillOrderRequest,
    RefundOrderRequest,
    UpdateUserRoleRequest,
    UpdateUserStatusRequest,
)
from app.modules.catalog.schemas import ensure_utc_datetime, strip_single_line
from app.modules.identity.dependencies import revoke_all_user_sessions
from app.modules.identity.schemas import UserRole
from app.modules.inventory.service import (
    reclaim_expired_if_any,
    reclaim_expired_reservations_for_variants,
)
from app.modules.orders.schemas import (
    AddressDTO,
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
)
from app.modules.orders.service import (
    SANDBOX_PROVIDER,
    lock_order_and_payment_intents_for_update,
    normalize_valid_order_number_or_none,
    to_order_event_dto,
    to_order_item_dto,
)
from app.modules.payments.schemas import PaymentIntentDTO

_INT32_MAX = 2_147_483_647


class PaymentRefundGateway(Protocol):
    """Sandbox/provider refund abstraction executing without direct HTTP calls in baseline."""

    def refund_intent(
        self,
        *,
        provider: str,
        provider_intent_id: str,
        amount_cents: int,
        currency: str,
        reason: str | None,
    ) -> str: ...


class SandboxSimpayRefundGateway:
    """In-process sandbox refund processor for sandbox_simpay payment intents."""

    def refund_intent(
        self,
        *,
        provider: str,
        provider_intent_id: str,
        amount_cents: int,
        currency: str,
        reason: str | None,
    ) -> str:
        if provider != SANDBOX_PROVIDER:
            raise ConflictError(
                code="REFUND_NOT_ALLOWED",
                message=f"Unsupported payment provider '{provider}' for sandbox refund.",
            )
        return f"re_sandbox_{uuid.uuid4().hex}"


_DEFAULT_REFUND_GATEWAY = SandboxSimpayRefundGateway()


def _normalize_optional_search_q(raw_q: str | None) -> str | None:
    if raw_q is None:
        return None
    try:
        cleaned = strip_single_line(raw_q, field_name="Search query")
    except ValueError as exc:
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message=str(exc),
        ) from exc
    if not isinstance(cleaned, str):
        return None
    if len(cleaned) > 100:
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="Search query must be at most 100 characters.",
        )
    return cleaned if cleaned else None


def _normalize_optional_filter_token(
    raw_val: str | None,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    if raw_val is None:
        return None
    try:
        cleaned = strip_single_line(raw_val, field_name=field_name)
    except ValueError as exc:
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message=str(exc),
        ) from exc
    if not isinstance(cleaned, str):
        return None
    if len(cleaned) > max_length:
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message=f"{field_name} must be at most {max_length} characters.",
        )
    return cleaned if cleaned else None


def _validate_optional_ts_range(
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    norm_from = ensure_utc_datetime(from_ts) if from_ts is not None else None
    norm_to = ensure_utc_datetime(to_ts) if to_ts is not None else None
    if norm_from is not None and norm_to is not None and norm_from > norm_to:
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="from_ts must be less than or equal to to_ts.",
        )
    return norm_from, norm_to


def _iso_or_none(dt: datetime | None) -> str | None:
    return ensure_utc_datetime(dt).isoformat() if dt is not None else None


def _order_audit_snapshot(order: Order) -> dict[str, Any]:
    return {
        "order_number": order.order_number,
        "status": order.status,
        "payment_status": order.payment_status,
        "fulfillment_status": order.fulfillment_status,
        "shipped_at": _iso_or_none(order.shipped_at),
        "completed_at": _iso_or_none(order.completed_at),
        "cancelled_at": _iso_or_none(order.cancelled_at),
        "cancellation_reason": order.cancellation_reason,
    }


def _user_audit_snapshot(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
    }


def _build_admin_order_detail_dto(db: Session, order: Order) -> AdminOrderDetailDTO:
    items = list(
        db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.variant_id.asc(), OrderItem.id.asc())
            .execution_options(populate_existing=True)
        ).all()
    )
    events = list(
        db.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == order.id)
            .order_by(OrderEvent.created_at.asc(), OrderEvent.id.asc())
            .execution_options(populate_existing=True)
        ).all()
    )
    intents = list(
        db.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.order_id == order.id)
            .order_by(PaymentIntent.created_at.desc(), PaymentIntent.id.desc())
            .execution_options(populate_existing=True)
        ).all()
    )
    reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == order.id)
            .order_by(InventoryReservation.created_at.asc(), InventoryReservation.id.asc())
            .execution_options(populate_existing=True)
        ).all()
    )

    active_expiries = [res.expires_at for res in reservations if res.status == "ACTIVE"]
    reservation_expires_at = (
        min(active_expiries, key=ensure_utc_datetime) if active_expiries else None
    )
    intent_dtos = [PaymentIntentDTO.model_validate(intent) for intent in intents]

    return AdminOrderDetailDTO(
        id=order.id,
        order_number=order.order_number,
        user_id=order.user_id,
        status=cast(OrderStatus, order.status),
        payment_status=cast(PaymentStatus, order.payment_status),
        fulfillment_status=cast(FulfillmentStatus, order.fulfillment_status),
        customer_email=order.customer_email,
        currency=order.currency,
        subtotal_cents=order.subtotal_cents,
        discount_cents=order.discount_cents,
        shipping_cents=order.shipping_cents,
        tax_cents=order.tax_cents,
        total_cents=order.total_cents,
        shipping_address=AddressDTO.model_validate(order.shipping_address_json),
        billing_address=AddressDTO.model_validate(order.billing_address_json),
        items=[to_order_item_dto(item) for item in items],
        payment_intent=intent_dtos[0] if intent_dtos else None,
        payment_intents=intent_dtos,
        reservations=[AdminOrderReservationDTO.model_validate(res) for res in reservations],
        reservation_expires_at=reservation_expires_at,
        events=[to_order_event_dto(event) for event in events],
        placed_at=order.placed_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        completed_at=order.completed_at,
        cancelled_at=order.cancelled_at,
        updated_at=order.updated_at,
        cancellation_reason=order.cancellation_reason,
    )


def list_admin_orders(
    db: Session,
    *,
    status: OrderStatus | None = None,
    payment_status: PaymentStatus | None = None,
    fulfillment_status: FulfillmentStatus | None = None,
    q: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    pagination: PaginationParams,
) -> PaginatedResponse[AdminOrderListItemDTO]:
    """List paginated orders across all customers/guests with filters and zero N+1 counts."""
    norm_from, norm_to = _validate_optional_ts_range(from_ts, to_ts)
    cleaned_q = _normalize_optional_search_q(q)
    reclaim_expired_if_any(db)

    filters: list[ColumnElement[bool]] = []
    if status is not None:
        filters.append(Order.status == status)
    if payment_status is not None:
        filters.append(Order.payment_status == payment_status)
    if fulfillment_status is not None:
        filters.append(Order.fulfillment_status == fulfillment_status)
    if cleaned_q is not None:
        pattern = f"%{escape_like_query(cleaned_q)}%"
        filters.append(
            or_(
                Order.order_number.ilike(pattern, escape="\\"),
                Order.customer_email.ilike(pattern, escape="\\"),
            )
        )
    if norm_from is not None:
        filters.append(ts_gte(db, Order.placed_at, norm_from))
    if norm_to is not None:
        filters.append(ts_lte(db, Order.placed_at, norm_to))

    total = int(
        db.scalar(select(func.count(Order.id)).where(*filters)) or 0
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
            .where(*filters)
            .order_by(Order.placed_at.desc(), Order.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
    )

    order_ids = [order.id for order in orders]
    item_counts: dict[uuid.UUID, int] = {oid: 0 for oid in order_ids}
    for oid, cnt in db.execute(
        select(OrderItem.order_id, func.coalesce(func.sum(OrderItem.quantity), 0))
        .where(OrderItem.order_id.in_(order_ids))
        .group_by(OrderItem.order_id)
    ).all():
        item_counts[oid] = int(cnt)

    items = [
        AdminOrderListItemDTO(
            id=order.id,
            order_number=order.order_number,
            user_id=order.user_id,
            customer_email=order.customer_email,
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
            shipped_at=order.shipped_at,
            completed_at=order.completed_at,
            cancelled_at=order.cancelled_at,
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


def get_admin_order_detail(db: Session, order_number: str) -> AdminOrderDetailDTO:
    """Return full order details, intents, reservations, and events for staff/admin."""
    valid_order_number = normalize_valid_order_number_or_none(order_number)
    if valid_order_number is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    order = db.scalar(select(Order).where(Order.order_number == valid_order_number))
    if order is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
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
    return _build_admin_order_detail_dto(db, order)


def fulfill_admin_order(
    db: Session,
    *,
    order_number: str,
    payload: FulfillOrderRequest,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminOrderDetailDTO:
    """Advance paid order fulfillment state machine and record OrderEvent + AuditLog."""
    valid_order_number = normalize_valid_order_number_or_none(order_number)
    if valid_order_number is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    order = db.scalar(
        select(Order)
        .where(Order.order_number == valid_order_number)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    if order.payment_status != "PAID":
        db.rollback()
        raise ConflictError(
            code="INVALID_ORDER_STATE_TRANSITION",
            message="Payment must succeed before fulfillment can proceed.",
        )

    prior_status = order.status
    before_state = _order_audit_snapshot(order)
    now = datetime.now(UTC)

    if payload.action == "START_PROCESSING":
        if prior_status != "CONFIRMED":
            db.rollback()
            raise ConflictError(
                code="INVALID_ORDER_STATE_TRANSITION",
                message="Only CONFIRMED orders can start processing.",
            )
        order.status = "PROCESSING"
        append_order_event(
            db,
            order_id=order.id,
            actor_user_id=actor_user_id,
            event_type="ORDER_PROCESSING_STARTED",
            from_status=prior_status,
            to_status="PROCESSING",
            metadata_json={"note": payload.note},
            now=now,
        )
    elif payload.action == "SHIP":
        if prior_status not in ("CONFIRMED", "PROCESSING"):
            db.rollback()
            raise ConflictError(
                code="INVALID_ORDER_STATE_TRANSITION",
                message="Only CONFIRMED or PROCESSING orders can be marked shipped.",
            )
        order.status = "SHIPPED"
        order.fulfillment_status = "SHIPPED"
        order.shipped_at = now
        append_order_event(
            db,
            order_id=order.id,
            actor_user_id=actor_user_id,
            event_type="ORDER_SHIPPED",
            from_status=prior_status,
            to_status="SHIPPED",
            metadata_json={
                "carrier": payload.carrier,
                "tracking_number": payload.tracking_number,
                "note": payload.note,
            },
            now=now,
        )
    elif payload.action == "COMPLETE":
        if prior_status != "SHIPPED":
            db.rollback()
            raise ConflictError(
                code="INVALID_ORDER_STATE_TRANSITION",
                message="Only SHIPPED orders can be marked completed.",
            )
        order.status = "COMPLETED"
        order.fulfillment_status = "DELIVERED"
        order.completed_at = now
        append_order_event(
            db,
            order_id=order.id,
            actor_user_id=actor_user_id,
            event_type="ORDER_COMPLETED",
            from_status=prior_status,
            to_status="COMPLETED",
            metadata_json={"note": payload.note},
            now=now,
        )

    after_state = {
        **_order_audit_snapshot(order),
        "action": payload.action,
        "carrier": payload.carrier,
        "tracking_number": payload.tracking_number,
        "note": payload.note,
    }
    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="ORDER_FULFILLMENT_UPDATED",
        resource_type="order",
        resource_id=order.order_number,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(order)
    return _build_admin_order_detail_dto(db, order)


def cancel_admin_order(
    db: Session,
    *,
    order_number: str,
    reason: str | None,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminOrderDetailDTO:
    """Cancel an unpaid PENDING_PAYMENT order in canonical lock order with audit trail."""
    valid_order_number = normalize_valid_order_number_or_none(order_number)
    if valid_order_number is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    order_id = db.scalar(select(Order.id).where(Order.order_number == valid_order_number))
    if order_id is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    # Universal lock order: PaymentIntent -> Order -> InventoryReservation -> InventoryStock.
    locked_order, intents = lock_order_and_payment_intents_for_update(
        db,
        order_id=order_id,
    )

    reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == locked_order.id)
            .order_by(InventoryReservation.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    active_reservations = [res for res in reservations if res.status == "ACTIVE"]
    variant_ids = sorted({res.variant_id for res in active_reservations})
    stocks_by_variant: dict[uuid.UUID, InventoryStock] = {}
    if variant_ids:
        stocks_by_variant = {
            stock.variant_id: stock
            for stock in db.scalars(
                select(InventoryStock)
                .where(InventoryStock.variant_id.in_(variant_ids))
                .order_by(InventoryStock.variant_id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }

    if (
        locked_order.status != "PENDING_PAYMENT"
        or locked_order.payment_status not in ("REQUIRES_PAYMENT", "FAILED")
    ):
        db.rollback()
        raise ConflictError(
            code="INVALID_ORDER_STATE_TRANSITION",
            message="Only unpaid PENDING_PAYMENT orders can be cancelled.",
        )

    before_state = _order_audit_snapshot(locked_order)
    now = datetime.now(UTC)
    cancel_reason = reason or "ADMIN_CANCELLED"

    for intent in intents:
        if intent.status in ("REQUIRES_ACTION", "PROCESSING", "FAILED"):
            intent.status = "CANCELLED"

    locked_order.status = "CANCELLED"
    locked_order.payment_status = "CANCELLED"
    locked_order.fulfillment_status = "CANCELLED"
    locked_order.cancelled_at = now
    locked_order.cancellation_reason = cancel_reason

    for res in sorted(active_reservations, key=lambda r: (r.variant_id, str(r.id))):
        res.status = "RELEASED"
        stock = stocks_by_variant.get(res.variant_id)
        if stock is None:
            continue
        actual_release = min(res.quantity, max(0, stock.reserved))
        stock.reserved -= actual_release
        stock.version += 1
        db.add(
            InventoryLedgerEntry(
                variant_id=res.variant_id,
                order_id=locked_order.id,
                actor_user_id=actor_user_id,
                reason="RELEASE",
                delta_on_hand=0,
                delta_reserved=-actual_release,
                on_hand_after=stock.on_hand,
                reserved_after=stock.reserved,
                note=f"Order {locked_order.order_number} admin cancelled",
            )
        )

    append_order_event(
        db,
        order_id=locked_order.id,
        actor_user_id=actor_user_id,
        event_type="ORDER_CANCELLED",
        from_status="PENDING_PAYMENT",
        to_status="CANCELLED",
        metadata_json={"reason": cancel_reason},
        now=now,
    )
    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="ORDER_CANCELLED",
        resource_type="order",
        resource_id=locked_order.order_number,
        before_state=before_state,
        after_state=_order_audit_snapshot(locked_order),
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(locked_order)
    return _build_admin_order_detail_dto(db, locked_order)


def _apply_refund_restock_if_needed(
    db: Session,
    *,
    locked_order: Order,
    qty_by_variant: dict[uuid.UUID, int],
    stocks_by_variant: dict[uuid.UUID, InventoryStock],
    actor_user_id: uuid.UUID,
    reason: str | None,
) -> None:
    if not qty_by_variant:
        return
    already_restocked = db.scalar(
        select(InventoryLedgerEntry.id)
        .where(
            InventoryLedgerEntry.order_id == locked_order.id,
            InventoryLedgerEntry.reason == "RETURN_RESTOCK",
        )
        .limit(1)
    )
    if already_restocked is not None:
        return

    base_note = (
        f"Order {locked_order.order_number} refund restock: {reason}"
        if reason
        else f"Order {locked_order.order_number} refund restock"
    )[:255]

    for variant_id in sorted(qty_by_variant.keys()):
        qty = qty_by_variant[variant_id]
        stock = stocks_by_variant.get(variant_id)
        if stock is None:
            continue
        stock.on_hand += qty
        stock.version += 1
        db.add(
            InventoryLedgerEntry(
                variant_id=variant_id,
                order_id=locked_order.id,
                actor_user_id=actor_user_id,
                reason="RETURN_RESTOCK",
                delta_on_hand=qty,
                delta_reserved=0,
                on_hand_after=stock.on_hand,
                reserved_after=stock.reserved,
                note=base_note,
            )
        )


def _mark_admin_refund_failed(
    db: Session,
    *,
    order_id: uuid.UUID,
    failed_req_id: uuid.UUID,
    failed_intent_id: uuid.UUID,
    revert_to_succeeded_ids: set[uuid.UUID],
    succeeded_results: list[tuple[uuid.UUID, uuid.UUID, str]],
    error_message: str,
) -> None:
    """Short TX recording gateway refund failure and reverting only pre-SUCCEEDED intents."""
    now = datetime.now(UTC)
    locked_order, intents = lock_order_and_payment_intents_for_update(
        db,
        order_id=order_id,
    )
    intents_by_id = {intent.id: intent for intent in intents}
    req_ids = [failed_req_id, *(req_id for req_id, _, _ in succeeded_results)]
    reqs = list(
        db.scalars(
            select(PaymentRefundRequest)
            .where(PaymentRefundRequest.id.in_(req_ids))
            .order_by(PaymentRefundRequest.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    reqs_by_id = {req.id: req for req in reqs}

    for req_id, intent_id, refund_ref in succeeded_results:
        req = reqs_by_id.get(req_id)
        if req is not None:
            req.status = "SUCCEEDED"
            req.provider_refund_id = refund_ref
            req.error_message = None
            req.completed_at = now
        intent = intents_by_id.get(intent_id)
        if intent is not None:
            intent.status = "REFUNDED"

    failed_req = reqs_by_id.get(failed_req_id)
    if failed_req is not None:
        failed_req.status = "FAILED"
        failed_req.error_message = error_message[:500]
        failed_req.completed_at = now

    failed_intent = intents_by_id.get(failed_intent_id)
    if (
        failed_intent is not None
        and failed_intent.id in revert_to_succeeded_ids
        and failed_intent.status == "REFUND_PENDING"
    ):
        failed_intent.status = "SUCCEEDED"

    for intent in intents:
        if intent.id in revert_to_succeeded_ids and intent.status == "REFUND_PENDING":
            intent.status = "SUCCEEDED"

    has_any_refunded = any(intent.status == "REFUNDED" for intent in intents)
    has_unreverted_admin_pending = any(
        intent.status == "REFUND_PENDING" and intent.id not in revert_to_succeeded_ids
        for intent in intents
    )
    if has_any_refunded:
        locked_order.payment_status = "REFUND_PENDING"
    elif any(intent.status == "SUCCEEDED" for intent in intents):
        locked_order.payment_status = "PAID"
    elif has_unreverted_admin_pending:
        locked_order.payment_status = "REFUND_PENDING"
    elif locked_order.payment_status == "REFUND_PENDING":
        locked_order.payment_status = "PAID"

    db.commit()


def refund_admin_order(
    db: Session,
    *,
    order_number: str,
    payload: RefundOrderRequest,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
    refund_gateway: PaymentRefundGateway = _DEFAULT_REFUND_GATEWAY,
) -> AdminOrderDetailDTO:
    """Refund a paid order in 3 phases with zero DB row locks held during gateway calls."""
    valid_order_number = normalize_valid_order_number_or_none(order_number)
    if valid_order_number is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    order_id = db.scalar(select(Order.id).where(Order.order_number == valid_order_number))
    if order_id is None:
        raise NotFoundError(
            code="ORDER_NOT_FOUND",
            message="Order not found.",
        )

    # Phase A (short locked TX): validate state, cancel open sibling intents, stage REFUND_PENDING.
    locked_order, intents = lock_order_and_payment_intents_for_update(
        db,
        order_id=order_id,
    )
    reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == locked_order.id)
            .order_by(InventoryReservation.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    order_items = list(
        db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == locked_order.id)
            .order_by(OrderItem.variant_id.asc(), OrderItem.id.asc())
        ).all()
    )

    variant_ids_to_lock: set[uuid.UUID] = {
        res.variant_id for res in reservations if res.status == "ACTIVE"
    }
    if payload.restock:
        variant_ids_to_lock.update(item.variant_id for item in order_items)
    sorted_variant_ids = sorted(variant_ids_to_lock)

    stocks_by_variant: dict[uuid.UUID, InventoryStock] = {}
    if sorted_variant_ids:
        stocks_by_variant = {
            stock.variant_id: stock
            for stock in db.scalars(
                select(InventoryStock)
                .where(InventoryStock.variant_id.in_(sorted_variant_ids))
                .order_by(InventoryStock.variant_id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }

    refundable_intents = [
        intent for intent in intents if intent.status in ("SUCCEEDED", "REFUND_PENDING")
    ]
    has_refunded_intent = any(intent.status == "REFUNDED" for intent in intents)
    can_recover_refund_pending = (
        locked_order.payment_status == "REFUND_PENDING"
        and (bool(refundable_intents) or has_refunded_intent)
    )
    if (
        locked_order.status not in ("CONFIRMED", "PROCESSING", "SHIPPED", "COMPLETED")
        or (locked_order.payment_status != "PAID" and not can_recover_refund_pending)
    ):
        db.rollback()
        raise ConflictError(
            code="REFUND_NOT_ALLOWED",
            message=(
                "Only paid orders in CONFIRMED, PROCESSING, SHIPPED, or COMPLETED "
                "state can be refunded."
            ),
        )

    qty_by_variant: dict[uuid.UUID, int] = {}
    if payload.restock:
        for item in order_items:
            qty_by_variant[item.variant_id] = (
                qty_by_variant.get(item.variant_id, 0) + item.quantity
            )
        for variant_id in sorted(qty_by_variant.keys()):
            qty = qty_by_variant[variant_id]
            stock = stocks_by_variant.get(variant_id)
            if stock is not None and stock.on_hand + qty > _INT32_MAX:
                db.rollback()
                raise ValidationAppError(
                    code="INVALID_STOCK_ADJUSTMENT",
                    message="Restocked on_hand quantity exceeds maximum supported limit.",
                )

    before_state = _order_audit_snapshot(locked_order)
    prior_status = locked_order.status
    prior_fulfillment = locked_order.fulfillment_status

    for intent in intents:
        if intent.status in ("REQUIRES_ACTION", "PROCESSING"):
            intent.status = "CANCELLED"

    existing_reqs: dict[uuid.UUID, PaymentRefundRequest] = {}
    if refundable_intents:
        existing_reqs = {
            req.payment_intent_id: req
            for req in db.scalars(
                select(PaymentRefundRequest)
                .where(
                    PaymentRefundRequest.payment_intent_id.in_(
                        [intent.id for intent in refundable_intents]
                    )
                )
                .order_by(PaymentRefundRequest.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }

    now_phase_a = datetime.now(UTC)
    already_succeeded_refs: list[str] = []
    revert_to_succeeded_ids: set[uuid.UUID] = set()
    pending_targets: list[tuple[uuid.UUID, uuid.UUID, str, str, int, str]] = []
    for intent in refundable_intents:
        req = existing_reqs.get(intent.id)
        if (
            intent.status == "REFUND_PENDING"
            and req is not None
            and req.trigger_source != "ADMIN_REFUND"
        ):
            # Do not hijack automated duplicate/stale refund requests into admin refund flow.
            continue
        if req is not None and req.status == "SUCCEEDED":
            intent.status = "REFUNDED"
            if req.provider_refund_id:
                already_succeeded_refs.append(req.provider_refund_id)
            continue
        if intent.status == "SUCCEEDED":
            revert_to_succeeded_ids.add(intent.id)
        if req is None:
            req = PaymentRefundRequest(
                order_id=locked_order.id,
                payment_intent_id=intent.id,
                provider=intent.provider,
                provider_intent_id=intent.provider_intent_id,
                amount_cents=intent.amount_cents,
                currency=intent.currency,
                reason=payload.reason,
                trigger_source="ADMIN_REFUND",
                status="PENDING",
                actor_user_id=actor_user_id,
                restock_on_confirm=payload.restock,
            )
            db.add(req)
            db.flush()
        elif req.status == "FAILED":
            req.status = "PENDING"
            req.error_message = None
            req.completed_at = None
            req.reason = payload.reason
            req.trigger_source = "ADMIN_REFUND"
            req.actor_user_id = actor_user_id
            req.restock_on_confirm = payload.restock
            req.updated_at = now_phase_a
        else:
            # status == "PENDING": guard against concurrent duplicate ADMIN_REFUND calls.
            if (
                req.trigger_source == "ADMIN_REFUND"
                and (now_phase_a - ensure_utc_datetime(req.updated_at)) < timedelta(seconds=60)
            ):
                db.rollback()
                raise ConflictError(
                    code="REFUND_IN_PROGRESS",
                    message="A refund request is already in progress for this order.",
                )
            req.reason = payload.reason
            req.trigger_source = "ADMIN_REFUND"
            req.actor_user_id = actor_user_id
            req.restock_on_confirm = payload.restock
            req.updated_at = now_phase_a
        intent.status = "REFUND_PENDING"
        pending_targets.append(
            (
                req.id,
                intent.id,
                intent.provider,
                intent.provider_intent_id,
                intent.amount_cents,
                intent.currency,
            )
        )

    if not pending_targets:
        locked_order.status = "REFUNDED"
        locked_order.payment_status = "REFUNDED"
        locked_order.fulfillment_status = (
            "RETURNED"
            if prior_status in ("SHIPPED", "COMPLETED")
            or prior_fulfillment in ("SHIPPED", "DELIVERED")
            else "CANCELLED"
        )
        if payload.restock:
            _apply_refund_restock_if_needed(
                db,
                locked_order=locked_order,
                qty_by_variant=qty_by_variant,
                stocks_by_variant=stocks_by_variant,
                actor_user_id=actor_user_id,
                reason=payload.reason,
            )
        append_order_event(
            db,
            order_id=locked_order.id,
            actor_user_id=actor_user_id,
            event_type="ORDER_REFUNDED",
            from_status=prior_status,
            to_status="REFUNDED",
            metadata_json={
                "reason": payload.reason,
                "restock": payload.restock,
                "refund_references": already_succeeded_refs,
            },
        )
        record_audit_log(
            db,
            actor_user_id=actor_user_id,
            action="ORDER_REFUNDED",
            resource_type="order",
            resource_id=locked_order.order_number,
            before_state=before_state,
            after_state={
                **_order_audit_snapshot(locked_order),
                "reason": payload.reason,
                "restock": payload.restock,
                "refund_references": already_succeeded_refs,
            },
            ip_address=ip_address,
        )
        db.commit()
        db.refresh(locked_order)
        return _build_admin_order_detail_dto(db, locked_order)

    locked_order.payment_status = "REFUND_PENDING"
    db.commit()

    # Phase B (no DB locks held): execute external/sandbox refund gateway calls.
    refund_results: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    for req_id, intent_id, provider, provider_intent_id, amount_cents, currency in pending_targets:
        try:
            refund_ref = refund_gateway.refund_intent(
                provider=provider,
                provider_intent_id=provider_intent_id,
                amount_cents=amount_cents,
                currency=currency,
                reason=payload.reason,
            )
        except Exception as exc:
            _mark_admin_refund_failed(
                db,
                order_id=order_id,
                failed_req_id=req_id,
                failed_intent_id=intent_id,
                revert_to_succeeded_ids=revert_to_succeeded_ids,
                succeeded_results=refund_results,
                error_message=str(exc) or exc.__class__.__name__,
            )
            raise
        refund_results.append((req_id, intent_id, refund_ref))

    # Phase C (short locked TX): finalize refund records, order status, restock, and audit trail.
    now = datetime.now(UTC)
    locked_order, intents = lock_order_and_payment_intents_for_update(
        db,
        order_id=order_id,
    )
    stocks_by_variant = {}
    if sorted_variant_ids:
        stocks_by_variant = {
            stock.variant_id: stock
            for stock in db.scalars(
                select(InventoryStock)
                .where(InventoryStock.variant_id.in_(sorted_variant_ids))
                .order_by(InventoryStock.variant_id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }

    intents_by_id = {intent.id: intent for intent in intents}
    reqs_by_id = {
        req.id: req
        for req in db.scalars(
            select(PaymentRefundRequest)
            .where(PaymentRefundRequest.id.in_([req_id for req_id, _, _ in refund_results]))
            .order_by(PaymentRefundRequest.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    }
    refund_references: list[str] = list(already_succeeded_refs)
    for req_id, intent_id, refund_ref in refund_results:
        refund_references.append(refund_ref)
        req_row = reqs_by_id.get(req_id)
        if req_row is not None:
            req_row.status = "SUCCEEDED"
            req_row.provider_refund_id = refund_ref
            req_row.error_message = None
            req_row.completed_at = req_row.completed_at or now
        intent_row = intents_by_id.get(intent_id)
        if intent_row is not None:
            intent_row.status = "REFUNDED"

    if payload.restock:
        for variant_id in sorted(qty_by_variant.keys()):
            qty = qty_by_variant[variant_id]
            stock = stocks_by_variant.get(variant_id)
            if stock is not None and stock.on_hand + qty > _INT32_MAX:
                db.rollback()
                raise ValidationAppError(
                    code="INVALID_STOCK_ADJUSTMENT",
                    message="Restocked on_hand quantity exceeds maximum supported limit.",
                )

    was_already_refunded = (
        locked_order.status == "REFUNDED" and locked_order.payment_status == "REFUNDED"
    )
    locked_order.status = "REFUNDED"
    locked_order.payment_status = "REFUNDED"
    locked_order.fulfillment_status = (
        "RETURNED"
        if prior_status in ("SHIPPED", "COMPLETED")
        or prior_fulfillment in ("SHIPPED", "DELIVERED")
        else "CANCELLED"
    )

    if not was_already_refunded:
        if payload.restock:
            _apply_refund_restock_if_needed(
                db,
                locked_order=locked_order,
                qty_by_variant=qty_by_variant,
                stocks_by_variant=stocks_by_variant,
                actor_user_id=actor_user_id,
                reason=payload.reason,
            )
        append_order_event(
            db,
            order_id=locked_order.id,
            actor_user_id=actor_user_id,
            event_type="ORDER_REFUNDED",
            from_status=prior_status,
            to_status="REFUNDED",
            metadata_json={
                "reason": payload.reason,
                "restock": payload.restock,
                "refund_references": refund_references,
            },
            now=now,
        )
        record_audit_log(
            db,
            actor_user_id=actor_user_id,
            action="ORDER_REFUNDED",
            resource_type="order",
            resource_id=locked_order.order_number,
            before_state=before_state,
            after_state={
                **_order_audit_snapshot(locked_order),
                "reason": payload.reason,
                "restock": payload.restock,
                "refund_references": refund_references,
            },
            ip_address=ip_address,
        )

    db.commit()
    db.refresh(locked_order)
    return _build_admin_order_detail_dto(db, locked_order)


def list_admin_users(
    db: Session,
    *,
    role: UserRole | None = None,
    is_active: bool | None = None,
    q: str | None = None,
    pagination: PaginationParams,
) -> PaginatedResponse[AdminUserDTO]:
    """List paginated users for admin management without exposing password_hash."""
    cleaned_q = _normalize_optional_search_q(q)
    filters: list[ColumnElement[bool]] = []
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    if cleaned_q is not None:
        pattern = f"%{escape_like_query(cleaned_q)}%"
        filters.append(
            or_(
                User.email.ilike(pattern, escape="\\"),
                User.full_name.ilike(pattern, escape="\\"),
            )
        )

    total = int(db.scalar(select(func.count(User.id)).where(*filters)) or 0)
    if total == 0 or pagination.offset >= total:
        return PaginatedResponse(
            items=[],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            has_next=False,
        )

    users = list(
        db.scalars(
            select(User)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
    )
    items = [AdminUserDTO.model_validate(user) for user in users]
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )


def _lock_target_and_active_admins(
    db: Session,
    user_id: uuid.UUID,
) -> tuple[User | None, list[User]]:
    """Lock target user and all active admins in one ascending User.id query (deadlock-free)."""
    locked_users = list(
        db.scalars(
            select(User)
            .where(
                or_(
                    User.id == user_id,
                    (User.role == "admin") & (User.is_active.is_(True)),
                )
            )
            .order_by(User.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    target = next((user for user in locked_users if user.id == user_id), None)
    active_admins = [
        user for user in locked_users if user.role == "admin" and user.is_active
    ]
    return target, active_admins


def update_admin_user_role(
    db: Session,
    *,
    target_user_id: uuid.UUID,
    payload: UpdateUserRoleRequest,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminUserMutationResponse:
    """Update a user's role with self-demotion & last-admin guards and session revocation."""
    target, active_admins = _lock_target_and_active_admins(db, target_user_id)
    if target is None:
        db.rollback()
        raise NotFoundError(
            code="USER_NOT_FOUND",
            message="User not found.",
        )

    if target.id == actor_user_id and payload.role != "admin":
        db.rollback()
        raise ConflictError(
            code="CANNOT_DEMOTE_SELF",
            message="You cannot demote your own administrator account.",
        )

    if (
        target.role == "admin"
        and target.is_active
        and payload.role != "admin"
        and len(active_admins) <= 1
    ):
        db.rollback()
        raise ConflictError(
            code="CANNOT_REMOVE_LAST_ADMIN",
            message="Cannot remove the last active administrator.",
        )

    before_state = _user_audit_snapshot(target)
    target.role = payload.role
    revoked_count = revoke_all_user_sessions(db, target.id)

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="USER_ROLE_UPDATED",
        resource_type="user",
        resource_id=str(target.id),
        before_state=before_state,
        after_state={
            **_user_audit_snapshot(target),
            "revoked_sessions_count": revoked_count,
        },
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(target)
    return AdminUserMutationResponse(
        user=AdminUserDTO.model_validate(target),
        revoked_sessions_count=revoked_count,
    )


def update_admin_user_status(
    db: Session,
    *,
    target_user_id: uuid.UUID,
    payload: UpdateUserStatusRequest,
    actor_user_id: uuid.UUID,
    ip_address: str | None,
) -> AdminUserMutationResponse:
    """Update a user's active status with self-deactivation & last-admin guards."""
    target, active_admins = _lock_target_and_active_admins(db, target_user_id)
    if target is None:
        db.rollback()
        raise NotFoundError(
            code="USER_NOT_FOUND",
            message="User not found.",
        )

    if target.id == actor_user_id and not payload.is_active:
        db.rollback()
        raise ConflictError(
            code="CANNOT_DEACTIVATE_SELF",
            message="You cannot deactivate your own administrator account.",
        )

    if (
        target.role == "admin"
        and target.is_active
        and not payload.is_active
        and len(active_admins) <= 1
    ):
        db.rollback()
        raise ConflictError(
            code="CANNOT_REMOVE_LAST_ADMIN",
            message="Cannot deactivate the last active administrator.",
        )

    before_state = _user_audit_snapshot(target)
    target.is_active = payload.is_active
    revoked_count = revoke_all_user_sessions(db, target.id)

    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        action="USER_STATUS_UPDATED",
        resource_type="user",
        resource_id=str(target.id),
        before_state=before_state,
        after_state={
            **_user_audit_snapshot(target),
            "revoked_sessions_count": revoked_count,
        },
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(target)
    return AdminUserMutationResponse(
        user=AdminUserDTO.model_validate(target),
        revoked_sessions_count=revoked_count,
    )


def list_admin_audit_logs(
    db: Session,
    *,
    resource_type: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    pagination: PaginationParams,
) -> PaginatedResponse[AuditLogEntryDTO]:
    """List paginated append-only audit logs with actor metadata (admin only)."""
    norm_from, norm_to = _validate_optional_ts_range(from_ts, to_ts)
    clean_resource_type = _normalize_optional_filter_token(
        resource_type, field_name="resource_type", max_length=48
    )
    clean_action = _normalize_optional_filter_token(
        action, field_name="action", max_length=64
    )

    filters: list[ColumnElement[bool]] = []
    if clean_resource_type is not None:
        filters.append(AuditLog.resource_type == clean_resource_type)
    if actor_user_id is not None:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if clean_action is not None:
        filters.append(AuditLog.action == clean_action)
    if norm_from is not None:
        filters.append(ts_gte(db, AuditLog.created_at, norm_from))
    if norm_to is not None:
        filters.append(ts_lte(db, AuditLog.created_at, norm_to))

    total = int(db.scalar(select(func.count(AuditLog.id)).where(*filters)) or 0)
    if total == 0 or pagination.offset >= total:
        return PaginatedResponse(
            items=[],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            has_next=False,
        )

    rows = db.execute(
        select(AuditLog, User.email, User.full_name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    items = [
        AuditLogEntryDTO(
            id=entry.id,
            actor_user_id=entry.actor_user_id,
            actor_email=actor_email,
            actor_full_name=actor_full_name,
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            before_state=entry.before_state,
            after_state=entry.after_state,
            ip_address=str(entry.ip_address) if entry.ip_address is not None else None,
            created_at=entry.created_at,
        )
        for entry, actor_email, actor_full_name in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_next=(pagination.offset + len(items)) < total,
    )
