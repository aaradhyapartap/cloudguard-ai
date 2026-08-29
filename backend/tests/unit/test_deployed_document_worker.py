"""Focused tests for the deployed document-processing Lambda task.

Validates that:
- The deployed task accepts a normalized Step Functions payload
- Missing or malformed fields fail clearly
- The deployed path constructs AuroraDataAPIDocumentProcessingRepository
- The deployed path does NOT depend on tenant_session or SQLAlchemy
- The local worker path remains unchanged
"""

from __future__ import annotations

import ast
import inspect
from typing import Any
from uuid import UUID

import pytest

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _patch_arns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure Aurora ARNs on the deployed worker settings."""
    from app import deployed_document_worker

    monkeypatch.setattr(
        deployed_document_worker._settings.aws,
        "aurora_cluster_arn",
        "arn:aws:rds:us-east-1:123456789012:cluster:cloudguard",
    )
    monkeypatch.setattr(
        deployed_document_worker._settings.aws,
        "aurora_secret_arn",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:cloudguard",
    )


# --- Normalized Step Functions task input ---


async def test_deployed_worker_processes_normalized_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid {organization_id, document_id} payload must route to processing."""
    from app import deployed_document_worker

    processed_ids: list[UUID] = []

    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def process_document(self, document_id: UUID) -> None:
            processed_ids.append(document_id)

    _patch_arns(monkeypatch)
    monkeypatch.setattr(
        deployed_document_worker,
        "DocumentProcessingService",
        FakeService,
    )

    await deployed_document_worker._handle(
        {
            "organization_id": str(ORG_ID),
            "document_id": str(DOCUMENT_ID),
        }
    )

    assert processed_ids == [DOCUMENT_ID]


async def test_deployed_worker_fails_missing_organization_id() -> None:
    """Missing organization_id must raise ValueError."""
    from app import deployed_document_worker

    with pytest.raises(ValueError, match="organization_id"):
        await deployed_document_worker._handle(
            {
                "document_id": str(DOCUMENT_ID),
            }
        )


async def test_deployed_worker_fails_missing_document_id() -> None:
    """Missing document_id must raise ValueError."""
    from app import deployed_document_worker

    with pytest.raises(ValueError, match="document_id"):
        await deployed_document_worker._handle(
            {
                "organization_id": str(ORG_ID),
            }
        )


async def test_deployed_worker_fails_malformed_organization_id() -> None:
    """A non-UUID organization_id must raise ValueError."""
    from app import deployed_document_worker

    with pytest.raises(ValueError, match="organization_id is not a valid UUID"):
        await deployed_document_worker._handle(
            {
                "organization_id": "not-a-uuid",
                "document_id": str(DOCUMENT_ID),
            }
        )


async def test_deployed_worker_fails_malformed_document_id() -> None:
    """A non-UUID document_id must raise ValueError."""
    from app import deployed_document_worker

    with pytest.raises(ValueError, match="document_id is not a valid UUID"):
        await deployed_document_worker._handle(
            {
                "organization_id": str(ORG_ID),
                "document_id": "not-a-uuid",
            }
        )


# --- Deployed path uses Data API repository ---


async def test_deployed_worker_constructs_data_api_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployed entrypoint must construct AuroraDataAPIDocumentProcessingRepository."""
    from app import deployed_document_worker
    from app.adapters.aws.document_processing_repository import (
        AuroraDataAPIDocumentProcessingRepository,
    )

    _patch_arns(monkeypatch)

    repository = deployed_document_worker._build_repository()

    assert isinstance(repository, AuroraDataAPIDocumentProcessingRepository)


# --- Missing configuration fails clearly ---


async def test_deployed_worker_fails_without_cluster_arn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing AWS_AURORA_CLUSTER_ARN must raise RuntimeError at build time."""
    from app import deployed_document_worker

    monkeypatch.setattr(
        deployed_document_worker._settings.aws,
        "aurora_cluster_arn",
        None,
    )
    monkeypatch.setattr(
        deployed_document_worker._settings.aws,
        "aurora_secret_arn",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:cloudguard",
    )

    with pytest.raises(RuntimeError, match="AWS_AURORA_CLUSTER_ARN"):
        deployed_document_worker._build_repository()


async def test_deployed_worker_fails_without_secret_arn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing AWS_AURORA_SECRET_ARN must raise RuntimeError at build time."""
    from app import deployed_document_worker

    monkeypatch.setattr(
        deployed_document_worker._settings.aws,
        "aurora_cluster_arn",
        "arn:aws:rds:us-east-1:123456789012:cluster:cloudguard",
    )
    monkeypatch.setattr(
        deployed_document_worker._settings.aws,
        "aurora_secret_arn",
        None,
    )

    with pytest.raises(RuntimeError, match="AWS_AURORA_SECRET_ARN"):
        deployed_document_worker._build_repository()


# --- Deployed path does NOT depend on tenant_session or SQLAlchemy ---


def test_deployed_worker_does_not_import_tenant_session() -> None:
    """The deployed entrypoint must not import tenant_session.

    This is the core architectural constraint from ADR-0008: deployed Lambdas
    remain outside the VPC and must not use the asyncpg-backed tenant_session.
    """
    import app.deployed_document_worker as module

    source = inspect.getsource(module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.append(alias.name if alias.asname is None else alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name if alias.asname is None else alias.asname)

    assert "tenant_session" not in imported_names, (
        "deployed_document_worker must not import tenant_session"
    )


def test_deployed_worker_does_not_import_sqlalchemy_repository() -> None:
    """The deployed entrypoint must not import the SQLAlchemy repository."""
    import app.deployed_document_worker as module

    source = inspect.getsource(module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.append(alias.name if alias.asname is None else alias.asname)

    assert "SQLAlchemyDocumentProcessingRepository" not in imported_names, (
        "deployed_document_worker must not import SQLAlchemyDocumentProcessingRepository"
    )


# --- Local worker path remains unchanged ---


def test_local_worker_imports_tenant_session() -> None:
    """The local worker must still use tenant_session for SQLAlchemy-based processing."""
    import app.document_worker as module

    source = inspect.getsource(module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.append(alias.name if alias.asname is None else alias.asname)

    assert "tenant_session" in imported_names, (
        "local document_worker must import tenant_session"
    )
    assert "SQLAlchemyDocumentProcessingRepository" in imported_names, (
        "local document_worker must import SQLAlchemyDocumentProcessingRepository"
    )
