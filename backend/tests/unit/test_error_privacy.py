"""Validation failures must not reflect submitted values or exception text."""

from __future__ import annotations

import pytest
from app.core.errors import ConflictError, register_exception_handlers
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1)


def test_real_pydantic_validation_does_not_echo_values_or_extra_field_names() -> None:
    sentinel = "synthetic-sensitive@example.test"
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/validate")
    async def validate(payload: _Payload) -> None:
        return None

    with TestClient(app) as client:
        response = client.post("/validate", json={"count": sentinel, sentinel: sentinel})

    assert response.status_code == 422
    assert sentinel not in response.text
    assert len(response.json()["error"]["details"]["errors"]) == 2


@pytest.mark.parametrize("location", ["body", "query", "path", "header", "cookie"])
def test_validation_error_exposes_only_safe_location(location: str) -> None:
    sentinel = "SYNTHETIC-SENSITIVE-VALUE"
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/invalid")
    async def invalid() -> None:
        raise RequestValidationError(
            [
                {
                    "type": sentinel,
                    "loc": (location, sentinel),
                    "msg": sentinel,
                    "input": sentinel,
                    "ctx": {"error": ValueError(sentinel)},
                    "url": f"https://example.test/{sentinel}",
                }
            ],
            body={"password": sentinel},
        )

    with TestClient(app) as client:
        response = client.get("/invalid")

    assert response.status_code == 422
    assert sentinel not in response.text
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert response.json()["error"]["details"]["errors"] == [
        {"type": "validation_error", "msg": "Invalid request value.", "loc": [location]}
    ]


def test_unexpected_exception_response_is_sanitized() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise RuntimeError("SYNTHETIC-CREDENTIAL")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/failure")

    assert response.status_code == 500
    assert "SYNTHETIC-CREDENTIAL" not in response.text
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_application_error_drops_internal_details_and_detected_sensitive_message() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError(
            "Rejected api_key=synthetic-credential-value",
            details={"task_token": "SYNTHETIC-INTERNAL-CAPABILITY"},
        )

    with TestClient(app) as client:
        response = client.get("/conflict")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert response.json()["error"]["details"] is None
    assert "synthetic-credential" not in response.text
    assert "SYNTHETIC-INTERNAL" not in response.text
