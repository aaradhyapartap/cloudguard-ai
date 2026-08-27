"""Authenticated API behaviour, against a real database.

Needs PostgreSQL because ``get_principal`` provisions the caller (just-in-time
user creation) before any route runs. Run with::

    docker compose up -d postgres
    cd backend && alembic upgrade head && python ../scripts/seed_data.py
    RUN_DB_TESTS=1 pytest -m integration

The centrepiece is :func:`test_route_protection_matrix`. It walks **every**
protected route against **every** role and asserts the exact expected status.
Adding a route without adding it to ``ROUTE_MATRIX`` fails
``test_matrix_covers_every_protected_route`` â€” so the coverage cannot silently
rot as the API grows, which is how authorization holes normally appear.
"""

from __future__ import annotations

import pytest
from app.adapters.local.identity import LocalIdentityProvider
from app.models.enums import Role
from app.models.principal import Principal
from app.repositories.database import dispose_engine
from fastapi.testclient import TestClient

from conftest import ORG_A, bearer, skip_without_database  # type: ignore[import-not-found]

pytestmark = [pytest.mark.integration, skip_without_database]

MISSING_DOCUMENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

DOCUMENT_CREATE_BODY = {
    "filename": "phase3-test-policy.pdf",
    "content_type": "application/pdf",
    "size_bytes": 1024,
    "document_type": "policy",
    "confidentiality_level": "internal",
    "department": "Audit",
    "source": "integration-test",
    "tags": ["phase3", "test"],
}

# route -> method -> {role: expected status}
ROUTE_MATRIX: dict[tuple[str, str], dict[Role, int]] = {
    ("GET", "/api/v1/me"): {
        Role.ANALYST: 200,
        Role.MANAGER: 200,
        Role.ADMIN: 200,
    },
    ("GET", "/api/v1/system/config"): {
        Role.ANALYST: 403,
        Role.MANAGER: 403,
        Role.ADMIN: 200,
    },
    ("POST", "/api/v1/documents"): {
        Role.ANALYST: 201,
        Role.MANAGER: 201,
        Role.ADMIN: 403,
    },
    ("POST", "/api/v1/documents/{document_id}/complete"): {
        Role.ANALYST: 404,
        Role.MANAGER: 404,
        Role.ADMIN: 403,
    },
    ("GET", "/api/v1/documents"): {
        Role.ANALYST: 200,
        Role.MANAGER: 200,
        Role.ADMIN: 200,
    },
    ("GET", "/api/v1/documents/{document_id}"): {
        Role.ANALYST: 404,
        Role.MANAGER: 404,
        Role.ADMIN: 404,
    },
}


@pytest.fixture(autouse=True)
async def _cleanup() -> object:
    yield None
    await dispose_engine()


def principal_for(role: Role) -> Principal:
    return Principal(
        user_id={
            Role.ANALYST: "33333333-3333-4333-8333-333333333333",
            Role.MANAGER: "44444444-4444-4444-8444-444444444444",
            Role.ADMIN: "55555555-5555-4555-8555-555555555555",
        }[role],
        organization_id=ORG_A,
        role=role,
        email=f"{role.value}@acme.test",
        department="Audit",
    )


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("method", "path"), list(ROUTE_MATRIX))
def test_route_protection_matrix(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    role: Role,
    method: str,
    path: str,
) -> None:
    expected = ROUTE_MATRIX[(method, path)][role]

    request_path = path.replace("{document_id}", MISSING_DOCUMENT_ID)
    headers = bearer(token_signer, principal_for(role))

    if method == "POST" and path == "/api/v1/documents":
        response = client.request(
            method,
            request_path,
            headers=headers,
            json=DOCUMENT_CREATE_BODY,
        )
    else:
        response = client.request(
            method,
            request_path,
            headers=headers,
        )

    assert response.status_code == expected, (
        f"{role.value} {method} {path}: "
        f"expected {expected}, got {response.status_code}"
    )

def test_matrix_covers_every_protected_route(client: TestClient) -> None:
    """Guards the guard: a new protected route must declare its expectations."""
    schema = client.get("/openapi.json").json()
    public = {
        "/api/v1/health",
        "/api/v1/health/ready",
        "/api/v1/auth/config",
        "/api/v1/auth/dev-login",
    }

    documented = {
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        for method in methods
        if path not in public
    }
    missing = documented - set(ROUTE_MATRIX)
    assert not missing, f"routes with no authorization expectations declared: {missing}"


def test_me_reports_server_authoritative_permissions(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    analyst: Principal,
) -> None:
    body = client.get(
        "/api/v1/me",
        headers=bearer(token_signer, analyst),
    ).json()

    assert body["role"] == "analyst"
    assert "ai:query" in body["permissions"]
    assert "approval:decide" not in body["permissions"]


def test_admin_cannot_decide_approvals(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    admin: Principal,
) -> None:
    """Segregation of duties, verified through the API rather than only in a unit test."""
    body = client.get(
        "/api/v1/me",
        headers=bearer(token_signer, admin),
    ).json()

    assert "approval:decide" not in body["permissions"]
    assert "user:manage" in body["permissions"]


def test_token_for_an_unprovisioned_organization_is_refused(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    """A valid signature proves identity, not that the tenant should exist here.

    Auto-creating an organization from a claim would let anyone holding a pool
    token mint a new tenant.
    """
    token = token_signer.mint(
        subject="99999999-9999-4999-8999-999999999999",
        email="ghost@nowhere.test",
        groups=["analyst"],
        organization_id="88888888-8888-4888-8888-888888888888",
    )

    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_token_with_no_role_group_is_refused(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    token = token_signer.mint(
        subject="33333333-3333-4333-8333-333333333333",
        email="analyst@acme.test",
        groups=["sso-users"],
        organization_id=str(ORG_A),
    )

    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_token_with_two_role_groups_is_refused(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    """Ambiguity fails closed rather than resolving by precedence."""
    token = token_signer.mint(
        subject="33333333-3333-4333-8333-333333333333",
        email="analyst@acme.test",
        groups=["analyst", "admin"],
        organization_id=str(ORG_A),
    )

    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_dev_login_issues_a_usable_token(client: TestClient) -> None:
    """End to end: log in, then use what you got."""
    login = client.post(
        "/api/v1/auth/dev-login",
        json={"email": "analyst@acme.test"},
    )

    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert me.status_code == 200
    assert me.json()["email"] == "analyst@acme.test"


def test_dev_login_for_an_unknown_user_is_refused(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/auth/dev-login",
        json={"email": "nobody@acme.test"},
    )

    assert response.status_code == 401


def test_login_reconciles_role_from_the_token(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    analyst: Principal,
) -> None:
    """The token is authoritative for role; the local row follows it.

    An administrator moving someone between Cognito groups takes effect on their
    next login. A stale local role would mean a revoked privilege silently
    persisting â€” the finding this product exists to catch.
    """
    promoted = analyst.model_copy(update={"role": Role.MANAGER})

    body = client.get(
        "/api/v1/me",
        headers=bearer(token_signer, promoted),
    ).json()

    assert body["role"] == "manager"
    assert "approval:decide" in body["permissions"]

    # And back down again, proving it is reconciliation rather than a one-way upgrade.
    body = client.get(
        "/api/v1/me",
        headers=bearer(token_signer, analyst),
    ).json()

    assert body["role"] == "analyst"
    assert "approval:decide" not in body["permissions"]
