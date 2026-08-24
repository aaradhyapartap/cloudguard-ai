"""Claims → Principal is a policy boundary, not a data conversion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models.enums import Role
from app.models.identity import VerifiedClaims
from app.ports.identity_provider import TokenVerificationError
from app.security.claims import claims_to_principal, resolve_role

ORG_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "33333333-3333-4333-8333-333333333333"


def build_claims(**overrides: object) -> VerifiedClaims:
    payload: dict[str, object] = {
        "subject": USER_ID,
        "email": "analyst@acme.test",
        "groups": ["analyst"],
        "organization_id": ORG_ID,
        "department": "Finance",
        "issuer": "https://issuer.test",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "token_use": "id",
    }
    payload.update(overrides)
    return VerifiedClaims(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (["analyst"], Role.ANALYST),
        (["manager"], Role.MANAGER),
        (["admin"], Role.ADMIN),
        # Unrecognised groups are ignored rather than inherited. Real Cognito
        # pools accumulate groups for reasons unrelated to this application.
        (["analyst", "sso-users", "everyone"], Role.ANALYST),
    ],
)
def test_recognised_groups_map_to_roles(groups: list[str], expected: Role) -> None:
    assert resolve_role(groups) == expected


def test_no_recognised_group_is_rejected() -> None:
    with pytest.raises(TokenVerificationError, match="no recognised role group"):
        resolve_role(["sso-users"])


def test_empty_groups_are_rejected() -> None:
    with pytest.raises(TokenVerificationError, match="no recognised role group"):
        resolve_role([])


def test_ambiguous_group_membership_fails_closed() -> None:
    """Two role groups is a misconfiguration, not a puzzle to solve by precedence.

    Silently picking one is how somebody acquires approval rights nobody granted.
    """
    with pytest.raises(TokenVerificationError, match="ambiguous role"):
        resolve_role(["analyst", "manager"])


def test_principal_is_built_from_claims() -> None:
    principal = claims_to_principal(build_claims())
    assert str(principal.organization_id) == ORG_ID
    assert principal.role is Role.ANALYST
    assert principal.department == "Finance"


def test_missing_organization_claim_is_rejected() -> None:
    """There is no safe default tenant — certainly not 'all of them'."""
    with pytest.raises(TokenVerificationError, match="organization_id"):
        claims_to_principal(build_claims(organization_id=None))


def test_malformed_identifier_is_rejected() -> None:
    with pytest.raises(TokenVerificationError, match="malformed"):
        claims_to_principal(build_claims(organization_id="not-a-uuid"))


def test_clearance_follows_role() -> None:
    analyst = claims_to_principal(build_claims(groups=["analyst"]))
    admin = claims_to_principal(build_claims(groups=["admin"]))
    assert len(analyst.visible_confidentiality_levels) == 2
    assert len(admin.visible_confidentiality_levels) == 4
