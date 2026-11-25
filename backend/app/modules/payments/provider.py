"""Provider-independent payment adapter protocol and registry."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from app.core.errors import NotFoundError
from app.modules.payments.schemas import PaymentIntentStatus, WebhookPayload


@dataclass(frozen=True, slots=True)
class ProviderIntentResult:
    """Result returned by a payment provider adapter when creating an intent."""

    provider: str
    provider_intent_id: str
    status: PaymentIntentStatus = "REQUIRES_ACTION"
    client_secret: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderStatusResult:
    """Authoritative status snapshot returned by a payment provider adapter."""

    provider: str
    provider_intent_id: str
    status: PaymentIntentStatus
    amount_cents: int | None = None
    currency: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class PaymentProviderAdapter(Protocol):
    """Protocol implemented by all payment service provider (PSP) adapters."""

    @property
    def provider_name(self) -> str: ...

    @property
    def is_production_ready(self) -> bool: ...

    def create_intent(
        self,
        *,
        order_number: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderIntentResult: ...

    def retrieve_intent(
        self,
        *,
        provider_intent_id: str,
    ) -> ProviderStatusResult: ...

    def cancel_intent(
        self,
        *,
        provider_intent_id: str,
    ) -> None: ...

    def refund(
        self,
        *,
        provider_intent_id: str,
        amount_cents: int,
        currency: str,
        reason: str | None,
    ) -> str: ...

    def verify_and_parse_webhook(
        self,
        *,
        raw_body: bytes,
        timestamp_header: str | None,
        signature_header: str | None,
        webhook_secret: str,
        now_ts: int | None = None,
    ) -> WebhookPayload: ...


class PaymentProviderRegistry:
    """Thread-safe registry mapping provider identifiers to PaymentProviderAdapter instances."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[str, PaymentProviderAdapter] = {}

    @staticmethod
    def _normalize_name(provider_name: str) -> str:
        return provider_name.strip().lower()

    def register(
        self,
        adapter: PaymentProviderAdapter,
        *,
        overwrite: bool = False,
    ) -> None:
        """Register a payment provider adapter."""
        name = self._normalize_name(adapter.provider_name)
        if not name:
            raise ValueError("Payment provider name must not be blank.")
        with self._lock:
            if name in self._adapters and not overwrite:
                raise ValueError(f"Payment provider '{name}' is already registered.")
            self._adapters[name] = adapter

    def unregister(self, provider_name: str) -> PaymentProviderAdapter | None:
        """Remove a registered provider adapter (primarily for isolated tests)."""
        name = self._normalize_name(provider_name)
        with self._lock:
            return self._adapters.pop(name, None)

    def get(self, provider_name: str) -> PaymentProviderAdapter:
        """Resolve a registered adapter or raise 404 PROVIDER_NOT_FOUND."""
        name = self._normalize_name(provider_name)
        with self._lock:
            adapter = self._adapters.get(name)
        if adapter is None:
            raise NotFoundError(
                code="PROVIDER_NOT_FOUND",
                message="Payment provider not found.",
            )
        return adapter

    def get_or_none(self, provider_name: str) -> PaymentProviderAdapter | None:
        """Return registered adapter or None when not registered."""
        name = self._normalize_name(provider_name)
        with self._lock:
            return self._adapters.get(name)

    def allowed_webhook_providers(self) -> frozenset[str]:
        """Return all registered provider names accepted by webhook endpoints."""
        with self._lock:
            return frozenset(self._adapters.keys())

    def has_production_provider(self) -> bool:
        """Return True if at least one production-ready adapter is registered."""
        with self._lock:
            return any(adapter.is_production_ready for adapter in self._adapters.values())

    def is_production_ready(self, provider_name: str) -> bool:
        """Return True if provider_name is registered and marked production-ready."""
        adapter = self.get_or_none(provider_name)
        return bool(adapter is not None and adapter.is_production_ready)


_default_registry = PaymentProviderRegistry()
_defaults_loaded = False
_defaults_lock = threading.Lock()


def get_default_payment_registry() -> PaymentProviderRegistry:
    """Return the process-wide PaymentProviderRegistry with built-in adapters loaded."""
    global _defaults_loaded
    if not _defaults_loaded:
        with _defaults_lock:
            if not _defaults_loaded:
                from app.modules.payments.adapters.simpay import SandboxSimpayAdapter

                if _default_registry.get_or_none("sandbox_simpay") is None:
                    _default_registry.register(SandboxSimpayAdapter())
                _defaults_loaded = True
    return _default_registry


def is_production_provider_registered(provider_name: str) -> bool:
    """Check whether provider_name is registered as a production-ready live PSP."""
    return get_default_payment_registry().is_production_ready(provider_name)
