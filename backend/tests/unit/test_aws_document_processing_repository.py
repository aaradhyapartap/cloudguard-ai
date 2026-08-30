"""Focused tests for the Aurora Data API document-processing adapter."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from app.adapters.aws.document_processing_repository import (
    AuroraDataAPIDocumentProcessingRepository,
)
from app.core.errors import UpstreamError
from app.models.documents import ProcessingChunk
from app.models.enums import ProcessingStatus

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeRDSDataClient:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        number_of_records_updated: int = 1,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fail_on = fail_on
        self.number_of_records_updated = number_of_records_updated

    def begin_transaction(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("begin_transaction", kwargs))
        if self._fail_on == "begin_transaction":
            raise RuntimeError("simulated begin_transaction failure")
        return {"transactionId": "tx-123"}

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("execute_statement", kwargs))
        if self._fail_on == "execute_statement":
            raise RuntimeError("simulated execute_statement failure")

        sql = str(kwargs["sql"])
        if sql.startswith("SELECT set_config"):
            return {}

        if "UPDATE documents" in sql:
            return {"numberOfRecordsUpdated": self.number_of_records_updated}

        return {
            "records": [
                [
                    {"stringValue": str(DOCUMENT_ID)},
                    {"stringValue": str(ORG_ID)},
                    {"stringValue": "policy.txt"},
                    {
                        "stringValue": (
                            f"org/{ORG_ID}/documents/{DOCUMENT_ID}/policy.txt"
                        )
                    },
                    {"stringValue": "text/plain"},
                    {"stringValue": "extracting"},
                    {"stringValue": "internal"},
                ]
            ]
        }

    def batch_execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("batch_execute_statement", kwargs))
        if self._fail_on == "batch_execute_statement":
            raise RuntimeError("simulated batch_execute_statement failure")
        return {"updateResults": []}

    def commit_transaction(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("commit_transaction", kwargs))
        if self._fail_on == "commit_transaction":
            raise RuntimeError("simulated commit_transaction failure")
        return {}

    def rollback_transaction(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("rollback_transaction", kwargs))
        return {}


def _make_repository(
    client: FakeRDSDataClient,
) -> AuroraDataAPIDocumentProcessingRepository:
    return AuroraDataAPIDocumentProcessingRepository(
        resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:cloudguard",
        secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:cloudguard",
        database="cloudguard",
        region="us-east-1",
        client=client,
    )


# --- claim_for_processing ---


async def test_claim_for_processing_success() -> None:
    """claim_for_processing must conditionally UPDATE status from QUEUED to EXTRACTING."""
    client = FakeRDSDataClient(number_of_records_updated=1)
    repository = _make_repository(client)

    claimed = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert claimed is True
    assert [name for name, _ in client.calls] == [
        "begin_transaction",
        "execute_statement",
        "execute_statement",
        "commit_transaction",
    ]

    update_call = client.calls[2][1]
    assert update_call["transactionId"] == "tx-123"
    sql = update_call["sql"]
    assert "UPDATE documents" in sql
    assert "processing_status = CAST(:extracting AS processing_status)" in sql
    assert "processing_status = CAST(:queued AS processing_status)" in sql
    assert "organization_id = :organization_id" in sql
    assert "id = :document_id" in sql

    params = {p["name"]: p for p in update_call["parameters"]}
    assert params["extracting"]["value"]["stringValue"] == "extracting"
    assert params["queued"]["value"]["stringValue"] == "queued"
    assert params["organization_id"]["value"]["stringValue"] == str(ORG_ID)
    assert params["organization_id"]["typeHint"] == "UUID"
    assert params["document_id"]["value"]["stringValue"] == str(DOCUMENT_ID)
    assert params["document_id"]["typeHint"] == "UUID"


async def test_claim_for_processing_returns_false_when_not_queued() -> None:
    """claim_for_processing returns False when 0 rows are updated (e.g. not in QUEUED status)."""
    client = FakeRDSDataClient(number_of_records_updated=0)
    repository = _make_repository(client)

    claimed = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert claimed is False


# --- get_document ---


async def test_get_document_runs_inside_tenant_transaction() -> None:
    client = FakeRDSDataClient()
    repository = _make_repository(client)

    document = await repository.get_document(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert document is not None
    assert document.id == DOCUMENT_ID
    assert document.organization_id == ORG_ID
    assert document.filename == "policy.txt"
    assert document.content_type == "text/plain"
    assert document.processing_status is ProcessingStatus.EXTRACTING

    assert [name for name, _ in client.calls] == [
        "begin_transaction",
        "execute_statement",
        "execute_statement",
        "commit_transaction",
    ]

    tenant_call = client.calls[1][1]
    assert tenant_call["transactionId"] == "tx-123"
    assert "set_config" in tenant_call["sql"]

    # set_config organization_id must NOT have UUID typeHint (set_config expects text)
    tenant_params = {p["name"]: p for p in tenant_call["parameters"]}
    assert "typeHint" not in tenant_params["organization_id"]

    document_call = client.calls[2][1]
    assert document_call["transactionId"] == "tx-123"
    assert "FROM documents" in document_call["sql"]

    # UUID SQL parameters must carry typeHint "UUID"
    doc_params = {p["name"]: p for p in document_call["parameters"]}
    assert doc_params["organization_id"]["typeHint"] == "UUID"
    assert doc_params["document_id"]["typeHint"] == "UUID"


# --- set_status ---


async def test_set_status_updates_document_inside_tenant_transaction() -> None:
    """set_status must begin transaction, set tenant, UPDATE, commit."""
    client = FakeRDSDataClient()
    repository = _make_repository(client)

    await repository.set_status(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
        status=ProcessingStatus.READY,
        error=None,
    )

    call_names = [name for name, _ in client.calls]
    assert call_names == [
        "begin_transaction",
        "execute_statement",  # set_config
        "execute_statement",  # UPDATE
        "commit_transaction",
    ]

    # Verify tenant was set in the transaction
    tenant_call = client.calls[1][1]
    assert tenant_call["transactionId"] == "tx-123"
    assert "set_config" in tenant_call["sql"]

    # Verify UPDATE uses parameterized SQL with tenant filter and enum cast
    update_call = client.calls[2][1]
    assert update_call["transactionId"] == "tx-123"
    assert "UPDATE documents" in update_call["sql"]
    assert "CAST(:status AS processing_status)" in update_call["sql"]
    assert "organization_id = :organization_id" in update_call["sql"]
    assert "id = :document_id" in update_call["sql"]

    # set_config organization_id must NOT have UUID typeHint
    tenant_params = {p["name"]: p for p in tenant_call["parameters"]}
    assert "typeHint" not in tenant_params["organization_id"]

    # Verify parameters are passed, not concatenated, with UUID typeHints
    params = {p["name"]: p for p in update_call["parameters"]}
    assert params["status"]["value"] == {"stringValue": ProcessingStatus.READY.value}
    assert params["organization_id"]["value"] == {"stringValue": str(ORG_ID)}
    assert params["organization_id"]["typeHint"] == "UUID"
    assert params["document_id"]["value"] == {"stringValue": str(DOCUMENT_ID)}
    assert params["document_id"]["typeHint"] == "UUID"
    assert params["error"]["value"] == {"isNull": True}


async def test_set_status_passes_error_message() -> None:
    """set_status must forward a non-None error string as a parameter."""
    client = FakeRDSDataClient()
    repository = _make_repository(client)

    await repository.set_status(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
        status=ProcessingStatus.FAILED,
        error="extraction timed out",
    )

    update_call = client.calls[2][1]
    params = {p["name"]: p["value"] for p in update_call["parameters"]}
    assert params["status"] == {"stringValue": ProcessingStatus.FAILED.value}
    assert params["error"] == {"stringValue": "extraction timed out"}


# --- add_chunks ---


async def test_add_chunks_inserts_inside_tenant_transaction() -> None:
    """add_chunks must begin transaction, set tenant, batch INSERT, commit."""
    client = FakeRDSDataClient()
    repository = _make_repository(client)

    chunks = [
        ProcessingChunk(
            chunk_index=0,
            content="First section of the policy document.",
            token_count=8,
            metadata={"heading": "Introduction"},
        ),
        ProcessingChunk(
            chunk_index=1,
            content="Second section covering compliance requirements.",
            token_count=7,
            metadata={"heading": "Compliance"},
        ),
    ]

    await repository.add_chunks(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
        chunks=chunks,
    )

    call_names = [name for name, _ in client.calls]
    assert call_names == [
        "begin_transaction",
        "execute_statement",          # set_config
        "batch_execute_statement",    # INSERT chunks
        "commit_transaction",
    ]

    # Verify tenant was set
    tenant_call = client.calls[1][1]
    assert tenant_call["transactionId"] == "tx-123"
    assert "set_config" in tenant_call["sql"]

    # Verify batch INSERT uses parameterized SQL with explicit jsonb cast
    batch_call = client.calls[2][1]
    assert batch_call["transactionId"] == "tx-123"
    assert "INSERT INTO document_chunks" in batch_call["sql"]
    assert ":organization_id" in batch_call["sql"]
    assert ":document_id" in batch_call["sql"]
    assert ":chunk_index" in batch_call["sql"]
    assert ":content" in batch_call["sql"]
    assert ":token_count" in batch_call["sql"]
    assert "CAST(:metadata AS jsonb)" in batch_call["sql"]

    # set_config organization_id must NOT have UUID typeHint
    tenant_params = {p["name"]: p for p in tenant_call["parameters"]}
    assert "typeHint" not in tenant_params["organization_id"]

    # Verify two parameter sets (one per chunk)
    param_sets = batch_call["parameterSets"]
    assert len(param_sets) == 2

    # Check first chunk parameters including UUID typeHints
    first_params = {p["name"]: p for p in param_sets[0]}
    assert first_params["id"]["typeHint"] == "UUID"
    assert first_params["organization_id"]["value"] == {
        "stringValue": str(ORG_ID)
    }
    assert first_params["organization_id"]["typeHint"] == "UUID"
    assert first_params["document_id"]["value"] == {
        "stringValue": str(DOCUMENT_ID)
    }
    assert first_params["document_id"]["typeHint"] == "UUID"
    assert first_params["chunk_index"]["value"] == {"longValue": 0}
    assert first_params["content"]["value"] == {
        "stringValue": "First section of the policy document."
    }
    assert first_params["token_count"]["value"] == {"longValue": 8}
    assert json.loads(
        first_params["metadata"]["value"]["stringValue"]
    ) == {"heading": "Introduction"}

    # Check second chunk parameters
    second_params = {p["name"]: p for p in param_sets[1]}
    assert second_params["chunk_index"]["value"] == {"longValue": 1}
    assert second_params["content"]["value"] == {
        "stringValue": "Second section covering compliance requirements."
    }
    assert second_params["token_count"]["value"] == {"longValue": 7}


async def test_add_chunks_skips_empty_list() -> None:
    """add_chunks must no-op when the chunk list is empty."""
    client = FakeRDSDataClient()
    repository = _make_repository(client)

    await repository.add_chunks(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
        chunks=[],
    )

    assert client.calls == []


# --- rollback ---


async def test_transaction_rolls_back_on_execute_failure() -> None:
    """When an execute_statement fails, the transaction must be rolled back."""
    # The fake client will fail on the second execute_statement call (the
    # actual query, not the set_config). We configure it to fail on
    # execute_statement — the first succeed is set_config, then _execute
    # re-raises UpstreamError which triggers rollback.
    # We need a client that fails on the second execute_statement only.
    client = FakeRDSDataClient()
    repository = _make_repository(client)

    call_count = 0
    original_execute = client.execute_statement

    def _execute_fail_on_second(**kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise RuntimeError("simulated query failure")
        return original_execute(**kwargs)

    client.execute_statement = _execute_fail_on_second  # type: ignore[method-assign]

    with pytest.raises(UpstreamError):
        await repository.set_status(
            organization_id=ORG_ID,
            document_id=DOCUMENT_ID,
            status=ProcessingStatus.FAILED,
            error="test",
        )

    call_names = [name for name, _ in client.calls]
    assert "begin_transaction" in call_names
    assert "rollback_transaction" in call_names
    assert "commit_transaction" not in call_names

    # Verify rollback used the correct transaction ID
    rollback_call = next(
        kwargs for name, kwargs in client.calls if name == "rollback_transaction"
    )
    assert rollback_call["transactionId"] == "tx-123"


async def test_add_chunks_rolls_back_on_batch_failure() -> None:
    """When batch_execute_statement fails, the transaction must roll back."""
    client = FakeRDSDataClient(fail_on="batch_execute_statement")
    repository = _make_repository(client)

    chunks = [
        ProcessingChunk(
            chunk_index=0,
            content="Chunk that will cause a failure.",
            token_count=6,
            metadata={},
        ),
    ]

    with pytest.raises(UpstreamError):
        await repository.add_chunks(
            organization_id=ORG_ID,
            document_id=DOCUMENT_ID,
            chunks=chunks,
        )

    call_names = [name for name, _ in client.calls]
    assert "begin_transaction" in call_names
    assert "rollback_transaction" in call_names
    assert "commit_transaction" not in call_names
