"""Payment intent creation, strict 6-step HMAC webhook processing, and sandbox simulation."""

from __future__ import annotations

import hmac
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any, NoReturn

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
    UnauthorizedError,
    ValidationAppError,
)
from app.core.security import (
    RateLimiterProtocol,
    enforce_rate_limit,
    hash_token,
    sign_webhook_payload,
    verify_webhook_signature,
)
from app.models.catalog import Category, Product
from app.models.inventory import InventoryReservation, InventoryStock, ProductVariant
from app.models.order import Order
from app.models.payment import PaymentIntent, PaymentWebhookEvent
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.identity.dependencies import AuthContext
from app.modules.inventory.service import reclaim_expired_reservations_for_variants
from app.modules.orders.service import (
    SANDBOX_PROVIDER,
    authorize_order_access,
    compute_payment_intent_fingerprint,
    mark_order_paid,
    mark_order_payment_failed,
    mark_order_refunded,
    validate_idempotency_key,
)
from app.modules.payments.schemas import (
    CreatePaymentIntentRequest,
    PaymentIntentDTO,
    PaymentIntentResponse,
    SimulateSandboxPaymentRequest,
    SimulateSandboxPaymentResponse,
    WebhookPayload,
    WebhookResponse,
)

ALLOWED_WEBHOOK_PROVIDERS = frozenset({SANDBOX_PROVIDER})
MAX_WEBHOOK_BODY_BYTES = 65_536
MAX_WEBHOOK_TIMESTAMP_SKEW_SECONDS = 300

_PROVIDER_INTENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_TIMESTAMP_HEADER_RE = re.compile(r"^-?[0-9]{1,18}$")


def _normalize_valid_provider_intent_id_or_none(raw_id: str | None) -> str | None:
    if raw_id is None or len(raw_id) > 128:
        return None
    if not _PROVIDER_INTENT_ID_RE.fullmatch(raw_id):
        return None
    return raw_id


def _acquire_advisory_xact_lock(db: Session, scoped_key: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": scoped_key},
        )


def _reject_nonfinite_json(token: str) -> NoReturn:
    raise ValueError(f"Invalid non-finite JSON constant: {token}")


def create_payment_intent(
    db: Session,
    *,
    payload: CreatePaymentIntentRequest,
    raw_idempotency_key: str | None,
    auth: AuthContext | None,
    guest_token_header: str | None,
    guest_orders_cookie: str | None,
    settings: Settings,
    rate_limiter: RateLimiterProtocol,
    client_ip_key: str,
) -> tuple[PaymentIntentResponse, bool]:
    """Create or idempotently replay a sandbox PaymentIntent for a PENDING_PAYMENT order."""
    client_key = validate_idempotency_key(raw_idempotency_key)
    order = authorize_order_access(
        db,
        order_number=payload.order_number,
        auth=auth,
        guest_token_header=guest_token_header,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=client_ip_key,
    )

    if order.user_id is not None and auth is not None:
        actor_scope = f"user:{auth.user.id}"
    else:
        actor_scope = f"guest_order:{order.id}"

    scoped_key = hash_token(f"{actor_scope}:{client_key}")
    fingerprint = compute_payment_intent_fingerprint(
        order.order_number,
        order.total_cents,
        order.currency,
    )

    # Lazy expired-reservation reclamation before taking locks.
    if order.status == "PENDING_PAYMENT":
        active_variants = list(
            db.scalars(
                select(InventoryReservation.variant_id)
                .where(
                    InventoryReservation.order_id == order.id,
                    InventoryReservation.status == "ACTIVE",
                )
                .distinct()
            ).all()
        )
        if active_variants and reclaim_expired_reservations_for_variants(
            db, active_variants
        ) > 0:
            db.commit()

    _acquire_advisory_xact_lock(db, scoped_key)

    existing_intent = db.scalar(
        select(PaymentIntent)
        .where(PaymentIntent.idempotency_key == scoped_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing_intent is not None:
        return (
            _handle_existing_intent_idempotency(
                db,
                existing_intent=existing_intent,
                expected_order_id=order.id,
                expected_fingerprint=fingerprint,
            ),
            True,
        )

    # Universal lock order: PaymentIntent -> Order -> InventoryReservation.
    order_intents = list(
        db.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.order_id == order.id)
            .order_by(PaymentIntent.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    locked_order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert locked_order is not None

    reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == locked_order.id)
            .order_by(InventoryReservation.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    now = datetime.now(UTC)
    has_unexpired_reservation = any(
        res.status == "ACTIVE" and ensure_utc_datetime(res.expires_at) > now
        for res in reservations
    )
    if (
        locked_order.status != "PENDING_PAYMENT"
        or locked_order.payment_status not in ("REQUIRES_PAYMENT", "FAILED")
        or not has_unexpired_reservation
    ):
        db.rollback()
        raise ConflictError(
            code="INVALID_ORDER_STATE_TRANSITION",
            message="Order cannot accept a new payment intent in its current state.",
        )

    for prior_intent in order_intents:
        if prior_intent.status in ("REQUIRES_ACTION", "FAILED"):
            prior_intent.status = "CANCELLED"

    new_intent = PaymentIntent(
        order_id=locked_order.id,
        provider=SANDBOX_PROVIDER,
        provider_intent_id=f"pi_sandbox_{uuid.uuid4().hex}",
        idempotency_key=scoped_key,
        request_fingerprint=fingerprint,
        amount_cents=locked_order.total_cents,
        currency=locked_order.currency,
        status="REQUIRES_ACTION",
    )
    db.add(new_intent)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raced_intent = db.scalar(
            select(PaymentIntent).where(PaymentIntent.idempotency_key == scoped_key)
        )
        if raced_intent is not None:
            return (
                _handle_existing_intent_idempotency(
                    db,
                    existing_intent=raced_intent,
                    expected_order_id=order.id,
                    expected_fingerprint=fingerprint,
                ),
                True,
            )
        raise ConflictError(
            code="PAYMENT_INTENT_CONFLICT",
            message="Payment intent creation conflicted with another request.",
        ) from exc

    return (
        PaymentIntentResponse(payment_intent=PaymentIntentDTO.model_validate(new_intent)),
        False,
    )


def _handle_existing_intent_idempotency(
    db: Session,
    *,
    existing_intent: PaymentIntent,
    expected_order_id: uuid.UUID,
    expected_fingerprint: str,
) -> PaymentIntentResponse:
    if existing_intent.order_id != expected_order_id or not hmac.compare_digest(
        existing_intent.request_fingerprint, expected_fingerprint
    ):
        db.rollback()
        raise ConflictError(
            code="IDEMPOTENCY_KEY_CONFLICT",
            message="Idempotency-Key was already used with a different payment request.",
        )
    dto = PaymentIntentDTO.model_validate(existing_intent)
    db.rollback()
    return PaymentIntentResponse(payment_intent=dto)


def _verify_and_parse_webhook(
    *,
    provider: str,
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    webhook_secret: str,
    now_ts: int | None = None,
) -> WebhookPayload:
    """Execute strict steps 1..4 of webhook verification before any DB access."""
    if provider not in ALLOWED_WEBHOOK_PROVIDERS:
        raise NotFoundError(
            code="PROVIDER_NOT_FOUND",
            message="Payment provider not found.",
        )

    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        raise PayloadTooLargeError(
            code="PAYLOAD_TOO_LARGE",
            message=f"Webhook payload exceeds {MAX_WEBHOOK_BODY_BYTES} bytes.",
        )

    if timestamp_header is None or not timestamp_header.isascii():
        raise BadRequestError(
            code="WEBHOOK_TIMESTAMP_EXPIRED",
            message="Missing or invalid X-Webhook-Timestamp header.",
        )
    ts_clean = timestamp_header.strip()
    if not _TIMESTAMP_HEADER_RE.fullmatch(ts_clean):
        raise BadRequestError(
            code="WEBHOOK_TIMESTAMP_EXPIRED",
            message="Invalid X-Webhook-Timestamp header.",
        )
    ts_val = int(ts_clean)
    current_ts = int(time.time()) if now_ts is None else now_ts
    if abs(current_ts - ts_val) > MAX_WEBHOOK_TIMESTAMP_SKEW_SECONDS:
        raise BadRequestError(
            code="WEBHOOK_TIMESTAMP_EXPIRED",
            message="Webhook timestamp is outside the allowed 300-second replay window.",
        )

    if not verify_webhook_signature(signature_header, ts_val, raw_body, webhook_secret):
        raise UnauthorizedError(
            code="WEBHOOK_SIGNATURE_INVALID",
            message="Invalid webhook signature.",
        )

    try:
        decoded_body = raw_body.decode("utf-8")
        parsed_json = json.loads(decoded_body, parse_constant=_reject_nonfinite_json)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="Malformed JSON webhook payload.",
        ) from exc

    if not isinstance(parsed_json, dict):
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="Webhook payload must be a JSON object.",
        )

    try:
        return WebhookPayload.model_validate(parsed_json)
    except ValidationError as exc:
        sanitized_errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": str(err.get("msg", "Invalid value")),
                "type": str(err.get("type", "value_error")),
            }
            for err in exc.errors()
        ]
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="Webhook payload validation failed.",
            details={"errors": sanitized_errors},
        ) from exc


def _insert_webhook_event_dedup(
    db: Session,
    *,
    provider: str,
    payload: WebhookPayload,
    now: datetime,
) -> uuid.UUID | None:
    """Insert verified webhook event idempotently via ON CONFLICT DO NOTHING."""
    event_row_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": event_row_id,
        "provider": provider,
        "provider_event_id": payload.event_id,
        "event_type": payload.event_type,
        "signature_verified": True,
        "payload_json": payload.model_dump(mode="json"),
        "status": "PROCESSING",
        "received_at": now,
    }
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        stmt = (
            pg_insert(PaymentWebhookEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
            .returning(PaymentWebhookEvent.id)
        )
        inserted_id = db.scalar(stmt)
        db.flush()
        return inserted_id
    if dialect_name == "sqlite":
        stmt_sqlite = (
            sqlite_insert(PaymentWebhookEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
            .returning(PaymentWebhookEvent.id)
        )
        inserted_id = db.scalar(stmt_sqlite)
        db.flush()
        return inserted_id

    existing = db.scalar(
        select(PaymentWebhookEvent.id).where(
            PaymentWebhookEvent.provider == provider,
            PaymentWebhookEvent.provider_event_id == payload.event_id,
        )
    )
    if existing is not None:
        return None
    db.add(PaymentWebhookEvent(**values))
    db.flush()
    return event_row_id


def _record_webhook_failure_and_raise(
    db: Session,
    *,
    webhook_event_id: uuid.UUID,
    error_message: str,
    now: datetime,
) -> NoReturn:
    event_row = db.scalar(
        select(PaymentWebhookEvent).where(PaymentWebhookEvent.id == webhook_event_id)
    )
    if event_row is not None:
        event_row.status = "FAILED"
        event_row.error_message = error_message[:500]
        event_row.processed_at = now
        db.commit()
    else:
        db.rollback()
    raise ValidationAppError(
        code="WEBHOOK_PAYLOAD_MISMATCH",
        message=error_message,
    )


def _process_verified_webhook_tx(
    db: Session,
    *,
    provider: str,
    payload: WebhookPayload,
    prelocked_intent: PaymentIntent | None = None,
) -> tuple[WebhookResponse, Order | None]:
    """Execute steps 5 and 6 of webhook processing inside the active DB transaction."""
    now = datetime.now(UTC)
    webhook_event_id = _insert_webhook_event_dedup(
        db,
        provider=provider,
        payload=payload,
        now=now,
    )
    if webhook_event_id is None:
        db.rollback()
        return WebhookResponse(received=True, status="IGNORED_DUPLICATE"), None

    # Step 6: Universal lock hierarchy:
    # PaymentIntent (id ASC) -> Order -> InventoryReservation -> InventoryStock.
    sibling_intents: list[PaymentIntent] = []
    intent = prelocked_intent
    if intent is None:
        if payload.event_type == "charge.refunded":
            target_order_id = db.scalar(
                select(PaymentIntent.order_id).where(
                    PaymentIntent.provider == provider,
                    PaymentIntent.provider_intent_id == payload.data.provider_intent_id,
                )
            )
            if target_order_id is not None:
                sibling_intents = list(
                    db.scalars(
                        select(PaymentIntent)
                        .where(PaymentIntent.order_id == target_order_id)
                        .order_by(PaymentIntent.id.asc())
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    ).all()
                )
                for candidate_intent in sibling_intents:
                    if (
                        candidate_intent.provider == provider
                        and candidate_intent.provider_intent_id
                        == payload.data.provider_intent_id
                    ):
                        intent = candidate_intent
                        break
        else:
            intent = db.scalar(
                select(PaymentIntent)
                .where(
                    PaymentIntent.provider == provider,
                    PaymentIntent.provider_intent_id == payload.data.provider_intent_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
    else:
        sibling_intents = [intent]
    if intent is None:
        _record_webhook_failure_and_raise(
            db,
            webhook_event_id=webhook_event_id,
            error_message=(
                f"Payment intent '{payload.data.provider_intent_id}' "
                f"not found for provider '{provider}'."
            ),
            now=now,
        )

    order = db.scalar(
        select(Order)
        .where(Order.id == intent.order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        _record_webhook_failure_and_raise(
            db,
            webhook_event_id=webhook_event_id,
            error_message="Order associated with payment intent was not found.",
            now=now,
        )

    if payload.data.order_number != order.order_number:
        _record_webhook_failure_and_raise(
            db,
            webhook_event_id=webhook_event_id,
            error_message="Webhook order_number does not match payment intent order.",
            now=now,
        )
    if (
        payload.data.amount_cents != order.total_cents
        or payload.data.amount_cents != intent.amount_cents
    ):
        _record_webhook_failure_and_raise(
            db,
            webhook_event_id=webhook_event_id,
            error_message="Webhook amount_cents does not match authoritative order total.",
            now=now,
        )
    if payload.data.currency != order.currency or payload.data.currency != intent.currency:
        _record_webhook_failure_and_raise(
            db,
            webhook_event_id=webhook_event_id,
            error_message="Webhook currency does not match authoritative order currency.",
            now=now,
        )

    reservations = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == order.id)
            .order_by(InventoryReservation.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    variant_ids = sorted({res.variant_id for res in reservations})

    stocks_by_variant: dict[uuid.UUID, InventoryStock] = {}
    variants_by_id: dict[uuid.UUID, tuple[ProductVariant, Product, Category]] = {}
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
        variant_rows = db.execute(
            select(ProductVariant, Product, Category)
            .join(Product, Product.id == ProductVariant.product_id)
            .join(Category, Category.id == Product.category_id)
            .where(ProductVariant.id.in_(variant_ids))
            .order_by(ProductVariant.id.asc())
        ).all()
        variants_by_id = {
            variant.id: (variant, product, category)
            for variant, product, category in variant_rows
        }

    if payload.event_type == "payment_intent.succeeded":
        mark_order_paid(
            db,
            order=order,
            intent=intent,
            reservations=reservations,
            stocks_by_variant=stocks_by_variant,
            variants_by_id=variants_by_id,
            event_id=payload.event_id,
            now=now,
        )
    elif payload.event_type == "payment_intent.failed":
        mark_order_payment_failed(
            db,
            order=order,
            intent=intent,
            event_id=payload.event_id,
            failure_code=payload.data.failure_code,
            failure_message=payload.data.failure_message,
        )
    elif payload.event_type == "charge.refunded":
        mark_order_refunded(
            db,
            order=order,
            intent=intent,
            sibling_intents=sibling_intents,
            reservations=reservations,
            stocks_by_variant=stocks_by_variant,
            event_id=payload.event_id,
        )

    webhook_row = db.scalar(
        select(PaymentWebhookEvent).where(PaymentWebhookEvent.id == webhook_event_id)
    )
    if webhook_row is not None:
        webhook_row.status = "PROCESSED"
        webhook_row.processed_at = now

    db.commit()
    return WebhookResponse(received=True, status="PROCESSED"), order


def process_webhook_bytes(
    db: Session,
    *,
    provider: str,
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    settings: Settings,
) -> WebhookResponse:
    """Verify raw webhook request and apply idempotent transactional state update."""
    payload = _verify_and_parse_webhook(
        provider=provider,
        raw_body=raw_body,
        timestamp_header=timestamp_header,
        signature_header=signature_header,
        webhook_secret=settings.payment_webhook_secret,
    )
    response, _order = _process_verified_webhook_tx(
        db,
        provider=provider,
        payload=payload,
    )
    return response


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

    # Lock PaymentIntent FOR UPDATE first in canonical lock hierarchy.
    locked_intent = db.scalar(
        select(PaymentIntent)
        .where(PaymentIntent.provider_intent_id == valid_intent_id)
        .with_for_update()
        .execution_options(populate_existing=True)
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
