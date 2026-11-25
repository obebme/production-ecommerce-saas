"""Backward-compatible facade for Module 6 (Payments & Webhooks) services."""

from __future__ import annotations

from app.modules.orders.common import SANDBOX_PROVIDER, _acquire_advisory_xact_lock
from app.modules.payments.adapters.simpay import (
    MAX_WEBHOOK_BODY_BYTES,
    MAX_WEBHOOK_TIMESTAMP_SKEW_SECONDS,
    _reject_nonfinite_json,
)
from app.modules.payments.intents import (
    _handle_existing_intent_idempotency,
    create_payment_intent,
)
from app.modules.payments.provider import get_default_payment_registry
from app.modules.payments.reconciliation import reconcile_payment_intent
from app.modules.payments.sandbox import (
    _enforce_guest_simulation_rate_limit,
    _normalize_valid_provider_intent_id_or_none,
    simulate_sandbox_payment,
)
from app.modules.payments.webhooks import (
    _claim_webhook_event_for_processing,
    _persist_webhook_unexpected_failure,
    _process_verified_webhook_tx,
    _record_webhook_failure_and_raise,
    _verify_and_parse_webhook,
    process_webhook_bytes,
)

ALLOWED_WEBHOOK_PROVIDERS = frozenset({SANDBOX_PROVIDER})


def get_allowed_webhook_providers() -> frozenset[str]:
    """Return currently registered payment providers accepted by webhook routes."""
    return get_default_payment_registry().allowed_webhook_providers()


__all__ = [
    "ALLOWED_WEBHOOK_PROVIDERS",
    "MAX_WEBHOOK_BODY_BYTES",
    "MAX_WEBHOOK_TIMESTAMP_SKEW_SECONDS",
    "_acquire_advisory_xact_lock",
    "_claim_webhook_event_for_processing",
    "_enforce_guest_simulation_rate_limit",
    "_handle_existing_intent_idempotency",
    "_normalize_valid_provider_intent_id_or_none",
    "_persist_webhook_unexpected_failure",
    "_process_verified_webhook_tx",
    "_record_webhook_failure_and_raise",
    "_reject_nonfinite_json",
    "_verify_and_parse_webhook",
    "create_payment_intent",
    "get_allowed_webhook_providers",
    "process_webhook_bytes",
    "reconcile_payment_intent",
    "simulate_sandbox_payment",
]
