"""Claims → Principal.

This is a policy boundary, not a data conversion, and it is where several
security decisions live:

**Group ambiguity fails closed.** A user carrying two role groups is rejected,
not resolved by precedence. In a compliance product, silently picking a role for
someone with contradictory group membership is how a Manager quietly acquires
approval rights they were never meant to have. An explicit failure gets fixed;
a silent resolution does not.

**Unknown groups are ignored, not inherited.** Cognito pools accumulate groups
for all sorts of reasons. Only the three that map to a role are considered.

**A missing organization claim is fatal.** Every query in the system is scoped by
``organization_id``. A principal without one has no tenant, and there is no safe
default — certainly not "all tenants".
"""

from __future__ import annotations

from uuid import UUID

from app.models.enums import Role
from app.models.identity import VerifiedClaims
from app.models.principal import Principal
from app.ports.identity_provider import TokenVerificationError

# Cognito group name → application role. Groups outside this map are ignored.
GROUP_TO_ROLE: dict[str, Role] = {
    "analyst": Role.ANALYST,
    "manager": Role.MANAGER,
    "admin": Role.ADMIN,
}


def resolve_role(groups: list[str]) -> Role:
    """Exactly one recognised role group, or the token is rejected."""
    matched = {GROUP_TO_ROLE[group] for group in groups if group in GROUP_TO_ROLE}

    if not matched:
        raise TokenVerificationError(
            f"no recognised role group in {sorted(groups)!r}"
        )
    if len(matched) > 1:
        raise TokenVerificationError(
            f"ambiguous role: token carries {sorted(role.value for role in matched)!r}"
        )
    return matched.pop()


def claims_to_principal(claims: VerifiedClaims) -> Principal:
    if not claims.organization_id:
        raise TokenVerificationError("token carries no organization_id claim")

    try:
        organization_id = UUID(claims.organization_id)
        user_id = UUID(claims.subject)
    except ValueError as exc:
        raise TokenVerificationError(f"malformed identifier claim: {exc}") from exc

    return Principal(
        user_id=user_id,
        organization_id=organization_id,
        role=resolve_role(claims.groups),
        email=claims.email,
        department=claims.department,
    )
