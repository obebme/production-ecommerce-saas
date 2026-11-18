"""Request ID middleware, domain exception hierarchy, and unified OpenAPI error handlers."""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_app_settings

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

SESSION_COOKIE_NAME = "ecom_session"
CSRF_COOKIE_NAME = "ecom_csrf"


def generate_request_id() -> str:
    """Generate a request correlation ID matching the req_<hex> convention."""
    return f"req_{uuid.uuid4().hex}"


def get_request_id(request: Request | None = None) -> str:
    """Resolve the active request ID from request.state or ContextVar."""
    if request is not None:
        state_req_id = getattr(request.state, "request_id", None)
        if isinstance(state_req_id, str) and state_req_id:
            return state_req_id
    ctx_req_id = _request_id_ctx.get()
    if ctx_req_id:
        return ctx_req_id
    return generate_request_id()


class RequestIDMiddleware:
    """Pure ASGI middleware that propagates or generates X-Request-ID on every request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id: str | None = None
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == b"x-request-id":
                decoded = header_value.decode("latin-1").strip()
                if _SAFE_REQUEST_ID_RE.match(decoded):
                    incoming_id = decoded
                break

        request_id = incoming_id or generate_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id_ctx.set(request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers: list[tuple[bytes, bytes]] = [
                    (name, val)
                    for name, val in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                raw_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = raw_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _request_id_ctx.reset(token)


class AppError(Exception):
    """Base application error serialized into the OpenAPI error envelope."""

    status_code: int = 400
    default_code: str = "BAD_REQUEST"

    def __init__(
        self,
        *,
        code: str | None = None,
        message: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        clear_auth_cookies: bool = False,
    ) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        self.code = code or self.default_code
        self.message = message
        self.details: dict[str, Any] = details if details is not None else {}
        self.headers = headers
        self.clear_auth_cookies = clear_auth_cookies


class BadRequestError(AppError):
    """400 Bad Request."""

    status_code = 400
    default_code = "BAD_REQUEST"


class UnauthorizedError(AppError):
    """401 Unauthorized."""

    status_code = 401
    default_code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    """403 Forbidden."""

    status_code = 403
    default_code = "FORBIDDEN"


class NotFoundError(AppError):
    """404 Not Found."""

    status_code = 404
    default_code = "NOT_FOUND"


class ConflictError(AppError):
    """409 Conflict."""

    status_code = 409
    default_code = "CONFLICT"


class ValidationAppError(AppError):
    """422 Unprocessable Entity."""

    status_code = 422
    default_code = "VALIDATION_ERROR"


class RateLimitExceededError(AppError):
    """429 Too Many Requests."""

    status_code = 429
    default_code = "RATE_LIMIT_EXCEEDED"


class ServiceUnavailableError(AppError):
    """503 Service Unavailable."""

    status_code = 503
    default_code = "SERVICE_UNAVAILABLE"


def _build_error_payload(
    *,
    code: str,
    message: str,
    details: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


def clear_auth_cookie_headers(response: Response, *, cookie_secure: bool) -> None:
    """Attach Set-Cookie deletions for ecom_session and ecom_csrf."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        httponly=False,
        samesite="lax",
        secure=cookie_secure,
    )


_HTTP_STATUS_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    503: "SERVICE_UNAVAILABLE",
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register unified OpenAPI error envelope exception handlers on a FastAPI app."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = get_request_id(request)
        response = JSONResponse(
            status_code=exc.status_code,
            content=_build_error_payload(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                request_id=request_id,
            ),
            headers=exc.headers,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        if exc.clear_auth_cookies:
            cookie_secure = False
            try:
                cookie_secure = get_app_settings(request).cookie_secure
            except Exception:
                cookie_secure = False
            clear_auth_cookie_headers(response, cookie_secure=cookie_secure)
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = get_request_id(request)
        # Strip raw "input" values so sensitive credentials are never echoed in 422 payloads.
        sanitized_errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": str(err.get("msg", "Invalid value")),
                "type": str(err.get("type", "value_error")),
            }
            for err in exc.errors()
        ]
        response = JSONResponse(
            status_code=422,
            content=_build_error_payload(
                code="VALIDATION_ERROR",
                message="Request validation failed.",
                details={"errors": sanitized_errors},
                request_id=request_id,
            ),
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        request_id = get_request_id(request)
        code = _HTTP_STATUS_CODE_MAP.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP request error."
        response = JSONResponse(
            status_code=exc.status_code,
            content=_build_error_payload(
                code=code,
                message=message,
                details={},
                request_id=request_id,
            ),
            headers=dict(exc.headers) if exc.headers else None,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = get_request_id(request)
        logger.exception("Unhandled error for request_id=%s", request_id, exc_info=exc)
        response = JSONResponse(
            status_code=500,
            content=_build_error_payload(
                code="INTERNAL_SERVER_ERROR",
                message="An internal server error occurred.",
                details={},
                request_id=request_id,
            ),
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
