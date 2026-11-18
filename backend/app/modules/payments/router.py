"""FastAPI routes for Module 6 (/api/v1/payments/* and /api/v1/webhooks/payments/*)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_app_settings
from app.core.database import get_db
from app.core.errors import GUEST_ORDERS_COOKIE_NAME, NotFoundError, PayloadTooLargeError
from app.core.security import RateLimiterProtocol, get_client_ip_key, get_rate_limiter
from app.modules.identity.dependencies import AuthContext, get_optional_auth_context_with_csrf
from app.modules.payments import service
from app.modules.payments.schemas import (
    CreatePaymentIntentRequest,
    PaymentIntentResponse,
    SimulateSandboxPaymentRequest,
    SimulateSandboxPaymentResponse,
    WebhookResponse,
)

router = APIRouter(tags=["payments"])


async def _read_bounded_webhook_body(request: Request) -> bytes:
    """Stream webhook request body up to MAX_WEBHOOK_BODY_BYTES before buffering."""
    content_length_raw = request.headers.get("content-length")
    if content_length_raw is not None:
        try:
            content_length = int(content_length_raw.strip())
        except ValueError:
            content_length = -1
        if content_length > service.MAX_WEBHOOK_BODY_BYTES:
            raise PayloadTooLargeError(
                code="PAYLOAD_TOO_LARGE",
                message=f"Webhook payload exceeds {service.MAX_WEBHOOK_BODY_BYTES} bytes.",
            )

    buffer = bytearray()
    async for chunk in request.stream():
        if len(buffer) + len(chunk) > service.MAX_WEBHOOK_BODY_BYTES:
            raise PayloadTooLargeError(
                code="PAYLOAD_TOO_LARGE",
                message=f"Webhook payload exceeds {service.MAX_WEBHOOK_BODY_BYTES} bytes.",
            )
        buffer.extend(chunk)
    return bytes(buffer)


@router.post(
    "/payments/intents",
    status_code=status.HTTP_201_CREATED,
    response_model=PaymentIntentResponse,
)
def create_payment_intent(
    payload: CreatePaymentIntentRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
) -> PaymentIntentResponse:
    """Create or idempotently replay a payment intent for an unpaid PENDING_PAYMENT order."""
    intent_response, is_replay = service.create_payment_intent(
        db,
        payload=payload,
        raw_idempotency_key=request.headers.get("Idempotency-Key"),
        auth=auth,
        guest_token_header=request.headers.get("X-Guest-Order-Token"),
        guest_orders_cookie=request.cookies.get(GUEST_ORDERS_COOKIE_NAME),
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=get_client_ip_key(request),
    )
    if is_replay:
        response.status_code = status.HTTP_200_OK
    return intent_response


@router.post(
    "/payments/sandbox/{provider_intent_id}/simulate",
    status_code=status.HTTP_200_OK,
    response_model=SimulateSandboxPaymentResponse,
)
def simulate_sandbox_payment(
    provider_intent_id: str,
    payload: SimulateSandboxPaymentRequest,
    request: Request,
    auth: Annotated[AuthContext | None, Depends(get_optional_auth_context_with_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rate_limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
) -> SimulateSandboxPaymentResponse:
    """Simulate a sandbox payment outcome via the verified in-process webhook pipeline."""
    return service.simulate_sandbox_payment(
        db,
        provider_intent_id=provider_intent_id,
        payload=payload,
        auth=auth,
        guest_token_header=request.headers.get("X-Guest-Order-Token"),
        guest_orders_cookie=request.cookies.get(GUEST_ORDERS_COOKIE_NAME),
        settings=settings,
        rate_limiter=rate_limiter,
        client_ip_key=get_client_ip_key(request),
    )


@router.post(
    "/webhooks/payments/{provider}",
    status_code=status.HTTP_200_OK,
    response_model=WebhookResponse,
)
async def receive_payment_webhook(
    provider: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> WebhookResponse:
    """Receive and verify provider webhooks strictly in 6-step security order."""
    if provider not in service.ALLOWED_WEBHOOK_PROVIDERS:
        raise NotFoundError(
            code="PROVIDER_NOT_FOUND",
            message="Payment provider not found.",
        )

    raw_body = await _read_bounded_webhook_body(request)
    return await run_in_threadpool(
        service.process_webhook_bytes,
        db,
        provider=provider,
        raw_body=raw_body,
        timestamp_header=request.headers.get("X-Webhook-Timestamp"),
        signature_header=request.headers.get("X-Webhook-Signature"),
        settings=settings,
    )
