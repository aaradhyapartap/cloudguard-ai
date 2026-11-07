"""AWS Step Functions adapter for resuming approval wait states."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from botocore.config import Config

from app.core.errors import UpstreamError
from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision


class StepFunctionsApprovalCallback:
    """Resume a waiting Step Functions task with validated decision data."""

    def __init__(
        self,
        *,
        region: str | None = None,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return

        import boto3

        self._client = boto3.client(
            "stepfunctions",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    async def send_decision(
        self,
        *,
        task_token: str,
        decision: ApprovalDecision,
        effective_action: ApprovalAction | None,
    ) -> None:
        output = {
            "decision": decision.value,
            "effective_action": (
                effective_action.model_dump(mode="json")
                if effective_action is not None
                else None
            ),
        }

        try:
            await asyncio.to_thread(
                self._client.send_task_success,
                taskToken=task_token,
                output=json.dumps(
                    output,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        except Exception as exc:
            raise UpstreamError(
                "Could not resume the approval workflow."
            ) from exc
