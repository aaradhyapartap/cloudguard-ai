"""In-memory blob store backed by a dict."""

from __future__ import annotations

from uuid import UUID

from app.core.errors import NotFoundError
from app.ports.document_store import DocumentStore


class InMemoryDocumentStore(DocumentStore):
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    def build_key(self, *, organization_id: UUID, document_id: UUID, filename: str) -> str:
        # Same layout as the S3 adapter so key-handling bugs surface locally.
        safe = filename.replace("/", "_").replace("\\", "_")
        return f"org/{organization_id}/documents/{document_id}/{safe}"

    async def generate_upload_url(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        filename: str,
        content_type: str,
        expires_in_seconds: int = 900,
    ) -> str:
        key = self.build_key(
            organization_id=organization_id, document_id=document_id, filename=filename
        )
        return f"memory://upload/{key}?expires_in={expires_in_seconds}"

    async def get_object(self, *, key: str) -> bytes:
        if key not in self._objects:
            raise NotFoundError(f"No object at key {key!r}")
        return self._objects[key][0]

    async def put_object(self, *, key: str, body: bytes, content_type: str) -> None:
        self._objects[key] = (body, content_type)
