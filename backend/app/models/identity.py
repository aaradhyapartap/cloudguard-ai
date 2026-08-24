"""Identity types.

``VerifiedClaims`` is the boundary object between "a token was cryptographically
valid" and "we know who this is". Keeping it distinct from ``Principal`` matters:
claims are what the identity provider asserted, a Principal is what this
application decided to grant. The mapping between them is a policy decision with
its own tests (app/security/claims.py), not an implicit cast.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VerifiedClaims(BaseModel):
    """Claims from a token whose signature, issuer, audience and expiry all passed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    email: str
    groups: list[str] = Field(default_factory=list)
    organization_id: str | None = None
    department: str | None = None
    issuer: str
    expires_at: datetime
    token_use: str


class AuthConfig(BaseModel):
    """What the frontend needs to start a login flow.

    Public by design — a Cognito domain, client id and issuer are not secrets.
    Serving them from the API rather than baking them into the frontend build
    means rotating a user pool does not require rebuilding and redeploying the
    SPA.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    issuer: str
    # Populated for Cognito; null when running against the local provider.
    hosted_ui_domain: str | None = None
    client_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    local_login_enabled: bool = False


class TokenResponse(BaseModel):
    """Issued by the local development login endpoint only."""

    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
