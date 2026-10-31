"""Scheduled trusted worker for recovering durable Approval callbacks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.aws.approval_callback import (
    StepFunctionsApprovalCallback,
)
from app.adapters.aws.approval_callback_recovery_repository import (
    AuroraDataAPIApprovalCallbackRecoveryRepository,
)
from app.core.config import get_settings
from app.security.worker_errors import sanitize_worker_failures
from app.services.approval_callback import ApprovalCallbackService

LEASE_SECONDS = 300
ORGANIZATION_LIMIT = 500
APPROVAL_BATCH_SIZE = 25


def _build_repository() -> AuroraDataAPIApprovalCallbackRecoveryRepository:
    settings = get_settings()

    resource_arn = settings.aws.aurora_cluster_arn
    secret_arn = settings.aws.aurora_secret_arn

    if resource_arn is None or secret_arn is None:
        raise RuntimeError(
            "Approval callback recovery requires Aurora cluster and secret ARNs."
        )

    return AuroraDataAPIApprovalCallbackRecoveryRepository(
        resource_arn=resource_arn,
        secret_arn=secret_arn,
        database=settings.database.name,
        region=settings.aws.region,
        endpoint_url=settings.aws.endpoint_url,
    )


def _build_callback() -> StepFunctionsApprovalCallback:
    settings = get_settings()

    return StepFunctionsApprovalCallback(
        region=settings.aws.region,
        endpoint_url=settings.aws.endpoint_url,
    )


async def _run() -> dict[str, int]:
    repository = _build_repository()
    callback = _build_callback()

    service = ApprovalCallbackService(
        repository=repository,
        callback=callback,
    )

    stale_before = datetime.now(UTC) - timedelta(
        seconds=LEASE_SECONDS
    )

    organization_count = 0
    candidates = 0
    delivered = 0
    failed = 0
    after_id = None

    while True:
        organizations = await repository.list_organization_ids(
            after_id=after_id,
            limit=ORGANIZATION_LIMIT,
        )

        if not organizations:
            break

        organization_count += len(organizations)

        for organization_id in organizations:
            approval_ids = await repository.list_recoverable_approval_ids(
                organization_id=organization_id,
                stale_before=stale_before,
                limit=APPROVAL_BATCH_SIZE,
            )

            candidates += len(
                approval_ids
            )

            for approval_id in approval_ids:
                try:
                    resumed = await service.resume_decided(
                        organization_id=organization_id,
                        approval_id=approval_id,
                        stale_before=stale_before,
                    )
                except Exception:
                    failed += 1
                    continue

                if resumed:
                    delivered += 1

        if len(organizations) < ORGANIZATION_LIMIT:
            break

        after_id = organizations[-1]

    return {
        "organizations": organization_count,
        "candidates": candidates,
        "delivered": delivered,
        "failed": failed,
    }


@sanitize_worker_failures
def handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, int]:
    """Lambda entrypoint.

    The event payload is deliberately ignored. Recovery state is discovered
    from trusted persistence rather than supplied by the scheduler.
    """

    del event, context

    return asyncio.run(
        _run()
    )
