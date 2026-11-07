"""Deterministic upload budget contracts, with no provider calls."""

import pytest
from app.models.documents import DocumentCreateRequest
from app.security.ingestion_limits import (
    MAX_UPLOAD_BYTES,
    ObjectSizeLimitError,
    require_upload_size,
)
from pydantic import ValidationError


@pytest.mark.parametrize("value", [None, True, False, -1, "10", 1.5, MAX_UPLOAD_BYTES + 1])
def test_invalid_actual_size_is_rejected(value: object) -> None:
    with pytest.raises(ObjectSizeLimitError):
        require_upload_size(value)


@pytest.mark.parametrize("value", [0, 1, MAX_UPLOAD_BYTES])
def test_actual_size_boundary_is_accepted(value: int) -> None:
    require_upload_size(value)


def test_registered_size_is_bounded() -> None:
    with pytest.raises(ValidationError):
        DocumentCreateRequest(
            filename="policy.txt", content_type="text/plain", size_bytes=MAX_UPLOAD_BYTES + 1
        )
    request = DocumentCreateRequest(
        filename="policy.txt", content_type="text/plain", size_bytes=MAX_UPLOAD_BYTES
    )
    assert request.size_bytes == MAX_UPLOAD_BYTES
