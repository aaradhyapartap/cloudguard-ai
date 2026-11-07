"""Focused tests for the local SQLAlchemy document-processing repository adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from app.adapters.local.document_processing_repository import (
    SQLAlchemyDocumentProcessingRepository,
)
from app.models.tenant import TenantScope

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


async def test_sqlalchemy_claim_uses_conditional_update() -> None:
    """The SQLAlchemy repository must issue a conditional UPDATE matching QUEUED status."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute.return_value = mock_result

    scope = TenantScope(organization_id=ORG_ID)
    repository = SQLAlchemyDocumentProcessingRepository(
        session=mock_session,
        scope=scope,
    )

    claimed = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert claimed is True
    assert mock_session.execute.call_count == 1
    assert mock_session.flush.call_count == 1

    # Inspect the compiled SQL / statement
    executed_stmt = mock_session.execute.call_args[0][0]
    compiled_str = str(executed_stmt)

    assert "UPDATE documents" in compiled_str
    assert "processing_status" in compiled_str
    assert "WHERE documents.organization_id = :organization_id" in compiled_str
    assert "documents.id = :id" in compiled_str


async def test_sqlalchemy_claim_returns_false_when_zero_rows_updated() -> None:
    """When 0 rows match the conditional UPDATE, claim_for_processing must return False."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_session.execute.return_value = mock_result

    scope = TenantScope(organization_id=ORG_ID)
    repository = SQLAlchemyDocumentProcessingRepository(
        session=mock_session,
        scope=scope,
    )

    claimed = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert claimed is False
