"""API surface that needs no database and no credentials."""

from __future__ import annotations

from app.core.config import Environment, Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_liveness_is_unauthenticated(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_auth_config_is_public(client: TestClient) -> None:
    """The frontend must be able to discover where to log in before it has a token."""
    response = client.get("/api/v1/auth/config")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "local"
    assert body["local_login_enabled"] is True


def test_auth_config_exposes_no_secret(client: TestClient) -> None:
    body = client.get("/api/v1/auth/config").json()
    assert "secret" not in str(body).lower()


def test_protected_route_without_a_token_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_garbage_token_is_401(client: TestClient) -> None:
    response = client.get(
        "/api/v1/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert response.status_code == 401


def test_rejection_reason_is_not_leaked_to_the_caller(client: TestClient) -> None:
    """Which check failed tells a forger what to fix next. It stays in the log."""
    message = client.get(
        "/api/v1/me", headers={"Authorization": "Bearer not.a.jwt"}
    ).json()["error"]["message"]
    for leak in ("signature", "expired", "issuer", "audience", "algorithm", "kid"):
        assert leak not in message.lower()


def test_non_bearer_scheme_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/me", headers={"Authorization": "Basic abc123"})
    assert response.status_code == 401


def test_security_headers_are_present(client: TestClient) -> None:
    headers = client.get("/api/v1/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    assert client.get("/api/v1/health").headers["x-request-id"].startswith("req_")


def test_dev_login_route_exists_when_local(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/auth/dev-login" in schema["paths"]


def test_dev_login_route_is_absent_when_not_local() -> None:
    """Not disabled behind a flag — absent from the routing table entirely.

    A route that does not exist cannot be misconfigured back into existence.
    """
    settings = Settings(
        environment=Environment.DEV,
        identity_provider="cognito",
        llm_provider="mock",
        cognito={"user_pool_id": "us-east-1_Pool", "client_id": "client-123"},
    )
    with TestClient(create_app(settings)) as client:
        assert client.post(
            "/api/v1/auth/dev-login", json={"email": "a@b.test"}
        ).status_code == 404
        assert "/api/v1/auth/dev-login" not in client.get("/openapi.json").json()["paths"]


def test_cognito_environment_reports_its_issuer() -> None:
    settings = Settings(
        environment=Environment.DEV,
        identity_provider="cognito",
        llm_provider="mock",
        cognito={"user_pool_id": "us-east-1_Pool", "client_id": "client-123"},
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/auth/config").json()
    assert body["provider"] == "cognito"
    assert body["issuer"].endswith("us-east-1_Pool")
    assert body["local_login_enabled"] is False
