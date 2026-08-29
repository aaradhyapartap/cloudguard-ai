"""Focused tests for the EventBridge document worker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from uuid import UUID

import pytest
from app import document_worker

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeProcessingService:
    calls: ClassVar[list[UUID]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def process_document(self, document_id: UUID) -> None:
        self.calls.append(document_id)


@asynccontextmanager
async def fake_tenant_session(
    organization_id: UUID,
) -> AsyncIterator[object]:
    assert organization_id == ORG_ID
    yield object()


async def test_worker_ignores_unrelated_event(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeProcessingService.calls.clear()

    monkeypatch.setattr(
        document_worker,
        "DocumentProcessingService",
        FakeProcessingService,
    )
    monkeypatch.setattr(
        document_worker,
        "tenant_session",
        fake_tenant_session,
    )

    await document_worker._handle(
        {
            "detail-type": "SomethingElse",
            "detail": {},
        }
    )

    assert FakeProcessingService.calls == []


async def test_worker_rejects_non_object_detail() -> None:
    with pytest.raises(ValueError, match="detail must be an object"):
        await document_worker._handle(
            {
                "detail-type": "DocumentUploadCompleted",
                "detail": "not-an-object",
            }
        )


async def test_worker_processes_completed_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeProcessingService.calls.clear()

    monkeypatch.setattr(
        document_worker,
        "DocumentProcessingService",
        FakeProcessingService,
    )
    monkeypatch.setattr(
        document_worker,
        "tenant_session",
        fake_tenant_session,
    )

    await document_worker._handle(
        {
            "detail-type": "DocumentUploadCompleted",
            "detail": {
                "organization_id": str(ORG_ID),
                "document_id": str(DOCUMENT_ID),
            },
        }
    )

    assert FakeProcessingService.calls == [DOCUMENT_ID]
