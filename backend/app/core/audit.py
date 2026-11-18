"""Shared append-only audit logging with recursive secret redaction and IP normalization."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import parse_client_ip
from app.models.identity import AuditLog

REDACTED_VALUE = "[REDACTED]"
_MAX_SANITIZE_DEPTH = 16

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "csrf_token",
        "x_csrf_token",
        "secret",
        "cookie",
        "cookies",
        "set_cookie",
        "authorization",
        "raw_token",
        "token_hash",
        "guest_access_token",
        "x_guest_order_token",
        "ecom_session",
        "ecom_csrf",
        "ecom_guest_cart",
        "ecom_guest_orders",
        "csrf_hmac_secret",
        "guest_order_hmac_secret",
        "payment_webhook_secret",
    }
)

_SENSITIVE_KEY_MARKERS = (
    "password",
    "secret",
    "cookie",
    "authorization",
    "csrf",
    "api_key",
    "private_key",
    "access_key",
    "signing_key",
    "credential",
    "passphrase",
)


def _is_sensitive_key(key: str) -> bool:
    """Return True if an audit payload key may contain credentials, tokens, or secrets."""
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
        return True
    return normalized.endswith("_token") or normalized.startswith("token_")


def _sanitize_value(value: Any, *, depth: int = 0) -> Any:
    """Recursively convert values to JSON-safe structures while redacting secret keys."""
    if depth > _MAX_SANITIZE_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return value
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, item_val in value.items():
            key_str = str(raw_key).replace("\x00", "")
            if _is_sensitive_key(key_str):
                sanitized[key_str] = REDACTED_VALUE
            else:
                sanitized[key_str] = _sanitize_value(item_val, depth=depth + 1)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return [_sanitize_value(item, depth=depth + 1) for item in value]
    return str(value)


def sanitize_audit_payload(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a JSON-safe, secret-redacted copy of an audit state mapping."""
    if payload is None:
        return None
    sanitized = _sanitize_value(payload)
    if isinstance(sanitized, dict):
        return sanitized
    return {"value": sanitized}


def record_audit_log(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    before_state: Mapping[str, Any] | None = None,
    after_state: Mapping[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Append a secret-redacted AuditLog row within the caller's active transaction."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action.strip()[:64],
        resource_type=resource_type.strip()[:48],
        resource_id=str(resource_id).strip()[:64],
        before_state=sanitize_audit_payload(before_state),
        after_state=sanitize_audit_payload(after_state),
        ip_address=parse_client_ip(ip_address),
    )
    db.add(entry)
    return entry
