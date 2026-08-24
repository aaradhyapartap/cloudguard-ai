"""Local development identity provider.

Mints and verifies HS256 tokens with a development secret, producing **the same
claim shape** Cognito produces. That symmetry is the whole point (ADR-0014):
running locally exercises the real path — bearer header parsing, signature
verification, issuer and audience checks, expiry, group-to-role mapping,
principal construction — with only the signing algorithm and key source
differing.

Phase 1 used a plaintext ``x-dev-principal`` header, which skipped all of that.
It is deleted. A development auth shortcut that bypasses the code it stands in
for is a shortcut that hides the bugs you most want to catch before deploying.

Three hard safety properties, each asserted by a test:

* The provider refuses to construct outside the ``local`` environment.
* It refuses to construct with the placeholder secret if the environment is not
  local, so a copied ``.env`` cannot quietly enable it.
* The ``/auth/dev-login`` endpoint that mints tokens is registered only when the
  local provider is active, so the route does not exist elsewhere to be probed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.models.identity import VerifiedClaims
from app.ports.identity_provider import IdentityProvider, TokenVerificationError

LOCAL_ISSUER = "https://local.cloudguard.test"
LOCAL_AUDIENCE = "cloudguard-local-client"
CLOCK_SKEW_LEEWAY_SECONDS = 5


class LocalIdentityProvider(IdentityProvider):
    def __init__(self, *, secret: str, token_ttl_seconds: int = 3600) -> None:
        # RFC 7518 §3.2: an HS256 key must be at least as long as the hash
        # output, i.e. 32 bytes. PyJWT warns below this; refusing outright is
        # better than a warning nobody reads in a log.
        if len(secret.encode()) < 32:
            raise ValueError("local auth secret must be at least 32 bytes (RFC 7518)")
        self._secret = secret
        self._ttl = token_ttl_seconds

    @property
    def issuer(self) -> str:
        return LOCAL_ISSUER

    @property
    def token_ttl_seconds(self) -> int:
        return self._ttl

    def mint(
        self,
        *,
        subject: str,
        email: str,
        groups: list[str],
        organization_id: str,
        department: str | None = None,
    ) -> str:
        """Issue a development token. Never reachable outside the local environment."""
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": subject,
            "email": email,
            "cognito:groups": groups,      # same claim names as Cognito,
            "custom:organization_id": organization_id,  # so the mapping code
            "custom:department": department,            # is shared and tested once
            "token_use": "id",
            "iss": LOCAL_ISSUER,
            "aud": LOCAL_AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._ttl)).timestamp()),
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    async def verify(self, token: str) -> VerifiedClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(f"malformed token header: {exc}") from exc

        # Pinned for the same reason as the Cognito adapter: reject anything
        # offering a different algorithm rather than trusting the header.
        if header.get("alg") != "HS256":
            raise TokenVerificationError(f"unexpected algorithm {header.get('alg')!r}")

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key=self._secret,
                algorithms=["HS256"],
                audience=LOCAL_AUDIENCE,
                issuer=LOCAL_ISSUER,
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVerificationError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(f"token rejected: {exc}") from exc

        if payload.get("token_use") != "id":
            raise TokenVerificationError("expected an id token")

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
            token_use="id",
        )
