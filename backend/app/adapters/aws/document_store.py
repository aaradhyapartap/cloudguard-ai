"""S3 document store.

Implements :class:`app.ports.document_store.DocumentStore` against Amazon S3,
and against LocalStack when ``AWS_ENDPOINT_URL`` is set.

Three properties matter more than the code around them.

**Presigned uploads.** The browser PUTs directly to S3; file bytes never pass
through Lambda. That removes the API Gateway payload limit, removes a
memory-exhaustion class from the API tier, and means a 25 MB upload costs no
Lambda duration. The URL is scoped to one key, one method and a short TTL.

**The key layout is a security control, not a filing habit.**
``org/{organization_id}/documents/{document_id}/{filename}`` means an IAM policy
can restrict a role to one tenant's prefix. A flat layout would make that
impossible and leave tenant isolation entirely to application code, which
contradicts the defence-in-depth argument the rest of the system is built on.

**boto3 is synchronous.** Every call is pushed to a worker thread. A blocking
network call inside an async handler stalls the event loop for every concurrent
request, not just its own.

No credentials are read or stored here. boto3 resolves them from the standard
chain — environment, shared config, instance/task role — so production uses an
IAM role and nothing is ever committed.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any
from uuid import UUID

from app.core.errors import NotFoundError, UpstreamError
from app.core.logging import get_logger
from app.ports.document_store import DocumentStore

logger = get_logger(__name__)

DEFAULT_PRESIGN_TTL_SECONDS = 900

# botocore raises ClientError for everything and distinguishes by this code.
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def sanitise_filename(filename: str) -> str:
    """Strip any path component before it reaches an object key.

    ``../../etc/passwd`` and ``C:\\Windows\\evil.txt`` both reduce to their last
    segment. The key is built from an organization id and a document id, so this
    is belt and braces — but a filename is attacker-controlled and ends up in
    logs, the UI and the key itself.
    """
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in '\0"')
    return cleaned[:255] or "unnamed"


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return ""


class S3DocumentStore(DocumentStore):
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        """``client`` is injectable so unit tests can stub boto3 entirely.

        An adapter that can only be exercised against live AWS is an adapter
        nobody exercises.
        """
        self._bucket = bucket

        if client is not None:
            self._client = client
            return

        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                # LocalStack serves everything from one host, so path-style
                # addressing is required there and harmless against real S3.
                s3={"addressing_style": "path"} if endpoint_url else {},
            ),
        )

    # ------------------------------------------------------------------ keys

    def build_key(
        self, *, organization_id: UUID, document_id: UUID, filename: str
    ) -> str:
        return (
            f"org/{organization_id}/documents/{document_id}/"
            f"{sanitise_filename(filename)}"
        )

    # ------------------------------------------------------------------ calls

    async def _call(self, method: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(partial(getattr(self._client, method), **kwargs))

    async def generate_upload_url(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        filename: str,
        content_type: str,
        expires_in_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS,
    ) -> str:
        key = self.build_key(
            organization_id=organization_id,
            document_id=document_id,
            filename=filename,
        )
        try:
            url: str = await asyncio.to_thread(
                partial(
                    self._client.generate_presigned_url,
                    ClientMethod="put_object",
                    Params={
                        "Bucket": self._bucket,
                        "Key": key,
                        # Signed into the URL: the client cannot substitute a
                        # different content type after the fact.
                        "ContentType": content_type,
                    },
                    ExpiresIn=expires_in_seconds,
                    HttpMethod="PUT",
                )
            )
        except Exception as exc:
            logger.error("presign_failed", error=str(exc))
            raise UpstreamError("Could not prepare the upload.") from exc

        logger.info(
            "upload_url_issued",
            document_id=str(document_id),
            expires_in_seconds=expires_in_seconds,
        )
        return url

    async def get_object(self, *, key: str) -> bytes:
        try:
            response = await self._call("get_object", Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                raise NotFoundError() from exc
            logger.error("s3_get_failed", key=key, error=str(exc))
            raise UpstreamError("Could not read the stored document.") from exc

        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body

    async def put_object(self, *, key: str, body: bytes, content_type: str) -> None:
        try:
            await self._call(
                "put_object",
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                # Set per object as well as on the bucket. A bucket policy can
                # be changed later; an object written before that change keeps
                # the setting it was written with.
                ServerSideEncryption="AES256",
            )
        except Exception as exc:
            logger.error("s3_put_failed", key=key, error=str(exc))
            raise UpstreamError("Could not store the document.") from exc

    # ------------------------------------------------- upload completion (P3)

    async def head_object(self, *, key: str) -> dict[str, Any] | None:
        """Confirm an object exists and report what actually arrived.

        The upload-completion endpoint needs this. At registration time the
        client told us how large the file *would* be; only the store knows what
        landed. Returns ``None`` when absent — absence is an expected outcome
        (an abandoned upload), not an error worth raising.
        """
        try:
            response = await self._call("head_object", Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return None
            logger.error("s3_head_failed", key=key, error=str(exc))
            raise UpstreamError("Could not verify the uploaded document.") from exc

        return {
            "size_bytes": int(response.get("ContentLength", 0)),
            "content_type": str(response.get("ContentType", "")),
            "etag": str(response.get("ETag", "")).strip('"'),
        }

    async def object_exists(self, *, key: str) -> bool:
        return await self.head_object(key=key) is not None

    async def delete_object(self, *, key: str) -> None:
        """Absent objects are not an error — delete is idempotent."""
        try:
            await self._call("delete_object", Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return
            logger.error("s3_delete_failed", key=key, error=str(exc))
            raise UpstreamError("Could not delete the stored document.") from exc
