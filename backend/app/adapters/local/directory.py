"""The local development user roster.

**Why this is a Python fixture and not a database query** (ADR-0016).

Logging in requires finding a user by email. ``users`` is under Row-Level
Security, and RLS needs a tenant context — but establishing the tenant context
is what logging in is *for*. Circular.

The first attempt at breaking that circle was a ``SECURITY DEFINER`` view. It
does not work, and the reason is worth remembering: migration 0001 uses ``FORCE
ROW LEVEL SECURITY``, which applies the policy to the table owner as well. The
view runs as its owner, the owner is still subject to the policy, and the view
returns zero rows. Verified against real PostgreSQL, not assumed.

The remaining database-level option is a second role holding ``BYPASSRLS``. That
is the right production pattern for genuine cross-tenant administration, and it
is deliberately not introduced here for a development convenience: a bypass role
that exists is a bypass role that eventually gets used for something else.

So the roster lives in code. Production never needs it — Cognito holds its own
user directory and never queries this table to authenticate. It is imported by
``scripts/seed_data.py`` as well, so the seeded rows and the local logins are
guaranteed to describe the same people rather than drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

# Fixed ids so tokens, seed data and documentation all agree, and so a
# developer can hardcode one in a curl command without looking it up.
ACME = UUID("11111111-1111-4111-8111-111111111111")
GLOBEX = UUID("22222222-2222-4222-8222-222222222222")


@dataclass(frozen=True, slots=True)
class LocalUser:
    user_id: UUID
    organization_id: UUID
    email: str
    role: str
    department: str | None


@dataclass(frozen=True, slots=True)
class LocalOrganization:
    organization_id: UUID
    name: str
    slug: str


LOCAL_ORGANIZATIONS: tuple[LocalOrganization, ...] = (
    LocalOrganization(ACME, "Acme Manufacturing", "acme"),
    LocalOrganization(GLOBEX, "Globex Industrial", "globex"),
)

# Two tenants on purpose. A single-tenant fixture makes cross-tenant leaks
# invisible, because there is nothing to leak. With two, every manual poke at
# the API is also an isolation check.
LOCAL_USERS: tuple[LocalUser, ...] = (
    LocalUser(
        UUID("33333333-3333-4333-8333-333333333333"),
        ACME,
        "analyst@acme.test",
        "analyst",
        "Finance",
    ),
    LocalUser(
        UUID("44444444-4444-4444-8444-444444444444"),
        ACME,
        "manager@acme.test",
        "manager",
        "Audit",
    ),
    LocalUser(
        UUID("55555555-5555-4555-8555-555555555555"),
        ACME,
        "admin@acme.test",
        "admin",
        "IT",
    ),
    LocalUser(
        UUID("66666666-6666-4666-8666-666666666666"),
        GLOBEX,
        "analyst@globex.test",
        "analyst",
        "Ops",
    ),
)

_BY_EMAIL: dict[str, LocalUser] = {user.email.lower(): user for user in LOCAL_USERS}


def find_by_email(email: str) -> LocalUser | None:
    return _BY_EMAIL.get(email.strip().lower())
