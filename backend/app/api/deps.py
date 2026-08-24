"""FastAPI dependencies.

**Changed in Phase 2.** The ``x-dev-principal`` header is gone. Every request now
carries an ``Authorization: Bearer <jwt>`` token, verified by the configured
:class:`IdentityProvider` — Cognito in AWS, a local HS256 signer offline. Both
run the same verification and mapping code (ADR-0014), so local development is
not a different security model wearing the same URLs.

Ordering here is a security design — cheapest and most conclusive first:

1. Extract the bearer token; a malformed header never reaches crypto.
2. Verify signature, issuer, audience, expiry, token use.
3. Map claims to a role, failing closed on ambiguity.
4. Provision or reconcile the local user record.
5. Bind identity to the logging context so every later line is attributable.

Note that the tenant-scoped session depends on the principal. There is no way to
obtain a database session without having authenticated first — not by
convention, but because the dependency graph contains no such edge.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.params import Depends as DependsMarker
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.container import Container
from app.core.errors import AuthenticationError
from app.core.logging import bind_request_context, get_logger
from app.models.principal import Principal
from app.ports.identity_provider import TokenVerificationError
from app.repositories.database import tenant_session
from app.security.authz import Permission, require_permission
from app.services.identity import IdentityService

logger = get_logger(__name__)

# auto_error=False so a missing header produces our structured 401 envelope
# rather than FastAPI's default shape. One error format across the whole API.
bearer_scheme = HTTPBearer(auto_error=False, description="Cognito ID token")


def get_app_settings(request: Request) -> Settings:
    """Read settings from *this* application instance, not a module global."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_app_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ContainerDep = Annotated[Container, Depends(get_app_container)]


async def get_principal(
    request: Request,
    container: ContainerDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("A bearer token is required.")

    # Both stages are inside the same handler on purpose. Signature failures
    # and claim-policy failures (no role group, two role groups, no
    # organization) are both "this token is not acceptable" and must both
    # produce 401. An earlier version wrapped only the signature check, so a
    # token with an unrecognised group escaped as an unhandled 500 — which
    # leaks a stack trace and reads as a server fault rather than a rejection.
    try:
        claims = await container.identity.verify(credentials.credentials)
        # IdentityService manages its own sessions: it reads the untenanted
        # organizations table to learn which tenant this caller belongs to,
        # then writes inside that tenant's context.
        principal = await IdentityService().resolve(claims)
    except TokenVerificationError as exc:
        # The specific reason is logged for operators and withheld from the
        # caller. Telling a forger which check failed tells them what to fix.
        logger.warning("token_rejected", reason=exc.reason)
        raise AuthenticationError("Authentication failed.") from exc

    request.state.organization_id = str(principal.organization_id)
    request.state.user_id = str(principal.user_id)
    bind_request_context(
        request_id=getattr(request.state, "request_id", "unknown"),
        organization_id=str(principal.organization_id),
        user_id=str(principal.user_id),
        route=request.url.path,
    )
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_db_session(principal: PrincipalDep) -> AsyncIterator[AsyncSession]:
    """A transaction with Row-Level Security bound to the caller's organization."""
    async with tenant_session(principal.organization_id) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def requires(permission: Permission) -> DependsMarker:
    """Route guard: ``dependencies=[requires(Permission.AI_QUERY)]``.

    Server-side, every request, no exceptions. The frontend hiding a button is
    presentation; this is the control.
    """

    async def _guard(principal: PrincipalDep) -> None:
        require_permission(principal, permission)

    return cast(DependsMarker, Depends(_guard))
