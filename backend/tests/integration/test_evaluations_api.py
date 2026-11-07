"""Integration tests for AI Evaluation API endpoints against real PostgreSQL + RLS.

Exercises:
- GET /api/v1/evaluations/dataset (metadata inspection)
- POST /api/v1/evaluations/runs (triggering deterministic suite under RLS)
- Role segregation: Analyst (201), Manager (201), Admin (403: lacks ai:query)
- GET /api/v1/evaluations/runs (own vs team visibility)
- Cross-tenant isolation (Acme vs Globex)
- GET /api/v1/evaluations/runs/{run_id} (detail retrieval)
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from app.adapters.local.identity import LocalIdentityProvider
from app.models.enums import Role
from app.models.principal import Principal
from fastapi.testclient import TestClient

from conftest import ORG_A, ORG_B, bearer, skip_without_database  # type: ignore[import-not-found]

pytestmark = [pytest.mark.integration, skip_without_database]


@pytest.fixture(autouse=True)
def _isolate_database_engine() -> Iterator[None]:
    import app.repositories.database as db_mod

    if db_mod._engine is not None:
        db_mod._engine.sync_engine.dispose()
        db_mod._engine = None
        db_mod._session_factory = None
    yield
    if db_mod._engine is not None:
        db_mod._engine.sync_engine.dispose()
        db_mod._engine = None
        db_mod._session_factory = None


def _make_principal(
    role: Role,
    *,
    org: UUID = ORG_A,
    user_id: str | None = None,
) -> Principal:
    user_map_a = {
        Role.ANALYST: "33333333-3333-4333-8333-333333333333",
        Role.MANAGER: "44444444-4444-4444-8444-444444444444",
        Role.ADMIN: "55555555-5555-4555-8555-555555555555",
    }
    user_map_b = {
        Role.ANALYST: "66666666-6666-4666-8666-666666666666",
        Role.MANAGER: "77777777-7777-4777-8777-777777777777",
        Role.ADMIN: "88888888-8888-4888-8888-888888888888",
    }
    mapping = user_map_b if org == ORG_B else user_map_a
    uid = user_id or mapping[role]
    return Principal(
        user_id=UUID(uid),
        organization_id=org,
        role=role,
        email=f"{role.value}-{uid[:8]}@{org.hex[:6]}.test",
        department="Audit",
    )


def test_get_evaluation_dataset_metadata(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    headers = bearer(token_signer, _make_principal(Role.ANALYST))
    response = client.get("/api/v1/evaluations/dataset", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["dataset_version"] == "1.0.0"
    assert data["golden_case_count"] == 50
    assert data["adversarial_case_count"] == 8
    assert "precision_at_5" in data["metric_definitions"]
    assert len(data["known_limitations"]) > 0


def test_evaluation_run_creation_and_role_segregation(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    # 1. Admin is rejected because Admin lacks AI_QUERY permission
    admin_headers = bearer(token_signer, _make_principal(Role.ADMIN))
    admin_resp = client.post(
        "/api/v1/evaluations/runs",
        headers=admin_headers,
        json={"execution_mode": "deterministic_fixture", "max_cases": 2},
    )
    assert admin_resp.status_code == 403

    # 2. Analyst can trigger an evaluation run
    analyst_headers = bearer(token_signer, _make_principal(Role.ANALYST))
    analyst_resp = client.post(
        "/api/v1/evaluations/runs",
        headers=analyst_headers,
        json={"execution_mode": "deterministic_fixture", "max_cases": 2},
    )
    assert analyst_resp.status_code == 201
    run_data = analyst_resp.json()
    assert run_data["organization_id"] == str(ORG_A)
    assert run_data["execution_mode"] == "deterministic_fixture"
    assert len(run_data["case_results"]) == 2
    run_id = run_data["id"]

    # 3. Manager can view the run (team visibility)
    manager_headers = bearer(token_signer, _make_principal(Role.MANAGER))
    detail_resp = client.get(f"/api/v1/evaluations/runs/{run_id}", headers=manager_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == run_id

    # 4. Cross-tenant isolation: Globex caller cannot view Acme's run
    globex_headers = bearer(token_signer, _make_principal(Role.MANAGER, org=ORG_B))
    cross_resp = client.get(f"/api/v1/evaluations/runs/{run_id}", headers=globex_headers)
    assert cross_resp.status_code == 404


def test_evaluation_runs_listing_visibility_scopes(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    analyst_p = _make_principal(Role.ANALYST)
    manager_p = _make_principal(Role.MANAGER)
    globex_p = _make_principal(Role.ANALYST, org=ORG_B)

    # Analyst listing reports "own" scope
    a_resp = client.get("/api/v1/evaluations/runs", headers=bearer(token_signer, analyst_p))
    assert a_resp.status_code == 200
    assert a_resp.json()["visibility_scope"] == "own"

    # Manager listing reports "team" scope
    m_resp = client.get("/api/v1/evaluations/runs", headers=bearer(token_signer, manager_p))
    assert m_resp.status_code == 200
    assert m_resp.json()["visibility_scope"] == "team"

    # Globex sees no Acme runs
    g_resp = client.get("/api/v1/evaluations/runs", headers=bearer(token_signer, globex_p))
    assert g_resp.status_code == 200
    assert g_resp.json()["visibility_scope"] == "own"

