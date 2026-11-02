"""Aurora Data API implementation of the Phase 7.5 execution boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import boto3
from botocore.config import Config

from app.core.errors import UpstreamError
from app.models.approval import DecidedApproval
from app.models.approved_action_execution import (
    ActionExecutionDisposition,
    ActionExecutionResult,
)
from app.models.enums import ApprovalDecision, ApprovalStatus
from app.ports.approval_execution_repository import ApprovalExecutionRepository
from app.services.approved_action_policy import (
    BoundedActionValidationError,
    validate_phase7_demo_action,
)
from app.services.approved_action_projection import ApprovedActionProjector

_DEFAULT_READINESS_ATTEMPTS = 4
_DEFAULT_READINESS_BACKOFF_SECONDS = 0.05


class CallbackDeliveryNotCommittedError(ValueError):
    """Raised when Step Functions resumes before callback_delivered_at is committed."""


def _required_text(
    record: list[dict[str, Any]],
    index: int,
) -> str:
    field = record[index]
    value = field.get("stringValue")
    if value is None:
        raise UpstreamError(
            "Approval execution persistence returned invalid data."
        )
    return str(value)


def _optional_text(
    record: list[dict[str, Any]],
    index: int,
) -> str | None:
    field = record[index]
    if field.get("isNull") is True:
        return None

    value = field.get("stringValue")
    if value is None:
        return None

    return str(value)


def _decoded_json(
    record: list[dict[str, Any]],
    index: int,
) -> Any:
    return json.loads(
        _required_text(record, index)
    )


def _approval_from_record(
    record: list[dict[str, Any]],
) -> DecidedApproval:
    score_text = _optional_text(record, 6)
    modified_text = _optional_text(record, 17)

    decision_text = _required_text(record, 12)
    approver_text = _required_text(record, 13)
    decided_text = _required_text(record, 14)

    return DecidedApproval(
        id=UUID(_required_text(record, 0)),
        organization_id=UUID(
            _required_text(record, 1)
        ),
        workflow_execution_id=_required_text(
            record,
            2,
        ),
        recommendation_id=UUID(
            _required_text(record, 3)
        ),
        proposed_action=_decoded_json(
            record,
            4,
        ),
        evidence=tuple(
            _decoded_json(
                record,
                5,
            )
        ),
        score_context=(
            None
            if score_text is None
            else json.loads(score_text)
        ),
        agent_trace_ids=tuple(
            _decoded_json(
                record,
                7,
            )
        ),
        generator_model_id=_optional_text(
            record,
            8,
        ),
        reviewer_model_id=_optional_text(
            record,
            9,
        ),
        status=ApprovalStatus(
            _required_text(
                record,
                10,
            )
        ),
        created_at=datetime.fromisoformat(
            _required_text(
                record,
                11,
            )
        ),
        decision=ApprovalDecision(
            decision_text
        ),
        approver_id=UUID(
            approver_text
        ),
        decided_at=datetime.fromisoformat(
            decided_text
        ),
        justification=_optional_text(
            record,
            15,
        ),
        comment=_optional_text(
            record,
            16,
        ),
        modified_action=(
            None
            if modified_text is None
            else json.loads(modified_text)
        ),
    )


class AuroraDataAPIApprovalExecutionRepository(
    ApprovalExecutionRepository
):
    """Execute the bounded security-review action atomically in Aurora."""

    def __init__(
        self,
        *,
        resource_arn: str,
        secret_arn: str,
        database: str,
        region: str,
        endpoint_url: str | None = None,
        client: Any | None = None,
        max_readiness_attempts: int = _DEFAULT_READINESS_ATTEMPTS,
        readiness_backoff_seconds: float = _DEFAULT_READINESS_BACKOFF_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not resource_arn or not secret_arn or not database:
            raise ValueError(
                "Approval execution requires Aurora connection configuration."
            )
        if max_readiness_attempts < 1:
            raise ValueError(
                "max_readiness_attempts must be at least 1."
            )
        if readiness_backoff_seconds < 0:
            raise ValueError(
                "readiness_backoff_seconds must be non-negative."
            )

        self._resource_arn = resource_arn
        self._secret_arn = secret_arn
        self._database = database
        self._max_readiness_attempts = max_readiness_attempts
        self._readiness_backoff_seconds = readiness_backoff_seconds
        self._sleep = sleep

        self._client = client or boto3.client(
            "rds-data",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(
                retries={
                    "max_attempts": 3,
                    "mode": "standard",
                }
            ),
        )

    async def _call(
        self,
        method: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            operation = getattr(
                self._client,
                method,
            )
            result = await asyncio.to_thread(
                operation,
                **kwargs,
            )
        except Exception as exc:
            raise UpstreamError(
                "Approval execution persistence failed."
            ) from exc

        if not isinstance(result, dict):
            raise UpstreamError(
                "Approval execution persistence returned invalid data."
            )

        return result

    async def _begin(self) -> str:
        response = await self._call(
            "begin_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
        )

        transaction_id = response.get(
            "transactionId"
        )
        if not isinstance(
            transaction_id,
            str,
        ):
            raise UpstreamError(
                "Approval execution transaction could not begin."
            )

        return transaction_id

    async def _execute(
        self,
        *,
        transaction_id: str,
        sql: str,
        parameters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._call(
            "execute_statement",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
            transactionId=transaction_id,
            sql=sql,
            parameters=parameters,
        )

    async def _commit(
        self,
        transaction_id: str,
    ) -> None:
        await self._call(
            "commit_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            transactionId=transaction_id,
        )

    async def _rollback(
        self,
        transaction_id: str,
    ) -> None:
        await self._call(
            "rollback_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            transactionId=transaction_id,
        )

    @staticmethod
    def _uuid_parameter(
        name: str,
        value: UUID,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "value": {
                "stringValue": str(value),
            },
            "typeHint": "UUID",
        }

    @staticmethod
    def _string_parameter(
        name: str,
        value: str,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "value": {
                "stringValue": value,
            },
        }

    async def _set_tenant(
        self,
        *,
        transaction_id: str,
        organization_id: UUID,
    ) -> None:
        await self._execute(
            transaction_id=transaction_id,
            sql=(
                "SELECT set_config("
                "'app.current_organization_id', "
                ":organization_id, true)"
            ),
            parameters=[
                self._string_parameter(
                    "organization_id",
                    str(organization_id),
                ),
            ],
        )

    async def _load_locked(
        self,
        *,
        transaction_id: str,
        organization_id: UUID,
        workflow_execution_id: UUID,
    ) -> tuple[
        DecidedApproval,
        str,
        str | None,
        str | None,
        str | None,
    ] | None:
        response = await self._execute(
            transaction_id=transaction_id,
            sql=(
                "SELECT "
                "id::text, "
                "organization_id::text, "
                "workflow_execution_id, "
                "recommendation_id::text, "
                "proposed_action::text, "
                "evidence::text, "
                "score_context::text, "
                "agent_trace_ids::text, "
                "generator_model_id, "
                "reviewer_model_id, "
                "status::text, "
                "created_at::text, "
                "decision::text, "
                "approver_id::text, "
                "decided_at::text, "
                "justification, "
                "comment, "
                "modified_action::text, "
                "callback_delivered_at::text, "
                "execution_idempotency_key, "
                "execution_completed_at::text, "
                "execution_reference, "
                "execution_error_code "
                "FROM approvals "
                "WHERE organization_id = "
                "CAST(:organization_id AS uuid) "
                "AND workflow_execution_id = "
                ":workflow_execution_id "
                "AND status <> CAST('pending' AS approval_status) "
                "FOR UPDATE"
            ),
            parameters=[
                self._uuid_parameter(
                    "organization_id",
                    organization_id,
                ),
                self._string_parameter(
                    "workflow_execution_id",
                    str(workflow_execution_id),
                ),
            ],
        )

        records = response.get(
            "records",
            [],
        )
        if not records:
            return None

        record = records[0]
        if len(record) < 23:
            raise UpstreamError(
                "Approval execution persistence returned invalid data."
            )

        approval = _approval_from_record(
            record
        )

        callback_delivered_at = _optional_text(
            record,
            18,
        )
        if callback_delivered_at is None:
            raise CallbackDeliveryNotCommittedError(
                "Approval execution requires a delivered workflow callback."
            )

        return (
            approval,
            callback_delivered_at,
            _optional_text(record, 19),
            _optional_text(record, 21),
            _optional_text(record, 22),
        )

    async def _acquire_ready_approval(
        self,
        *,
        organization_id: UUID,
        workflow_execution_id: UUID,
    ) -> tuple[
        str,
        tuple[
            DecidedApproval,
            str,
            str | None,
            str | None,
            str | None,
        ],
    ]:
        """Acquire a locked decided approval once callback delivery has committed.

        ``SendTaskSuccess`` can resume Step Functions before the callback worker's
        ``mark_callback_delivered`` transaction finishes committing. Rolling back
        the read transaction immediately releases the ``FOR UPDATE`` row lock so
        the callback delivery transaction can commit during bounded readiness
        polls, without ever marking delivery complete here or retrying any
        approved side effect.
        """
        for attempt in range(1, self._max_readiness_attempts + 1):
            transaction_id = await self._begin()
            try:
                await self._set_tenant(
                    transaction_id=transaction_id,
                    organization_id=organization_id,
                )

                loaded = await self._load_locked(
                    transaction_id=transaction_id,
                    organization_id=organization_id,
                    workflow_execution_id=workflow_execution_id,
                )
                if loaded is None:
                    raise ValueError(
                        "No decided Approval exists for this workflow execution."
                    )

                return transaction_id, loaded
            except CallbackDeliveryNotCommittedError:
                await self._rollback(transaction_id)
                if attempt >= self._max_readiness_attempts:
                    raise
                if self._readiness_backoff_seconds > 0:
                    await self._sleep(self._readiness_backoff_seconds)
            except Exception:
                try:
                    await self._rollback(transaction_id)
                except UpstreamError:
                    pass
                raise

        raise AssertionError("Unreachable readiness loop termination.")

    async def _mark_failed(
        self,
        *,
        transaction_id: str,
        organization_id: UUID,
        approval_id: UUID,
        idempotency_key: str,
        error_code: str,
    ) -> None:
        response = await self._execute(
            transaction_id=transaction_id,
            sql=(
                "UPDATE approvals "
                "SET "
                "status = CAST("
                "'execution_failed' AS approval_status"
                "), "
                "execution_idempotency_key = "
                ":idempotency_key, "
                "execution_completed_at = now(), "
                "execution_reference = NULL, "
                "execution_error_code = :error_code "
                "WHERE organization_id = "
                "CAST(:organization_id AS uuid) "
                "AND id = CAST(:approval_id AS uuid) "
                "AND status = CAST('decided' AS approval_status) "
                "AND callback_delivered_at IS NOT NULL "
                "RETURNING id::text"
            ),
            parameters=[
                self._uuid_parameter(
                    "organization_id",
                    organization_id,
                ),
                self._uuid_parameter(
                    "approval_id",
                    approval_id,
                ),
                self._string_parameter(
                    "idempotency_key",
                    idempotency_key,
                ),
                self._string_parameter(
                    "error_code",
                    error_code,
                ),
            ],
        )

        if not response.get("records"):
            raise UpstreamError(
                "Approval execution failure could not be persisted."
            )

    async def execute_for_workflow(
        self,
        *,
        organization_id: UUID,
        workflow_execution_id: UUID,
    ) -> ActionExecutionResult:
        transaction_id, loaded = await self._acquire_ready_approval(
            organization_id=organization_id,
            workflow_execution_id=workflow_execution_id,
        )
        committed = False

        try:
            (
                approval,
                _callback_delivered_at,
                stored_idempotency_key,
                stored_reference,
                stored_error_code,
            ) = loaded

            if approval.status is ApprovalStatus.EXECUTION_SUCCEEDED:
                if stored_idempotency_key is None:
                    raise UpstreamError(
                        "Terminal Approval is missing execution metadata."
                    )

                effective_action = (
                    approval.proposed_action
                    if approval.decision is ApprovalDecision.APPROVED
                    else approval.modified_action
                )

                await self._commit(
                    transaction_id
                )
                committed = True

                return ActionExecutionResult(
                    approval_id=approval.id,
                    idempotency_key=stored_idempotency_key,
                    disposition=ActionExecutionDisposition.REPLAYED,
                    action_type=(
                        None
                        if effective_action is None
                        else effective_action.action_type
                    ),
                    resource_id=(
                        None
                        if effective_action is None
                        else effective_action.resource_id
                    ),
                    execution_reference=stored_reference,
                )

            if approval.status is ApprovalStatus.EXECUTION_FAILED:
                if (
                    stored_idempotency_key is None
                    or stored_error_code is None
                ):
                    raise UpstreamError(
                        "Terminal Approval is missing failure metadata."
                    )

                await self._commit(
                    transaction_id
                )
                committed = True

                return ActionExecutionResult(
                    approval_id=approval.id,
                    idempotency_key=stored_idempotency_key,
                    disposition=ActionExecutionDisposition.FAILED,
                    error_code=stored_error_code,
                )

            projection = ApprovedActionProjector.project(
                approval
            )

            if projection.action is None:
                await self._commit(
                    transaction_id
                )
                committed = True

                return ActionExecutionResult(
                    approval_id=projection.approval_id,
                    idempotency_key=projection.idempotency_key,
                    disposition=ActionExecutionDisposition.SKIPPED,
                )

            try:
                action = validate_phase7_demo_action(
                    projection
                )
            except BoundedActionValidationError as exc:
                await self._mark_failed(
                    transaction_id=transaction_id,
                    organization_id=organization_id,
                    approval_id=approval.id,
                    idempotency_key=projection.idempotency_key,
                    error_code=exc.error_code,
                )

                await self._commit(
                    transaction_id
                )
                committed = True

                return ActionExecutionResult(
                    approval_id=approval.id,
                    idempotency_key=projection.idempotency_key,
                    disposition=ActionExecutionDisposition.FAILED,
                    error_code=exc.error_code,
                )

            review_id = uuid5(
                NAMESPACE_URL,
                (
                    "cloudguard-security-review:"
                    f"{projection.idempotency_key}"
                ),
            )

            insert_response = await self._execute(
                transaction_id=transaction_id,
                sql=(
                    "INSERT INTO security_reviews ("
                    "id, organization_id, approval_id, "
                    "workflow_execution_id, action_type, "
                    "summary, idempotency_key"
                    ") VALUES ("
                    "CAST(:review_id AS uuid), "
                    "CAST(:organization_id AS uuid), "
                    "CAST(:approval_id AS uuid), "
                    "CAST(:workflow_execution_id AS uuid), "
                    ":action_type, :summary, :idempotency_key"
                    ") "
                    "ON CONFLICT (approval_id) DO NOTHING "
                    "RETURNING id::text"
                ),
                parameters=[
                    self._uuid_parameter(
                        "review_id",
                        review_id,
                    ),
                    self._uuid_parameter(
                        "organization_id",
                        organization_id,
                    ),
                    self._uuid_parameter(
                        "approval_id",
                        approval.id,
                    ),
                    self._uuid_parameter(
                        "workflow_execution_id",
                        action.resource_id,
                    ),
                    self._string_parameter(
                        "action_type",
                        action.action_type,
                    ),
                    self._string_parameter(
                        "summary",
                        action.summary,
                    ),
                    self._string_parameter(
                        "idempotency_key",
                        projection.idempotency_key,
                    ),
                ],
            )

            if not insert_response.get("records"):
                existing_response = await self._execute(
                    transaction_id=transaction_id,
                    sql=(
                        "SELECT "
                        "id::text, "
                        "organization_id::text, "
                        "workflow_execution_id::text, "
                        "action_type, "
                        "summary, "
                        "idempotency_key "
                        "FROM security_reviews "
                        "WHERE organization_id = "
                        "CAST(:organization_id AS uuid) "
                        "AND approval_id = "
                        "CAST(:approval_id AS uuid)"
                    ),
                    parameters=[
                        self._uuid_parameter(
                            "organization_id",
                            organization_id,
                        ),
                        self._uuid_parameter(
                            "approval_id",
                            approval.id,
                        ),
                    ],
                )

                existing_records = existing_response.get(
                    "records",
                    [],
                )
                if not existing_records:
                    raise UpstreamError(
                        "Security-review execution conflict "
                        "could not be reconciled."
                    )

                existing = existing_records[0]

                exact_match = (
                    _required_text(existing, 0) == str(review_id)
                    and _required_text(existing, 1)
                    == str(organization_id)
                    and _required_text(existing, 2)
                    == str(action.resource_id)
                    and _required_text(existing, 3)
                    == action.action_type
                    and _required_text(existing, 4)
                    == action.summary
                    and _required_text(existing, 5)
                    == projection.idempotency_key
                )

                if not exact_match:
                    raise UpstreamError(
                        "Security-review execution conflict "
                        "did not match the approved action."
                    )

            execution_reference = (
                f"security-review:{review_id}"
            )

            update_response = await self._execute(
                transaction_id=transaction_id,
                sql=(
                    "UPDATE approvals "
                    "SET "
                    "status = CAST("
                    "'execution_succeeded' AS approval_status"
                    "), "
                    "execution_idempotency_key = "
                    ":idempotency_key, "
                    "execution_completed_at = now(), "
                    "execution_reference = "
                    ":execution_reference, "
                    "execution_error_code = NULL "
                    "WHERE organization_id = "
                    "CAST(:organization_id AS uuid) "
                    "AND id = CAST(:approval_id AS uuid) "
                    "AND status = CAST('decided' AS approval_status) "
                    "AND callback_delivered_at IS NOT NULL "
                    "RETURNING id::text"
                ),
                parameters=[
                    self._uuid_parameter(
                        "organization_id",
                        organization_id,
                    ),
                    self._uuid_parameter(
                        "approval_id",
                        approval.id,
                    ),
                    self._string_parameter(
                        "idempotency_key",
                        projection.idempotency_key,
                    ),
                    self._string_parameter(
                        "execution_reference",
                        execution_reference,
                    ),
                ],
            )

            if not update_response.get(
                "records"
            ):
                raise UpstreamError(
                    "Approval execution result could not be finalized."
                )

            await self._commit(
                transaction_id
            )
            committed = True

            return ActionExecutionResult(
                approval_id=approval.id,
                idempotency_key=projection.idempotency_key,
                disposition=ActionExecutionDisposition.SUCCEEDED,
                action_type=action.action_type,
                resource_id=action.resource_id,
                execution_reference=execution_reference,
            )

        except Exception:
            if not committed:
                try:
                    await self._rollback(
                        transaction_id
                    )
                except UpstreamError:
                    pass
            raise
