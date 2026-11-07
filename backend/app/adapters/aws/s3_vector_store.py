"""Amazon S3 Vectors adapter for vector store persistence and similarity search.

Implements the VectorStore domain port using Amazon S3 Vectors (boto3 client 's3vectors').
Enforces strict multi-tenant isolation, caller clearance boundaries, and input validation.
"""

from __future__ import annotations

import asyncio
import math
import re
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

# S3 Vectors put_vectors API batch size ceiling
MAX_PUT_VECTORS_BATCH_SIZE = 500
SUPPORTED_DIMENSION = 1024
SUPPORTED_DISTANCE_METRIC = "cosine"

S3_VECTORS_INDEX_ARN_REGEX = re.compile(
    r"^arn:aws:s3vectors:[a-z0-9-]+:[0-9]{12}:bucket/([a-zA-Z0-9.-]+)/index/([a-zA-Z0-9_-]+)$"
)


class S3VectorStore(VectorStore):
    """Vector store persistence backed by Amazon S3 Vectors."""

    def __init__(
        self,
        *,
        index_arn: str | None = None,
        vector_bucket_name: str | None = None,
        bucket_name: str | None = None,
        index_name: str | None = None,
        dimension: int = SUPPORTED_DIMENSION,
        distance_metric: str = SUPPORTED_DISTANCE_METRIC,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if index_arn:
            match = S3_VECTORS_INDEX_ARN_REGEX.match(index_arn.strip())
            if not match:
                raise ValueError(f"Malformed S3 Vectors index ARN: '{index_arn}'.")
            resolved_bucket = match.group(1)
            resolved_index = match.group(2)
        else:
            resolved_bucket = (bucket_name or vector_bucket_name or "").strip()
            resolved_index = (index_name or "").strip()

        if not resolved_bucket:
            raise ValueError("S3 vector bucket name is required.")
        if not resolved_index:
            raise ValueError("S3 vector index name is required.")
        if dimension != SUPPORTED_DIMENSION:
            raise ValueError(
                f"S3 Vectors index requires dimension={SUPPORTED_DIMENSION}, got {dimension}."
            )
        if distance_metric != SUPPORTED_DISTANCE_METRIC:
            raise ValueError(
                f"S3 Vectors index requires distance_metric='{SUPPORTED_DISTANCE_METRIC}', "
                f"got '{distance_metric}'."
            )

        self._vector_bucket_name = resolved_bucket
        self._index_name = resolved_index
        self._dimension = dimension
        self._distance_metric = distance_metric
        self._index_validated = False

        if client is not None:
            self._client = client
            return

        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "s3vectors",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    async def _ensure_index_validated(self) -> None:
        """Ensure the remote index schema has been validated once before data operations."""
        if not self._index_validated:
            await self.validate_index_contract()
            self._index_validated = True

    async def validate_index_contract(self) -> dict[str, Any]:
        """Validate the remote S3 Vectors index matches the required schema contract.

        Verifies that:
        - The index exists and is accessible.
        - Response contains the SDK 'index' object.
        - Dimension matches SUPPORTED_DIMENSION (1024).
        - Distance metric matches SUPPORTED_DISTANCE_METRIC ("cosine").
        - metadataConfiguration does not mark required filter attributes as non-filterable.

        Raises UpstreamError on communication failure and ValueError on contract mismatch.
        """
        response = await self._call(
            "get_index",
            vectorBucketName=self._vector_bucket_name,
            indexName=self._index_name,
        )
        if not isinstance(response, dict):
            raise UpstreamError(
                "Could not retrieve S3 Vectors index description: response is not a dict."
            )

        index_data = response.get("index")
        if not isinstance(index_data, dict):
            raise ValueError(
                "S3 Vectors GetIndex response missing required 'index' object."
            )

        # Check dimension
        raw_dim = index_data.get("dimension")
        if raw_dim is None:
            raise ValueError("S3 Vectors index missing required 'dimension' attribute.")
        try:
            dim = int(raw_dim)
        except (ValueError, TypeError):
            raise ValueError(f"Malformed S3 Vectors index dimension: {raw_dim}.") from None

        if dim != self._dimension:
            raise ValueError(
                f"S3 Vectors index dimension mismatch: expected {self._dimension}, got {dim}."
            )

        # Check distance metric
        raw_metric = index_data.get("distanceMetric")
        if raw_metric is None:
            raise ValueError("S3 Vectors index missing required 'distanceMetric' attribute.")
        if not isinstance(raw_metric, str):
            raise ValueError(f"Malformed S3 Vectors distanceMetric: {raw_metric}.")

        if raw_metric.lower() != self._distance_metric.lower():
            raise ValueError(
                f"S3 Vectors index distance metric mismatch: expected '{self._distance_metric}', "
                f"got '{raw_metric}'."
            )

        # Check metadataConfiguration
        metadata_config = index_data.get("metadataConfiguration")
        if metadata_config is not None:
            if not isinstance(metadata_config, dict):
                raise ValueError("Malformed S3 Vectors metadataConfiguration: expected dict.")

            raw_keys = metadata_config.get("nonFilterableMetadataKeys")
            if raw_keys is not None:
                if not isinstance(raw_keys, list):
                    raise ValueError(
                        "Malformed S3 Vectors metadataConfiguration: "
                        "nonFilterableMetadataKeys must be a list."
                    )
                for k in raw_keys:
                    if not isinstance(k, str):
                        raise ValueError(
                            "Malformed S3 Vectors metadataConfiguration: "
                            f"nonFilterableMetadataKeys item must be str, got {type(k).__name__}."
                        )

                required_filter_attrs = (
                    "organization_id",
                    "confidentiality_level",
                    "document_id",
                )
                conflicts = [attr for attr in required_filter_attrs if attr in raw_keys]
                if conflicts:
                    raise ValueError(
                        f"Incompatible S3 Vectors metadataConfiguration: {conflicts} "
                        "marked non-filterable in nonFilterableMetadataKeys."
                    )

        return response

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(
                partial(getattr(self._client, method), **kwargs)
            )
        except Exception as exc:
            logger.error(
                "s3_vectors_call_failed",
                method=method,
                error_type=type(exc).__name__,
            )
            raise UpstreamError("Could not access vector persistence.") from exc

    @staticmethod
    def _make_vector_key(organization_id: str, document_id: str, chunk_id: str) -> str:
        """Construct a deterministic, tenant-partitioned vector key."""
        return f"{organization_id}#{document_id}#{chunk_id}"

    async def upsert(self, records: list[VectorRecord]) -> int:
        """Insert or replace vector embeddings on document chunks."""
        if not records:
            return 0

        await self._ensure_index_validated()

        # Validate dimensions and values upfront before any API call
        for record in records:
            validate_embedding(
                record.embedding,
                label=f"chunk {record.chunk_id} embedding",
            )
            if not record.organization_id or not record.document_id or not record.chunk_id:
                raise ValueError("record organization_id, document_id, and chunk_id are required.")

            # Validate against contradictory metadata
            meta_org = record.metadata.get("organization_id")
            if meta_org is not None and str(meta_org) != str(record.organization_id):
                raise ValueError("Contradictory organization_id in record metadata.")
            meta_doc = record.metadata.get("document_id")
            if meta_doc is not None and str(meta_doc) != str(record.document_id):
                raise ValueError("Contradictory document_id in record metadata.")
            meta_chunk = record.metadata.get("chunk_id")
            if meta_chunk is not None and str(meta_chunk) != str(record.chunk_id):
                raise ValueError("Contradictory chunk_id in record metadata.")

        vectors_payload: list[dict[str, Any]] = []
        for record in records:
            key = self._make_vector_key(
                str(record.organization_id),
                str(record.document_id),
                str(record.chunk_id),
            )

            confidentiality = record.metadata.get(
                "confidentiality_level", ConfidentialityLevel.INTERNAL.value
            )
            if isinstance(confidentiality, ConfidentialityLevel):
                confidentiality_str = confidentiality.value
            else:
                confidentiality_str = str(confidentiality)

            metadata: dict[str, Any] = {
                "organization_id": str(record.organization_id),
                "document_id": str(record.document_id),
                "chunk_id": str(record.chunk_id),
                "confidentiality_level": confidentiality_str,
                "content": str(record.content),
            }

            # Propagate additional scalar metadata attributes safely within bounds
            for k, v in record.metadata.items():
                if k not in metadata and isinstance(v, (str, int, float, bool)):
                    if len(k) <= 64:
                        metadata[k] = v

            vectors_payload.append(
                {
                    "key": key,
                    "data": {"float32": [float(x) for x in record.embedding]},
                    "metadata": metadata,
                }
            )

        total_written = 0
        for i in range(0, len(vectors_payload), MAX_PUT_VECTORS_BATCH_SIZE):
            batch = vectors_payload[i : i + MAX_PUT_VECTORS_BATCH_SIZE]
            await self._call(
                "put_vectors",
                vectorBucketName=self._vector_bucket_name,
                indexName=self._index_name,
                vectors=batch,
            )
            total_written += len(batch)

        return total_written

    async def search(
        self,
        *,
        embedding: list[float],
        organization_id: UUID,
        confidentiality_levels: tuple[ConfidentialityLevel, ...],
        top_k: int = 10,
        document_ids: list[UUID] | None = None,
    ) -> list[VectorMatch]:
        """Nearest neighbours within the caller's tenant and clearance.

        Enforces tenant isolation and confidentiality clearance in metadata filter.
        Never trusts caller request body for tenant or clearance scope.
        """
        validate_embedding(embedding, label="query embedding")
        validate_top_k(top_k)

        if not confidentiality_levels:
            return []

        await self._ensure_index_validated()

        org_id_str = str(organization_id)
        clearance_values = [lvl.value for lvl in confidentiality_levels]

        # Construct mandatory server-side filter
        filter_conditions: list[dict[str, Any]] = [
            {"organization_id": {"$eq": org_id_str}},
            {"confidentiality_level": {"$in": clearance_values}},
        ]

        doc_id_strings: list[str] | None = None
        if document_ids:
            doc_id_strings = [str(d) for d in document_ids]
            filter_conditions.append({"document_id": {"$in": doc_id_strings}})

        metadata_filter: dict[str, Any]
        if len(filter_conditions) == 1:
            metadata_filter = filter_conditions[0]
        else:
            metadata_filter = {"$and": filter_conditions}

        response = await self._call(
            "query_vectors",
            vectorBucketName=self._vector_bucket_name,
            indexName=self._index_name,
            queryVector={"float32": [float(x) for x in embedding]},
            topK=top_k,
            filter=metadata_filter,
            returnDistance=True,
            returnMetadata=True,
        )

        matches: list[VectorMatch] = []
        raw_vectors = response.get("vectors", []) if isinstance(response, dict) else []
        if not isinstance(raw_vectors, list):
            logger.warning("s3_vectors_malformed_response_vectors_type")
            return []

        for item in raw_vectors:
            if not isinstance(item, dict):
                logger.warning("s3_vectors_malformed_item_skipped")
                continue

            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            item_org_id = metadata.get("organization_id")
            key = str(item.get("key", ""))

            # Key format: {org_id}#{doc_id}#{chunk_id}
            key_org: str | None = None
            key_doc: str | None = None
            key_chunk: str | None = None
            if "#" in key:
                parts = key.split("#")
                if len(parts) == 3:
                    key_org, key_doc, key_chunk = parts[0], parts[1], parts[2]

            # 1. Tenant boundary enforcement: key and metadata must strictly match caller org
            if key_org and key_org != org_id_str:
                logger.warning("s3_vectors_tenant_mismatch_suppressed")
                continue
            if item_org_id and str(item_org_id) != org_id_str:
                logger.warning("s3_vectors_tenant_mismatch_suppressed")
                continue
            if not key_org and not item_org_id:
                logger.warning("s3_vectors_tenant_missing_suppressed")
                continue

            # 2. Contradictory key vs metadata check
            if key_org and item_org_id and key_org != str(item_org_id):
                logger.warning("s3_vectors_contradictory_ids_suppressed")
                continue

            meta_doc_id = metadata.get("document_id")
            if key_doc and meta_doc_id and key_doc != str(meta_doc_id):
                logger.warning("s3_vectors_contradictory_ids_suppressed")
                continue

            meta_chunk_id = metadata.get("chunk_id")
            if key_chunk and meta_chunk_id and key_chunk != str(meta_chunk_id):
                logger.warning("s3_vectors_contradictory_ids_suppressed")
                continue

            final_doc_id = str(meta_doc_id or key_doc or "")
            final_chunk_id = str(meta_chunk_id or key_chunk or "")
            if not final_doc_id or not final_chunk_id:
                logger.warning(
                    "s3_vectors_missing_chunk_or_doc_id",
                    has_key=bool(key),
                )
                continue

            # 3. Document filter violations check (defense in depth)
            if doc_id_strings is not None and final_doc_id not in doc_id_strings:
                logger.warning("s3_vectors_document_filter_violation_suppressed")
                continue

            # 4. Clearance check (defense in depth)
            item_clearance = metadata.get("confidentiality_level")
            if item_clearance not in clearance_values:
                logger.warning("s3_vectors_clearance_mismatch_suppressed")
                continue

            # 5. Distance validation and similarity score conversion
            raw_distance = item.get("distance")
            if raw_distance is None:
                logger.warning("s3_vectors_missing_distance_suppressed")
                continue
            try:
                dist = float(raw_distance)
            except (ValueError, TypeError):
                logger.warning("s3_vectors_malformed_distance_suppressed")
                continue

            if not math.isfinite(dist) or dist < 0.0:
                logger.warning("s3_vectors_nonfinite_distance_suppressed")
                continue

            # In S3 Vectors cosine distance, similarity score = 1.0 - distance
            similarity_score = max(0.0, min(1.0, 1.0 - dist))
            content = str(metadata.get("content", ""))

            matches.append(
                VectorMatch(
                    chunk_id=final_chunk_id,
                    document_id=final_doc_id,
                    content=content,
                    score=similarity_score,
                    metadata=metadata,
                )
            )

        return matches

    async def delete_by_document(self, *, document_id: UUID, organization_id: UUID) -> int:
        """Remove every vector for a document within the caller's tenant."""
        org_id_str = str(organization_id)
        doc_id_str = str(document_id)
        key_prefix = f"{org_id_str}#{doc_id_str}#"

        await self._ensure_index_validated()

        # List vectors in the index to find matching keys for this document and tenant
        keys_to_delete: list[str] = []
        next_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "vectorBucketName": self._vector_bucket_name,
                "indexName": self._index_name,
                "returnMetadata": True,
                "maxResults": 100,
            }
            if next_token:
                kwargs["nextToken"] = next_token

            list_resp = await self._call("list_vectors", **kwargs)
            if not isinstance(list_resp, dict):
                raise UpstreamError("Could not list vectors for deletion: malformed response.")
            vectors = list_resp.get("vectors")
            if not isinstance(vectors, list):
                raise UpstreamError(
                    "Could not list vectors for deletion: vectors payload is not a list."
                )

            for vec in vectors:
                if not isinstance(vec, dict):
                    raise UpstreamError(
                        "Could not list vectors for deletion: vector item is not a dictionary."
                    )
                key = str(vec.get("key", ""))
                raw_meta = vec.get("metadata")
                meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}

                # Never delete a foreign-tenant key based only on matching metadata.
                # The key MUST strictly start with the caller tenant and document prefix.
                if not key.startswith(key_prefix):
                    continue

                # If metadata is present, it must not contradict the key
                meta_org = meta.get("organization_id")
                meta_doc = meta.get("document_id")
                if meta_org is not None and str(meta_org) != org_id_str:
                    logger.warning("s3_vectors_contradictory_delete_suppressed")
                    continue
                if meta_doc is not None and str(meta_doc) != doc_id_str:
                    logger.warning("s3_vectors_contradictory_delete_suppressed")
                    continue

                keys_to_delete.append(key)

            raw_next = list_resp.get("nextToken")
            if raw_next is not None and not isinstance(raw_next, str):
                raise UpstreamError(
                    "Could not list vectors for deletion: nextToken is not a string."
                )
            next_token = raw_next
            if not next_token:
                break

        if not keys_to_delete:
            return 0

        # Delete in batches
        deleted_count = 0
        for i in range(0, len(keys_to_delete), MAX_PUT_VECTORS_BATCH_SIZE):
            batch = keys_to_delete[i : i + MAX_PUT_VECTORS_BATCH_SIZE]
            await self._call(
                "delete_vectors",
                vectorBucketName=self._vector_bucket_name,
                indexName=self._index_name,
                keys=batch,
            )
            deleted_count += len(batch)

        return deleted_count
