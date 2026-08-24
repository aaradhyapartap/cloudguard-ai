"""Identity service — the first module in the service layer.

Its job is **just-in-time provisioning**: Cognito owns the credential, this
database owns the application record, and the two are reconciled on first login
rather than by a separate sync process that can silently fall behind.

Two decisions worth defending:

**The token is authoritative for role and organization; the database is not.**
On every login the local row is updated to match the claims. If an administrator
moves someone from the manager group to analyst in Cognito, that takes effect on
their next login without anyone touching the database. The alternative — trusting
a stale local role — means a revoked privilege quietly persists, which is exactly
the finding this product exists to catch.

**An unknown organization is refused, not created.** A valid signature proves
who someone is, not that their tenant should exist here. Auto-creating an
organization from a claim would let anyone holding a token for a pool mint a new
tenant. Organizations are provisioned deliberately by an administrator.

Note what this module does *not* import: no ``boto3``, no ``jwt``, no FastAPI.
It takes verified claims and a session, and returns a domain object. That is the
service layer boundary from ADR-0013 doing its job.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.models.identity import VerifiedClaims
from app.models.principal import Principal
from app.repositories.database import tenant_session, untenanted_session
from app.security.claims import claims_to_principal

logger = get_logger(__name__)


class IdentityService:
    """Turns verified claims into a Principal, provisioning the local user record.

    This service manages its own sessions rather than receiving one, because
    identity resolution is the one operation that spans two tenancy contexts by
    nature: it reads a table that has no tenant (``organizations``) in order to
    establish which tenant the caller belongs to, and only then can it write
    inside that tenant.
    """

    async def resolve(self, claims: VerifiedClaims) -> Principal:
        principal = claims_to_principal(claims)
        await self._ensure_organization_exists(principal.organization_id)
        await self._upsert_user(principal)
        return principal

    async def _ensure_organization_exists(self, organization_id: UUID) -> None:
        # `organizations` carries no RLS policy: a tenant must be resolvable
        # before a tenant context can exist. It holds no tenant-owned data.
        async with untenanted_session() as session:
            result = await session.execute(
                text("SELECT 1 FROM organizations WHERE id = :id"),
                {"id": organization_id},
            )
            found = result.scalar_one_or_none()

        if found is None:
            logger.warning(
                "login_rejected_unknown_organization",
                organization_id=str(organization_id),
            )
            # Generic message: whether a given organization id exists is not
            # something an unauthenticated caller should be able to probe.
            raise AuthenticationError("Your account is not provisioned for this service.")

    async def _upsert_user(self, principal: Principal) -> None:
        """Insert on first login; reconcile role and department on every login.

        Runs inside the caller's own tenant context, so the RLS ``WITH CHECK``
        clause enforces that the row being written belongs to the organization
        in the token. A provisioning bug cannot place a user in the wrong
        tenant — the database refuses the write.
        """
        async with tenant_session(principal.organization_id) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO users (
                        id, organization_id, email, role, department, last_login_at
                )
                    VALUES (:id, :org, :email, CAST(:role AS role), :dept, now())
                    ON CONFLICT (id) DO UPDATE SET
                        email         = EXCLUDED.email,
                        role          = EXCLUDED.role,
                        department    = EXCLUDED.department,
                        last_login_at = now(),
                        updated_at    = now()
                    """
                ),
                {
                    "id": principal.user_id,
                    "org": principal.organization_id,
                    "email": principal.email,
                    "role": principal.role.value,
                    "dept": principal.department,
                },
            )
