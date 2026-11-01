"""Shared single-region admission through conditional DynamoDB updates."""

import asyncio
import threading
from typing import Any

from app.core.errors import UpstreamError
from app.ports.rate_limit_store import RateLimitStore


class DynamoDBRateLimitStore(RateLimitStore):
    def __init__(
        self, *, table_name: str, region: str, endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not table_name.strip():
            raise ValueError("A shared rate-limit table is required.")
        self._table_name = table_name
        self._region = region
        self._endpoint_url = endpoint_url
        # Lazy SDK initialization keeps composition and offline synthesis free
        # of credential discovery. No store call is made until request admission.
        self._client = client
        self._initialization_lock = threading.Lock()

    def _consume(self, key: str, limit: int, expires_at: int) -> bool:
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
            client.update_item(
                TableName=self._table_name,
                Key={"counter_key": {"S": key}},
                UpdateExpression="SET #expires = :expires ADD #count :one",
                ConditionExpression="attribute_not_exists(#count) OR #count < :limit",
                ExpressionAttributeNames={"#count": "request_count", "#expires": "expires_at"},
                ExpressionAttributeValues={
                    ":one": {"N": "1"}, ":limit": {"N": str(limit)},
                    ":expires": {"N": str(expires_at)},
                },
                ReturnValues="NONE",
            )
            return True
        except Exception as exc:
            response = getattr(exc, "response", None)
            if isinstance(response, dict) and isinstance(response.get("Error"), dict):
                if response["Error"].get("Code") == "ConditionalCheckFailedException":
                    return False
            # Never log keys, IPs, provider exception text, or counter contents.
            raise UpstreamError("Request admission is temporarily unavailable.") from exc

    async def consume(self, *, key: str, limit: int, expires_at: int) -> bool:
        if limit < 1:
            return False
        return await asyncio.to_thread(self._consume, key, limit, expires_at)
