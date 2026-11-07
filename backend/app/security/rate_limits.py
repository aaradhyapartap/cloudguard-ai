"""Central API quota policy; identities are supplied only after verification."""

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.errors import RateLimitError, UpstreamError
from app.ports.rate_limit_store import RateLimitStore


@dataclass(frozen=True, slots=True)
class RatePolicy:
    name: str
    requests: int
    window_seconds: int = 60

    def __post_init__(self) -> None:
        if self.requests < 1 or self.window_seconds < 1:
            raise ValueError("Rate policy budgets must be positive.")


AUTH_ATTEMPT = RatePolicy("authentication", 300)
LOCAL_LOGIN = RatePolicy("local-login", 10)
AUTH_DISCOVERY = RatePolicy("auth-discovery", 120)
GENERAL = RatePolicy("general", 120)
DOCUMENT_WRITE = RatePolicy("document-write", 30)
RETRIEVAL = RatePolicy("retrieval", 60)
RAG = RatePolicy("rag", 20)
COMPLIANCE_READ = RatePolicy("compliance-read", 120)
COMPLIANCE_WRITE = RatePolicy("compliance-write", 30)
EXTRACTION = RatePolicy("model-extraction", 10)
APPROVAL_READ = RatePolicy("approval-read", 120)
APPROVAL_DECIDE = RatePolicy("approval-decision", 30)
TENANT_MULTIPLIER = 5


def pre_auth_policy(route: str) -> RatePolicy | None:
    if route in {"/health", "/health/ready"}:
        return None
    if route == "/auth/dev-login":
        return LOCAL_LOGIN
    if route == "/auth/config":
        return AUTH_DISCOVERY
    return AUTH_ATTEMPT


def authenticated_policy(method: str, route: str) -> RatePolicy:
    if route.startswith("/documents"):
        return GENERAL if method == "GET" else DOCUMENT_WRITE
    if route.startswith("/retrieval/"):
        return RETRIEVAL
    if route.startswith("/rag/"):
        return RAG
    if route.startswith("/compliance/"):
        if method == "GET":
            return COMPLIANCE_READ
        return EXTRACTION if route.endswith("/candidates") else COMPLIANCE_WRITE
    if route.startswith("/approvals"):
        return APPROVAL_READ if method == "GET" else APPROVAL_DECIDE
    return GENERAL


async def enforce_rate_limit(
    store: RateLimitStore, *, policy: RatePolicy, subject: tuple[str, ...],
    multiplier: int = 1, clock: Callable[[], float] = time.time,
    namespace: str = "api",
) -> None:
    if multiplier < 1:
        raise ValueError("The quota multiplier must be positive.")
    if namespace not in {"api", "worker"}:
        raise ValueError("The admission namespace is invalid.")
    now = clock()
    window = math.floor(now / policy.window_seconds)
    window_end = (window + 1) * policy.window_seconds
    digest = hashlib.sha256(json.dumps(subject, separators=(",", ":")).encode()).hexdigest()
    key = f"{namespace}:v1:{policy.name}:{policy.window_seconds}:{window}:{digest}"
    try:
        admitted = await store.consume(
            key=key, limit=policy.requests * multiplier, expires_at=window_end,
        )
    except Exception as exc:
        raise UpstreamError("Request admission is temporarily unavailable.") from exc
    if not admitted:
        raise RateLimitError(retry_after_seconds=max(1, math.ceil(window_end - now)))
