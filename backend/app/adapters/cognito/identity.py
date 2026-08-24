"""Cognito token verification.

Validates every property that matters, in an order chosen so the cheapest checks
run first:

1. **Header parse and ``kid`` lookup** — no crypto yet.
2. **Signature** against the matching JWKS key (RS256).
3. **Issuer** — must be exactly this user pool. A token from *some* Cognito pool
   is not a token from *your* Cognito pool, and this is the check people forget.
4. **Audience** — must be this app client.
5. **``token_use``** — must be ``id``. PyJWT does not know this claim exists;
   without an explicit check, an access token from the same pool would sail
   through signature, issuer and audience validation.
6. **Expiry and not-before** — PyJWT, with a small leeway for clock skew.

**JWKS caching.** Fetching the key set on every request would add a network
round trip to every call and hammer Cognito. It is cached with a TTL. Critically,
an *unknown* ``kid`` forces one immediate refetch: that is what makes key
rotation a non-event instead of a total outage until the TTL happens to lapse.
The refetch is rate-limited so a stream of tokens with garbage ``kid`` values
cannot be turned into a request amplifier against Cognito.

**Why the ID token rather than the access token** (ADR-0015): the application
needs ``custom:organization_id``, and Cognito access tokens do not carry custom
attributes without a pre-token-generation Lambda. Using ID tokens as API
credentials is a known compromise; the hardening path is documented in the ADR
and scheduled for Phase 8.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.core.logging import get_logger
from app.models.identity import VerifiedClaims
from app.ports.identity_provider import IdentityProvider, TokenVerificationError

logger = get_logger(__name__)

JWKS_CACHE_SECONDS = 3600
JWKS_REFETCH_COOLDOWN_SECONDS = 30
CLOCK_SKEW_LEEWAY_SECONDS = 30

JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class CognitoIdentityProvider(IdentityProvider):
    def __init__(
        self,
        *,
        user_pool_id: str,
        client_id: str,
        region: str,
        jwks_fetcher: JwksFetcher | None = None,
    ) -> None:
        self._user_pool_id = user_pool_id
        self._client_id = client_id
        self._region = region
        # Injectable so tests can supply a key set generated in-process instead
        # of reaching for the network. A verifier that can only be tested
        # against live AWS is a verifier that does not get tested.
        self._fetch_jwks = jwks_fetcher or self._fetch_jwks_over_http

        self._keys: dict[str, Any] = {}
        # -inf, not 0.0, for both timestamps. time.monotonic() counts from boot,
        # so on a host that has been up for less than the cache TTL a 0.0
        # sentinel reads as "fetched recently" and the key set is never loaded
        # at all. Same class of bug for the cooldown. Using -inf makes "never"
        # mean never on any machine, whatever its uptime.
        self._fetched_at: float = float("-inf")
        self._last_refetch_attempt: float = float("-inf")

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self._region}.amazonaws.com/{self._user_pool_id}"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    async def _fetch_jwks_over_http(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self.jwks_uri)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

    async def _load_keys(self, *, force: bool = False) -> None:
        now = time.monotonic()
        fresh = bool(self._keys) and (now - self._fetched_at) < JWKS_CACHE_SECONDS

        if fresh and not force:
            return
        if force:
            if (now - self._last_refetch_attempt) < JWKS_REFETCH_COOLDOWN_SECONDS:
                # Cooldown: unknown-kid refetches are rate-limited so a flood of
                # forged tokens cannot be amplified into traffic at Cognito.
                return
            # Armed only on a forced refetch. Arming it on an ordinary TTL load
            # would block the first rotation-triggered refetch for the whole
            # cooldown window after every cold start — a 401 storm during key
            # rotation, which is precisely when the refetch must work.
            self._last_refetch_attempt = now

        try:
            document = await self._fetch_jwks()
        except Exception as exc:
            logger.error("jwks_fetch_failed", error=str(exc))
            if not self._keys:
                raise TokenVerificationError(f"could not load JWKS: {exc}") from exc
            # Stale keys beat no keys: a Cognito blip should not log everyone out.
            return

        self._keys = {key["kid"]: key for key in document.get("keys", [])}
        self._fetched_at = now
        logger.info("jwks_loaded", key_count=len(self._keys))

    async def _signing_key(self, kid: str) -> Any:
        await self._load_keys()
        if kid not in self._keys:
            # Probably key rotation. Refetch once before rejecting.
            await self._load_keys(force=True)
        if kid not in self._keys:
            raise TokenVerificationError(f"unknown key id {kid!r}")
        return RSAAlgorithm.from_jwk(self._keys[kid])

    async def verify(self, token: str) -> VerifiedClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(f"malformed token header: {exc}") from exc

        # Algorithm is pinned first, before anything else in the header is
        # trusted. This defeats alg-confusion attacks — `alg: none`, or an
        # HS256 token signed with the RSA public key as the HMAC secret —
        # regardless of what the rest of the header claims.
        if header.get("alg") != "RS256":
            raise TokenVerificationError(f"unexpected algorithm {header.get('alg')!r}")

        kid = header.get("kid")
        if not kid:
            raise TokenVerificationError("token header carries no kid")

        key = await self._signing_key(kid)

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self.issuer,
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVerificationError("token expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenVerificationError("audience mismatch") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenVerificationError("issuer mismatch") from exc
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(f"token rejected: {exc}") from exc

        # PyJWT has no opinion about token_use — this check is ours to make.
        token_use = payload.get("token_use")
        if token_use != "id":
            raise TokenVerificationError(f"expected an id token, got {token_use!r}")

        email = payload.get("email")
        if not email:
            raise TokenVerificationError("token carries no email claim")

        return VerifiedClaims(
            subject=str(payload["sub"]),
            email=str(email),
            groups=list(payload.get("cognito:groups", [])),
            organization_id=payload.get("custom:organization_id"),
            department=payload.get("custom:department"),
            issuer=str(payload["iss"]),
            expires_at=datetime.fromtimestamp(float(payload["exp"]), tz=UTC),
            token_use=str(token_use),
        )
