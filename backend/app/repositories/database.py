"""Database engine and the tenant-scoped session.

The important function here is :func:`tenant_session`. It opens a transaction
and runs::

    SET LOCAL app.current_organization_id = '<uuid>';

Every Row-Level Security policy in the schema reads that setting. The result is
that a query which forgets its ``WHERE organization_id = ...`` clause returns
**zero rows from other tenants** rather than leaking them.

Why this is worth the effort (Phase 0, §I.2): application-level filtering is one
layer. It is a layer written by hand, in many places, by someone in a hurry. RLS
is a second layer enforced by the database on every statement regardless of what
the application code says. The difference between "I filter by tenant" and "the
database refuses to return other tenants' rows even when my code is wrong" is
the difference between a claim and a control.

``SET LOCAL`` (not ``SET``) is load-bearing: it is scoped to the transaction, so
a pooled connection returned to the pool cannot carry one tenant's identity into
the next request. Getting this wrong is the classic RLS-with-connection-pooling
bug.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TENANT_SETTING = "app.current_organization_id"


class Base(DeclarativeBase):
    """Declarative base for every ORM table."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is None:
        resolved = settings or get_settings()
        _engine = create_async_engine(
            resolved.database.async_dsn,
            echo=resolved.database.echo_sql,
            pool_size=resolved.database.pool_size,
            max_overflow=2,
            pool_pre_ping=True,  # Aurora scale-to-zero drops idle connections
        )
        logger.info(
            "database_engine_created",
            host=resolved.database.host,
            database=resolved.database.name,
        )
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(settings),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def dispose_engine() -> None:
    """Close pooled connections on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("database_engine_disposed")
    _engine = None
    _session_factory = None


@asynccontextmanager
async def tenant_session(organization_id: UUID) -> AsyncIterator[AsyncSession]:
    """A transaction scoped to one tenant, with RLS active.

    Commits on success, rolls back on any exception.
    """
    factory = get_session_factory()
    async with factory() as session, session.begin():
        # Parameter binding on a SET statement is not supported by PostgreSQL,
        # so set_config() is used instead. `true` = transaction-local, the
        # equivalent of SET LOCAL. This is the safe way to do it: the value
        # travels as a bound parameter, not as concatenated SQL.
        await session.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": TENANT_SETTING, "value": str(organization_id)},
        )
        yield session


@asynccontextmanager
async def untenanted_session() -> AsyncIterator[AsyncSession]:
    """A session with no tenant context set.

    Named for what it is rather than for what it might be assumed to be. This is
    **not** an escape hatch: because migration 0001 uses ``FORCE ROW LEVEL
    SECURITY``, the policies apply to the table owner too, so this session sees
    *nothing* in any RLS-protected table and cannot write to one. It is useful
    only for tables with no policy — currently ``organizations``.

    Discovered the hard way in Phase 2: just-in-time user provisioning was
    written against this session and was refused by the database, which is the
    correct outcome. Provisioning now runs inside the target tenant's own
    context (see :class:`app.services.identity.IdentityService`), so a bug
    cannot provision a user into the wrong organization — the database rejects
    it rather than trusting the application to have got it right.

    The production pattern when a genuine cross-tenant write is needed (bulk
    administration, data migration) is a separate database role holding
    ``BYPASSRLS``, connected to explicitly and audited. That is deliberately not
    wired up here: nothing in the application needs it yet, and a bypass role
    that exists is a bypass role that eventually gets used.
    """
    factory = get_session_factory()
    async with factory() as session, session.begin():
        yield session


async def check_database_health() -> bool:
    try:
        async with untenanted_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("database_health_check_failed", error=str(exc))
        return False
