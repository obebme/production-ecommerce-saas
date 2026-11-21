"""FastAPI application factory, middleware stack, health check, and module router mounting."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
from pydantic_settings.sources import DotEnvSettingsSource
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
    RequestIDMiddleware,
    ServiceUnavailableError,
    register_exception_handlers,
)
from app.core.security import (
    GuestPendingOrderTracker,
    RateLimiterProtocol,
    SlidingWindowRateLimiter,
)
from app.modules.admin.router import router as admin_router
from app.modules.analytics.router import router as analytics_router
from app.modules.cart.router import router as cart_router
from app.modules.catalog.router import router as catalog_router
from app.modules.identity.dependencies import require_valid_origin
from app.modules.identity.router import router as identity_router
from app.modules.inventory.router import router as inventory_router
from app.modules.inventory.service import reclaim_all_expired_reservations
from app.modules.orders.router import router as orders_router
from app.modules.payments.router import router as payments_router

logger = logging.getLogger(__name__)


def _sweep_expired_reservations_once(settings: Settings) -> int:
    factory = get_session_factory(settings)
    with factory() as db:
        return reclaim_all_expired_reservations(db, batch_size=100, max_batches=20)


async def _run_reservation_sweeper_loop(settings: Settings) -> None:
    interval = settings.reservation_sweeper_interval_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_sweep_expired_reservations_once, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic expired reservation sweeper tick failed.")


class HealthResponse(BaseModel):
    """Health check response matching /api/v1/health OpenAPI contract."""

    status: Literal["ok"]
    database: Literal["ok"]
    sandbox_payments: bool


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
        resolved_default_engine = False
        if getattr(fastapi_app.state, "db_session_factory", None) is None:
            get_engine(active_settings)
            resolved_default_engine = True

        sweeper_task: asyncio.Task[None] | None = None
        if (
            active_settings.environment != "test"
            and active_settings.reservation_sweeper_interval_seconds > 0
        ):
            sweeper_task = asyncio.create_task(
                _run_reservation_sweeper_loop(active_settings),
                name="expired-reservation-sweeper",
            )
        try:
            yield
        finally:
            if sweeper_task is not None:
                sweeper_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper_task
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
    fastapi_app.state.rate_limiter = rate_limiter or SlidingWindowRateLimiter()
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
