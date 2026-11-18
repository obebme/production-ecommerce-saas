"""FastAPI application factory, middleware stack, health check, and module router mounting."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings, get_app_settings, get_settings
from app.core.database import check_db_connection, get_db, get_engine
from app.core.errors import (
    RequestIDMiddleware,
    ServiceUnavailableError,
    register_exception_handlers,
)
from app.core.security import RateLimiterProtocol, SlidingWindowRateLimiter
from app.modules.catalog.router import router as catalog_router
from app.modules.identity.dependencies import require_valid_origin
from app.modules.identity.router import router as identity_router
from app.modules.inventory.router import router as inventory_router


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


def create_app(
    settings: Settings | None = None,
    *,
    rate_limiter: RateLimiterProtocol | None = None,
) -> FastAPI:
    """Construct the FastAPI modular-monolith application."""

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        # Eagerly validate Settings and fail closed in production on server startup.
        active_settings = (
            getattr(fastapi_app.state, "settings", None) or get_settings()
        )
        fastapi_app.state.settings = active_settings
        get_engine(active_settings)
        yield

    is_production = (
        settings.environment == "production"
        if settings is not None
        else os.environ.get("ENVIRONMENT", "").strip().lower() == "production"
    )

    fastapi_app = FastAPI(
        title="E-Commerce SaaS API",
        version="1.1.0-arch",
        docs_url=None if is_production else "/api/v1/docs",
        redoc_url=None if is_production else "/api/v1/redoc",
        openapi_url=None if is_production else "/api/v1/openapi.json",
        dependencies=[Depends(require_valid_origin)],
        lifespan=lifespan,
    )

    if settings is not None:
        fastapi_app.state.settings = settings
    fastapi_app.state.rate_limiter = rate_limiter or SlidingWindowRateLimiter()

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
    fastapi_app.include_router(api_v1)

    return fastapi_app


app = create_app()
