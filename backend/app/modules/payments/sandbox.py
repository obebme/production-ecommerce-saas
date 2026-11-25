"""Sandbox payment simulation invoking the verified in-process webhook pipeline."""

from __future__ import annotations

import json
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.security import RateLimiterProtocol, enforce_rate_limit, sign_webhook_payload
from app.models.order import Order
from app.models.payment import PaymentIntent
from app.modules.identity.dependencies import AuthContext
from app.modules.orders.authorization import authorize_order_access
from app.modules.orders.common import SANDBOX_PROVIDER
from app.modules.payments.schemas import (
    SimulateSandboxPaymentRequest,
    SimulateSandboxPaymentResponse,
)
from app.modules.payments.webhooks import (
    _process_verified_webhook_tx,
    _verify_and_parse_webhook,
)

_PROVIDER_INTENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _normalize_valid_provider_intent_id_or_none(raw_id: str | None) -> str | None:
    if raw_id is None or len(raw_id) > 128:
        return None
    if not _PROVIDER_INTENT_ID_RE.fullmatch(raw_id):
        return None
    return raw_id


def _enforce_guest_simulation_rate_limit(
    rate_limiter: RateLimiterProtocol,
    *,
    auth: AuthContext | None,
    client_ip_key: str,
) -> bool:
    """Rate-limit anonymous and non-staff/admin callers probing intents or guest orders."""
    if auth is None:
        enforce_rate_limit(
            rate_limiter,
            key=f"payments:guest_lookup:ip:{client_ip_key}",
            limit=30,
            window_seconds=60,
        )
        return True
    if auth.user.role not in ("staff", "admin"):
        enforce_rate_limit(
            rate_limiter,
            key=f"payments:guest_lookup:user:{auth.user.id}",
            limit=30,
            window_seconds=60,
        )
        return True
    return False


def simulate_sandbox_payment(
    db: Session,
    *,
    provider_intent_id: str,
    payload: SimulateSandboxPaymentRequest,
    auth: AuthContext | None,
    guest_token_header: str | None,
    guest_orders_cookie: str | None,
    settings: Settings,
    rate_limiter: RateLimiterProtocol,
    client_ip_key: str,
) -> SimulateSandboxPaymentResponse:
    """Simulate a sandbox payment outcome by invoking the verified webhook pipeline in-process."""
    if not settings.payment_sandbox_mode:
        raise ForbiddenError(
            code="SANDBOX_MODE_DISABLED",
            message="Sandbox payment simulation is disabled.",
        )

    valid_intent_id = _normalize_valid_provider_intent_id_or_none(provider_intent_id)
    if valid_intent_id is None:
        _enforce_guest_simulation_rate_limit(
            rate_limiter,
            auth=auth,
            client_ip_key=client_ip_key,
        )
        raise NotFoundError(
            code="PAYMENT_INTENT_NOT_FOUND",
            message="Payment intent not found.",
        )

    lookup_row = db.execute(
        select(PaymentIntent.id, Order.order_number, Order.user_id)
        .join(Order, Order.id == PaymentIntent.order_id)
        .where(PaymentIntent.provider_intent_id == valid_intent_id)
    ).first()
    if lookup_row is None:
        _enforce_guest_simulation_rate_limit(
            rate_limiter,
            auth=auth,
            client_ip_key=client_ip_key,
        )
        raise NotFoundError(
            code="PAYMENT_INTENT_NOT_FOUND",
            message="Payment intent not found.",
        )

    _intent_id, order_number, order_user_id = lookup_row
    applied_guest_rate_limit = False
    if order_user_id is None:
        applied_guest_rate_limit = _enforce_guest_simulation_rate_limit(
            rate_limiter,
            auth=auth,
            client_ip_key=client_ip_key,
        )

    order = authorize_order_access(
        db,
        order_number=order_number,
        auth=auth,
        guest_token_header=guest_token_header,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=client_ip_key,
        allow_staff_admin=True,
        skip_guest_rate_limit=applied_guest_rate_limit,
    )

    # Lock all PaymentIntent rows for order_id in id ASC order first in canonical hierarchy.
    order_intents = list(
        db.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.order_id == order.id)
            .order_by(PaymentIntent.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    locked_intent = next(
        (
            candidate
            for candidate in order_intents
            if candidate.provider_intent_id == valid_intent_id
        ),
        None,
    )
    if locked_intent is None:
        raise NotFoundError(
            code="PAYMENT_INTENT_NOT_FOUND",
            message="Payment intent not found.",
        )

    if (
        locked_intent.provider != SANDBOX_PROVIDER
        or locked_intent.status not in ("REQUIRES_ACTION", "PROCESSING")
    ):
        db.rollback()
        raise ConflictError(
            code="PAYMENT_INTENT_ALREADY_FINAL",
            message="Payment intent has already reached a final state.",
        )

    event_id = f"evt_sandbox_{uuid.uuid4().hex}"
    event_type = (
        "payment_intent.succeeded"
        if payload.outcome == "succeeded"
        else "payment_intent.failed"
    )
    failure_code = payload.failure_code if payload.outcome == "failed" else None
    failure_message: str | None = None
    if payload.outcome == "failed":
        failure_message = (
            f"Simulated payment failure ({failure_code})."
            if failure_code
            else "Simulated sandbox payment failure."
        )

    signed_dict = {
        "event_id": event_id,
        "event_type": event_type,
        "data": {
            "provider_intent_id": locked_intent.provider_intent_id,
            "order_number": order.order_number,
            "amount_cents": order.total_cents,
            "currency": order.currency,
            "failure_code": failure_code,
            "failure_message": failure_message,
        },
    }
    raw_body = json.dumps(signed_dict, separators=(",", ":")).encode("utf-8")
    ts_now = int(time.time())
    signature_header = sign_webhook_payload(
        ts_now,
        raw_body,
        settings.payment_webhook_secret,
    )

    verified_payload = _verify_and_parse_webhook(
        provider=SANDBOX_PROVIDER,
        raw_body=raw_body,
        timestamp_header=str(ts_now),
        signature_header=signature_header,
        webhook_secret=settings.payment_webhook_secret,
        now_ts=ts_now,
    )
    webhook_response, updated_order = _process_verified_webhook_tx(
        db,
        provider=SANDBOX_PROVIDER,
        payload=verified_payload,
        prelocked_intent=locked_intent,
    )
    final_order = updated_order or order
    return SimulateSandboxPaymentResponse(
        event_id=event_id,
        webhook_status=webhook_response.status,
        order_status=final_order.status,
        payment_status=final_order.payment_status,
    )
