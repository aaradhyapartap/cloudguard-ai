"""Port: tenant-scoped persistence operations for AI evaluation runs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.models.evaluation import (
    EvaluationRunCreate,
    EvaluationRunRead,
    EvaluationRunSummaryRead,
)


class EvaluationRepository(ABC):
    """Persistence boundary for tenant-scoped AI evaluation runs."""

    @abstractmethod
    async def create_run(self, payload: EvaluationRunCreate) -> EvaluationRunRead:
        """Persist an immutable evaluation run record for one tenant."""

    @abstractmethod
    async def get_run(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        triggered_by_filter: UUID | None = None,
    ) -> EvaluationRunRead | None:
        """Fetch one tenant-scoped evaluation run, optionally restricted to a user."""

    @abstractmethod
    async def list_runs(
        self,
        *,
        organization_id: UUID,
        triggered_by_filter: UUID | None = None,
        limit: int = 25,
    ) -> list[EvaluationRunSummaryRead]:
        """List recent tenant-scoped evaluation runs, newest first."""

