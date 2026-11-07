"""Owner-safe leases, tenant quotas, expiry, failure, and shared-instance tests."""

import asyncio
import threading
from typing import Any
from uuid import UUID

import pytest
from app.adapters.aws.rate_limit_store import DynamoDBRateLimitStore
from app.adapters.aws.work_lease_store import DynamoDBWorkLeaseStore
from app.adapters.local.rate_limit_store import InMemoryRateLimitStore
from app.adapters.local.work_lease_store import InMemoryWorkLeaseStore
from app.core.errors import RateLimitError, UpstreamError
from app.security.rate_limits import RatePolicy, enforce_rate_limit
from app.services.work_admission import WorkAdmission, WorkKind

ORG_A = UUID(int=1)
ORG_B = UUID(int=2)


def _local(now: list[float]) -> tuple[WorkAdmission, InMemoryWorkLeaseStore]:
    leases = InMemoryWorkLeaseStore()
    service = WorkAdmission(
        leases=leases, rates=InMemoryRateLimitStore(clock=lambda: now[0]), clock=lambda: now[0],
    )
    return service, leases


async def test_tenant_and_kind_scopes_are_isolated_and_slots_release() -> None:
    service, leases = _local([1.0])
    async with service.admit(organization_id=ORG_A, kind=WorkKind.INGESTION):
        with pytest.raises(RateLimitError):
            async with service.admit(organization_id=ORG_A, kind=WorkKind.INGESTION):
                raise AssertionError("Denied work must not run")
        async with service.admit(organization_id=ORG_B, kind=WorkKind.INGESTION):
            async with service.admit(organization_id=ORG_A, kind=WorkKind.MODEL):
                assert len(leases._slots) == 3
    assert leases._slots == {}


@pytest.mark.parametrize("kind,limit", [(WorkKind.INGESTION, 10), (WorkKind.MODEL, 60)])
async def test_temporal_worker_quota_is_not_reset_by_slot_release(
    kind: WorkKind, limit: int,
) -> None:
    now = [59.0]
    service, leases = _local(now)
    for _ in range(limit):
        async with service.admit(organization_id=ORG_A, kind=kind):
            pass
    with pytest.raises(RateLimitError):
        async with service.admit(organization_id=ORG_A, kind=kind):
            raise AssertionError("Denied work must not run")
    assert leases._slots == {}
    now[0] = 60.0
    async with service.admit(organization_id=ORG_A, kind=kind):
        pass


async def test_body_failure_releases_lease_without_replacing_original_error() -> None:
    service, leases = _local([1.0])
    with pytest.raises(ValueError, match="original failure"):
        async with service.admit(organization_id=ORG_A, kind=WorkKind.MODEL):
            raise ValueError("original failure")
    assert leases._slots == {}


async def test_api_and_worker_counters_have_disjoint_namespaces() -> None:
    rates = InMemoryRateLimitStore(clock=lambda: 1)
    policy = RatePolicy("same-policy", 1)
    for namespace in ("api", "worker"):
        await enforce_rate_limit(
            rates, policy=policy, subject=("same-subject",), namespace=namespace, clock=lambda: 1,
        )
        with pytest.raises(RateLimitError):
            await enforce_rate_limit(
                rates, policy=policy, subject=("same-subject",),
                namespace=namespace, clock=lambda: 1,
            )


async def test_local_expiry_and_owner_condition_prevent_stale_deletion() -> None:
    leases = InMemoryWorkLeaseStore(max_keys=1)
    assert await leases.try_acquire(key="slot", owner="old", now=1, expires_at=2)
    assert not await leases.try_acquire(key="slot", owner="new", now=1, expires_at=3)
    assert await leases.try_acquire(key="slot", owner="new", now=2, expires_at=3)
    assert not await leases.release(key="slot", owner="old")
    assert not await leases.try_acquire(key="other", owner="other", now=2, expires_at=4)
    assert await leases.release(key="slot", owner="new")
    assert await leases.try_acquire(key="other", owner="other", now=3, expires_at=4)


class ConditionalFailureError(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class SharedDynamo:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("put_item", kwargs))
            item = kwargs["Item"]
            key = item["counter_key"]["S"]
            old = self.rows.get(key)
            now = int(kwargs["ExpressionAttributeValues"][":now"]["N"])
            if old is not None and int(old["expires_at"]["N"]) > now:
                raise ConditionalFailureError()
            self.rows[key] = dict(item)
        return {}

    def delete_item(self, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("delete_item", kwargs))
            key = kwargs["Key"]["counter_key"]["S"]
            owner = kwargs["ExpressionAttributeValues"][":owner"]["S"]
            old = self.rows.get(key)
            if old is None or old["lease_owner"]["S"] != owner:
                raise ConditionalFailureError()
            del self.rows[key]
        return {}

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("update_item", kwargs))
            key = kwargs["Key"]["counter_key"]["S"]
            values = kwargs["ExpressionAttributeValues"]
            count = int(self.rows.get(key, {}).get("request_count", {"N": "0"})["N"])
            if count >= int(values[":limit"]["N"]):
                raise ConditionalFailureError()
            self.rows[key] = {
                "request_count": {"N": str(count + 1)}, "expires_at": values[":expires"],
            }
        return {}


def _shared(client: SharedDynamo) -> WorkAdmission:
    return WorkAdmission(
        leases=DynamoDBWorkLeaseStore(table_name="admission", region="us-east-1", client=client),
        rates=DynamoDBRateLimitStore(table_name="admission", region="us-east-1", client=client),
        clock=lambda: 1,
    )


async def test_two_worker_instances_admit_only_two_concurrent_model_steps_per_tenant() -> None:
    client = SharedDynamo()
    services = [_shared(client), _shared(client)]
    active = 0
    denied = 0
    ready = asyncio.Event()
    all_denied = asyncio.Event()
    release = asyncio.Event()

    async def attempt(index: int) -> bool:
        nonlocal active, denied
        try:
            async with services[index % 2].admit(organization_id=ORG_A, kind=WorkKind.MODEL):
                active += 1
                assert active <= 2
                if active == 2:
                    ready.set()
                await release.wait()
                active -= 1
                return True
        except RateLimitError:
            denied += 1
            if denied == 18:
                all_denied.set()
            return False

    tasks = [asyncio.create_task(attempt(index)) for index in range(20)]
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        await asyncio.wait_for(all_denied.wait(), timeout=10)
        assert active == 2
        async with services[0].admit(organization_id=ORG_B, kind=WorkKind.MODEL):
            pass
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert sum(results) == 2
    assert not any(key.startswith("slot:") for key in client.rows)
    assert all(key.startswith("worker:v1:") for key in client.rows)
    assert str(ORG_A) not in str(client.rows)


async def test_dynamodb_expiry_and_stale_release_preserve_new_owner() -> None:
    client = SharedDynamo()
    store = DynamoDBWorkLeaseStore(table_name="admission", region="us-east-1", client=client)
    assert await store.try_acquire(key="slot:v1:test", owner="old", now=1, expires_at=2)
    assert await store.try_acquire(key="slot:v1:test", owner="new", now=2, expires_at=3)
    assert not await store.release(key="slot:v1:test", owner="old")
    assert await store.release(key="slot:v1:test", owner="new")
    put = client.calls[0][1]
    assert put["ConditionExpression"] == "attribute_not_exists(#key) OR #expires <= :now"
    assert client.calls[2][1]["ConditionExpression"] == "#owner = :owner"


async def test_acquisition_outage_fails_closed_without_entering_work() -> None:
    class BrokenDynamo:
        def put_item(self, **kwargs: Any) -> None:
            raise RuntimeError("SYNTHETIC-SENSITIVE-FAILURE")

    service = WorkAdmission(
        leases=DynamoDBWorkLeaseStore(
            table_name="admission", region="us-east-1", client=BrokenDynamo(),
        ),
        rates=InMemoryRateLimitStore(),
    )
    with pytest.raises(UpstreamError) as caught:
        async with service.admit(organization_id=ORG_A, kind=WorkKind.MODEL):
            raise AssertionError("Denied work must not run")
    assert "SYNTHETIC-SENSITIVE" not in caught.value.message


async def test_rate_store_outage_releases_slot_without_entering_work() -> None:
    class BrokenRate(InMemoryRateLimitStore):
        async def consume(self, *, key: str, limit: int, expires_at: int) -> bool:
            raise UpstreamError("SYNTHETIC-SENSITIVE-FAILURE")

    leases = InMemoryWorkLeaseStore()
    service = WorkAdmission(leases=leases, rates=BrokenRate())
    with pytest.raises(UpstreamError):
        async with service.admit(organization_id=ORG_A, kind=WorkKind.MODEL):
            raise AssertionError("Denied work must not run")
    assert leases._slots == {}


async def test_cleanup_outage_does_not_turn_completed_work_into_a_replay_trigger() -> None:
    class BrokenCleanup(InMemoryWorkLeaseStore):
        async def release(self, *, key: str, owner: str) -> bool:
            raise UpstreamError("SYNTHETIC-SENSITIVE-FAILURE")

    leases = BrokenCleanup()
    service = WorkAdmission(leases=leases, rates=InMemoryRateLimitStore())
    completed: list[str] = []
    async with service.admit(organization_id=ORG_A, kind=WorkKind.MODEL):
        completed.append("done")
    assert completed == ["done"]
    assert len(leases._slots) == 1  # Conservatively retained until expiry.
    with pytest.raises(ValueError, match="original failure"):
        async with service.admit(organization_id=ORG_A, kind=WorkKind.MODEL):
            raise ValueError("original failure")
