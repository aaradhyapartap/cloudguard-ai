"""Port: durable blob storage for uploaded documents.

Presigned uploads are the reason ``generate_upload_url`` exists: the browser
PUTs directly to S3 and file bytes never pass through Lambda. That keeps request
payloads small, avoids the API Gateway body size limit, and removes an entire
class of memory-exhaustion failure from the API tier.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID


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

    @abstractmethod
    async def put_object(self, *, key: str, body: bytes, content_type: str) -> None:
        """Write an object."""

    @abstractmethod
    def build_key(self, *, organization_id: UUID, document_id: UUID, filename: str) -> str:
        """Object key layout.

        Tenant-prefixed (``org/{id}/...``) so an IAM policy can scope access by
        key prefix. Storage layout is a security control, not a filing habit.
        """
