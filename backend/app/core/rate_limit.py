"""Sliding-window rate limiters (in-memory and distributed Redis Lua-backed)."""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from fastapi import Request
from redis import Redis
from redis.exceptions import RedisError

from app.core.errors import RateLimitExceededError, ServiceUnavailableError
from app.core.observability import (
    extract_rate_limit_scope,
    rate_limit_decisions_total,
    rate_limit_redis_errors_total,
)

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

RateLimitFailureMode = Literal["fail_closed_sensitive", "fail_closed_all", "local_fallback"]

SENSITIVE_RATE_LIMIT_PREFIXES: tuple[str, ...] = (
    "auth:",
    "checkout:",
    "payments:",
    "orders:guest",
)

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local cutoff_ms = now_ms - window_ms

redis.call("ZREMRANGEBYSCORE", key, "-inf", cutoff_ms)
local current = redis.call("ZCARD", key)

if limit <= 0 then
    return {0, 0, window_ms}
end

if current >= limit then
    local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
    local retry_ms = window_ms
    if oldest and #oldest >= 2 then
        local oldest_score = tonumber(oldest[2])
        if oldest_score ~= nil then
            retry_ms = math.max(1000, (oldest_score + window_ms) - now_ms)
        end
    end
    return {0, 0, retry_ms}
end

redis.call("ZADD", key, now_ms, member)
redis.call("PEXPIRE", key, window_ms)
return {1, math.max(0, limit - current - 1), 0}
"""


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
        scope = extract_rate_limit_scope(key)

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
                rate_limit_decisions_total.labels(
                    backend="memory", scope=scope, decision="denied"
                ).inc()
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after_seconds=max(1, window_seconds),
                )

            if len(timestamps) >= limit:
                retry_after = max(1, math.ceil(timestamps[0] + window_seconds - now))
                rate_limit_decisions_total.labels(
                    backend="memory", scope=scope, decision="denied"
                ).inc()
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after_seconds=retry_after,
                )

            timestamps.append(now)
            rate_limit_decisions_total.labels(
                backend="memory", scope=scope, decision="allowed"
            ).inc()
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


class RedisSlidingWindowRateLimiter:
    """Distributed atomic Lua-scripted Redis sliding-window rate limiter."""

    def __init__(
        self,
        redis_client: Redis,
        *,
        key_prefix: str = "atelier:rl",
        failure_mode: RateLimitFailureMode = "fail_closed_sensitive",
        clock: Callable[[], float] = time.time,
        local_fallback: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self._client = redis_client
        self._key_prefix = key_prefix.strip(":") or "atelier:rl"
        self._failure_mode: RateLimitFailureMode = failure_mode
        self._clock = clock
        self._local_fallback = local_fallback or SlidingWindowRateLimiter()
        self._script = self._client.register_script(_SLIDING_WINDOW_LUA)

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        key_prefix: str = "atelier:rl",
        failure_mode: RateLimitFailureMode = "fail_closed_sensitive",
        socket_connect_timeout: float = 0.5,
        socket_timeout: float = 0.5,
        health_check_interval: int = 30,
    ) -> RedisSlidingWindowRateLimiter:
        """Construct a Redis rate limiter with short operational socket timeouts."""
        client = Redis.from_url(
            redis_url,
            socket_connect_timeout=socket_connect_timeout,
            socket_timeout=socket_timeout,
            health_check_interval=health_check_interval,
            decode_responses=False,
        )
        return cls(
            client,
            key_prefix=key_prefix,
            failure_mode=failure_mode,
        )

    @property
    def failure_mode(self) -> RateLimitFailureMode:
        """Return configured Redis failure policy."""
        return self._failure_mode

    @staticmethod
    def is_sensitive_key(key: str) -> bool:
        """Return True when the rate-limit key protects a sensitive commerce/auth path."""
        return key.startswith(SENSITIVE_RATE_LIMIT_PREFIXES)

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

    def check_and_record(
        self, key: str, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        scope = extract_rate_limit_scope(key)
        now_ms = int(self._clock() * 1000)
        window_ms = max(1, int(window_seconds * 1000))
        member = f"{now_ms}:{uuid.uuid4().hex}"

        try:
            raw_result = self._script(
                keys=[self._full_key(key)],
                args=[now_ms, window_ms, limit, member],
            )
            allowed = int(raw_result[0]) == 1
            remaining = max(0, int(raw_result[1]))
            retry_ms = int(raw_result[2])
            retry_after_seconds = 0 if allowed else max(1, math.ceil(retry_ms / 1000))
            rate_limit_decisions_total.labels(
                backend="redis",
                scope=scope,
                decision="allowed" if allowed else "denied",
            ).inc()
            return RateLimitDecision(
                allowed=allowed,
                remaining=remaining,
                retry_after_seconds=retry_after_seconds,
            )
        except RedisError as exc:
            rate_limit_redis_errors_total.inc()
            logger.warning(
                "Redis rate limiter error; applying failure_mode=%s scope=%s error=%s",
                self._failure_mode,
                scope,
                exc.__class__.__name__,
            )
            sensitive = self.is_sensitive_key(key)
            if self._failure_mode == "fail_closed_all" or (
                self._failure_mode == "fail_closed_sensitive" and sensitive
            ):
                rate_limit_decisions_total.labels(
                    backend="redis", scope=scope, decision="unavailable"
                ).inc()
                raise ServiceUnavailableError(
                    code="RATE_LIMITER_UNAVAILABLE",
                    message="Rate limiting service temporarily unavailable.",
                ) from exc

            fallback_limit = 0 if limit <= 0 else max(1, limit // 2)
            return self._local_fallback.check_and_record(
                key=key,
                limit=fallback_limit,
                window_seconds=window_seconds,
            )

    def ping(self) -> bool:
        """Probe Redis connectivity for readiness checks without raising."""
        try:
            return bool(self._client.ping())
        except RedisError:
            return False

    def close(self) -> None:
        """Close underlying Redis connection pool safely."""
        try:
            self._client.close()
        except Exception:
            return

    def reset(self) -> None:
        """Clear local fallback buckets and prefixed Redis rate-limit keys (for tests)."""
        self._local_fallback.reset()
        try:
            keys = list(self._client.scan_iter(match=f"{self._key_prefix}:*", count=500))
            if keys:
                self._client.delete(*keys)
        except RedisError:
            return


default_rate_limiter = SlidingWindowRateLimiter()


def build_rate_limiter(settings: Settings) -> RateLimiterProtocol:
    """Build Redis rate limiter when REDIS_URL is configured, else local sliding window."""
    if settings.redis_url:
        return RedisSlidingWindowRateLimiter.from_url(
            settings.redis_url,
            key_prefix=settings.rate_limit_key_prefix,
            failure_mode=settings.rate_limit_failure_mode,
        )
    return SlidingWindowRateLimiter()


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
