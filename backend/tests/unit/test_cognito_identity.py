"""Cognito token verification.

Every negative case here is a real attack or a real outage:

* wrong issuer      — a token from a *different* Cognito pool. Valid signature,
                      valid structure, wrong tenant entirely. The check people
                      forget.
* wrong audience    — a token minted for another app client in the same pool.
* access token      — same pool, same signature, wrong ``token_use``. PyJWT has
                      no opinion about this claim; if we do not check it, it is
                      not checked.
* ``alg: none``     — the classic JWT forgery.
* HS256 substitution — algorithm confusion, signing with the public key as an
                      HMAC secret.
* unknown ``kid``   — key rotation. Must trigger one refetch, not an outage.
* expired           — with clock-skew leeway applied.

The JWKS is generated in-process and injected, so the verifier is fully tested
with no AWS account and no network. A verifier that can only be exercised
against live Cognito is a verifier nobody exercises.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from app.adapters.cognito.identity import CognitoIdentityProvider
from app.ports.identity_provider import TokenVerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

REGION = "us-east-1"
POOL_ID = "us-east-1_TestPool1"
CLIENT_ID = "1example23clientid"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
ORG_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "33333333-3333-4333-8333-333333333333"


class KeyPair:
    """An in-process RSA key plus the JWKS document Cognito would publish."""

    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(self.private.public_key()))
        jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        self.jwk = jwk

    def sign(self, payload: dict[str, Any]) -> str:
        return jwt.encode(
            payload, self.private, algorithm="RS256", headers={"kid": self.kid}
        )


def base_claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": USER_ID,
        "email": "analyst@acme.test",
        "cognito:groups": ["analyst"],
        "custom:organization_id": ORG_ID,
        "custom:department": "Finance",
        "token_use": "id",
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    return claims


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _forge_hs256(
    *, header: dict[str, Any], payload: dict[str, Any], secret: bytes
) -> str:
    """Build an HS256 JWT by hand, bypassing PyJWT's own guard rails."""
    signing_input = b".".join(
        (
            _b64(json.dumps(header, separators=(",", ":")).encode()),
            _b64(json.dumps(payload, separators=(",", ":")).encode()),
        )
    )
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + _b64(signature)).decode()


@pytest.fixture
def keypair() -> KeyPair:
    return KeyPair("key-1")


@pytest.fixture
def provider(keypair: KeyPair) -> CognitoIdentityProvider:
    async def fetch() -> dict[str, Any]:
        return {"keys": [keypair.jwk]}

    return CognitoIdentityProvider(
        user_pool_id=POOL_ID,
        client_id=CLIENT_ID,
        region=REGION,
        jwks_fetcher=fetch,
    )


async def test_valid_token_yields_claims(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    claims = await provider.verify(keypair.sign(base_claims()))
    assert claims.subject == USER_ID
    assert claims.email == "analyst@acme.test"
    assert claims.groups == ["analyst"]
    assert claims.organization_id == ORG_ID
    assert claims.department == "Finance"


async def test_token_from_another_user_pool_is_rejected(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    other = f"https://cognito-idp.{REGION}.amazonaws.com/us-east-1_OtherPool"
    with pytest.raises(TokenVerificationError, match="issuer"):
        await provider.verify(keypair.sign(base_claims(iss=other)))


async def test_token_for_another_client_is_rejected(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    with pytest.raises(TokenVerificationError, match="audience"):
        await provider.verify(keypair.sign(base_claims(aud="some-other-client")))


async def test_access_token_is_rejected(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    """Correct signature, correct pool, wrong purpose."""
    with pytest.raises(TokenVerificationError, match="id token"):
        await provider.verify(keypair.sign(base_claims(token_use="access")))


async def test_expired_token_is_rejected(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    with pytest.raises(TokenVerificationError, match="expired"):
        await provider.verify(keypair.sign(base_claims(exp=int(past.timestamp()))))


async def test_unsigned_token_is_rejected(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    """`alg: none`, with a legitimate kid so only the algorithm pin can catch it."""
    forged = jwt.encode(
        base_claims(), key="", algorithm="none", headers={"kid": keypair.kid}
    )
    with pytest.raises(TokenVerificationError, match="algorithm"):
        await provider.verify(forged)


async def test_algorithm_confusion_is_rejected(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    """HS256 signed with the public key — the classic algorithm-confusion attack."""
    public_pem = keypair.private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Hand-assembled, because current PyJWT refuses to HMAC-sign with an
    # asymmetric key — a defence it added precisely against this attack. The
    # attacker is not using our library, so the test must not either.
    forged = _forge_hs256(
        header={"alg": "HS256", "typ": "JWT", "kid": keypair.kid},
        payload=base_claims(),
        secret=public_pem,
    )
    with pytest.raises(TokenVerificationError, match="algorithm"):
        await provider.verify(forged)


async def test_token_signed_by_an_unrelated_key_is_rejected(
    provider: CognitoIdentityProvider
) -> None:
    intruder = KeyPair("key-1")  # same kid, different private key
    with pytest.raises(TokenVerificationError):
        await provider.verify(intruder.sign(base_claims()))


async def test_missing_email_claim_is_rejected(
    provider: CognitoIdentityProvider, keypair: KeyPair
) -> None:
    payload = base_claims()
    del payload["email"]
    with pytest.raises(TokenVerificationError, match="email"):
        await provider.verify(keypair.sign(payload))


async def test_key_rotation_triggers_one_refetch() -> None:
    """A new signing key must not require a restart or a TTL expiry."""
    old, new = KeyPair("old-key"), KeyPair("new-key")
    published = [old.jwk]
    fetches = 0

    async def fetch() -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        return {"keys": list(published)}

    provider = CognitoIdentityProvider(
        user_pool_id=POOL_ID, client_id=CLIENT_ID, region=REGION, jwks_fetcher=fetch
    )

    await provider.verify(old.sign(base_claims()))
    assert fetches == 1

    published.append(new.jwk)  # Cognito rotates
    claims = await provider.verify(new.sign(base_claims()))
    assert claims.subject == USER_ID
    assert fetches == 2, "unknown kid should force exactly one refetch"


async def test_jwks_is_cached_across_requests(keypair: KeyPair) -> None:
    fetches = 0

    async def fetch() -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        return {"keys": [keypair.jwk]}

    provider = CognitoIdentityProvider(
        user_pool_id=POOL_ID, client_id=CLIENT_ID, region=REGION, jwks_fetcher=fetch
    )
    for _ in range(5):
        await provider.verify(keypair.sign(base_claims()))
    assert fetches == 1, "JWKS should not be refetched per request"


async def test_unknown_kid_refetch_is_rate_limited(keypair: KeyPair) -> None:
    """A flood of forged tokens must not be amplified into traffic at Cognito."""
    fetches = 0

    async def fetch() -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        return {"keys": [keypair.jwk]}

    provider = CognitoIdentityProvider(
        user_pool_id=POOL_ID, client_id=CLIENT_ID, region=REGION, jwks_fetcher=fetch
    )
    bogus = KeyPair("attacker-kid")
    for _ in range(10):
        with pytest.raises(TokenVerificationError):
            await provider.verify(bogus.sign(base_claims()))

    assert fetches <= 2, f"expected at most one refetch under cooldown, got {fetches}"


async def test_issuer_property_matches_cognito_format(
    provider: CognitoIdentityProvider
) -> None:
    assert provider.issuer == ISSUER
    assert provider.jwks_uri == f"{ISSUER}/.well-known/jwks.json"
