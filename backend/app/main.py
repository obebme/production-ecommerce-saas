"""FastAPI application factory, middleware stack, health check, and module router mounting."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
from pydantic_settings.sources import DotEnvSettingsSource
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, get_app_settings, get_settings
from app.core.database import (
    check_db_connection,
    dispose_engine,
    get_db,
    get_engine,
    get_session_factory,
)
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    RequestIDMiddleware,
    ServiceUnavailableError,
    UnauthorizedError,
    register_exception_handlers,
)
from app.core.observability import (
    ObservabilityMiddleware,
    configure_logging,
    render_prometheus_metrics,
    sample_db_pool_metrics,
)
from app.core.security import (
    GuestPendingOrderTracker,
    RateLimiterProtocol,
    RedisSlidingWindowRateLimiter,
    SlidingWindowRateLimiter,
    build_rate_limiter,
    get_rate_limiter,
)
from app.modules.admin.router import router as admin_router
from app.modules.analytics.router import router as analytics_router
from app.modules.cart.router import router as cart_router
from app.modules.catalog.router import router as catalog_router
from app.modules.identity.dependencies import (
    _resolve_auth_context_from_cookie,
    require_valid_origin,
)
from app.modules.identity.router import router as identity_router
from app.modules.inventory.router import router as inventory_router
from app.modules.inventory.service import run_coordinated_expired_reservation_sweep
from app.modules.orders.router import router as orders_router
from app.modules.payments.router import router as payments_router

logger = logging.getLogger(__name__)


def _sweep_expired_reservations_once(
    settings: Settings,
    stop_event: threading.Event,
) -> tuple[int, bool]:
    factory = get_session_factory(settings)
    with factory() as db:
        return run_coordinated_expired_reservation_sweep(
            db,
            batch_size=100,
            max_batches=20,
            stop_event=stop_event,
        )


async def _run_reservation_sweeper_loop(
    settings: Settings,
    stop_async: asyncio.Event,
    stop_thread: threading.Event,
) -> None:
    interval = settings.reservation_sweeper_interval_seconds
    while not stop_async.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_async.wait(), timeout=interval)
        if stop_async.is_set():
            break
        try:
            await asyncio.to_thread(_sweep_expired_reservations_once, settings, stop_thread)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic expired reservation sweeper tick failed.")


class HealthResponse(BaseModel):
    """Health check response matching /api/v1/health OpenAPI contract."""

    status: Literal["ok"]
    database: Literal["ok"]
    sandbox_payments: bool


class LiveHealthResponse(BaseModel):
    """Liveness probe response for /api/v1/health/live."""

    status: Literal["ok"]


class ReadyHealthResponse(BaseModel):
    """Readiness probe response for /api/v1/health/ready."""

    status: Literal["ok"]
    database: Literal["ok"]
    redis: Literal["ok", "degraded", "disabled"]


class _SettingsAwareCORSMiddleware:
    """Lazily resolves CORS origins from injected Settings or environment on startup."""

    def __init__(self, app: ASGIApp, *, app_ref: FastAPI) -> None:
        self.app = app
        self.app_ref = app_ref
        self._cors: CORSMiddleware | None = None

    def _get_or_init_cors(self) -> CORSMiddleware:
        if self._cors is None:
            state_settings = getattr(self.app_ref.state, "settings", None)
            settings = state_settings if isinstance(state_settings, Settings) else get_settings()
            self._cors = CORSMiddleware(
                app=self.app,
                allow_origins=settings.allowed_origins,
                allow_credentials=True,
                allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
                allow_headers=[
                    "Content-Type",
                    "X-CSRF-Token",
                    "Idempotency-Key",
                    "X-Guest-Order-Token",
                    "X-Request-ID",
                ],
                expose_headers=["X-Request-ID"],
            )
        return self._cors

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cors = self._get_or_init_cors()
        await cors(scope, receive, send)


_SENSITIVE_NO_STORE_SEGMENTS = frozenset(
    {"auth", "cart", "checkout", "orders", "payments", "admin"}
)


def _is_sensitive_no_store_path(path: str) -> bool:
    if not path.startswith("/api/v1/"):
        return False
    remainder = path[len("/api/v1/") :]
    segment = remainder.split("/", 1)[0]
    return segment in _SENSITIVE_NO_STORE_SEGMENTS


class SensitiveRouteNoStoreMiddleware:
    """Attach Cache-Control: no-store to sensitive auth/cart/checkout/order/payment/admin APIs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_sensitive_no_store_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if headers.get("Cache-Control") != "no-store":
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _resolve_environment_for_docs(settings: Settings | None) -> str:
    """Resolve runtime environment for OpenAPI/Swagger visibility."""
    if settings is not None:
        return settings.environment
    try:
        return get_settings().environment
    except ValidationError:
        env_var = os.environ.get("ENVIRONMENT", "").strip().lower()
        if env_var:
            return env_var
        try:
            dotenv_values = DotEnvSettingsSource(Settings)()
            dotenv_env = dotenv_values.get("environment") or dotenv_values.get("ENVIRONMENT")
            if isinstance(dotenv_env, str) and dotenv_env.strip():
                return dotenv_env.strip().lower()
        except Exception:
            pass
        return "development"


def _authorize_metrics_request(
    request: Request,
    db: Session,
    active_settings: Settings,
) -> None:
    """Permit Prometheus scrapes via METRICS_BEARER_TOKEN or an admin session cookie."""
    auth_header = request.headers.get("authorization")
    if auth_header is not None:
        scheme, _, token = auth_header.partition(" ")
        if (
            scheme.lower() == "bearer"
            and token
            and active_settings.metrics_bearer_token
            and hmac.compare_digest(
                token.encode("utf-8"),
                active_settings.metrics_bearer_token.encode("utf-8"),
            )
        ):
            return
        raise UnauthorizedError(
            code="UNAUTHORIZED",
            message="Invalid metrics authorization credentials.",
        )

    auth = _resolve_auth_context_from_cookie(request, db, active_settings)
    if auth is None:
        raise UnauthorizedError(
            code="AUTH_REQUIRED",
            message="Authentication required.",
        )
    if auth.user.role != "admin":
        raise ForbiddenError(
            code="FORBIDDEN",
            message="Admin role is required to access metrics.",
        )


def _resolve_metrics_engine(request: Request, active_settings: Settings) -> Engine | None:
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is not None:
        bound_engine = getattr(factory, "kw", {}).get("bind")
        if isinstance(bound_engine, Engine):
            return bound_engine
        return None
    try:
        return get_engine(active_settings)
    except Exception:
        return None


def create_app(
    settings: Settings | None = None,
    *,
    rate_limiter: RateLimiterProtocol | None = None,
) -> FastAPI:
    """Construct the FastAPI modular-monolith application."""

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        # Eagerly validate Settings and fail closed in staging/production on server startup.
        active_settings = (
            getattr(fastapi_app.state, "settings", None) or get_settings()
        )
        fastapi_app.state.settings = active_settings
        if active_settings.environment != "test":
            configure_logging(active_settings.log_format)

        if rate_limiter is None and settings is None and active_settings.redis_url:
            fastapi_app.state.rate_limiter = build_rate_limiter(active_settings)

        resolved_default_engine = False
        if getattr(fastapi_app.state, "db_session_factory", None) is None:
            get_engine(active_settings)
            resolved_default_engine = True

        stop_async = asyncio.Event()
        stop_thread = threading.Event()
        sweeper_task: asyncio.Task[None] | None = None
        if (
            active_settings.environment != "test"
            and active_settings.reservation_sweeper_interval_seconds > 0
        ):
            sweeper_task = asyncio.create_task(
                _run_reservation_sweeper_loop(active_settings, stop_async, stop_thread),
                name="expired-reservation-sweeper",
            )
        try:
            yield
        finally:
            if sweeper_task is not None:
                stop_async.set()
                stop_thread.set()
                try:
                    await asyncio.wait_for(sweeper_task, timeout=5.0)
                except TimeoutError:
                    sweeper_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                        await sweeper_task
                except asyncio.CancelledError:
                    pass

            active_limiter = getattr(fastapi_app.state, "rate_limiter", None)
            close_limiter = getattr(active_limiter, "close", None)
            if callable(close_limiter):
                close_limiter()

            if (
                resolved_default_engine
                and getattr(fastapi_app.state, "db_session_factory", None) is None
            ):
                dispose_engine(active_settings)

    disable_docs = _resolve_environment_for_docs(settings) in ("production", "staging")

    fastapi_app = FastAPI(
        title="E-Commerce SaaS API",
        version="1.1.0-arch",
        docs_url=None if disable_docs else "/api/v1/docs",
        redoc_url=None if disable_docs else "/api/v1/redoc",
        openapi_url=None if disable_docs else "/api/v1/openapi.json",
        dependencies=[Depends(require_valid_origin)],
        lifespan=lifespan,
    )

    if settings is not None:
        fastapi_app.state.settings = settings
    if rate_limiter is not None:
        fastapi_app.state.rate_limiter = rate_limiter
    elif settings is not None:
        fastapi_app.state.rate_limiter = build_rate_limiter(settings)
    else:
        fastapi_app.state.rate_limiter = SlidingWindowRateLimiter()
    fastapi_app.state.guest_pending_tracker = GuestPendingOrderTracker()

    # Middlewares wrap in reverse registration order: RequestIDMiddleware is outermost.
    if settings is not None:
        fastapi_app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "X-CSRF-Token",
                "Idempotency-Key",
                "X-Guest-Order-Token",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID"],
        )
    else:
        fastapi_app.add_middleware(_SettingsAwareCORSMiddleware, app_ref=fastapi_app)

    fastapi_app.add_middleware(SensitiveRouteNoStoreMiddleware)
    fastapi_app.add_middleware(ObservabilityMiddleware)
    fastapi_app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(fastapi_app)

    api_v1 = APIRouter(prefix="/api/v1")

    @api_v1.get(
        "/health",
        response_model=HealthResponse,
        tags=["health"],
    )
    def health_check(
        db: Annotated[Session, Depends(get_db)],
        active_settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> HealthResponse:
        if not check_db_connection(db):
            raise ServiceUnavailableError(
                code="DATABASE_UNAVAILABLE",
                message="Database connectivity check failed.",
            )
        return HealthResponse(
            status="ok",
            database="ok",
            sandbox_payments=active_settings.payment_sandbox_mode,
        )

    @api_v1.get(
        "/health/live",
        response_model=LiveHealthResponse,
        tags=["health"],
    )
    def health_live() -> LiveHealthResponse:
        return LiveHealthResponse(status="ok")

    @api_v1.get(
        "/health/ready",
        response_model=ReadyHealthResponse,
        tags=["health"],
    )
    def health_ready(
        db: Annotated[Session, Depends(get_db)],
        limiter: Annotated[RateLimiterProtocol, Depends(get_rate_limiter)],
    ) -> ReadyHealthResponse:
        if not check_db_connection(db):
            raise ServiceUnavailableError(
                code="DATABASE_UNAVAILABLE",
                message="Database connectivity check failed.",
            )

        redis_status: Literal["ok", "degraded", "disabled"] = "disabled"
        if isinstance(limiter, RedisSlidingWindowRateLimiter):
            if limiter.ping():
                redis_status = "ok"
            elif limiter.failure_mode in ("fail_closed_all", "fail_closed_sensitive"):
                raise ServiceUnavailableError(
                    code="REDIS_UNAVAILABLE",
                    message="Redis connectivity check failed.",
                )
            else:
                redis_status = "degraded"

        return ReadyHealthResponse(
            status="ok",
            database="ok",
            redis=redis_status,
        )

    @api_v1.get(
        "/metrics",
        tags=["observability"],
    )
    def prometheus_metrics(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        active_settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> Response:
        if not active_settings.metrics_enabled:
            raise NotFoundError(
                code="NOT_FOUND",
                message="Metrics endpoint is disabled.",
            )
        _authorize_metrics_request(request, db, active_settings)
        sample_db_pool_metrics(_resolve_metrics_engine(request, active_settings))
        payload_bytes, content_type = render_prometheus_metrics()
        return Response(
            content=payload_bytes,
            media_type=content_type,
            headers={"Cache-Control": "no-store"},
        )

    api_v1.include_router(identity_router)
    api_v1.include_router(catalog_router)
    api_v1.include_router(inventory_router)
    api_v1.include_router(cart_router)
    api_v1.include_router(orders_router)
    api_v1.include_router(payments_router)
    api_v1.include_router(admin_router)
    api_v1.include_router(analytics_router)
    fastapi_app.include_router(api_v1)

    return fastapi_app


app = create_app()
