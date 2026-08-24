#!/usr/bin/env python3
"""Seed the local development database.

The roster itself lives in ``app/adapters/local/directory.py`` and is imported
here, so the rows written to PostgreSQL and the users the local login endpoint
accepts describe the same people. Two copies of a fixture list drift; one does
not.

**Two** tenants, not one, deliberately. A single-tenant fixture makes it
impossible to notice a cross-tenant leak by accident, because there is nothing
to leak. With two, every manual poke at the API is also an isolation check.

Idempotent: safe to run repeatedly.

    python scripts/seed_data.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text  # noqa: E402

from app.adapters.local.directory import (  # noqa: E402
    LOCAL_ORGANIZATIONS,
    LOCAL_USERS,
)
from app.repositories.database import (  # noqa: E402
    dispose_engine,
    tenant_session,
    untenanted_session,
)


async def seed() -> None:
    # `organizations` carries no RLS policy — a tenant must be resolvable
    # before a tenant context can exist — so it writes without one.
    async with untenanted_session() as session:
        for org in LOCAL_ORGANIZATIONS:
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (:id, :name, :slug) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": org.organization_id, "name": org.name, "slug": org.slug},
            )

    # One transaction per tenant. Not a limitation to work around — it is the
    # RLS WITH CHECK clause proving that even the seed script cannot write a
    # user into the wrong organization.
    for org in LOCAL_ORGANIZATIONS:
        members = [u for u in LOCAL_USERS if u.organization_id == org.organization_id]
        if not members:
            continue
        async with tenant_session(org.organization_id) as session:
            for user in members:
                await session.execute(
                    text(
                        "INSERT INTO users "
                        "(id, organization_id, email, role, department) "
                        "VALUES (:id, :org, :email, CAST(:role AS role), :dept) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": user.user_id,
                        "org": user.organization_id,
                        "email": user.email,
                        "role": user.role,
                        "dept": user.department,
                    },
                )

    await dispose_engine()


def print_login_help() -> None:
    print("\nSeeded logins. Get a token with:\n")
    for user in LOCAL_USERS:
        print(
            f"  {user.role:<8} curl -s localhost:8000/api/v1/auth/dev-login "
            f"-H 'content-type: application/json' "
            f'-d \'{{"email":"{user.email}"}}\''
        )
    print(
        "\nTwo organizations, deliberately: every manual check is also a "
        "cross-tenant check.\nAcme must never see a Globex row.\n"
    )


if __name__ == "__main__":
    asyncio.run(seed())
    print(
        f"Seeded {len(LOCAL_ORGANIZATIONS)} organizations "
        f"and {len(LOCAL_USERS)} users."
    )
    print_login_help()
