"""Clock-controlled policy, bounded memory, and shared-provider admission tests."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from app.adapters.aws.rate_limit_store import DynamoDBRateLimitStore
from app.adapters.local.rate_limit_store import InMemoryRateLimitStore
from app.core.errors import RateLimitError, UpstreamError
from app.security.rate_limits import (
    RatePolicy,
    authenticated_policy,
    enforce_rate_limit,
    pre_auth_policy,
)


@pytest.mark.parametrize("method,route,name", [
    ("POST", "/documents", "document-write"),
    ("POST", "/documents/{document_id}/complete", "document-write"),
    ("GET", "/documents/{document_id}", "general"),
    ("POST", "/retrieval/search", "retrieval"),
    ("POST", "/rag/query", "rag"),
    ("POST", "/compliance/assessments/{assessment_id}/candidates", "model-extraction"),
    ("POST", "/compliance/assessments/{assessment_id}/compute", "compliance-write"),
    ("GET", "/compliance/assessments", "compliance-read"),
    ("GET", "/approvals/{approval_id}", "approval-read"),
    ("POST", "/approvals/{approval_id}/decision", "approval-decision"),
    ("GET", "/me", "general"),
])
def test_existing_route_policy(method: str, route: str, name: str) -> None:
    assert authenticated_policy(method, route).name == name


@pytest.mark.parametrize("route,name", [
    ("/health", None), ("/health/ready", None),
    ("/auth/config", "auth-discovery"), ("/auth/dev-login", "local-login"),
    ("/me", "authentication"),
])
def test_pre_auth_policy(route: str, name: str | None) -> None:
    policy = pre_auth_policy(route)
    assert (policy.name if policy else None) == name


async def test_window_rollover_retry_after_and_isolated_subjects() -> None:
    now = [59.5]
    store = InMemoryRateLimitStore(clock=lambda: now[0])
    policy = RatePolicy("test", 2)

    async def admit(subject: str) -> None:
        await enforce_rate_limit(
            store, policy=policy, subject=(subject,), clock=lambda: now[0],
        )

    await admit("tenant-a")
    await admit("tenant-a")
    with pytest.raises(RateLimitError) as caught:
        await admit("tenant-a")
    assert caught.value.retry_after_seconds == 1
    await admit("tenant-b")
    now[0] = 60
    await admit("tenant-a")


async def test_local_counter_capacity_is_bounded_and_expired_slots_are_reclaimed() -> None:
    now = [1.0]
    store = InMemoryRateLimitStore(max_keys=2, clock=lambda: now[0])
    assert await store.consume(key="a", limit=1, expires_at=10)
    assert await store.consume(key="b", limit=1, expires_at=10)
    assert not await store.consume(key="c", limit=1, expires_at=10)
    assert len(store._counters) == 2
    now[0] = 11
    assert await store.consume(key="c", limit=1, expires_at=20)
    assert len(store._counters) <= 2


def test_local_counter_is_atomic_across_threads() -> None:
    store = InMemoryRateLimitStore(clock=lambda: 1)

    def attempt(_index: int) -> bool:
        return asyncio.run(store.consume(key="shared", limit=7, expires_at=60))

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(80))) == 7


class ConditionalFailureError(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class SharedDynamo:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self.calls: list[dict[str, Any]] = []

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            self.calls.append(kwargs)
            key = kwargs["Key"]["counter_key"]["S"]
            limit = int(kwargs["ExpressionAttributeValues"][":limit"]["N"])
            count = self.counts.get(key, 0)
            if count >= limit:
                raise ConditionalFailureError()
            self.counts[key] = count + 1
        return {}


async def test_two_deployed_instances_share_a_single_atomic_limit() -> None:
    client = SharedDynamo()
    stores = [
        DynamoDBRateLimitStore(table_name="counters", region="us-east-1", client=client)
        for _ in range(2)
    ]
    results = await asyncio.gather(*[
        stores[index % 2].consume(key="same-window", limit=10, expires_at=60)
        for index in range(80)
    ])
    assert sum(results) == 10
    assert client.counts == {"same-window": 10}
    call = client.calls[0]
    assert call["ConditionExpression"] == "attribute_not_exists(#count) OR #count < :limit"
    assert call["UpdateExpression"] == "SET #expires = :expires ADD #count :one"
    assert call["ExpressionAttributeNames"]["#expires"] == "expires_at"


async def test_provider_failure_is_fail_closed_and_has_a_static_message() -> None:
    class BrokenDynamo:
        def update_item(self, **kwargs: Any) -> None:
            raise RuntimeError("SYNTHETIC-SENSITIVE-FAILURE")

    store = DynamoDBRateLimitStore(table_name="counters", region="us-east-1", client=BrokenDynamo())
    with pytest.raises(UpstreamError) as caught:
        await enforce_rate_limit(store, policy=RatePolicy("test", 1), subject=("user",))
    assert "SYNTHETIC-SENSITIVE" not in caught.value.message


async def test_bucket_keys_contain_no_raw_identity_and_separate_scope_components() -> None:
    client = SharedDynamo()
    store = DynamoDBRateLimitStore(table_name="counters", region="us-east-1", client=client)
    for subject in [("user", "a:b", "c"), ("user", "a", "b:c"), ("tenant", "a:b")]:
        await enforce_rate_limit(
            store, policy=RatePolicy("test", 1), subject=subject, clock=lambda: 1,
        )
    keys = list(client.counts)
    assert len(set(keys)) == 3
    assert all("a:b" not in key and "b:c" not in key for key in keys)


async def test_new_windows_do_not_depend_on_dynamodb_ttl_deletion() -> None:
    client = SharedDynamo()
    store = DynamoDBRateLimitStore(table_name="counters", region="us-east-1", client=client)
    for now in (59.0, 60.0):
        await enforce_rate_limit(
            store, policy=RatePolicy("test", 1), subject=("same-user",), clock=lambda now=now: now,
        )
    assert len(client.counts) == 2  # Old row intentionally not deleted by the fake.
