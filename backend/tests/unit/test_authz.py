"""Exhaustive authorization matrix.

Every (role, permission) pair is asserted, not the handful someone remembered.
When a permission is added, this test fails until its expected grants are
declared — which forces the decision to be made deliberately instead of by
copying whichever role's set was nearest.
"""

from __future__ import annotations

import pytest
from app.core.errors import AuthorizationError
from app.models.enums import Role
from app.models.principal import Principal
from app.security.authz import (
    ROLE_PERMISSIONS,
    Permission,
    assert_same_organization,
    has_permission,
    require_permission,
)

EXPECTED: dict[Role, set[Permission]] = {
    Role.ANALYST: {
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_READ,
        Permission.AI_QUERY,
        Permission.RISK_READ,
        Permission.INVESTIGATION_CREATE,
        Permission.INVESTIGATION_READ,
        Permission.ANALYTICS_READ_OWN,
    },
    Role.MANAGER: {
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_READ,
        Permission.AI_QUERY,
        Permission.RISK_READ,
        Permission.RISK_REVIEW,
        Permission.RISK_MODIFY_SEVERITY,
        Permission.INVESTIGATION_CREATE,
        Permission.INVESTIGATION_READ,
        Permission.INVESTIGATION_CLOSE,
        Permission.APPROVAL_READ,
        Permission.APPROVAL_DECIDE,
        Permission.ANALYTICS_READ_OWN,
        Permission.ANALYTICS_READ_TEAM,
    },
    Role.ADMIN: {
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_DELETE,
        Permission.RISK_READ,
        Permission.INVESTIGATION_READ,
        Permission.APPROVAL_READ,
        Permission.ANALYTICS_READ_TEAM,
        Permission.USER_MANAGE,
        Permission.SETTINGS_MANAGE,
        Permission.AUDIT_LOG_READ,
    },
}


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("permission", list(Permission))
def test_every_role_permission_pair(role: Role, permission: Permission) -> None:
    principal = Principal(
        user_id="00000000-0000-4000-8000-000000000001",  # type: ignore[arg-type]
        organization_id="00000000-0000-4000-8000-000000000002",  # type: ignore[arg-type]
        role=role,
        email="user@example.test",
    )
    assert has_permission(principal, permission) is (permission in EXPECTED[role])


def test_matrix_matches_declaration() -> None:
    assert {role: set(perms) for role, perms in ROLE_PERMISSIONS.items()} == EXPECTED


def test_analyst_cannot_decide_approvals(analyst: Principal) -> None:
    with pytest.raises(AuthorizationError):
        require_permission(analyst, Permission.APPROVAL_DECIDE)


def test_admin_cannot_decide_approvals(admin: Principal) -> None:
    """Segregation of duties: platform admin is not compliance judgement."""
    with pytest.raises(AuthorizationError):
        require_permission(admin, Permission.APPROVAL_DECIDE)


def test_manager_can_decide_approvals(manager: Principal) -> None:
    require_permission(manager, Permission.APPROVAL_DECIDE)


def test_cross_organization_access_is_refused(analyst: Principal) -> None:
    with pytest.raises(AuthorizationError):
        assert_same_organization(analyst, "99999999-9999-4999-8999-999999999999")


def test_clearance_ladder(analyst: Principal, manager: Principal, admin: Principal) -> None:
    assert len(analyst.visible_confidentiality_levels) == 2
    assert len(manager.visible_confidentiality_levels) == 3
    assert len(admin.visible_confidentiality_levels) == 4
