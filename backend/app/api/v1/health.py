"""Liveness and readiness.

Two endpoints, because they answer different questions:

* ``/health``  — is this process alive? No dependency checks. A load balancer
                 must not take a node out of rotation because the database
                 blinked; that would turn one outage into a cascade.
* ``/health/ready`` — can this process serve traffic? Checks dependencies.

Both are unauthenticated by design: an infrastructure probe has no credentials.
Neither leaks anything an attacker can use — no versions of dependencies, no
hostnames, no connection strings.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.models.common import HealthStatus
from app.repositories.database import check_database_health

router = APIRouter(tags=["system"])

APP_VERSION = "0.1.0"


@router.get("/health", response_model=HealthStatus, summary="Liveness")
async def health() -> HealthStatus:
    settings = get_settings()
    return HealthStatus(
        status="ok",
        environment=settings.environment.value,
        version=APP_VERSION,
        checked_at=datetime.now(UTC),
        dependencies={},
    )


@router.get("/health/ready", response_model=HealthStatus, summary="Readiness")
async def readiness(response: Response) -> HealthStatus:
    settings = get_settings()
    database_ok = await check_database_health()

    dependencies = {"database": "ok" if database_ok else "unavailable"}
    overall = "ok" if database_ok else "degraded"
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthStatus(
        status=overall,
        environment=settings.environment.value,
        version=APP_VERSION,
        checked_at=datetime.now(UTC),
        dependencies=dependencies,
    )
