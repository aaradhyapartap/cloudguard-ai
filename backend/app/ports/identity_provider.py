"""Port: token verification and identity resolution.

One port, two adapters (ADR-0014):

* ``cognito`` — verifies RS256 tokens against the user pool's JWKS.
* ``local``   — verifies HS256 tokens signed with a development secret.

Both produce the **same claim shape** and both hand it to the **same** mapping
code in :mod:`app.security.claims`. That is the point of the design: local
development exercises the real token pipeline — parse, verify signature, check
issuer/audience/expiry, map groups to a role, build a ``Principal`` — rather
than a shortcut that bypasses it.

Phase 1 used a plaintext ``x-dev-principal`` header. That header is now gone.
Auth shortcuts that skip the code path they stand in for hide exactly the bugs
you most want to find before deploying.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.identity import VerifiedClaims


class TokenVerificationError(Exception):
    """A token was rejected.

    Carries a ``reason`` for logging that is deliberately never returned to the
    caller. "Signature invalid" versus "token expired" versus "unknown issuer"
    tells an attacker which part of their forgery to fix next; the API says only
    that authentication failed.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class IdentityProvider(ABC):
    @abstractmethod
    async def verify(self, token: str) -> VerifiedClaims:
        """Verify a bearer token and return its claims.

        Implementations must validate, at minimum: signature, issuer, audience,
        expiry, not-before, and intended token use. Raise
        :class:`TokenVerificationError` on any failure — never return partial
        or unverified claims.
        """

    @property
    @abstractmethod
    def issuer(self) -> str:
        """The issuer this provider accepts. Surfaced by ``/auth/config``."""
