"""Port: durable blob storage for uploaded documents.

Presigned uploads are the reason ``generate_upload_url`` exists: the browser
PUTs directly to S3 and file bytes never pass through Lambda. That keeps request
payloads small, avoids the API Gateway body size limit, and removes an entire
class of memory-exhaustion failure from the API tier.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.security.ingestion_limits import ObjectSizeLimitError


class DocumentStore(ABC):
    @abstractmethod
    async def generate_upload_url(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        filename: str,
        content_type: str,
        expires_in_seconds: int = 900,
    ) -> str:
        """A short-lived, single-object presigned PUT URL."""

    @abstractmethod
    async def get_object(self, *, key: str) -> bytes:
        """Read an object. Raises NotFoundError if absent."""

    async def get_object_bounded(self, *, key: str, max_bytes: int) -> bytes:
        """Compatibility fallback; streaming adapters must override for bounded reads."""
        if max_bytes < 0:
            raise ValueError("Object read limit must be non-negative.")
        body = await self.get_object(key=key)
        if len(body) > max_bytes:
            raise ObjectSizeLimitError("The document exceeds the upload size policy.")
        return body

    @abstractmethod
    async def put_object(self, *, key: str, body: bytes, content_type: str) -> None:
        """Write an object."""

    @abstractmethod
    async def head_object(self, *, key: str) -> dict[str, object] | None:
        """Return object metadata without downloading the object."""

    @abstractmethod
    async def delete_object(self, *, key: str) -> None:
        """Delete an object if it exists."""

    @abstractmethod
    def build_key(self, *, organization_id: UUID, document_id: UUID, filename: str) -> str:
        """Object key layout.

        Tenant-prefixed (``org/{id}/...``) so an IAM policy can scope access by
        key prefix. Storage layout is a security control, not a filing habit.
        """
