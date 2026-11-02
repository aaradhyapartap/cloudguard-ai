"""Owner-checked fixed-slot leases in the operational admission table."""

import asyncio
import threading
from typing import Any

from app.core.errors import UpstreamError
from app.ports.work_lease_store import WorkLeaseStore


class DynamoDBWorkLeaseStore(WorkLeaseStore):
    def __init__(
        self, *, table_name: str, region: str, endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not table_name.strip():
            raise ValueError("A shared worker-admission table is required.")
        self._table_name = table_name
        self._region = region
        self._endpoint_url = endpoint_url
        self._client = client
        self._initialization_lock = threading.Lock()

    def _call(self, operation: str, kwargs: dict[str, Any]) -> bool:
        try:
            with self._initialization_lock:
                if self._client is None:
                    import boto3
                    from botocore.config import Config

                    self._client = boto3.session.Session().client(
                        "dynamodb", region_name=self._region, endpoint_url=self._endpoint_url,
                        config=Config(
                            connect_timeout=2, read_timeout=2,
                            retries={"total_max_attempts": 1, "mode": "standard"},
                        ),
                    )
                client = self._client
            getattr(client, operation)(TableName=self._table_name, ReturnValues="NONE", **kwargs)
            return True
        except Exception as exc:
            response = getattr(exc, "response", None)
            if isinstance(response, dict) and isinstance(response.get("Error"), dict):
                if response["Error"].get("Code") == "ConditionalCheckFailedException":
                    return False
            raise UpstreamError("Worker admission is temporarily unavailable.") from exc

    async def try_acquire(
        self, *, key: str, owner: str, now: int, expires_at: int,
    ) -> bool:
        if expires_at <= now:
            raise ValueError("A lease expiry must be in the future.")
        return await asyncio.to_thread(self._call, "put_item", {
            "Item": {
                "counter_key": {"S": key}, "lease_owner": {"S": owner},
                "expires_at": {"N": str(expires_at)},
            },
            "ConditionExpression": "attribute_not_exists(#key) OR #expires <= :now",
            "ExpressionAttributeNames": {"#key": "counter_key", "#expires": "expires_at"},
            "ExpressionAttributeValues": {":now": {"N": str(now)}},
        })

    async def release(self, *, key: str, owner: str) -> bool:
        return await asyncio.to_thread(self._call, "delete_item", {
            "Key": {"counter_key": {"S": key}},
            "ConditionExpression": "#owner = :owner",
            "ExpressionAttributeNames": {"#owner": "lease_owner"},
            "ExpressionAttributeValues": {":owner": {"S": owner}},
        })
