"""Aurora Data API adapter for document-processing persistence.

Deployed ingestion workers remain outside the database VPC and access Aurora
Serverless v2 through the RDS Data API. boto3 is synchronous, so provider calls
are dispatched to worker threads rather than blocking the asyncio event loop.

All tenant-scoped operations will run inside one Data API transaction so the
transaction-local PostgreSQL tenant setting used by RLS applies to every
statement in that operation.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any
from uuid import UUID

from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus
from app.ports.document_processing_repository import DocumentProcessingRepository

logger = get_logger(__name__)


class AuroraDataAPIDocumentProcessingRepository(DocumentProcessingRepository):
    """Document-processing persistence backed by the Aurora Data API."""

    def __init__(
        self,
        *,
        resource_arn: str,
        secret_arn: str,
        database: str,
        region: str,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not resource_arn:
            raise ValueError("Aurora Data API resource ARN is required.")
        if not secret_arn:
            raise ValueError("Aurora Data API secret ARN is required.")
        if not database:
            raise ValueError("Aurora database name is required.")

        self._resource_arn = resource_arn
        self._secret_arn = secret_arn
        self._database = database

        if client is not None:
            self._client = client
            return

        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "rds-data",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(
                partial(getattr(self._client, method), **kwargs)
            )
        except Exception as exc:
            logger.error(
                "aurora_data_api_call_failed",
                method=method,
                error=str(exc),
            )
            raise UpstreamError("Could not access document persistence.") from exc

    async def _begin_transaction(self) -> str:
        response = await self._call(
            "begin_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
        )
        transaction_id = str(response.get("transactionId", ""))
        if not transaction_id:
            raise UpstreamError("Could not start the database transaction.")
        return transaction_id

    async def _commit_transaction(self, transaction_id: str) -> None:
        await self._call(
            "commit_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            transactionId=transaction_id,
        )

    async def _rollback_transaction(self, transaction_id: str) -> None:
        await self._call(
            "rollback_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            transactionId=transaction_id,
        )

    async def _execute(
        self,
        *,
        sql: str,
        transaction_id: str,
        parameters: list[dict[str, Any]] | None = None,
        include_result_metadata: bool = False,
    ) -> dict[str, Any]:
        response = await self._call(
            "execute_statement",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
            transactionId=transaction_id,
            sql=sql,
            parameters=parameters or [],
            includeResultMetadata=include_result_metadata,
        )
        return dict(response)

    async def _set_tenant(
        self,
        *,
        transaction_id: str,
        organization_id: UUID,
    ) -> None:
        await self._execute(
            sql=(
                "SELECT set_config("
                "'app.current_organization_id', "
                ":organization_id, "
                "true"
                ")"
            ),
            transaction_id=transaction_id,
            parameters=[
                {
                    "name": "organization_id",
                    "value": {"stringValue": str(organization_id)},
                }
            ],
        )

    async def _run_in_tenant_transaction(
        self,
        *,
        organization_id: UUID,
        operation: Any,
    ) -> Any:
        transaction_id = await self._begin_transaction()

        try:
            await self._set_tenant(
                transaction_id=transaction_id,
                organization_id=organization_id,
            )

            result = await operation(transaction_id)

            await self._commit_transaction(transaction_id)
            return result

        except Exception:
            try:
                await self._rollback_transaction(transaction_id)
            except UpstreamError:
                logger.exception(
                    "aurora_data_api_rollback_failed",
                    transaction_id=transaction_id,
                )
            raise

    async def claim_for_processing(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> bool:
        async def operation(transaction_id: str) -> bool:
            response = await self._execute(
                sql=(
                    "UPDATE documents "
                    "SET processing_status = "
                    "CAST(:extracting AS processing_status), "
                    "processing_error = NULL, "
                    "updated_at = now() "
                    "WHERE organization_id = :organization_id "
                    "AND id = :document_id "
                    "AND processing_status = "
                    "CAST(:queued AS processing_status)"
                ),
                transaction_id=transaction_id,
                parameters=[
                    {
                        "name": "extracting",
                        "value": {"stringValue": ProcessingStatus.EXTRACTING.value},
                    },
                    {
                        "name": "queued",
                        "value": {"stringValue": ProcessingStatus.QUEUED.value},
                    },
                    {
                        "name": "organization_id",
                        "value": {"stringValue": str(organization_id)},
                        "typeHint": "UUID",
                    },
                    {
                        "name": "document_id",
                        "value": {"stringValue": str(document_id)},
                        "typeHint": "UUID",
                    },
                ],
            )
            updated_count = int(response.get("numberOfRecordsUpdated", 0) or 0)
            return updated_count == 1

        result = await self._run_in_tenant_transaction(
            organization_id=organization_id,
            operation=operation,
        )
        return bool(result)

    async def get_document(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> ProcessingDocument | None:
        async def operation(transaction_id: str) -> ProcessingDocument | None:
            response = await self._execute(
                sql=(
                    "SELECT id, organization_id, filename, storage_key, "
                    "content_type, processing_status "
                    "FROM documents "
                    "WHERE organization_id = :organization_id "
                    "AND id = :document_id "
                    "LIMIT 1"
                ),
                transaction_id=transaction_id,
                parameters=[
                    {
                        "name": "organization_id",
                        "value": {"stringValue": str(organization_id)},
                        "typeHint": "UUID",
                    },
                    {
                        "name": "document_id",
                        "value": {"stringValue": str(document_id)},
                        "typeHint": "UUID",
                    },
                ],
            )

            records = response.get("records", [])
            if not records:
                return None

            record = records[0]
            values = [
                str(field.get("stringValue", ""))
                for field in record
            ]

            return ProcessingDocument(
                id=UUID(values[0]),
                organization_id=UUID(values[1]),
                filename=values[2],
                storage_key=values[3],
                content_type=values[4],
                processing_status=ProcessingStatus(values[5]),
            )

        result: ProcessingDocument | None = await self._run_in_tenant_transaction(
            organization_id=organization_id,
            operation=operation,
        )
        return result

    async def set_status(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        status: ProcessingStatus,
        error: str | None,
    ) -> None:
        async def operation(transaction_id: str) -> None:
            error_value: dict[str, Any]
            if error is None:
                error_value = {"isNull": True}
            else:
                error_value = {"stringValue": error}

            await self._execute(
                sql=(
                    "UPDATE documents "
                    "SET processing_status = "
                    "CAST(:status AS processing_status), "
                    "processing_error = :error, "
                    "updated_at = now() "
                    "WHERE organization_id = :organization_id "
                    "AND id = :document_id"
                ),
                transaction_id=transaction_id,
                parameters=[
                    {
                        "name": "status",
                        "value": {"stringValue": status.value},
                    },
                    {
                        "name": "error",
                        "value": error_value,
                    },
                    {
                        "name": "organization_id",
                        "value": {"stringValue": str(organization_id)},
                        "typeHint": "UUID",
                    },
                    {
                        "name": "document_id",
                        "value": {"stringValue": str(document_id)},
                        "typeHint": "UUID",
                    },
                ],
            )

        await self._run_in_tenant_transaction(
            organization_id=organization_id,
            operation=operation,
        )

    async def _batch_execute(
        self,
        *,
        sql: str,
        transaction_id: str,
        parameter_sets: list[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        response = await self._call(
            "batch_execute_statement",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
            transactionId=transaction_id,
            sql=sql,
            parameterSets=parameter_sets,
        )
        return dict(response)

    async def add_chunks(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        chunks: list[ProcessingChunk],
    ) -> None:
        if not chunks:
            return

        async def operation(transaction_id: str) -> None:
            from uuid import uuid4

            parameter_sets: list[list[dict[str, Any]]] = []
            for chunk in chunks:
                parameter_sets.append(
                    [
                        {
                            "name": "id",
                            "value": {"stringValue": str(uuid4())},
                            "typeHint": "UUID",
                        },
                        {
                            "name": "organization_id",
                            "value": {"stringValue": str(organization_id)},
                            "typeHint": "UUID",
                        },
                        {
                            "name": "document_id",
                            "value": {"stringValue": str(document_id)},
                            "typeHint": "UUID",
                        },
                        {
                            "name": "chunk_index",
                            "value": {"longValue": chunk.chunk_index},
                        },
                        {
                            "name": "content",
                            "value": {"stringValue": chunk.content},
                        },
                        {
                            "name": "token_count",
                            "value": {"longValue": chunk.token_count},
                        },
                        {
                            "name": "metadata",
                            "value": {
                                "stringValue": json.dumps(chunk.metadata),
                            },
                            "typeHint": "JSON",
                        },
                    ]
                )

            await self._batch_execute(
                sql=(
                    "INSERT INTO document_chunks "
                    "(id, organization_id, document_id, chunk_index, "
                    "content, token_count, metadata) "
                    "VALUES (:id, :organization_id, :document_id, "
                    ":chunk_index, :content, :token_count, "
                    "CAST(:metadata AS jsonb))"
                ),
                transaction_id=transaction_id,
                parameter_sets=parameter_sets,
            )

        await self._run_in_tenant_transaction(
            organization_id=organization_id,
            operation=operation,
        )
