"""Structured application errors.

Every error the API returns has a **stable machine-readable code**. Clients
branch on ``error.code``; humans read ``error.message``. Changing a message is
a copy edit, changing a code is a breaking API change — keeping them separate
means you can improve wording without breaking a consumer.

Security note on ``AuthorizationError``: the message deliberately does not say
*which* resource was denied or whether it exists. Telling an attacker "document
7f3a exists but you may not read it" is an information leak; 403 with a generic
message is not.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)


class ErrorCode(StrEnum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody = Field(...)


class AppError(Exception):
    """Base class for every error this application raises deliberately."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class ValidationError(AppError):
    code = ErrorCode.VALIDATION_FAILED
    status_code = 422  # literal: starlette renamed this constant mid-2025
    message = "The request could not be validated."


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = status.HTTP_404_NOT_FOUND
    message = "The requested resource does not exist."


class AuthenticationError(AppError):
    code = ErrorCode.UNAUTHENTICATED
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "Authentication is required."


class AuthorizationError(AppError):
    code = ErrorCode.FORBIDDEN
    status_code = status.HTTP_403_FORBIDDEN
    # Intentionally non-specific — see module docstring.
    message = "You do not have permission to perform this action."


class ConflictError(AppError):
    code = ErrorCode.CONFLICT
    status_code = status.HTTP_409_CONFLICT
    message = "The request conflicts with the current state of the resource."


class RateLimitError(AppError):
    code = ErrorCode.RATE_LIMITED
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Too many requests."


class UpstreamError(AppError):
    code = ErrorCode.UPSTREAM_UNAVAILABLE
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "A dependency is unavailable."


class FeatureDisabledError(AppError):
    code = ErrorCode.FEATURE_DISABLED
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    message = "This capability is not enabled in this environment."


def _render(
    status_code: int,
    code: ErrorCode,
    message: str,
    request: Request,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details,
            request_id=getattr(request.state, "request_id", None),
        )
    )
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "application_error",
            error_code=exc.code.value,
            status_code=exc.status_code,
            detail=exc.message,
        )
        return _render(exc.status_code, exc.code, exc.message, request, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _render(
            422,
            ErrorCode.VALIDATION_FAILED,
            "The request could not be validated.",
            request,
            {"errors": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Log the detail; return none of it. Stack traces and internal messages
        # are for operators, not for whoever sent the request.
        logger.exception("unhandled_exception", exception_type=type(exc).__name__)
        return _render(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ErrorCode.INTERNAL_ERROR,
            "An unexpected error occurred.",
            request,
        )
