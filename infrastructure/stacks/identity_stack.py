"""Cognito user pool, custom attributes, groups and app client.

Three details here are load-bearing and easy to get wrong:

**Custom attributes are immutable after creation.** ``custom:organization_id``
cannot be renamed, retyped or removed once the pool exists — changing it means
building a new pool and migrating every user. It is defined here at the start
for exactly that reason. It is also marked mutable so an administrator can move
someone between organizations without deleting the account.

**Group names must match ``GROUP_TO_ROLE`` in the application.** The mapping in
``app/security/claims.py`` is what turns a Cognito group into a role. A typo
here produces a user who authenticates successfully and is then rejected for
carrying no recognised role group — which looks like a broken login rather than
a naming mismatch.

**The pool survives a stack teardown.** ``RemovalPolicy.RETAIN`` on anything but
a throwaway environment: deleting a user pool deletes every account in it, and
``cdk destroy`` is one keystroke away from ``cdk deploy``.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from constructs import Construct

# Must equal the keys of GROUP_TO_ROLE in app/security/claims.py.
ROLE_GROUPS: tuple[tuple[str, str, int], ...] = (
    ("analyst", "Upload documents, query the corpus, create investigations", 30),
    ("manager", "Review risks, decide approvals, see team analytics", 20),
    ("admin", "Manage users, configure the platform, read audit logs", 10),
)


class IdentityStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        is_ephemeral = environment_name == "dev"

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"cloudguard-{environment_name}",
            self_sign_up_enabled=False,  # accounts are provisioned, never self-served
            sign_in_aliases=cognito.SignInAliases(email=True, username=False),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
                fullname=cognito.StandardAttribute(required=False, mutable=True),
            ),
            custom_attributes={
                # Immutable in shape once the pool exists — see module docstring.
                # `mutable=True` refers to the value, so a user can be moved
                # between organizations without recreating the account.
                "organization_id": cognito.StringAttribute(
                    min_len=36, max_len=36, mutable=True
                ),
                "department": cognito.StringAttribute(
                    min_len=1, max_len=120, mutable=True
                ),
            },
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=cdk.Duration.days(3),
            ),
            # Threat protection replaced the deprecated advanced_security_mode
            # when Cognito moved to feature plans. AUDIT records risk signals
            # (impossible travel, credential stuffing) without blocking anyone —
            # visibility without the enforcement tier's cost or its false
            # positives during a demo.
            standard_threat_protection_mode=(
                cognito.StandardThreatProtectionMode.AUDIT_ONLY
            ),
            feature_plan=cognito.FeaturePlan.PLUS,
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            removal_policy=(
                cdk.RemovalPolicy.DESTROY if is_ephemeral else cdk.RemovalPolicy.RETAIN
            ),
        )

        for name, description, precedence in ROLE_GROUPS:
            cognito.CfnUserPoolGroup(
                self,
                f"Group{name.capitalize()}",
                user_pool_id=self.user_pool.user_pool_id,
                group_name=name,
                description=description,
                # Precedence only matters if a user ends up in several groups.
                # The application refuses that case outright (claims.py fails
                # closed on ambiguity), so this is documentation of intent
                # rather than a resolution mechanism.
                precedence=precedence,
            )

        self.user_pool_client = cognito.UserPoolClient(
            self,
            "WebClient",
            user_pool=self.user_pool,
            user_pool_client_name=f"cloudguard-web-{environment_name}",
            # No client secret: a static SPA cannot keep one. Security comes
            # from PKCE plus exact-match callback URLs, not from a shared secret
            # shipped to every browser.
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=False),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                    # Implicit grant puts tokens in the URL, where they land in
                    # browser history and server logs. Off, deliberately.
                    implicit_code_grant=False,
                ),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=self._callback_urls(environment_name),
                logout_urls=self._callback_urls(environment_name),
            ),
            prevent_user_existence_errors=True,  # do not confirm which emails exist
            access_token_validity=cdk.Duration.hours(1),
            id_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.days(7),
            enable_token_revocation=True,
        )

        self.domain = self.user_pool.add_domain(
            "HostedUiDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                # Must be globally unique across all AWS accounts. The account
                # id keeps it unique without leaking anything useful.
                domain_prefix=f"cloudguard-{environment_name}-{self.account[-6:]}"
            ),
        )

        for key, value, description in (
            ("UserPoolId", self.user_pool.user_pool_id, "COGNITO_USER_POOL_ID"),
            ("ClientId", self.user_pool_client.user_pool_client_id, "COGNITO_CLIENT_ID"),
            (
                "HostedUiDomain",
                f"{self.domain.domain_name}.auth.{self.region}.amazoncognito.com",
                "COGNITO_HOSTED_UI_DOMAIN",
            ),
            (
                "Issuer",
                f"https://cognito-idp.{self.region}.amazonaws.com/"
                f"{self.user_pool.user_pool_id}",
                "Token issuer the API validates against",
            ),
        ):
            cdk.CfnOutput(self, key, value=value, description=description)

    @staticmethod
    def _callback_urls(environment_name: str) -> list[str]:
        """Exact-match redirect targets.

        Cognito matches these exactly — no wildcards, no prefix matching. That
        strictness is the control that stops an open redirect from turning into
        token theft, so the localhost entry must not survive into production.
        """
        if environment_name == "dev":
            return ["http://localhost:3000/login/"]
        return [f"https://{environment_name}.cloudguard.example/login/"]
