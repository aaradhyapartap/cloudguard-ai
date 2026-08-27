"""AWS adapters, exercised with stubbed clients.

No AWS account, no LocalStack, no network. The adapters take an injectable
client precisely so this is possible — an adapter that can only be tested
against live infrastructure is one nobody tests.
"""

from __future__ import annotations

import io
import json
from typing import Any
from uuid import UUID

import pytest
from app.adapters.aws.document_store import S3DocumentStore, sanitise_filename
from app.adapters.aws.event_publisher import (
    EVENT_SOURCE,
    MAX_ENTRIES_PER_CALL,
    EventBridgePublisher,
)
from app.core.errors import NotFoundError, UpstreamError
from app.models.ai import DomainEvent

ORG = UUID("11111111-1111-4111-8111-111111111111")
DOC = UUID("33333333-3333-4333-8333-333333333333")


class ClientError(Exception):
    """Mirrors botocore's shape closely enough for the code under test."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class StubS3:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.objects: dict[str, bytes] = {}

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error

    def generate_presigned_url(self, **kwargs: Any) -> str:
        self._record("generate_presigned_url", kwargs)
        return "https://bucket.s3.amazonaws.com/signed"

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("get_object", kwargs)
        return {"Body": io.BytesIO(self.objects.get(kwargs["Key"], b"payload"))}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("put_object", kwargs)
        return {}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("head_object", kwargs)
        return {"ContentLength": 1234, "ContentType": "text/plain", "ETag": '"abc"'}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_object", kwargs)
        return {}


def store(**kwargs: Any) -> tuple[S3DocumentStore, StubS3]:
    stub = StubS3(**kwargs)
    return (
        S3DocumentStore(
            bucket="cloudguard-docs", region="us-east-1", client=stub
        ),
        stub,
    )


# ------------------------------------------------------------------ key layout


def test_key_is_tenant_prefixed() -> None:
    """An IAM policy can scope a role to org/{id}/* only if the layout holds."""
    s3, _ = store()
    key = s3.build_key(organization_id=ORG, document_id=DOC, filename="policy.pdf")
    assert key == f"org/{ORG}/documents/{DOC}/policy.pdf"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../../etc/passwd", "passwd"),
        (r"C:\Windows\system32\evil.txt", "evil.txt"),
        ("nested/dir/report.pdf", "report.pdf"),
        ("", "unnamed"),
    ],
)
def test_path_traversal_cannot_escape_the_tenant_prefix(
    raw: str, expected: str
) -> None:
    s3, _ = store()
    key = s3.build_key(organization_id=ORG, document_id=DOC, filename=raw)
    assert key == f"org/{ORG}/documents/{DOC}/{expected}"
    assert ".." not in key


def test_sanitiser_strips_control_characters() -> None:
    assert "\x00" not in sanitise_filename("bad\x00name.pdf")


# ------------------------------------------------------------------ presigning


async def test_presigned_url_pins_bucket_key_method_and_ttl() -> None:
    s3, stub = store()
    url = await s3.generate_upload_url(
        organization_id=ORG,
        document_id=DOC,
        filename="policy.pdf",
        content_type="application/pdf",
        expires_in_seconds=600,
    )
    assert url.startswith("https://")

    _, kwargs = stub.calls[0]
    assert kwargs["ClientMethod"] == "put_object"
    assert kwargs["HttpMethod"] == "PUT"
    assert kwargs["ExpiresIn"] == 600
    assert kwargs["Params"]["Bucket"] == "cloudguard-docs"
    assert kwargs["Params"]["Key"] == f"org/{ORG}/documents/{DOC}/policy.pdf"


async def test_content_type_is_signed_into_the_url() -> None:
    """A client cannot substitute a different content type after the fact."""
    s3, stub = store()
    await s3.generate_upload_url(
        organization_id=ORG,
        document_id=DOC,
        filename="a.pdf",
        content_type="application/pdf",
    )
    assert stub.calls[0][1]["Params"]["ContentType"] == "application/pdf"


async def test_presign_failure_becomes_an_upstream_error() -> None:
    s3, _ = store(error=RuntimeError("endpoint unreachable"))
    with pytest.raises(UpstreamError):
        await s3.generate_upload_url(
            organization_id=ORG,
            document_id=DOC,
            filename="a.pdf",
            content_type="application/pdf",
        )


# ------------------------------------------------------------------ get / put


async def test_get_object_returns_bytes() -> None:
    s3, stub = store()
    stub.objects["k"] = b"vendor policy"
    assert await s3.get_object(key="k") == b"vendor policy"


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
async def test_missing_object_maps_to_not_found(code: str) -> None:
    s3, _ = store(error=ClientError(code))
    with pytest.raises(NotFoundError):
        await s3.get_object(key="absent")


async def test_other_s3_failures_map_to_upstream_error() -> None:
    """AccessDenied is not "not found" — conflating them hides misconfiguration."""
    s3, _ = store(error=ClientError("AccessDenied"))
    with pytest.raises(UpstreamError):
        await s3.get_object(key="k")


async def test_put_object_requests_encryption_at_rest() -> None:
    s3, stub = store()
    await s3.put_object(key="k", body=b"x", content_type="text/plain")
    assert stub.calls[0][1]["ServerSideEncryption"] == "AES256"


async def test_put_failure_maps_to_upstream_error() -> None:
    s3, _ = store(error=ClientError("SlowDown"))
    with pytest.raises(UpstreamError):
        await s3.put_object(key="k", body=b"x", content_type="text/plain")


# ------------------------------------------------- upload completion support


async def test_head_object_reports_what_actually_arrived() -> None:
    s3, _ = store()
    head = await s3.head_object(key="k")
    assert head == {
        "size_bytes": 1234,
        "content_type": "text/plain",
        "etag": "abc",
    }


async def test_head_returns_none_for_an_abandoned_upload() -> None:
    """Absence is an expected outcome, not an error worth raising."""
    s3, _ = store(error=ClientError("404"))
    assert await s3.head_object(key="k") is None
    assert await s3.object_exists(key="k") is False


async def test_head_still_raises_on_a_real_failure() -> None:
    s3, _ = store(error=ClientError("AccessDenied"))
    with pytest.raises(UpstreamError):
        await s3.head_object(key="k")


async def test_delete_is_idempotent_for_absent_objects() -> None:
    s3, _ = store(error=ClientError("NoSuchKey"))
    await s3.delete_object(key="gone")  # must not raise


# ------------------------------------------------------------------ eventbridge


class StubEvents:
    def __init__(
        self, *, error: Exception | None = None, failed: int = 0
    ) -> None:
        self.error = error
        self.failed = failed
        self.batches: list[list[dict[str, Any]]] = []

    def put_events(self, *, Entries: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: N803
        self.batches.append(Entries)
        if self.error is not None:
            raise self.error
        if self.failed:
            return {
                "FailedEntryCount": self.failed,
                "Entries": [{"ErrorCode": "ThrottlingException"}] * self.failed,
            }
        return {"FailedEntryCount": 0, "Entries": []}


def publisher(**kwargs: Any) -> tuple[EventBridgePublisher, StubEvents]:
    stub = StubEvents(**kwargs)
    return (
        EventBridgePublisher(
            bus_name="cloudguard-events", region="us-east-1", client=stub
        ),
        stub,
    )


def event(index: int = 0) -> DomainEvent:
    from datetime import UTC, datetime

    return DomainEvent(
        event_type="DocumentUploadRegistered",
        organization_id=str(ORG),
        payload={"document_id": str(DOC), "index": index},
        occurred_at=datetime.now(UTC),
        correlation_id=str(DOC),
    )


async def test_entry_uses_the_agreed_source_and_bus() -> None:
    pub, stub = publisher()
    await pub.publish(event())
    entry = stub.batches[0][0]
    assert entry["Source"] == EVENT_SOURCE
    assert entry["EventBusName"] == "cloudguard-events"
    assert entry["DetailType"] == "DocumentUploadRegistered"


async def test_detail_carries_the_tenant_so_rules_can_filter_on_it() -> None:
    pub, stub = publisher()
    await pub.publish(event())
    detail = json.loads(stub.batches[0][0]["Detail"])
    assert detail["organization_id"] == str(ORG)
    assert detail["correlation_id"] == str(DOC)
    assert detail["document_id"] == str(DOC)


async def test_batches_respect_the_ten_entry_api_limit() -> None:
    """Exceeding it is a hard API error, not a soft truncation."""
    pub, stub = publisher()
    await pub.publish_batch([event(i) for i in range(23)])
    assert [len(batch) for batch in stub.batches] == [
        MAX_ENTRIES_PER_CALL,
        MAX_ENTRIES_PER_CALL,
        3,
    ]


async def test_empty_batch_makes_no_call() -> None:
    pub, stub = publisher()
    await pub.publish_batch([])
    assert stub.batches == []


async def test_publish_failure_does_not_propagate() -> None:
    """The work already succeeded; losing a notification is the smaller harm."""
    pub, _ = publisher(error=RuntimeError("bus unreachable"))
    await pub.publish(event())  # must not raise


async def test_partial_failure_is_detected_despite_a_200_response() -> None:
    """PutEvents returns 200 with per-entry failures — invisible without this."""
    pub, stub = publisher(failed=2)
    await pub.publish_batch([event(0), event(1)])
    assert len(stub.batches) == 1


async def test_non_serialisable_payload_degrades_rather_than_raising() -> None:
    from datetime import UTC, datetime

    awkward = DomainEvent(
        event_type="DocumentUploadRegistered",
        organization_id=str(ORG),
        payload={"document_id": DOC},  # a UUID, not a string
        occurred_at=datetime.now(UTC),
    )
    pub, stub = publisher()
    await pub.publish(awkward)
    assert json.loads(stub.batches[0][0]["Detail"])["document_id"] == str(DOC)
