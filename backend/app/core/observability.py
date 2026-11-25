"""Structured JSON logging, secret redaction, and Prometheus observability metrics."""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy.engine import Engine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import _request_id_ctx

REDACTED_VALUE = "[REDACTED]"
_MAX_REDACT_DEPTH = 16

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
        "metrics_bearer_token",
        "redis_url",
        "database_url",
        "migration_database_url",
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
    "bearer",
    "guest_order_token",
)


def is_sensitive_log_key(key: str) -> bool:
    """Return True if a log field key may contain credentials, cookies, or secrets."""
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
        return True
    return normalized.endswith("_token") or normalized.startswith("token_")


def redact_log_value(value: Any, *, depth: int = 0) -> Any:
    """Recursively serialize log data to JSON-safe structures while redacting secret keys."""
    if depth > _MAX_REDACT_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return value
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, item_val in value.items():
            key_str = str(raw_key).replace("\x00", "")
            if is_sensitive_log_key(key_str):
                sanitized[key_str] = REDACTED_VALUE
            else:
                sanitized[key_str] = redact_log_value(item_val, depth=depth + 1)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return [redact_log_value(item, depth=depth + 1) for item in value]
    return str(value)


_STANDARD_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class StructuredJsonFormatter(logging.Formatter):
    """JSON log formatter with request_id propagation and recursive secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": _request_id_ctx.get(),
        }

        extra_fields: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
                continue
            extra_fields[key] = value
        if extra_fields:
            redacted_extra = redact_log_value(extra_fields)
            if isinstance(redacted_extra, dict):
                for key, value in redacted_extra.items():
                    if key not in payload:
                        payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(log_format: Literal["json", "text"] = "json") -> None:
    """Configure root logger formatting if no custom StructuredJsonFormatter is attached."""
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        stream_handler = logging.StreamHandler()
        root_logger.addHandler(stream_handler)
        root_logger.setLevel(logging.INFO)
    if log_format == "json":
        formatter: logging.Formatter = StructuredJsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    for log_handler in root_logger.handlers:
        log_handler.setFormatter(formatter)


METRICS_REGISTRY = CollectorRegistry(auto_describe=True)

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the FastAPI application.",
    labelnames=("method", "route", "status_class"),
    registry=METRICS_REGISTRY,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds by method and route template.",
    labelnames=("method", "route"),
    registry=METRICS_REGISTRY,
)

db_pool_connections = Gauge(
    "db_pool_connections",
    "SQLAlchemy connection pool statistics sampled on /metrics scrape.",
    labelnames=("state",),
    registry=METRICS_REGISTRY,
)

checkout_events_total = Counter(
    "checkout_events_total",
    "Checkout order creation outcomes by actor type.",
    labelnames=("outcome", "actor_type"),
    registry=METRICS_REGISTRY,
)

payment_webhook_events_total = Counter(
    "payment_webhook_events_total",
    "Payment webhook processing outcomes by provider and event type.",
    labelnames=("provider", "event_type", "outcome"),
    registry=METRICS_REGISTRY,
)

reservation_sweeper_runs_total = Counter(
    "reservation_sweeper_runs_total",
    "Coordinated reservation sweeper runs by outcome.",
    labelnames=("outcome",),
    registry=METRICS_REGISTRY,
)

reservation_sweeper_reclaimed_total = Counter(
    "reservation_sweeper_reclaimed_total",
    "Total expired reservations reclaimed by the background sweeper.",
    registry=METRICS_REGISTRY,
)

reservation_sweeper_duration_seconds = Histogram(
    "reservation_sweeper_duration_seconds",
    "Duration of coordinated reservation sweeper execution in seconds.",
    registry=METRICS_REGISTRY,
)

rate_limit_decisions_total = Counter(
    "rate_limit_decisions_total",
    "Rate limiter decisions by backend, scope, and outcome.",
    labelnames=("backend", "scope", "decision"),
    registry=METRICS_REGISTRY,
)

rate_limit_redis_errors_total = Counter(
    "rate_limit_redis_errors_total",
    "Total Redis rate limiter backend errors.",
    registry=METRICS_REGISTRY,
)


def extract_rate_limit_scope(key: str) -> str:
    """Collapse a dynamic rate-limit key into a low-cardinality scope label."""
    if not key:
        return "unknown"
    parts = key.split(":", 2)
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return parts[0]


def sample_db_pool_metrics(engine: Engine | None) -> None:
    """Sample SQLAlchemy QueuePool statistics safely without raising on non-pooled engines."""
    if engine is None:
        return
    pool = getattr(engine, "pool", None)
    if pool is None:
        return
    try:
        size_fn = getattr(pool, "size", None)
        checkedin_fn = getattr(pool, "checkedin", None)
        checkedout_fn = getattr(pool, "checkedout", None)
        overflow_fn = getattr(pool, "overflow", None)
        if callable(size_fn):
            db_pool_connections.labels(state="size").set(float(size_fn()))
        if callable(checkedin_fn):
            db_pool_connections.labels(state="checkedin").set(float(checkedin_fn()))
        if callable(checkedout_fn):
            db_pool_connections.labels(state="checkedout").set(float(checkedout_fn()))
        if callable(overflow_fn):
            db_pool_connections.labels(state="overflow").set(float(overflow_fn()))
    except Exception:
        return


def render_prometheus_metrics() -> tuple[bytes, str]:
    """Serialize dedicated Prometheus metrics registry into exposition bytes and content type."""
    return generate_latest(METRICS_REGISTRY), CONTENT_TYPE_LATEST


class ObservabilityMiddleware:
    """Pure ASGI middleware recording route-templated HTTP latency and status counters."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        start_time = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = max(0.0, time.perf_counter() - start_time)
            route = scope.get("route")
            route_path = getattr(route, "path", None)
            route_label = route_path if isinstance(route_path, str) and route_path else "UNMATCHED"
            status_class = f"{status_code // 100}xx"
            http_requests_total.labels(
                method=method,
                route=route_label,
                status_class=status_class,
            ).inc()
            http_request_duration_seconds.labels(
                method=method,
                route=route_label,
            ).observe(duration)
