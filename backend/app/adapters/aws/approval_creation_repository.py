"""Aurora Data API persistence for deployed Approval creation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from functools import partial
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.models.approval import (
    ApprovalAction,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
    PendingApproval,
)
from app.models.enums import ApprovalStatus
from app.ports.approval_creation_repository import ApprovalCreationRepository

logger = get_logger(__name__)


class AuroraDataAPIApprovalCreationRepository(ApprovalCreationRepository):
    """Create pending Approvals through Aurora Data API with tenant RLS."""

    def __init__(
        self,
        *,
        resource_arn: str,
        secret_arn: str,
        database: str,
        region: str,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not resource_arn:
            raise ValueError("Aurora Data API resource ARN is required.")
        if not secret_arn:
            raise ValueError("Aurora Data API secret ARN is required.")
        if not database:
            raise ValueError("Aurora database name is required.")

        self._resource_arn = resource_arn
        self._secret_arn = secret_arn
        self._database = database

        if client is not None:
            self._client = client
            return

        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "rds-data",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(
                partial(getattr(self._client, method), **kwargs)
            )
        except Exception as exc:
            logger.error(
                "aurora_data_api_approval_call_failed",
                method=method,
                error_type=type(exc).__name__,
            )
            raise UpstreamError("Could not access approval persistence.") from exc

    async def _begin_transaction(self) -> str:
        response = await self._call(
            "begin_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
        )
        transaction_id = str(response.get("transactionId", ""))
        if not transaction_id:
            raise UpstreamError("Could not start the approval database transaction.")
        return transaction_id

    async def _commit_transaction(self, transaction_id: str) -> None:
        await self._call(
            "commit_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            transactionId=transaction_id,
        )

    async def _rollback_transaction(self, transaction_id: str) -> None:
        await self._call(
            "rollback_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            transactionId=transaction_id,
        )

    async def _execute(
        self,
        *,
        sql: str,
        transaction_id: str,
        parameters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        response = await self._call(
            "execute_statement",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
            transactionId=transaction_id,
            sql=sql,
            parameters=parameters or [],
        )
        return dict(response)

    async def _set_tenant(
        self,
        *,
        transaction_id: str,
        organization_id: UUID,
    ) -> None:
        await self._execute(
            sql=(
                "SELECT set_config("
                "'app.current_organization_id', "
                ":organization_id, "
                "true"
                ")"
            ),
            transaction_id=transaction_id,
            parameters=[
                {
                    "name": "organization_id",
                    "value": {"stringValue": str(organization_id)},
                }
            ],
        )

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
        approval_id = uuid4()
        transaction_id = await self._begin_transaction()

        try:
            await self._set_tenant(
                transaction_id=transaction_id,
                organization_id=organization_id,
            )

            score_context_parameter: dict[str, Any]
            if score_context is None:
                score_context_parameter = {"isNull": True}
            else:
                score_context_parameter = {
                    "stringValue": json.dumps(
                        score_context.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                }

            generator_parameter: dict[str, Any]
            if generator_model_id is None:
                generator_parameter = {"isNull": True}
            else:
                generator_parameter = {"stringValue": generator_model_id}

            reviewer_parameter: dict[str, Any]
            if reviewer_model_id is None:
                reviewer_parameter = {"isNull": True}
            else:
                reviewer_parameter = {"stringValue": reviewer_model_id}

            response = await self._execute(
                sql=(
                    "INSERT INTO approvals ("
                    "id, organization_id, workflow_execution_id, recommendation_id, "
                    "proposed_action, evidence, score_context, agent_trace_ids, "
                    "generator_model_id, reviewer_model_id, status, decision, "
                    "approver_id, decided_at, justification, comment, modified_action, "
                    "task_token"
                    ") VALUES ("
                    ":id, :organization_id, :workflow_execution_id, :recommendation_id, "
                    "CAST(:proposed_action AS jsonb), CAST(:evidence AS jsonb), "
                    "CAST(:score_context AS jsonb), CAST(:agent_trace_ids AS jsonb), "
                    ":generator_model_id, :reviewer_model_id, "
                    "CAST(:status AS approval_status), NULL, NULL, NULL, NULL, NULL, "
                    "NULL, :task_token"
                    ") "
                    "ON CONFLICT (organization_id, workflow_execution_id) "
                    "DO UPDATE SET id = approvals.id "
                    "WHERE approvals.status = CAST('pending' AS approval_status) "
                    "AND approvals.decision IS NULL "
                    "AND approvals.recommendation_id = EXCLUDED.recommendation_id "
                    "AND approvals.proposed_action = EXCLUDED.proposed_action "
                    "AND approvals.evidence = EXCLUDED.evidence "
                    "AND approvals.score_context "
                    "IS NOT DISTINCT FROM EXCLUDED.score_context "
                    "AND approvals.agent_trace_ids = EXCLUDED.agent_trace_ids "
                    "AND approvals.generator_model_id "
                    "IS NOT DISTINCT FROM EXCLUDED.generator_model_id "
                    "AND approvals.reviewer_model_id "
                    "IS NOT DISTINCT FROM EXCLUDED.reviewer_model_id "
                    "AND approvals.task_token = EXCLUDED.task_token "
                    "RETURNING approvals.id::text, approvals.created_at::text"
                ),
                transaction_id=transaction_id,
                parameters=[
                    {
                        "name": "id",
                        "value": {"stringValue": str(approval_id)},
                        "typeHint": "UUID",
                    },
                    {
                        "name": "organization_id",
                        "value": {"stringValue": str(organization_id)},
                        "typeHint": "UUID",
                    },
                    {
                        "name": "workflow_execution_id",
                        "value": {"stringValue": workflow_execution_id},
                    },
                    {
                        "name": "recommendation_id",
                        "value": {"stringValue": str(recommendation_id)},
                        "typeHint": "UUID",
                    },
                    {
                        "name": "proposed_action",
                        "value": {
                            "stringValue": json.dumps(
                                proposed_action.model_dump(mode="json"),
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        },
                        "typeHint": "JSON",
                    },
                    {
                        "name": "evidence",
                        "value": {
                            "stringValue": json.dumps(
                                [
                                    reference.model_dump(mode="json")
                                    for reference in evidence
                                ],
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        },
                        "typeHint": "JSON",
                    },
                    {
                        "name": "score_context",
                        "value": score_context_parameter,
                    },
                    {
                        "name": "agent_trace_ids",
                        "value": {
                            "stringValue": json.dumps(
                                list(agent_trace_ids),
                                separators=(",", ":"),
                            )
                        },
                        "typeHint": "JSON",
                    },
                    {
                        "name": "generator_model_id",
                        "value": generator_parameter,
                    },
                    {
                        "name": "reviewer_model_id",
                        "value": reviewer_parameter,
                    },
                    {
                        "name": "status",
                        "value": {"stringValue": ApprovalStatus.PENDING.value},
                    },
                    {
                        "name": "task_token",
                        "value": {"stringValue": task_token},
                    },
                ],
            )

            records = response.get("records", [])
            if (
                not records
                or len(records[0]) < 2
                or "stringValue" not in records[0][0]
                or "stringValue" not in records[0][1]
            ):
                raise UpstreamError(
                    "Approval persistence could not create or recover "
                    "the pending Approval."
                )

            returned_approval_id = UUID(
                str(records[0][0]["stringValue"])
            )
            created_at = datetime.fromisoformat(
                str(records[0][1]["stringValue"])
            )

            await self._commit_transaction(transaction_id)

        except Exception:
            try:
                await self._rollback_transaction(transaction_id)
            except UpstreamError:
                logger.error(
                    "aurora_data_api_approval_rollback_failed",
                    error_type="UpstreamError",
                )
            raise

        return PendingApproval(
            id=returned_approval_id,
            organization_id=organization_id,
            workflow_execution_id=workflow_execution_id,
            recommendation_id=recommendation_id,
            proposed_action=proposed_action,
            evidence=evidence,
            score_context=score_context,
            agent_trace_ids=agent_trace_ids,
            generator_model_id=generator_model_id,
            reviewer_model_id=reviewer_model_id,
            status=ApprovalStatus.PENDING,
            created_at=created_at,
        )
