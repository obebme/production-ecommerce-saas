"""Cryptographic hashing, HMAC CSRF protection, Origin checks, and sliding-window rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import re
import secrets
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from fastapi import Request

from app.core.errors import ForbiddenError, RateLimitExceededError

_ARGON2_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Precomputed valid Argon2id hash with identical parameters to mitigate login timing enumeration.
_DUMMY_PASSWORD_HASH: str = _ARGON2_HASHER.hash("ecom-timing-mitigation-dummy-secret")

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WEBHOOK_PATH_RE = re.compile(r"^/api/v1/webhooks/payments/[a-z0-9_-]+$")


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id (64 MiB, t=3, p=1)."""
    return _ARGON2_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    try:
        return _ARGON2_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_password_dummy() -> None:
    """Execute a constant-parameter Argon2id verification when an email is unknown."""
    verify_password("ecom-unknown-account-probe", _DUMMY_PASSWORD_HASH)


def generate_session_token() -> str:
    """Generate a 256-bit URL-safe random token for cookie sessions."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """Return lowercase hex SHA-256 digest of a raw token string."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hmac_sha256_hex(secret: str, message: bytes) -> str:
    """Compute lowercase hex HMAC-SHA256 digest."""
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _verify_ascii_hmac_hex(provided: str | None, expected_hex: str) -> bool:
    """Verify an ASCII hex digest against an expected lowercase hex digest in constant time."""
    if not provided or not provided.isascii():
        return False
    return hmac.compare_digest(provided.encode("ascii"), expected_hex.encode("ascii"))


def derive_csrf_token(session_id: uuid.UUID | str, csrf_version: int, secret: str) -> str:
    """Derive deterministic per-session CSRF token via HMAC-SHA256."""
    return _hmac_sha256_hex(secret, f"{session_id}:{csrf_version}".encode())


def verify_csrf_token(
    provided_token: str | None,
    session_id: uuid.UUID | str,
    csrf_version: int,
    secret: str,
) -> bool:
    """Verify a provided X-CSRF-Token against the session's derived HMAC token."""
    expected = derive_csrf_token(session_id, csrf_version, secret)
    return _verify_ascii_hmac_hex(provided_token, expected)


def derive_guest_order_token(
    order_id: uuid.UUID | str,
    order_number: str,
    guest_token_version: int,
    secret: str,
) -> str:
    """Derive deterministic guest order access token via HMAC-SHA256."""
    return _hmac_sha256_hex(
        secret,
        f"{order_id}:{order_number}:{guest_token_version}".encode(),
    )


def verify_guest_order_token(
    provided_token: str | None,
    order_id: uuid.UUID | str,
    order_number: str,
    guest_token_version: int,
    secret: str,
) -> bool:
    """Verify a guest order token against the derived HMAC token in constant time."""
    expected = derive_guest_order_token(order_id, order_number, guest_token_version, secret)
    return _verify_ascii_hmac_hex(provided_token, expected)


def compute_webhook_signature_hex(timestamp: int, raw_body: bytes, secret: str) -> str:
    """Compute lowercase hex HMAC-SHA256 signature over '<timestamp>.<raw_body>'."""
    signed_payload = f"{timestamp}.".encode("ascii") + raw_body
    return _hmac_sha256_hex(secret, signed_payload)


def sign_webhook_payload(timestamp: int, raw_body: bytes, secret: str) -> str:
    """Format a webhook X-Webhook-Signature header value ('v1=<hex>')."""
    return f"v1={compute_webhook_signature_hex(timestamp, raw_body, secret)}"


def verify_webhook_signature(
    provided_signature: str | None,
    timestamp: int,
    raw_body: bytes,
    secret: str,
) -> bool:
    """Verify a 'v1=<hex>' webhook signature in constant time."""
    if not provided_signature or not provided_signature.isascii():
        return False
    if not provided_signature.startswith("v1="):
        return False
    expected = compute_webhook_signature_hex(timestamp, raw_body, secret)
    return _verify_ascii_hmac_hex(provided_signature[3:], expected)


def parse_client_ip(raw_host: str | None) -> str | None:
    """Validate and normalize a client IP string for PostgreSQL INET columns."""
    if not raw_host:
        return None
    try:
        return str(ipaddress.ip_address(raw_host.strip()))
    except ValueError:
        return None


def _resolve_trusted_proxies(request: Request) -> Sequence[str]:
    """Resolve trusted proxy CIDRs from app.state.settings or Settings without failing on mocks."""
    scope = getattr(request, "scope", None)
    app_obj = scope.get("app") if isinstance(scope, Mapping) else getattr(request, "app", None)
    state = getattr(app_obj, "state", None)
    state_settings = getattr(state, "settings", None)
    if state_settings is not None:
        proxies = getattr(state_settings, "trusted_proxy_ips", None)
        if proxies is not None:
            return proxies  # type: ignore[no-any-return]
    try:
        from app.core.config import get_settings

        return get_settings().trusted_proxy_ips
    except Exception:
        return ()


def resolve_client_ip(
    request: Request,
    trusted_proxy_ips: Sequence[str] | None = None,
) -> str | None:
    """Resolve client IP, parsing X-Forwarded-For right-to-left only from trusted proxies."""
    peer_ip = parse_client_ip(request.client.host if request.client else None)
    if peer_ip is None:
        return None

    proxies = (
        _resolve_trusted_proxies(request)
        if trusted_proxy_ips is None
        else trusted_proxy_ips
    )
    if not proxies:
        return peer_ip

    trusted_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in proxies:
        try:
            network = ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError:
            continue
        if network.prefixlen == 0:
            continue
        trusted_networks.append(network)
    if not trusted_networks:
        return peer_ip

    peer_addr = ipaddress.ip_address(peer_ip)
    if not any(peer_addr in net for net in trusted_networks):
        return peer_ip

    xff_headers = request.headers.getlist("x-forwarded-for")
    if not xff_headers:
        return peer_ip
    xff_raw = ",".join(xff_headers)
    if not xff_raw.strip():
        return peer_ip

    for raw_hop in reversed(xff_raw.split(",")):
        hop = raw_hop.strip()
        if not hop:
            return peer_ip
        hop_ip = parse_client_ip(hop)
        if hop_ip is None:
            return peer_ip
        hop_addr = ipaddress.ip_address(hop_ip)
        if any(hop_addr in net for net in trusted_networks):
            continue
        return hop_ip

    return peer_ip


def get_client_ip_key(request: Request) -> str:
    """Extract a stable client identifier using trusted proxy configuration."""
    return resolve_client_ip(request) or "unknown"


def _extract_origin_tuple(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return None
    if (
        not parsed.scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
    ):
        return None
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    if port is not None and (port < 1 or port > 65535):
        return None
    hostname = parsed.hostname.lower()
    host_part = f"[{hostname}]" if ":" in hostname else hostname
    if (
        port is None
        or (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc_normalized = host_part
    else:
        netloc_normalized = f"{host_part}:{port}"
    return (scheme, netloc_normalized)


def validate_request_origin(request: Request, allowed_origins: Sequence[str]) -> None:
    """Enforce strict Origin/Referer validation on state-changing routes (excluding webhooks)."""
    if request.method.upper() in _SAFE_METHODS:
        return
    if _WEBHOOK_PATH_RE.fullmatch(request.url.path) is not None:
        return

    for header_name in ("origin", "referer", "sec-fetch-site"):
        if len(request.headers.getlist(header_name)) > 1:
            raise ForbiddenError(
                code="CSRF_INVALID",
                message="Duplicate origin security headers are not allowed.",
            )

    allowed_tuples: set[tuple[str, str]] = set()
    for origin in allowed_origins:
        parsed_allowed = _extract_origin_tuple(origin)
        if parsed_allowed is not None:
            allowed_tuples.add(parsed_allowed)

    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site is not None and sec_fetch_site.strip().lower() == "cross-site":
        raise ForbiddenError(
            code="CSRF_INVALID",
            message="Cross-site state-changing request rejected.",
        )

    origin_header = request.headers.get("origin")
    if origin_header is not None:
        normalized_origin = origin_header.strip()
        if not normalized_origin or normalized_origin.lower() == "null":
            raise ForbiddenError(
                code="CSRF_INVALID",
                message="Null or empty Origin header is not allowed.",
            )
        try:
            parsed = urlsplit(normalized_origin)
        except ValueError as exc:
            raise ForbiddenError(
                code="CSRF_INVALID",
                message="Request Origin is malformed.",
            ) from exc
        origin_tuple = _extract_origin_tuple(normalized_origin)
        if (
            origin_tuple is None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or origin_tuple not in allowed_tuples
        ):
            raise ForbiddenError(
                code="CSRF_INVALID",
                message="Request Origin is not in ALLOWED_ORIGINS.",
            )
        return

    referer_header = request.headers.get("referer")
    if referer_header is not None:
        referer_tuple = _extract_origin_tuple(referer_header)
        if referer_tuple is None or referer_tuple not in allowed_tuples:
            raise ForbiddenError(
                code="CSRF_INVALID",
                message="Request Referer origin is not in ALLOWED_ORIGINS.",
            )
        return

    # Browser requests carrying cookies or Sec-Fetch-Site must include Origin or Referer.
    if "cookie" in request.headers or sec_fetch_site is not None:
        raise ForbiddenError(
            code="CSRF_INVALID",
            message="Missing Origin or Referer header on browser state-changing request.",
        )


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a sliding-window rate-limit check."""

    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiterProtocol(Protocol):
    """Protocol for rate limiter implementations."""

    def check_and_record(
        self, key: str, limit: int, window_seconds: int
    ) -> RateLimitDecision: ...

    def reset(self) -> None: ...


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter for single-instance deployments."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10000,
    ) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be at least 1.")
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, tuple[deque[float], int]] = OrderedDict()

    def check_and_record(
        self, key: str, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        now = self._clock()
        cutoff = now - window_seconds

        with self._lock:
            entry = self._buckets.get(key)
            if entry is None:
                if len(self._buckets) >= self._max_keys:
                    self._prune_stale_buckets(now)
                while len(self._buckets) >= self._max_keys:
                    self._buckets.popitem(last=False)
                timestamps: deque[float] = deque()
                self._buckets[key] = (timestamps, window_seconds)
            else:
                timestamps, stored_window = entry
                if stored_window != window_seconds:
                    self._buckets[key] = (timestamps, window_seconds)
                self._buckets.move_to_end(key)

            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if limit <= 0:
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after_seconds=max(1, window_seconds),
                )

            if len(timestamps) >= limit:
                retry_after = max(1, math.ceil(timestamps[0] + window_seconds - now))
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after_seconds=retry_after,
                )

            timestamps.append(now)
            return RateLimitDecision(
                allowed=True,
                remaining=max(0, limit - len(timestamps)),
                retry_after_seconds=0,
            )

    def _prune_stale_buckets(self, now: float) -> None:
        stale_keys = [
            bucket_key
            for bucket_key, (timestamps, bucket_window) in self._buckets.items()
            if not timestamps or timestamps[-1] <= now - bucket_window
        ]
        for bucket_key in stale_keys:
            del self._buckets[bucket_key]

    def reset(self) -> None:
        """Clear all tracked rate-limit buckets (for tests)."""
        with self._lock:
            self._buckets.clear()


default_rate_limiter = SlidingWindowRateLimiter()


def get_rate_limiter(request: Request) -> RateLimiterProtocol:
    """Resolve the active rate limiter from app.state or fallback singleton."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        return limiter  # type: ignore[no-any-return]
    return default_rate_limiter


def enforce_rate_limit(
    limiter: RateLimiterProtocol,
    *,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Enforce a sliding-window rate limit or raise 429 RATE_LIMIT_EXCEEDED."""
    decision = limiter.check_and_record(key=key, limit=limit, window_seconds=window_seconds)
    if not decision.allowed:
        raise RateLimitExceededError(
            code="RATE_LIMIT_EXCEEDED",
            message="Too many requests. Please try again later.",
            details={"retry_after_seconds": decision.retry_after_seconds},
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


class GuestPendingOrderTracker:
    """Bounded thread-safe tracker mapping client IP keys to candidate guest order IDs."""

    def __init__(self, *, max_keys: int = 10000, max_orders_per_key: int = 32) -> None:
        self._max_keys = max_keys
        self._max_orders_per_key = max_orders_per_key
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, list[uuid.UUID]] = OrderedDict()

    def record(self, key: str, order_id: uuid.UUID) -> None:
        """Record a newly placed PENDING_PAYMENT guest order ID for an IP key."""
        with self._lock:
            order_ids = self._buckets.get(key)
            if order_ids is None:
                while len(self._buckets) >= self._max_keys:
                    self._buckets.popitem(last=False)
                self._buckets[key] = [order_id]
            else:
                if order_id not in order_ids:
                    order_ids.append(order_id)
                if len(order_ids) > self._max_orders_per_key:
                    del order_ids[: len(order_ids) - self._max_orders_per_key]
                self._buckets.move_to_end(key)

    def get_ids(self, key: str) -> list[uuid.UUID]:
        """Return candidate guest order IDs recorded for an IP key."""
        with self._lock:
            order_ids = self._buckets.get(key)
            return list(order_ids) if order_ids is not None else []

    def retain_active(self, key: str, active_ids: set[uuid.UUID]) -> None:
        """Prune tracked order IDs that are no longer active PENDING_PAYMENT in the database."""
        with self._lock:
            order_ids = self._buckets.get(key)
            if order_ids is None:
                return
            remaining = [oid for oid in order_ids if oid in active_ids]
            if remaining:
                self._buckets[key] = remaining
            else:
                del self._buckets[key]

    def reset(self) -> None:
        """Clear all tracked guest order IDs (for tests)."""
        with self._lock:
            self._buckets.clear()


default_guest_pending_tracker = GuestPendingOrderTracker()


def get_guest_pending_tracker(request: Request) -> GuestPendingOrderTracker:
    """Resolve the active GuestPendingOrderTracker from app.state or fallback singleton."""
    tracker = getattr(request.app.state, "guest_pending_tracker", None)
    if isinstance(tracker, GuestPendingOrderTracker):
        return tracker
    return default_guest_pending_tracker
