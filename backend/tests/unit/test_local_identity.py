"""Local development identity provider.

The value of this adapter is that it produces the same claim shape Cognito does,
so the mapping code is shared. These tests assert that symmetry — and that the
adapter still refuses tokens it should refuse, because a development provider
that accepts anything teaches nothing and hides bugs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.adapters.local.identity import (
    LOCAL_AUDIENCE,
    LOCAL_ISSUER,
    LocalIdentityProvider,
)
from app.ports.identity_provider import TokenVerificationError

SECRET = "a-sufficiently-long-development-secret-of-32-plus-bytes"
ORG_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def provider() -> LocalIdentityProvider:
    return LocalIdentityProvider(secret=SECRET)


def mint(provider: LocalIdentityProvider, **overrides: object) -> str:
    kwargs: dict[str, object] = {
        "subject": USER_ID,
        "email": "analyst@acme.test",
        "groups": ["analyst"],
        "organization_id": ORG_ID,
        "department": "Finance",
    }
    kwargs.update(overrides)
    return provider.mint(**kwargs)  # type: ignore[arg-type]


def test_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        LocalIdentityProvider(secret="short")


def test_secret_below_rfc_minimum_is_refused() -> None:
    """31 bytes: long enough to look fine, short enough to be wrong."""
    with pytest.raises(ValueError, match="at least 32 bytes"):
        LocalIdentityProvider(secret="x" * 31)


async def test_round_trip_produces_cognito_shaped_claims(
    provider: LocalIdentityProvider,
) -> None:
    claims = await provider.verify(mint(provider))
    assert claims.subject == USER_ID
    assert claims.groups == ["analyst"]
    assert claims.organization_id == ORG_ID
    assert claims.department == "Finance"
    assert claims.token_use == "id"
    assert claims.issuer == LOCAL_ISSUER


async def test_claim_names_match_cognito_exactly(provider: LocalIdentityProvider) -> None:
    """The whole point of ADR-0014: one mapping implementation, two issuers.

    If these claim names drift, local development stops exercising the code that
    runs in AWS and starts exercising a lookalike.
    """
    payload = jwt.decode(
        mint(provider),
        key=SECRET,
        algorithms=["HS256"],
        audience=LOCAL_AUDIENCE,
        issuer=LOCAL_ISSUER,
    )
    assert payload["cognito:groups"] == ["analyst"]
    assert payload["custom:organization_id"] == ORG_ID
    assert payload["custom:department"] == "Finance"
    assert payload["token_use"] == "id"


async def test_token_signed_with_another_secret_is_rejected(
    provider: LocalIdentityProvider,
) -> None:
    other = LocalIdentityProvider(secret="a-completely-different-secret-thats-long-enough")
    with pytest.raises(TokenVerificationError):
        await provider.verify(mint(other))


async def test_expired_token_is_rejected() -> None:
    provider = LocalIdentityProvider(secret=SECRET, token_ttl_seconds=-3600)
    with pytest.raises(TokenVerificationError, match="expired"):
        await provider.verify(mint(provider))


async def test_unsigned_token_is_rejected(provider: LocalIdentityProvider) -> None:
    now = datetime.now(UTC)
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "sub": USER_ID,
        "email": "attacker@evil.test",
        "cognito:groups": ["admin"],
        "custom:organization_id": ORG_ID,
        "token_use": "id",
        "iss": LOCAL_ISSUER,
        "aud": LOCAL_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    forged = (
        b64(json.dumps(header).encode()) + b"." + b64(json.dumps(payload).encode()) + b"."
    ).decode()
    with pytest.raises(TokenVerificationError, match="algorithm"):
        await provider.verify(forged)


async def test_wrong_issuer_is_rejected(provider: LocalIdentityProvider) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": USER_ID,
        "email": "a@b.test",
        "cognito:groups": ["analyst"],
        "custom:organization_id": ORG_ID,
        "token_use": "id",
        "iss": "https://evil.test",
        "aud": LOCAL_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(TokenVerificationError):
        await provider.verify(token)


async def test_access_token_use_is_rejected(provider: LocalIdentityProvider) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": USER_ID,
        "email": "a@b.test",
        "cognito:groups": ["analyst"],
        "custom:organization_id": ORG_ID,
        "token_use": "access",
        "iss": LOCAL_ISSUER,
        "aud": LOCAL_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(TokenVerificationError, match="id token"):
        await provider.verify(token)


async def test_tampered_payload_is_rejected(provider: LocalIdentityProvider) -> None:
    """Escalate analyst to admin by editing the payload and keeping the signature."""
    header_b64, payload_b64, signature_b64 = mint(provider).split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["cognito:groups"] = ["admin"]
    tampered_payload = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    )
    with pytest.raises(TokenVerificationError):
        await provider.verify(f"{header_b64}.{tampered_payload}.{signature_b64}")


def test_hmac_is_the_only_thing_protecting_the_payload() -> None:
    """Sanity check on the test above: the signature really is over the payload."""
    provider = LocalIdentityProvider(secret=SECRET)
    header_b64, payload_b64, signature_b64 = mint(provider).split(".")
    expected = base64.urlsafe_b64encode(
        hmac.new(
            SECRET.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
        ).digest()
    ).rstrip(b"=")
    assert expected.decode() == signature_b64
