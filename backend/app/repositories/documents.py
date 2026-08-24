"""Document repository.

Phase 1 provides only what the foundation needs to prove the wiring works.
Phase 3 adds status transitions, presigned-upload registration and filtering.
"""

from __future__ import annotations

from app.models.enums import ProcessingStatus
from app.repositories.base import TenantRepository
from app.repositories.tables import Document


class DocumentRepository(TenantRepository[Document]):
    model = Document

    async def list_by_status(
        self, status: ProcessingStatus, *, limit: int = 25
    ) -> list[Document]:
        result = await self._session.execute(
            self._scoped()
            .where(Document.processing_status == status)
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
