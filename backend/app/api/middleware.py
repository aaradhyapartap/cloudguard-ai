"""Request correlation, access logging, and security headers."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import bind_request_context, get_logger
from app.utilities.ids import new_request_id

logger = get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind logging context, emit one access log line.

    The client may supply ``x-request-id`` so a trace can be followed from the
    browser through to CloudWatch. If it does not, one is generated. Either way
    it comes back on the response, which is what lets a user paste an id into a
    support request and have it mean something.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.request_id = request_id
        bind_request_context(request_id=request_id, route=request.url.path)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["x-request-id"] = request_id
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            # organization_id/user_id are already bound by the principal
            # dependency and merged in by structlog's contextvars processor.
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defensive response headers.

    The API returns JSON, never HTML, so the CSP can be maximally restrictive:
    nothing is allowed to load or execute from an API response under any
    circumstance. The frontend ships its own, looser policy.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        response = await call_next(request)
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("referrer-policy", "no-referrer")
        if request.url.path.startswith("/docs"):
            response.headers.setdefault(
                "content-security-policy",
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "frame-ancestors 'none'",
            )
        else:
            response.headers.setdefault(
                "content-security-policy",
                "default-src 'none'; frame-ancestors 'none'",
            )
        response.headers.setdefault(
            "strict-transport-security", "max-age=31536000; includeSubDomains"
        )
        response.headers.setdefault("cache-control", "no-store")
        return response


def register_middleware(app: FastAPI) -> None:
    # Starlette runs middleware in reverse registration order, so security
    # headers are added last and therefore apply to every response, including
    # ones produced by an exception handler.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
