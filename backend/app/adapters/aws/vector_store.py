"""Aurora Data API adapter for vector store persistence and similarity search.

Deployed Lambda workers access Aurora Serverless v2 pgvector through the RDS Data API
without requiring VPC attachment or a NAT Gateway.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from functools import partial
from typing import Any
from uuid import UUID

from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.models.ai import VectorMatch, VectorRecord
from app.models.enums import ConfidentialityLevel
from app.ports.vector_store import (
    VectorStore,
    validate_embedding,
    validate_top_k,
)

logger = get_logger(__name__)


def _serialize_vector(embedding: list[float]) -> str:
    """Format float list into a PostgreSQL vector string literal like '[0.1,0.2,...]'."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


class AuroraDataAPIVectorStore(VectorStore):
    """Vector store persistence backed by the Aurora Data API with pgvector."""

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
                "aurora_data_api_vector_call_failed",
                method=method,
                error=str(exc),
            )
            raise UpstreamError("Could not access vector persistence.") from exc

    async def _begin_transaction(self) -> str:
        response = await self._call(
            "begin_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
        )
        transaction_id = str(response.get("transactionId", ""))
        if not transaction_id:
            raise UpstreamError("Could not start database transaction.")
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
                    "aurora_data_api_vector_rollback_failed",
                    transaction_id=transaction_id,
                )
            raise

    async def upsert(self, records: list[VectorRecord]) -> int:
        """Insert or replace vector embeddings on existing document chunks."""
        if not records:
            return 0

        # Validate dimensions and values before starting transactions
        for record in records:
            validate_embedding(
                record.embedding,
                label=f"chunk {record.chunk_id} embedding",
            )

        grouped: dict[str, list[VectorRecord]] = defaultdict(list)
        for record in records:
            grouped[record.organization_id].append(record)

        total_updated = 0
        for org_id_str, org_records in grouped.items():
            current_org_id = UUID(org_id_str)
            records_to_update = list(org_records)

            async def operation(
                transaction_id: str,
                records: list[VectorRecord] = records_to_update,
                target_org_id: UUID = current_org_id,
            ) -> int:
                updated_count = 0
                for rec in records:
                    response = await self._execute(
                        sql=(
                            "UPDATE document_chunks "
                            "SET embedding = CAST(:embedding AS vector) "
                            "WHERE organization_id = :organization_id "
                            "AND document_id = :document_id "
                            "AND id = :chunk_id"
                        ),
                        transaction_id=transaction_id,
                        parameters=[
                            {
                                "name": "embedding",
                                "value": {"stringValue": _serialize_vector(rec.embedding)},
                            },
                            {
                                "name": "organization_id",
                                "value": {"stringValue": str(target_org_id)},
                                "typeHint": "UUID",
                            },
                            {
                                "name": "document_id",
                                "value": {"stringValue": str(rec.document_id)},
                                "typeHint": "UUID",
                            },
                            {
                                "name": "chunk_id",
                                "value": {"stringValue": str(rec.chunk_id)},
                                "typeHint": "UUID",
                            },
                        ],
                    )
                    updated_count += int(response.get("numberOfRecordsUpdated", 0) or 0)
                return updated_count

            count = await self._run_in_tenant_transaction(
                organization_id=current_org_id,
                operation=operation,
            )
            total_updated += int(count)

        return total_updated

    async def search(
        self,
        *,
        embedding: list[float],
        organization_id: UUID,
        confidentiality_levels: tuple[ConfidentialityLevel, ...],
        top_k: int = 10,
        document_ids: list[UUID] | None = None,
    ) -> list[VectorMatch]:
        """Nearest neighbours within the caller's tenant and clearance."""
        validate_embedding(embedding, label="query embedding")
        validate_top_k(top_k)

        if not confidentiality_levels:
            return []

        vector_str = _serialize_vector(embedding)
        has_doc_filter = bool(document_ids)
        doc_id_strings = [str(d) for d in (document_ids or [])]

        async def operation(transaction_id: str) -> list[VectorMatch]:
            parameters: list[dict[str, Any]] = [
                {
                    "name": "query_embedding",
                    "value": {"stringValue": vector_str},
                },
                {
                    "name": "organization_id",
                    "value": {"stringValue": str(organization_id)},
                    "typeHint": "UUID",
                },
                {
                    "name": "confidentiality_levels",
                    "value": {
                        "arrayValue": {
                            "stringValues": [lvl.value for lvl in confidentiality_levels]
                        }
                    },
                },
                {
                    "name": "top_k",
                    "value": {"longValue": top_k},
                },
                {
                    "name": "has_doc_filter",
                    "value": {"booleanValue": has_doc_filter},
                },
                {
                    "name": "document_ids",
                    "value": {
                        "arrayValue": {
                            "stringValues": doc_id_strings
                        }
                    },
                },
            ]

            sql = (
                "SELECT "
                "c.id, "
                "c.document_id, "
                "c.content, "
                "c.metadata, "
                "1 - (c.embedding <=> CAST(:query_embedding AS vector)) AS score "
                "FROM document_chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "AND d.organization_id = c.organization_id "
                "WHERE c.organization_id = :organization_id "
                "AND c.embedding IS NOT NULL "
                "AND CAST(d.confidentiality_level AS text) = ANY(:confidentiality_levels) "
                "AND (:has_doc_filter = false "
                "OR c.document_id = ANY(CAST(:document_ids AS uuid[]))) "
                "ORDER BY c.embedding <=> CAST(:query_embedding AS vector) ASC "
                "LIMIT :top_k"
            )

            response = await self._execute(
                sql=sql,
                transaction_id=transaction_id,
                parameters=parameters,
            )

            matches: list[VectorMatch] = []
            records = response.get("records", [])
            for row in records:
                chunk_id = str(row[0].get("stringValue", ""))
                document_id = str(row[1].get("stringValue", ""))
                content = str(row[2].get("stringValue", ""))

                # metadata column
                meta_field = row[3]
                raw_meta = meta_field.get("stringValue", "{}")
                try:
                    metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
                except Exception:
                    metadata = {}

                # score column
                score_field = row[4]
                if "doubleValue" in score_field:
                    score = float(score_field["doubleValue"])
                elif "stringValue" in score_field:
                    score = float(score_field["stringValue"])
                elif "longValue" in score_field:
                    score = float(score_field["longValue"])
                else:
                    score = 0.0

                matches.append(
                    VectorMatch(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        content=content,
                        score=score,
                        metadata=metadata,
                    )
                )

            return matches

        result: list[VectorMatch] = await self._run_in_tenant_transaction(
            organization_id=organization_id,
            operation=operation,
        )
        return result

    async def delete_by_document(self, *, document_id: UUID, organization_id: UUID) -> int:
        """Remove every vector for a document within the caller's tenant without deleting chunks."""
        async def operation(transaction_id: str) -> int:
            response = await self._execute(
                sql=(
                    "UPDATE document_chunks "
                    "SET embedding = NULL "
                    "WHERE organization_id = :organization_id "
                    "AND document_id = :document_id "
                    "AND embedding IS NOT NULL"
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
            return int(response.get("numberOfRecordsUpdated", 0) or 0)

        result: int = await self._run_in_tenant_transaction(
            organization_id=organization_id,
            operation=operation,
        )
        return result
