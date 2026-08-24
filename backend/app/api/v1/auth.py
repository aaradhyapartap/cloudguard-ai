"""Authentication endpoints.

``/auth/config`` is public: it tells the frontend where to send users to log in.
A Cognito domain, client id and issuer are not secrets — they appear in the
browser URL bar during login. Serving them from the API instead of baking them
into the SPA build means rotating a user pool is a redeploy of the backend, not
a rebuild of the frontend.

``/auth/dev-login`` is **registered only when the local identity provider is
active**. Not disabled by a flag check inside the handler — absent from the
routing table entirely, so there is no endpoint to probe, and it does not appear
in the OpenAPI schema. A route that does not exist cannot be misconfigured into
existence.

There is no logout endpoint, and that is not an omission. Bearer tokens are
stateless: the server cannot revoke one it has already signed. Logout is the
client discarding its tokens plus, for Cognito, a redirect to the Hosted UI
logout endpoint that clears the pool session. Server-side revocation would
require a token denylist — a real option, and the right one if session
termination ever becomes a compliance requirement here.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.adapters.local.directory import find_by_email
from app.adapters.local.identity import LocalIdentityProvider
from app.api.deps import ContainerDep
from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.models.identity import AuthConfig, TokenResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfig, summary="Login configuration")
async def auth_config(container: ContainerDep) -> AuthConfig:
    settings = container.settings
    is_local = settings.identity_provider == "local"
    return AuthConfig(
        provider=settings.identity_provider,
        issuer=container.identity.issuer,
        hosted_ui_domain=None if is_local else settings.cognito.hosted_ui_domain,
        client_id=None if is_local else settings.cognito.client_id,
        scopes=[] if is_local else ["openid", "email", "profile"],
        local_login_enabled=is_local,
    )


class DevLoginRequest(BaseModel):
    # A plain bounded string, not EmailStr. EmailStr rejects RFC 2606
    # special-use domains (.test, .invalid, .localhost) — which are exactly the
    # domains sample data should use, since they can never resolve to a real
    # mailbox. Cognito is the authority on email validity in production; this
    # endpoint only looks up a row that was already seeded.
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")


def build_dev_login_router(provider: LocalIdentityProvider) -> APIRouter:
    """Router mounted only when the local identity provider is active."""
    dev = APIRouter(prefix="/auth", tags=["auth"])

    @dev.post("/dev-login", response_model=TokenResponse, summary="Local login")
    async def dev_login(payload: DevLoginRequest) -> TokenResponse:
        """Mint a development token for a user in the local roster.

        No password: this exists so the token *pipeline* can be exercised
        offline, not to model credential verification. Cognito owns that.

        The roster is a Python fixture rather than a database lookup — see
        app/adapters/local/directory.py for why a query cannot work here.
        """
        user = find_by_email(payload.email)
        if user is None:
            logger.warning("dev_login_unknown_user", email=payload.email)
            raise AuthenticationError(
                "No such local user. Run scripts/seed_data.py, or check "
                "app/adapters/local/directory.py for the roster."
            )

        token = provider.mint(
            subject=str(user.user_id),
            email=user.email,
            groups=[user.role],
            organization_id=str(user.organization_id),
            department=user.department,
        )
        return TokenResponse(access_token=token, expires_in=provider.token_ttl_seconds)

    return dev
