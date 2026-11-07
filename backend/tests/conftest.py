"""Shared test fixtures.

**Changed in Phase 2.** ``dev_header`` is gone. Tests now mint real tokens
through :class:`LocalIdentityProvider` and send them as bearer credentials, so
the test suite exercises the same verification path production does.

``get_principal`` provisions the caller into PostgreSQL, so API tests that
authenticate need a database. Tests that only need token verification or
authorization logic do not, and live in ``tests/unit``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from app.adapters.local.identity import LocalIdentityProvider
from app.core.config import Environment, Settings
from app.models.enums import Role
from app.models.principal import Principal
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")

TEST_SECRET = "test-only-signing-secret-of-at-least-32-bytes"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment=Environment.LOCAL,
        log_format="json",
        identity_provider="local",
        local_auth={"secret": TEST_SECRET},
        llm_provider="mock",
        vector_store="memory",
        document_store="memory",
        event_publisher="memory",
    )


@pytest.fixture
def token_signer() -> LocalIdentityProvider:
    return LocalIdentityProvider(secret=TEST_SECRET)


@pytest.fixture
def analyst() -> Principal:
    return Principal(
        user_id=UUID("33333333-3333-4333-8333-333333333333"),
        organization_id=ORG_A,
        role=Role.ANALYST,
        email="analyst@acme.test",
        department="Finance",
    )


@pytest.fixture
def manager() -> Principal:
    return Principal(
        user_id=UUID("44444444-4444-4444-8444-444444444444"),
        organization_id=ORG_A,
        role=Role.MANAGER,
        email="manager@acme.test",
        department="Audit",
    )


@pytest.fixture
def admin() -> Principal:
    return Principal(
        user_id=UUID("55555555-5555-4555-8555-555555555555"),
        organization_id=ORG_A,
        role=Role.ADMIN,
        email="admin@acme.test",
        department="IT",
    )


def bearer(signer: LocalIdentityProvider, principal: Principal) -> dict[str, str]:
    """Mint a real token for a principal and format it as an Authorization header."""
    token = signer.mint(
        subject=str(principal.user_id),
        email=principal.email,
        groups=[principal.role.value],
        organization_id=str(principal.organization_id),
        department=principal.department,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app(settings)) as test_client:
        yield test_client


def requires_database() -> bool:
    return os.getenv("RUN_DB_TESTS") == "1"


skip_without_database = pytest.mark.skipif(
    not requires_database(),
    reason="set RUN_DB_TESTS=1 with PostgreSQL running",
)


@asynccontextmanager
async def database_admin_session() -> AsyncIterator[AsyncSession]:
    """Test-only control-plane session for prerequisite data and FK checks.

    Runtime integration tests connect through cloudguard_app. Fixtures must not
    expand that role merely to create organizations/framework definitions or
    exercise destructive schema behavior that the application never performs.
    """
    url = URL.create(
        "postgresql+asyncpg",
        username=os.getenv("TEST_DB_ADMIN_USER", "cloudguard"),
        password=os.getenv("TEST_DB_ADMIN_PASSWORD", "cloudguard"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "cloudguard"),
    )
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session, session.begin():
            yield session
    finally:
        await engine.dispose()


def random_org() -> UUID:
    return uuid4()
