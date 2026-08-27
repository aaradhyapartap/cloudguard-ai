"""Aggregate router for API v1.

Routers are registered here and nowhere else, so the full API surface is
readable in one screen. Phase 3 onward appends documents, ai, risks,
investigations, approvals, analytics and audit.

``build_api_router`` takes the container because one router — local development
login — is mounted conditionally. Registering it based on the active identity
provider means it is absent from the routing table and the OpenAPI schema in
every other environment, rather than present and guarded.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.adapters.local.identity import LocalIdentityProvider
from app.api.v1 import auth, documents, health, system
from app.core.container import Container


def build_api_router(container: Container) -> APIRouter:
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(system.router)
    router.include_router(auth.router)
    router.include_router(documents.router)

    if isinstance(container.identity, LocalIdentityProvider):
        router.include_router(auth.build_dev_login_router(container.identity))

    return router
