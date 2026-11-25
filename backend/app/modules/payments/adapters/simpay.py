"""Sandbox SimPay payment provider adapter."""

from __future__ import annotations

import json
import time
import uuid
from typing import NoReturn

from pydantic import ValidationError

from app.core.errors import (
    BadRequestError,
    PayloadTooLargeError,
    UnauthorizedError,
    ValidationAppError,
)
from app.core.security import verify_webhook_signature
from app.modules.payments.provider import ProviderIntentResult, ProviderStatusResult
from app.modules.payments.schemas import WebhookPayload

SANDBOX_PROVIDER = "sandbox_simpay"
MAX_WEBHOOK_BODY_BYTES = 65_536
MAX_WEBHOOK_TIMESTAMP_SKEW_SECONDS = 300


def _reject_nonfinite_json(token: str) -> NoReturn:
    raise ValueError(f"Invalid non-finite JSON constant: {token}")


class SandboxSimpayAdapter:
    """In-process deterministic sandbox adapter for local/staging/CI checkout flows."""

    @property
    def provider_name(self) -> str:
        return SANDBOX_PROVIDER

    @property
    def is_production_ready(self) -> bool:
        return False

    def create_intent(
        self,
        *,
        order_number: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderIntentResult:
        _ = (order_number, amount_cents, currency, idempotency_key)
        return ProviderIntentResult(
            provider=self.provider_name,
            provider_intent_id=f"pi_sandbox_{uuid.uuid4().hex}",
            status="REQUIRES_ACTION",
        )

    def retrieve_intent(
        self,
        *,
        provider_intent_id: str,
    ) -> ProviderStatusResult:
        return ProviderStatusResult(
            provider=self.provider_name,
            provider_intent_id=provider_intent_id,
            status="REQUIRES_ACTION",
        )

    def cancel_intent(
        self,
        *,
        provider_intent_id: str,
    ) -> None:
        _ = provider_intent_id
        return None

    def refund(
        self,
        *,
        provider_intent_id: str,
        amount_cents: int,
        currency: str,
        reason: str | None,
    ) -> str:
        _ = (provider_intent_id, amount_cents, currency, reason)
        return f"re_sandbox_{uuid.uuid4().hex}"

    def verify_and_parse_webhook(
        self,
        *,
        raw_body: bytes,
        timestamp_header: str | None,
        signature_header: str | None,
        webhook_secret: str,
        now_ts: int | None = None,
    ) -> WebhookPayload:
        """Execute strict steps 1..4 of webhook verification before any DB access."""
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
        if not ts_clean or len(ts_clean) > 18 or not (
            ts_clean.isdigit() or (ts_clean[0] == "-" and ts_clean[1:].isdigit())
        ):
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
