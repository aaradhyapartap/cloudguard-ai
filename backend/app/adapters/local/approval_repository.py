"""SQLAlchemy persistence for tenant-scoped human approvals."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import (
    ApprovalAction,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
    DecidedApproval,
    PendingApproval,
)
from app.models.enums import ApprovalDecision, ApprovalStatus
from app.ports.approval_repository import (
    ApprovalCallbackContext,
    ApprovalRepository,
)
from app.repositories.tables import Approval


class SQLAlchemyApprovalRepository(ApprovalRepository):
    """PostgreSQL-backed Approval repository.

    Public/domain projections are explicitly constructed so the persistence-only
    task token cannot leak through generic ORM serialization.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        organization_id: UUID,
        workflow_execution_id: str,
        recommendation_id: UUID,
        proposed_action: ApprovalAction,
        evidence: tuple[ApprovalEvidenceReference, ...],
        score_context: ApprovalScoreContext | None,
        agent_trace_ids: tuple[str, ...],
        generator_model_id: str | None,
        reviewer_model_id: str | None,
        task_token: str,
    ) -> PendingApproval:
        row = Approval(
            organization_id=organization_id,
            workflow_execution_id=workflow_execution_id,
            recommendation_id=recommendation_id,
            proposed_action=proposed_action.model_dump(mode="json"),
            evidence=[
                reference.model_dump(mode="json")
                for reference in evidence
            ],
            score_context=(
                score_context.model_dump(mode="json")
                if score_context is not None
                else None
            ),
            agent_trace_ids=list(agent_trace_ids),
            generator_model_id=generator_model_id,
            reviewer_model_id=reviewer_model_id,
            status=ApprovalStatus.PENDING,
            decision=None,
            approver_id=None,
            decided_at=None,
            justification=None,
            comment=None,
            modified_action=None,
            task_token=task_token,
        )

        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)

        return self._to_pending(row)

    async def get_by_id(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
    ) -> PendingApproval | DecidedApproval | None:
        result = await self._session.execute(
            select(Approval).where(
                Approval.organization_id == organization_id,
                Approval.id == approval_id,
            )
        )
        row = result.scalar_one_or_none()

        if row is None:
            return None

        return self._to_public(row)

    async def list_pending(
        self,
        *,
        organization_id: UUID,
        limit: int = 25,
    ) -> list[PendingApproval]:
        if limit < 1 or limit > 100:
            raise ValueError("Approval queue limit must be between 1 and 100.")

        result = await self._session.execute(
            select(Approval)
            .where(
                Approval.organization_id == organization_id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.decision.is_(None),
            )
            .order_by(
                Approval.created_at.asc(),
                Approval.id.asc(),
            )
            .limit(limit)
        )

        return [
            self._to_pending(row)
            for row in result.scalars().all()
        ]

    async def decide_pending(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        decision: ApprovalDecision,
        approver_id: UUID,
        decided_at: datetime,
        justification: str | None,
        comment: str | None,
        modified_action: ApprovalAction | None,
    ) -> DecidedApproval | None:
        statement = (
            update(Approval)
            .where(
                Approval.organization_id == organization_id,
                Approval.id == approval_id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.decision.is_(None),
            )
            .values(
                status=ApprovalStatus.DECIDED,
                decision=decision,
                approver_id=approver_id,
                decided_at=decided_at,
                justification=justification,
                comment=comment,
                modified_action=(
                    modified_action.model_dump(mode="json")
                    if modified_action is not None
                    else None
                ),
            )
            .returning(Approval)
        )

        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()

        if row is None:
            return None

        return self._to_decided(row)

    async def get_decided_callback_context(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
    ) -> ApprovalCallbackContext | None:
        result = await self._session.execute(
            select(Approval).where(
                Approval.organization_id == organization_id,
                Approval.id == approval_id,
                Approval.status == ApprovalStatus.DECIDED,
                Approval.decision.is_not(None),
            )
        )
        row = result.scalar_one_or_none()

        if row is None or row.decision is None:
            return None

        effective_action: ApprovalAction | None

        if row.decision is ApprovalDecision.APPROVED:
            effective_action = ApprovalAction.model_validate(
                row.proposed_action
            )
        elif row.decision is ApprovalDecision.MODIFIED:
            if row.modified_action is None:
                raise ValueError(
                    "Modified Approval is missing its persisted replacement action."
                )
            effective_action = ApprovalAction.model_validate(
                row.modified_action
            )
        else:
            effective_action = None

        return ApprovalCallbackContext(
            approval_id=row.id,
            organization_id=row.organization_id,
            task_token=row.task_token,
            decision=row.decision,
            effective_action=effective_action,
        )

    @staticmethod
    def _common_projection(row: Approval) -> dict[str, object]:
        """Explicitly enumerate safe public fields.

        Deliberately excludes ``task_token``.
        """

        return {
            "id": row.id,
            "organization_id": row.organization_id,
            "workflow_execution_id": row.workflow_execution_id,
            "recommendation_id": row.recommendation_id,
            "proposed_action": row.proposed_action,
            "evidence": row.evidence,
            "score_context": row.score_context,
            "agent_trace_ids": row.agent_trace_ids,
            "generator_model_id": row.generator_model_id,
            "reviewer_model_id": row.reviewer_model_id,
            "status": row.status,
            "created_at": row.created_at,
        }

    @classmethod
    def _to_pending(cls, row: Approval) -> PendingApproval:
        if row.status is not ApprovalStatus.PENDING:
            raise ValueError("Expected a pending Approval persistence row.")

        return PendingApproval.model_validate(
            cls._common_projection(row)
        )

    @classmethod
    def _to_decided(cls, row: Approval) -> DecidedApproval:
        if row.status is ApprovalStatus.PENDING:
            raise ValueError("Expected a decided Approval persistence row.")

        if (
            row.decision is None
            or row.approver_id is None
            or row.decided_at is None
        ):
            raise ValueError(
                "Recorded Approval persistence row is missing decision metadata."
            )

        payload = cls._common_projection(row)
        payload.update(
            {
                "decision": row.decision,
                "approver_id": row.approver_id,
                "decided_at": row.decided_at,
                "justification": row.justification,
                "comment": row.comment,
                "modified_action": row.modified_action,
            }
        )

        return DecidedApproval.model_validate(payload)

    @classmethod
    def _to_public(
        cls,
        row: Approval,
    ) -> PendingApproval | DecidedApproval:
        if row.status is ApprovalStatus.PENDING:
            return cls._to_pending(row)

        return cls._to_decided(row)
