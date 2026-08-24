"""FastAPI application factory.

A factory rather than a module-level ``app = FastAPI()`` because tests need to
build an application with overridden settings. A global singleton forces tests
to mutate shared state, and tests that mutate shared state fail in whatever
order they happen to run tomorrow.

Startup deliberately fails fast: if configuration is invalid or a selected
adapter does not exist, the process dies at boot with a message naming the
problem. A Lambda that starts successfully and then 500s on every request is
much harder to diagnose than one that refuses to start.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware import register_middleware
from app.api.v1.router import build_api_router
from app.core.config import Settings, get_settings
from app.core.container import build_container
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.repositories.database import dispose_engine

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved)

    # Built here, not in a module-level global: the container belongs to this
    # application instance. Tests construct an app with their own settings and
    # get their own adapters, with no shared mutable state between test cases.
    # It is eager so that misconfiguration kills the process at construction
    # rather than 500-ing on the first request.
    container = build_container(resolved)

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        logger.info("application_started", environment=resolved.environment.value)
        yield
        await dispose_engine()
        logger.info("application_stopped")

    app = FastAPI(
        title="CloudGuard AI",
        description="Agentic audit & compliance intelligence platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,  # exact origins; never "*"
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "x-request-id", "x-dev-principal"],
        expose_headers=["x-request-id"],
        max_age=600,
    )

    # Dependencies resolve these off the request's app — see app/api/deps.py.
    app.state.settings = resolved
    app.state.container = container

    register_middleware(app)
    register_exception_handlers(app)
    app.include_router(build_api_router(container), prefix=resolved.api_v1_prefix)
    return app


app = create_app()
