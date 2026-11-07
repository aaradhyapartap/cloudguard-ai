"""Application service for tenant-scoped AI evaluation runs and analytics."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from app.core.errors import AuthorizationError, NotFoundError
from app.evaluations.dataset import (
    DATASET_VERSION,
    load_adversarial_corpus,
    load_golden_dataset,
)
from app.evaluations.harness import EvaluationHarness
from app.evaluations.metrics import KNOWN_LIMITATIONS, METRIC_DEFINITIONS
from app.models.evaluation import (
    EvaluationDatasetMetadataResponse,
    EvaluationExecutionMode,
    EvaluationRunListResponse,
    EvaluationRunRead,
    TriggerEvaluationRequest,
)
from app.models.principal import Principal
from app.ports.evaluation_repository import EvaluationRepository
from app.security.authz import Permission, has_permission, require_permission


def _resolve_analytics_visibility(
    principal: Principal,
) -> tuple[Literal["own", "team"], UUID | None]:
    """Determine whether the caller sees all tenant evaluation runs or only their own."""
    if has_permission(principal, Permission.ANALYTICS_READ_TEAM):
        return ("team", None)
    if has_permission(principal, Permission.ANALYTICS_READ_OWN):
        return ("own", principal.user_id)
    raise AuthorizationError()


class EvaluationService:
    """Orchestrates evaluation dataset inspection, run listing, and suite execution."""

    def __init__(self, *, repository: EvaluationRepository) -> None:
        self._repository = repository

    def get_dataset_metadata(
        self,
        *,
        principal: Principal,
    ) -> EvaluationDatasetMetadataResponse:
        """Return golden dataset and adversarial corpus metadata for authorized viewers."""
        _resolve_analytics_visibility(principal)
        golden_cases = load_golden_dataset()
        dataset_version = (
            golden_cases[0].dataset_version if golden_cases else DATASET_VERSION
        )
        adversarial_cases = load_adversarial_corpus()

        counts: dict[str, int] = {}
        for case in golden_cases:
            key = case.category.value
            counts[key] = counts.get(key, 0) + 1

        return EvaluationDatasetMetadataResponse(
            dataset_version=dataset_version,
            golden_case_count=len(golden_cases),
            adversarial_case_count=len(adversarial_cases),
            category_counts=counts,
            metric_definitions=dict(METRIC_DEFINITIONS),
            known_limitations=KNOWN_LIMITATIONS,
        )

    async def list_runs(
        self,
        *,
        principal: Principal,
        limit: int = 25,
    ) -> EvaluationRunListResponse:
        """List evaluation runs visible to the caller within their tenant."""
        visibility_scope, triggered_by_filter = _resolve_analytics_visibility(principal)
        runs = await self._repository.list_runs(
            organization_id=principal.organization_id,
            triggered_by_filter=triggered_by_filter,
            limit=limit,
        )
        return EvaluationRunListResponse(
            items=tuple(runs),
            total=len(runs),
            visibility_scope=visibility_scope,
        )

    async def get_run(
        self,
        *,
        principal: Principal,
        run_id: UUID,
    ) -> EvaluationRunRead:
        """Fetch one tenant-scoped evaluation run if visible to the caller."""
        _visibility_scope, triggered_by_filter = _resolve_analytics_visibility(principal)
        run = await self._repository.get_run(
            organization_id=principal.organization_id,
            run_id=run_id,
            triggered_by_filter=triggered_by_filter,
        )
        if run is None:
            raise NotFoundError("The requested evaluation run does not exist.")
        return run

    async def trigger_run(
        self,
        *,
        principal: Principal,
        request: TriggerEvaluationRequest,
    ) -> EvaluationRunRead:
        """Execute an evaluation run and persist its results in the caller's tenant.

        Requires `DOCUMENT_READ`, `AI_QUERY`, and analytics read access.
        Consequently, `Analyst` and `Manager` may trigger runs, while `Admin`
        (who lacks `AI_QUERY`) is rejected with `403 Forbidden`.
        """
        _resolve_analytics_visibility(principal)
        require_permission(principal, Permission.DOCUMENT_READ)
        require_permission(principal, Permission.AI_QUERY)
        # Defense in depth: request-triggered runs must never seed the fixed
        # synthetic tenants in PostgreSQL or call live providers.
        if request.execution_mode != EvaluationExecutionMode.DETERMINISTIC_FIXTURE:
            raise AuthorizationError()

        harness = EvaluationHarness(execution_mode=request.execution_mode)
        run_create = await harness.run_suite(
            organization_id=principal.organization_id,
            triggered_by=principal.user_id,
            categories=request.categories,
            max_cases=request.max_cases,
        )
        return await self._repository.create_run(run_create)
