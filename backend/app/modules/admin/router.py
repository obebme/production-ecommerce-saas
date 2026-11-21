"""FastAPI routes for Module 7 (/api/v1/admin/orders, /users, /audit-logs)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import MAX_PAGINATION_PAGE, PaginatedResponse, PaginationParams
from app.core.security import resolve_client_ip
from app.models.identity import User
from app.modules.admin import service
from app.modules.admin.schemas import (
    AdminOrderDetailDTO,
    AdminOrderListItemDTO,
    AdminUserDTO,
    AdminUserMutationResponse,
    AuditLogEntryDTO,
    FulfillOrderRequest,
    RefundOrderRequest,
    UpdateUserRoleRequest,
    UpdateUserStatusRequest,
)
from app.modules.identity.dependencies import (
    AuthContext,
    require_roles,
    require_roles_with_csrf,
)
from app.modules.identity.schemas import UserRole
from app.modules.orders.schemas import (
    CancelOrderRequest,
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _client_ip(request: Request) -> str | None:
    return resolve_client_ip(request)


@router.get(
    "/orders",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[AdminOrderListItemDTO],
)
def list_admin_orders(
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    payment_status: Annotated[PaymentStatus | None, Query()] = None,
    fulfillment_status: Annotated[FulfillmentStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    from_ts: Annotated[datetime | None, Query()] = None,
    to_ts: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[AdminOrderListItemDTO]:
    """List paginated orders across all customers/guests with status, search, and date filters."""
    return service.list_admin_orders(
        db,
        status=order_status,
        payment_status=payment_status,
        fulfillment_status=fulfillment_status,
        q=q,
        from_ts=from_ts,
        to_ts=to_ts,
        pagination=PaginationParams(page=page, page_size=page_size),
    )


@router.get(
    "/orders/{order_number}",
    status_code=status.HTTP_200_OK,
    response_model=AdminOrderDetailDTO,
)
def get_admin_order_detail(
    order_number: str,
    _actor: Annotated[User, Depends(require_roles("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminOrderDetailDTO:
    """Retrieve full administrative order detail, intents, reservations, and event history."""
    return service.get_admin_order_detail(db, order_number)


@router.post(
    "/orders/{order_number}/fulfill",
    status_code=status.HTTP_200_OK,
    response_model=AdminOrderDetailDTO,
)
def fulfill_admin_order(
    order_number: str,
    payload: FulfillOrderRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminOrderDetailDTO:
    """Transition a paid order through START_PROCESSING, SHIP, or COMPLETE with audit logging."""
    return service.fulfill_admin_order(
        db,
        order_number=order_number,
        payload=payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.post(
    "/orders/{order_number}/cancel",
    status_code=status.HTTP_200_OK,
    response_model=AdminOrderDetailDTO,
)
def cancel_admin_order(
    order_number: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("staff", "admin"))],
    db: Annotated[Session, Depends(get_db)],
    payload: Annotated[CancelOrderRequest | None, Body()] = None,
) -> AdminOrderDetailDTO:
    """Cancel an unpaid PENDING_PAYMENT order and release ACTIVE reservations."""
    return service.cancel_admin_order(
        db,
        order_number=order_number,
        reason=payload.reason if payload is not None else None,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.post(
    "/orders/{order_number}/refund",
    status_code=status.HTTP_200_OK,
    response_model=AdminOrderDetailDTO,
)
def refund_admin_order(
    order_number: str,
    payload: RefundOrderRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminOrderDetailDTO:
    """Issue a full refund on a paid order (strictly admin only) with optional restock."""
    return service.refund_admin_order(
        db,
        order_number=order_number,
        payload=payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.get(
    "/users",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[AdminUserDTO],
)
def list_admin_users(
    _actor: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    role: Annotated[UserRole | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[AdminUserDTO]:
    """List paginated user accounts for administrator management."""
    return service.list_admin_users(
        db,
        role=role,
        is_active=is_active,
        q=q,
        pagination=PaginationParams(page=page, page_size=page_size),
    )


@router.patch(
    "/users/{user_id}/role",
    status_code=status.HTTP_200_OK,
    response_model=AdminUserMutationResponse,
)
def update_admin_user_role(
    user_id: uuid.UUID,
    payload: UpdateUserRoleRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserMutationResponse:
    """Update a user's role with self-demotion/last-admin guards and session revocation."""
    return service.update_admin_user_role(
        db,
        target_user_id=user_id,
        payload=payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.patch(
    "/users/{user_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=AdminUserMutationResponse,
)
def update_admin_user_status(
    user_id: uuid.UUID,
    payload: UpdateUserStatusRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_roles_with_csrf("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserMutationResponse:
    """Activate or deactivate a user account with self-deactivation/last-admin guards."""
    return service.update_admin_user_status(
        db,
        target_user_id=user_id,
        payload=payload,
        actor_user_id=auth.user.id,
        ip_address=_client_ip(request),
    )


@router.get(
    "/audit-logs",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedResponse[AuditLogEntryDTO],
)
def list_admin_audit_logs(
    _actor: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    resource_type: Annotated[str | None, Query(max_length=48)] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    from_ts: Annotated[datetime | None, Query()] = None,
    to_ts: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGINATION_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[AuditLogEntryDTO]:
    """List paginated append-only audit logs with actor metadata (admin only)."""
    return service.list_admin_audit_logs(
        db,
        resource_type=resource_type,
        actor_user_id=actor_user_id,
        action=action,
        from_ts=from_ts,
        to_ts=to_ts,
        pagination=PaginationParams(page=page, page_size=page_size),
    )
