"""Deployed Lambda task for document ingestion.

This module is invoked as a Step Functions task, not directly by EventBridge.
The production trigger path is:

    S3 ObjectCreated -> EventBridge -> Step Functions -> this Lambda task

Step Functions is responsible for extracting the tenant and document identifiers
from the upstream event and supplying them as a normalized task input::

    {
        "organization_id": "<uuid>",
        "document_id": "<uuid>"
    }

This module constructs ``AuroraDataAPIDocumentProcessingRepository`` from
environment variables so the Lambda remains outside the database VPC and never
imports ``tenant_session()`` or asyncpg.

Local development and CI use ``app.document_worker`` instead, which connects
through SQLAlchemy and asyncpg for fast, realistic RLS integration tests.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.core.config import get_worker_settings
from app.core.container import build_worker_container
from app.core.logging import configure_logging, get_logger
from app.models.tenant import TenantScope
from app.services.document_processing import DocumentProcessingService

logger = get_logger(__name__)

_settings = get_worker_settings()
configure_logging(_settings)
_container = build_worker_container(_settings)


def _build_repository() -> Any:
    """Construct the Data API repository from environment configuration.

    Imported lazily so boto3 stays inside ``app/adapters/``.
    """
    from app.adapters.aws.document_processing_repository import (
        AuroraDataAPIDocumentProcessingRepository,
    )

    resource_arn = _settings.aws.aurora_cluster_arn
    secret_arn = _settings.aws.aurora_secret_arn

    if not resource_arn:
        raise RuntimeError(
            "AWS_AURORA_CLUSTER_ARN is required for deployed document processing."
        )
    if not secret_arn:
        raise RuntimeError(
            "AWS_AURORA_SECRET_ARN is required for deployed document processing."
        )

    return AuroraDataAPIDocumentProcessingRepository(
        resource_arn=resource_arn,
        secret_arn=secret_arn,
        database=_settings.database.name,
        region=_settings.aws.region,
        endpoint_url=_settings.aws.endpoint_url,
    )


def _parse_task_input(event: dict[str, Any]) -> tuple[UUID, UUID]:
    """Extract and validate organization_id and document_id from task input."""
    raw_org = event.get("organization_id")
    if raw_org is None:
        raise ValueError("Task input is missing 'organization_id'.")

    raw_doc = event.get("document_id")
    if raw_doc is None:
        raise ValueError("Task input is missing 'document_id'.")

    try:
        organization_id = UUID(str(raw_org))
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"organization_id is not a valid UUID: {raw_org!r}"
        ) from exc

    try:
        document_id = UUID(str(raw_doc))
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"document_id is not a valid UUID: {raw_doc!r}"
        ) from exc

    return organization_id, document_id


async def _handle(event: dict[str, Any]) -> None:
    organization_id, document_id = _parse_task_input(event)

    scope = TenantScope(organization_id=organization_id)
    repository = _build_repository()

    service = DocumentProcessingService(
        scope=scope,
        repository=repository,
        document_store=_container.documents,
        event_publisher=_container.events,
    )
    await service.process_document(document_id)

    logger.info(
        "deployed_document_processing_completed",
        organization_id=str(organization_id),
        document_id=str(document_id),
    )


def handler(event: dict[str, Any], context: Any) -> None:
    del context
    asyncio.run(_handle(event))
