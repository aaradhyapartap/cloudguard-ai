"""Central, deterministic per-document ingestion budgets.

These are application safety ceilings, not tenant quotas or storage-upload limits.
"""

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_EXTRACTED_CHARACTERS = 200_000
MAX_DOCUMENT_CHUNKS = 200


class ObjectSizeLimitError(ValueError):
    """A stored object exceeds the permitted processing size."""


def require_upload_size(value: object) -> None:
    """Reject missing, malformed, boolean, negative, and oversized metadata."""
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAX_UPLOAD_BYTES
    ):
        raise ObjectSizeLimitError("The document exceeds the upload size policy.")
