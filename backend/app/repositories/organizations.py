"""Organization and user lookups.

These sit slightly outside the tenant-repository pattern because resolving a
user is what *establishes* the tenant. Chicken and egg: they run on an admin
session and are the one place that is allowed to.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.tables import Organization, User


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_organization(session: AsyncSession, organization_id: UUID) -> Organization | None:
    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    return result.scalar_one_or_none()
