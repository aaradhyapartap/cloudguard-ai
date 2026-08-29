"""EventBridge worker for document ingestion."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.container import build_container
from app.core.logging import configure_logging, get_logger
from app.models.tenant import TenantScope
from app.repositories.database import tenant_session
from app.services.document_processing import DocumentProcessingService

logger = get_logger(__name__)

_settings = get_settings()
configure_logging(_settings)
_container = build_container(_settings)


async def _handle(event: dict[str, Any]) -> None:
    detail_type = event.get("detail-type")
    if detail_type != "DocumentUploadCompleted":
        logger.info(
            "document_worker_event_ignored",
            detail_type=detail_type,
        )
        return

    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise ValueError("EventBridge event detail must be an object.")

    organization_id = UUID(str(detail["organization_id"]))
    document_id = UUID(str(detail["document_id"]))

    scope = TenantScope(organization_id=organization_id)

    async with tenant_session(organization_id) as session:
        service = DocumentProcessingService(
            session=session,
            scope=scope,
            document_store=_container.documents,
            event_publisher=_container.events,
        )
        await service.process_document(document_id)

    logger.info(
        "document_processing_completed",
        organization_id=str(organization_id),
        document_id=str(document_id),
    )


def handler(event: dict[str, Any], context: Any) -> None:
    del context
    asyncio.run(_handle(event))
