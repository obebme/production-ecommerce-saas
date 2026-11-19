"""Monotonic OrderEvent creation helper preventing sub-microsecond timeline ties."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import _to_utc_datetime
from app.models.order import OrderEvent


def append_order_event(
    db: Session,
    *,
    order_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    event_type: str,
    from_status: str | None,
    to_status: str,
    metadata_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> OrderEvent:
    """Append an OrderEvent with a strictly increasing UTC timestamp per order.

    Callers must ensure the target Order row is already flushed (checkout) or locked
    FOR UPDATE before calling this helper so concurrent transitions serialize cleanly.
    """
    base_time = _to_utc_datetime(now) if now is not None else datetime.now(UTC)
    last_created_at = db.scalar(
        select(func.max(OrderEvent.created_at)).where(OrderEvent.order_id == order_id)
    )
    latest_utc = _to_utc_datetime(last_created_at) if last_created_at is not None else None
    for pending in db.new:
        if (
            isinstance(pending, OrderEvent)
            and pending.order_id == order_id
            and pending.created_at is not None
        ):
            pending_utc = _to_utc_datetime(pending.created_at)
            if latest_utc is None or pending_utc > latest_utc:
                latest_utc = pending_utc

    if latest_utc is not None and base_time <= latest_utc:
        base_time = latest_utc + timedelta(microseconds=1)

    event = OrderEvent(
        order_id=order_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        metadata_json={} if metadata_json is None else metadata_json,
        created_at=base_time,
    )
    db.add(event)
    return event
