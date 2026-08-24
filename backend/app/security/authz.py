"""Role-based authorization.

The permission matrix lives in **one place**, as data, and is checked
server-side on every request. Two consequences worth being explicit about:

* Hiding a button in the frontend is user experience, not access control. The
  server re-checks on every call and does not care what the UI rendered.
* Because the matrix is data rather than scattered ``if role == "admin"``
  branches, it can be tested exhaustively — see ``tests/unit/test_authz.py``,
  which asserts every (role, permission) pair rather than the handful someone
  remembered to check.

Adding a permission requires adding it to every role's set deliberately. There
is no wildcard and no inheritance: ADMIN does not implicitly get everything,
because "admin can do anything" is how a compliance platform ends up with an
administrator who can silently rewrite an audit log.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.errors import AuthorizationError
from app.models.enums import Role
from app.models.principal import Principal


class Permission(StrEnum):
    # Documents
    DOCUMENT_UPLOAD = "document:upload"
    DOCUMENT_READ = "document:read"
    DOCUMENT_DELETE = "document:delete"

    # AI
    AI_QUERY = "ai:query"

    # Risks and findings
    RISK_READ = "risk:read"
    RISK_REVIEW = "risk:review"
    RISK_MODIFY_SEVERITY = "risk:modify_severity"

    # Investigations
    INVESTIGATION_CREATE = "investigation:create"
    INVESTIGATION_READ = "investigation:read"
    INVESTIGATION_CLOSE = "investigation:close"

    # Approvals — the human-in-the-loop gate
    APPROVAL_READ = "approval:read"
    APPROVAL_DECIDE = "approval:decide"

    # Analytics
    ANALYTICS_READ_OWN = "analytics:read_own"
    ANALYTICS_READ_TEAM = "analytics:read_team"

    # Administration
    USER_MANAGE = "user:manage"
    SETTINGS_MANAGE = "settings:manage"
    AUDIT_LOG_READ = "audit:read"


_ANALYST: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_READ,
        Permission.AI_QUERY,
        Permission.RISK_READ,
        Permission.INVESTIGATION_CREATE,
        Permission.INVESTIGATION_READ,
        Permission.ANALYTICS_READ_OWN,
    }
)

_MANAGER: frozenset[Permission] = _ANALYST | frozenset(
    {
        Permission.RISK_REVIEW,
        Permission.RISK_MODIFY_SEVERITY,
        Permission.INVESTIGATION_CLOSE,
        Permission.APPROVAL_READ,
        Permission.APPROVAL_DECIDE,
        Permission.ANALYTICS_READ_TEAM,
    }
)

# Note what an admin does NOT get: APPROVAL_DECIDE and RISK_MODIFY_SEVERITY.
# Platform administration and compliance judgement are different jobs. An
# administrator who can approve their own recommendation is a segregation-of-
# duties finding in the product that exists to find those.
_ADMIN: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_DELETE,
        Permission.RISK_READ,
        Permission.INVESTIGATION_READ,
        Permission.APPROVAL_READ,
        Permission.ANALYTICS_READ_TEAM,
        Permission.USER_MANAGE,
        Permission.SETTINGS_MANAGE,
        Permission.AUDIT_LOG_READ,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ANALYST: _ANALYST,
    Role.MANAGER: _MANAGER,
    Role.ADMIN: _ADMIN,
}


def has_permission(principal: Principal, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[principal.role]


def require_permission(principal: Principal, permission: Permission) -> None:
    """Raise :class:`AuthorizationError` unless the principal holds the permission."""
    if not has_permission(principal, permission):
        raise AuthorizationError()


def assert_same_organization(principal: Principal, organization_id: object) -> None:
    """Belt to the database's braces.

    Row-Level Security already prevents cross-tenant reads. This check exists so
    a mismatch fails loudly at the application boundary with a clear stack trace,
    rather than silently returning an empty result set that looks like a
    legitimate 'no rows found'.
    """
    if str(principal.organization_id) != str(organization_id):
        raise AuthorizationError()
