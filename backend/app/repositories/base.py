"""Repository base.

Every repository is constructed with a :class:`Principal` and a session that is
already tenant-scoped. There is no constructor that omits the principal, so
there is no way to write a repository method that accidentally queries across
tenants — the type signature refuses.

This is the application-level half of the isolation story. The database half is
the RLS policy in migration 0001. Both are present because either alone is one
mistake away from a cross-tenant leak.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.principal import Principal
from app.repositories.database import Base


class TenantRepository[ModelT: Base]:
    """Base for repositories over a table carrying ``organization_id``."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self._session = session
        self._principal = principal

    @property
    def organization_id(self) -> UUID:
        return self._principal.organization_id

    def _scoped(self) -> Select[tuple[ModelT]]:
        """A SELECT already filtered to this tenant.

        Repositories build from this, never from a bare ``select(Model)``.
        """
        return select(self.model).where(
            self.model.organization_id == self.organization_id  # type: ignore[attr-defined]
        )

    async def get(self, entity_id: UUID) -> ModelT | None:
        result = await self._session.execute(
            self._scoped().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(self.model)
            .where(self.model.organization_id == self.organization_id)  # type: ignore[attr-defined]
        )
        return int(result.scalar_one())

    async def list_page(self, *, limit: int = 25, offset: int = 0) -> list[ModelT]:
        result = await self._session.execute(
            self._scoped()
            .order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        await self._session.flush()
        return entity
