"""Real routing/verification and quota gates without PostgreSQL or AWS calls."""

from dataclasses import replace
from functools import partial
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.adapters.local.identity import LocalIdentityProvider
from app.adapters.local.rate_limit_store import InMemoryRateLimitStore
from app.api.deps import get_db_session
from app.core.config import Environment, Settings
from app.core.container import build_container
from app.core.errors import NotFoundError, UpstreamError
from app.main import create_app
from app.models.principal import Principal
from app.ports.rate_limit_store import RateLimitStore
from app.security.claims import claims_to_principal
from app.security.rate_limits import (
    AUTH_ATTEMPT,
    DOCUMENT_WRITE,
    LOCAL_LOGIN,
    RAG,
    enforce_rate_limit,
)
from app.services.identity import IdentityService
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def frozen_quota_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import deps
    from app.core import container as composition

    def clock() -> float:
        return 1_700_000_001.0

    monkeypatch.setattr(deps, "enforce_rate_limit", partial(enforce_rate_limit, clock=clock))
    monkeypatch.setattr(
        composition, "InMemoryRateLimitStore", partial(InMemoryRateLimitStore, clock=clock),
    )


def _headers(signer: LocalIdentityProvider, principal: Principal) -> dict[str, str]:
    return {"Authorization": "Bearer " + signer.mint(
        subject=str(principal.user_id), email=principal.email,
        groups=[principal.role.value], organization_id=str(principal.organization_id),
        department=principal.department,
    )}


def test_login_quota_cannot_be_reset_by_forwarded_or_identity_headers(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        for index in range(LOCAL_LOGIN.requests):
            response = client.post(
                "/api/v1/auth/dev-login", json={"email": "unknown@example.test"},
                headers={"x-forwarded-for": f"192.0.2.{index}", "x-user-id": str(index)},
            )
            assert response.status_code == 401
        response = client.post("/api/v1/auth/dev-login", json={"email": "unknown@example.test"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_rag_gate_runs_once_per_request_and_before_identity_database_work(
    settings: Settings, token_signer: LocalIdentityProvider, analyst: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve = AsyncMock(side_effect=claims_to_principal)
    monkeypatch.setattr(IdentityService, "resolve", resolve)
    with TestClient(create_app(settings)) as client:
        for _ in range(RAG.requests):
            response = client.post(
                "/api/v1/rag/query", json={"query": "Describe policy"},
                headers=_headers(token_signer, analyst),
            )
            assert response.status_code == 200
        response = client.post(
            "/api/v1/rag/query", json={"query": "Describe policy"},
            headers=_headers(token_signer, analyst),
        )
    assert response.status_code == 429
    assert resolve.await_count == RAG.requests


def test_store_outage_prevents_token_verification(settings: Settings) -> None:
    class BrokenStore(RateLimitStore):
        async def consume(self, *, key: str, limit: int, expires_at: int) -> bool:
            raise UpstreamError("SYNTHETIC-SENSITIVE-PROVIDER")

    app = create_app(settings)
    verify = AsyncMock()
    identity = app.state.container.identity
    identity.verify = verify
    app.state.container = replace(app.state.container, rate_limits=BrokenStore())
    with TestClient(app) as client:
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer invalid"})
        assert client.get("/api/v1/health").status_code == 200
    verify.assert_not_awaited()
    assert response.status_code == 503
    assert "SYNTHETIC-SENSITIVE" not in response.text


def test_tenant_quota_is_shared_across_verified_users_not_request_headers(
    settings: Settings, token_signer: LocalIdentityProvider, analyst: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(IdentityService, "resolve", AsyncMock(side_effect=claims_to_principal))
    with TestClient(create_app(settings)) as client:
        for index in range(100):
            user = analyst.model_copy(update={"user_id": UUID(int=100 + index // 20)})
            response = client.post(
                "/api/v1/rag/query", json={"query": "Describe policy"},
                headers=_headers(token_signer, user),
            )
            assert response.status_code == 200
        sixth = analyst.model_copy(update={"user_id": UUID(int=106)})
        headers = _headers(token_signer, sixth)
        headers["x-organization-id"] = str(UUID(int=999))
        assert client.post(
            "/api/v1/rag/query", json={"query": "Describe policy"}, headers=headers,
        ).status_code == 429
        other_tenant = analyst.model_copy(update={"organization_id": UUID(int=999)})
        assert client.post(
            "/api/v1/rag/query", json={"query": "Describe policy"},
            headers=_headers(token_signer, other_tenant),
        ).status_code == 200


def test_rate_admission_does_not_grant_approval_authority(
    settings: Settings, token_signer: LocalIdentityProvider, analyst: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(IdentityService, "resolve", AsyncMock(side_effect=claims_to_principal))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/v1/approvals/{UUID(int=100)}/decision",
            json={"decision": "approved"}, headers=_headers(token_signer, analyst),
        )
    assert response.status_code == 403


def test_changing_document_ids_does_not_create_new_user_budgets(
    settings: Settings, token_signer: LocalIdentityProvider, analyst: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import documents

    monkeypatch.setattr(IdentityService, "resolve", AsyncMock(side_effect=claims_to_principal))
    service = AsyncMock()
    service.complete_upload.side_effect = NotFoundError()
    monkeypatch.setattr(documents, "_service", lambda **kwargs: service)
    app = create_app(settings)
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    headers = _headers(token_signer, analyst)
    with TestClient(app) as client:
        for index in range(DOCUMENT_WRITE.requests):
            assert client.post(
                f"/api/v1/documents/{UUID(int=index + 100)}/complete", headers=headers,
            ).status_code == 404
        assert client.post(
            f"/api/v1/documents/{UUID(int=999)}/complete", headers=headers,
        ).status_code == 429
    assert service.complete_upload.await_count == DOCUMENT_WRITE.requests


def test_shared_authentication_bucket_counts_missing_and_invalid_tokens(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        for index in range(AUTH_ATTEMPT.requests):
            headers = {} if index % 2 else {"Authorization": "Bearer invalid"}
            assert client.get("/api/v1/me", headers=headers).status_code == 401
        assert client.get("/api/v1/me").status_code == 429


def test_custom_api_prefix_cannot_bypass_login_policy(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"api_v1_prefix": "/custom"}))
    with TestClient(app) as client:
        for _ in range(LOCAL_LOGIN.requests):
            client.post("/custom/auth/dev-login", json={"email": "unknown@example.test"})
        assert client.post(
            "/custom/auth/dev-login", json={"email": "unknown@example.test"},
        ).status_code == 429


def test_local_counter_instances_are_not_shared_between_applications(settings: Settings) -> None:
    assert isinstance(build_container(settings).rate_limits, InMemoryRateLimitStore)
    assert build_container(settings).rate_limits is not build_container(settings).rate_limits


def test_deployed_api_refuses_to_fall_back_to_local_counters() -> None:
    settings = Settings(
        environment=Environment.DEV, identity_provider="cognito",
        cognito={"user_pool_id": "us-east-1_mock", "client_id": "mock_client"},
    )
    with pytest.raises(ValueError, match="RATE_LIMIT_TABLE_NAME"):
        build_container(settings)
