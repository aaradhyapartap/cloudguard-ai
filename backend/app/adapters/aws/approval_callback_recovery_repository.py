"""Aurora Data API persistence for Approval callback recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from botocore.config import Config

from app.core.errors import UpstreamError
from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision
from app.ports.approval_callback_recovery_repository import (
    ApprovalCallbackClaim,
    ApprovalCallbackRecoveryRepository,
)

TENANT_SETTING = "app.current_organization_id"


def _string_parameter(name: str, value: str) -> dict[str, object]:
    return {
        "name": name,
        "value": {"stringValue": value},
    }


def _long_parameter(name: str, value: int) -> dict[str, object]:
    return {
        "name": name,
        "value": {"longValue": value},
    }


def _field_text(record: list[dict[str, Any]], index: int) -> str:
    value = record[index].get("stringValue")
    if not isinstance(value, str):
        raise ValueError("Aurora Data API returned an unexpected field shape.")
    return value


class AuroraDataAPIApprovalCallbackRecoveryRepository(
    ApprovalCallbackRecoveryRepository
):
    """Recovery persistence without requiring Lambda VPC connectivity."""

    def __init__(
        self,
        *,
        resource_arn: str,
        secret_arn: str,
        database: str,
        region: str | None = None,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not resource_arn.strip():
            raise ValueError("Aurora resource ARN is required.")
        if not secret_arn.strip():
            raise ValueError("Aurora secret ARN is required.")
        if not database.strip():
            raise ValueError("Database name is required.")

        self._resource_arn = resource_arn
        self._secret_arn = secret_arn
        self._database = database

        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client(
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
        method_name: str,
        **kwargs: object,
    ) -> dict[str, Any]:
        method = getattr(self._client, method_name)

        try:
            result = await asyncio.to_thread(method, **kwargs)
        except Exception as exc:
            raise UpstreamError(
                "Could not access approval callback persistence."
            ) from exc

        if not isinstance(result, dict):
            raise UpstreamError(
                "Could not access approval callback persistence."
            )

        return result

    async def _begin(self) -> str:
        response = await self._call(
            "begin_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            database=self._database,
        )

        transaction_id = response.get("transactionId")

        if not isinstance(transaction_id, str):
            raise UpstreamError(
                "Could not start approval callback persistence transaction."
            )

        return transaction_id

    async def _commit(self, transaction_id: str) -> None:
        await self._call(
            "commit_transaction",
            resourceArn=self._resource_arn,
            secretArn=self._secret_arn,
            transactionId=transaction_id,
        )

    async def _rollback(self, transaction_id: str) -> None:
        try:
            await self._call(
                "rollback_transaction",
                resourceArn=self._resource_arn,
                secretArn=self._secret_arn,
                transactionId=transaction_id,
            )
        except UpstreamError:
            pass

    async def _execute(
        self,
        *,
        sql: str,
        parameters: list[dict[str, object]] | None = None,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, object] = {
            "resourceArn": self._resource_arn,
            "secretArn": self._secret_arn,
            "database": self._database,
            "sql": sql,
        }

        if parameters:
            kwargs["parameters"] = parameters

        if transaction_id is not None:
            kwargs["transactionId"] = transaction_id

        return await self._call(
            "execute_statement",
            **kwargs,
        )

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
                ":setting_name, "
                ":organization_id, "
                "true"
                ")"
            ),
            parameters=[
                _string_parameter(
                    "setting_name",
                    TENANT_SETTING,
                ),
                _string_parameter(
                    "organization_id",
                    str(organization_id),
                ),
            ],
        )

    async def list_organization_ids(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = 500,
    ) -> tuple[UUID, ...]:
        if limit < 1 or limit > 5000:
            raise ValueError(
                "Organization recovery limit must be between 1 and 5000."
            )

        parameters: list[dict[str, object]] = [
            _long_parameter(
                "limit",
                limit,
            ),
        ]

        if after_id is None:
            sql = (
                "SELECT id::text "
                "FROM organizations "
                "ORDER BY id "
                "LIMIT :limit"
            )
        else:
            sql = (
                "SELECT id::text "
                "FROM organizations "
                "WHERE id > CAST(:after_id AS uuid) "
                "ORDER BY id "
                "LIMIT :limit"
            )
            parameters.append(
                _string_parameter(
                    "after_id",
                    str(after_id),
                )
            )

        response = await self._execute(
            sql=sql,
            parameters=parameters,
        )

        records = response.get("records", [])

        return tuple(
            UUID(
                _field_text(
                    record,
                    0,
                )
            )
            for record in records
        )

    async def list_recoverable_approval_ids(
        self,
        *,
        organization_id: UUID,
        stale_before: datetime,
        limit: int = 25,
    ) -> tuple[UUID, ...]:
        if limit < 1 or limit > 100:
            raise ValueError(
                "Approval recovery batch limit must be between 1 and 100."
            )

        transaction_id = await self._begin()

        try:
            await self._set_tenant(
                transaction_id=transaction_id,
                organization_id=organization_id,
            )

            response = await self._execute(
                transaction_id=transaction_id,
                sql=(
                    "SELECT id::text "
                    "FROM approvals "
                    "WHERE organization_id = CAST(:organization_id AS uuid) "
                    "AND status = CAST('decided' AS approval_status) "
                    "AND decision IS NOT NULL "
                    "AND callback_delivered_at IS NULL "
                    "AND ("
                    "callback_claimed_at IS NULL "
                    "OR callback_claimed_at < CAST(:stale_before AS timestamptz)"
                    ") "
                    "ORDER BY decided_at ASC, id ASC "
                    "LIMIT :limit"
                ),
                parameters=[
                    _string_parameter(
                        "organization_id",
                        str(organization_id),
                    ),
                    _string_parameter(
                        "stale_before",
                        stale_before.isoformat(),
                    ),
                    _long_parameter(
                        "limit",
                        limit,
                    ),
                ],
            )

            await self._commit(transaction_id)

        except Exception:
            await self._rollback(transaction_id)
            raise

        records = response.get("records", [])

        return tuple(
            UUID(
                _field_text(
                    record,
                    0,
                )
            )
            for record in records
        )

    async def claim_decided_callback(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        stale_before: datetime,
    ) -> ApprovalCallbackClaim | None:
        claim_id = uuid4()
        claimed_at = datetime.now(UTC).isoformat()

        transaction_id = await self._begin()

        try:
            await self._set_tenant(
                transaction_id=transaction_id,
                organization_id=organization_id,
            )

            response = await self._execute(
                transaction_id=transaction_id,
                sql=(
                    "UPDATE approvals "
                    "SET "
                    "callback_claim_id = CAST(:claim_id AS uuid), "
                    "callback_claimed_at = CAST(:claimed_at AS timestamptz), "
                    "callback_attempt_count = callback_attempt_count + 1, "
                    "updated_at = now() "
                    "WHERE organization_id = CAST(:organization_id AS uuid) "
                    "AND id = CAST(:approval_id AS uuid) "
                    "AND status = CAST('decided' AS approval_status) "
                    "AND decision IS NOT NULL "
                    "AND callback_delivered_at IS NULL "
                    "AND ("
                    "callback_claimed_at IS NULL "
                    "OR callback_claimed_at < CAST(:stale_before AS timestamptz)"
                    ") "
                    "RETURNING "
                    "id::text, "
                    "organization_id::text, "
                    "task_token, "
                    "decision::text, "
                    "proposed_action::text, "
                    "modified_action::text"
                ),
                parameters=[
                    _string_parameter(
                        "claim_id",
                        str(claim_id),
                    ),
                    _string_parameter(
                        "claimed_at",
                        claimed_at,
                    ),
                    _string_parameter(
                        "organization_id",
                        str(organization_id),
                    ),
                    _string_parameter(
                        "approval_id",
                        str(approval_id),
                    ),
                    _string_parameter(
                        "stale_before",
                        stale_before.isoformat(),
                    ),
                ],
            )

            await self._commit(transaction_id)

        except Exception:
            await self._rollback(transaction_id)
            raise

        records = response.get("records", [])

        if not records:
            return None

        record = records[0]

        decision = ApprovalDecision(
            _field_text(
                record,
                3,
            )
        )

        proposed_action = ApprovalAction.model_validate(
            json.loads(
                _field_text(
                    record,
                    4,
                )
            )
        )

        effective_action: ApprovalAction | None

        if decision is ApprovalDecision.APPROVED:
            effective_action = proposed_action
        elif decision is ApprovalDecision.MODIFIED:
            modified_raw = _field_text(
                record,
                5,
            )
            effective_action = ApprovalAction.model_validate(
                json.loads(
                    modified_raw
                )
            )
        else:
            effective_action = None

        return ApprovalCallbackClaim(
            claim_id=claim_id,
            approval_id=UUID(
                _field_text(
                    record,
                    0,
                )
            ),
            organization_id=UUID(
                _field_text(
                    record,
                    1,
                )
            ),
            task_token=_field_text(
                record,
                2,
            ),
            decision=decision,
            effective_action=effective_action,
        )

    async def mark_callback_delivered(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        transaction_id = await self._begin()

        try:
            await self._set_tenant(
                transaction_id=transaction_id,
                organization_id=organization_id,
            )

            response = await self._execute(
                transaction_id=transaction_id,
                sql=(
                    "UPDATE approvals "
                    "SET "
                    "callback_claim_id = NULL, "
                    "callback_claimed_at = NULL, "
                    "callback_delivered_at = now(), "
                    "updated_at = now() "
                    "WHERE organization_id = CAST(:organization_id AS uuid) "
                    "AND id = CAST(:approval_id AS uuid) "
                    "AND status = CAST('decided' AS approval_status) "
                    "AND callback_delivered_at IS NULL "
                    "AND callback_claim_id = CAST(:claim_id AS uuid) "
                    "RETURNING id::text"
                ),
                parameters=[
                    _string_parameter(
                        "organization_id",
                        str(organization_id),
                    ),
                    _string_parameter(
                        "approval_id",
                        str(approval_id),
                    ),
                    _string_parameter(
                        "claim_id",
                        str(claim_id),
                    ),
                ],
            )

            await self._commit(transaction_id)

        except Exception:
            await self._rollback(transaction_id)
            raise

        return bool(
            response.get(
                "records",
                [],
            )
        )

    async def release_callback_claim(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        transaction_id = await self._begin()

        try:
            await self._set_tenant(
                transaction_id=transaction_id,
                organization_id=organization_id,
            )

            response = await self._execute(
                transaction_id=transaction_id,
                sql=(
                    "UPDATE approvals "
                    "SET "
                    "callback_claim_id = NULL, "
                    "callback_claimed_at = NULL, "
                    "updated_at = now() "
                    "WHERE organization_id = CAST(:organization_id AS uuid) "
                    "AND id = CAST(:approval_id AS uuid) "
                    "AND status = CAST('decided' AS approval_status) "
                    "AND callback_delivered_at IS NULL "
                    "AND callback_claim_id = CAST(:claim_id AS uuid) "
                    "RETURNING id::text"
                ),
                parameters=[
                    _string_parameter(
                        "organization_id",
                        str(organization_id),
                    ),
                    _string_parameter(
                        "approval_id",
                        str(approval_id),
                    ),
                    _string_parameter(
                        "claim_id",
                        str(claim_id),
                    ),
                ],
            )

            await self._commit(transaction_id)

        except Exception:
            await self._rollback(transaction_id)
            raise

        return bool(
            response.get(
                "records",
                [],
            )
        )
