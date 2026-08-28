"""Row-Level Security must block cross-tenant reads at the database.

Marked ``integration`` because it needs a real PostgreSQL: SQLite has no RLS, so
faking this test would prove nothing at all. Run it with::

    docker compose up -d postgres
    cd backend && alembic upgrade head
    RUN_DB_TESTS=1 pytest -m integration

The final test here is the important one. It runs a **deliberately buggy
query** — a bare ``SELECT * FROM documents`` with no tenant filter, exactly the
mistake a tired developer makes — and asserts that the database still returns
only the current tenant's rows. That is what separates "we filter by tenant"
from a control you can point at in an interview.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from app.models.enums import Role
from app.repositories.database import (
    dispose_engine,
    tenant_session,
    untenanted_session,
)
from sqlalchemy import text

pytestmark = pytest.mark.integration

ORG_A = uuid4()
ORG_B = uuid4()
USER_A = uuid4()
USER_B = uuid4()
DOC_A = uuid4()
DOC_B = uuid4()
CHUNK_A = uuid4()
CHUNK_B = uuid4()


def _skip_without_database() -> None:
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 with PostgreSQL running")


@pytest.fixture(scope="module", autouse=True)
async def seed_two_tenants() -> object:
    _skip_without_database()

    # `organizations` has no RLS policy, so it seeds without a tenant context.
    async with untenanted_session() as session:
        for org_id, label in ((ORG_A, "acme"), (ORG_B, "globex")):
            # Slug carries the run-specific id. A fixed slug collides with the
            # seed data's unique constraint, ON CONFLICT DO NOTHING swallows the
            # insert, and the failure surfaces later as a confusing foreign-key
            # error rather than as the duplicate it actually is.
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (:id, :name, :slug)"
                ),
                {
                    "id": org_id,
                    "name": label.title(),
                    "slug": f"{label}-{org_id.hex[:12]}",
                },
            )

    # Everything else goes in inside its own tenant context. Note that the test
    # setup itself cannot cheat past the policy — that is the point.
    for org_id, user_id, email, doc_id, chunk_id, name in (
        (
            ORG_A,
            USER_A,
            "a@acme.test",
            DOC_A,
            CHUNK_A,
            "acme-vendor-policy.pdf",
        ),
        (
            ORG_B,
            USER_B,
            "b@globex.test",
            DOC_B,
            CHUNK_B,
            "globex-vendor-policy.pdf",
        ),
    ):
        async with tenant_session(org_id) as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, organization_id, email, role) "
                    "VALUES (:id, :org, :email, CAST(:role AS role)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": user_id,
                    "org": org_id,
                    "email": f"{org_id.hex[:8]}-{email}",
                    "role": Role.ANALYST.value,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO documents "
                    "(id, organization_id, filename, storage_key, content_type, uploader_id) "
                    "VALUES (:id, :org, :name, :key, 'application/pdf', :uploader) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": doc_id,
                    "org": org_id,
                    "name": name,
                    "key": f"org/{org_id}/documents/{doc_id}/{name}",
                    "uploader": user_id,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(id, organization_id, document_id, chunk_index, content, token_count) "
                    "VALUES (:id, :org, :document, 0, :content, 3) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": chunk_id,
                    "org": org_id,
                    "document": doc_id,
                    "content": f"{name} extracted text",
                },
            )

    yield None
    await dispose_engine()


async def test_tenant_sees_only_its_own_documents() -> None:
    async with tenant_session(ORG_A) as session:
        rows = (await session.execute(text("SELECT id FROM documents"))).scalars().all()
    assert {str(row) for row in rows} == {str(DOC_A)}


async def test_other_tenant_sees_only_its_own_documents() -> None:
    async with tenant_session(ORG_B) as session:
        rows = (await session.execute(text("SELECT id FROM documents"))).scalars().all()
    assert {str(row) for row in rows} == {str(DOC_B)}


async def test_targeting_another_tenants_row_by_id_returns_nothing() -> None:
    """Knowing the primary key is not sufficient. The policy still applies."""
    async with tenant_session(ORG_A) as session:
        result = await session.execute(
            text("SELECT id FROM documents WHERE id = :id"), {"id": DOC_B}
        )
    assert result.scalar_one_or_none() is None


async def test_insert_into_another_tenant_is_rejected() -> None:
    """WITH CHECK stops writes as well as reads."""
    with pytest.raises(Exception, match="policy"):
        async with tenant_session(ORG_A) as session:
            await session.execute(
                text(
                    "INSERT INTO documents (id, organization_id, filename, "
                    "storage_key, content_type, uploader_id) "
                    "VALUES (:id, :org, 'smuggled.pdf', 'k', 'application/pdf', :uploader)"
                ),
                {"id": uuid4(), "org": ORG_B, "uploader": USER_B},
            )


async def test_a_query_that_forgets_the_tenant_filter_is_still_safe() -> None:
    """The whole point of RLS.

    This query has no WHERE clause. It is the bug. The database contains rows
    for two tenants. Only one tenant's rows come back.
    """
    async with tenant_session(ORG_A) as session:
        rows = (
            await session.execute(text("SELECT organization_id FROM documents"))
        ).scalars().all()
    assert {str(row) for row in rows} == {str(ORG_A)}


async def test_a_session_without_a_tenant_sees_nothing() -> None:
    """Fail closed: no tenant set means no rows, not all rows.

    This holds even though the connecting role owns the tables, because
    migration 0001 uses FORCE ROW LEVEL SECURITY. Plain ENABLE would exempt the
    owner and this assertion would fail — which is exactly the silent
    misconfiguration the FORCE keyword prevents.
    """
    async with untenanted_session() as session:
        rows = (await session.execute(text("SELECT id FROM documents"))).scalars().all()
    assert rows == []


async def test_tenant_sees_only_its_own_document_chunks() -> None:
    async with tenant_session(ORG_A) as session:
        rows = (
            await session.execute(text("SELECT id FROM document_chunks"))
        ).scalars().all()
    assert {str(row) for row in rows} == {str(CHUNK_A)}


async def test_targeting_another_tenants_chunk_by_id_returns_nothing() -> None:
    async with tenant_session(ORG_A) as session:
        result = await session.execute(
            text("SELECT id FROM document_chunks WHERE id = :id"),
            {"id": CHUNK_B},
        )
    assert result.scalar_one_or_none() is None


async def test_insert_chunk_into_another_tenant_is_rejected() -> None:
    with pytest.raises(Exception, match="policy"):
        async with tenant_session(ORG_A) as session:
            await session.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(id, organization_id, document_id, chunk_index, content, token_count) "
                    "VALUES (:id, :org, :document, 99, 'smuggled chunk', 2)"
                ),
                {
                    "id": uuid4(),
                    "org": ORG_B,
                    "document": DOC_B,
                },
            )


async def test_untenanted_session_sees_no_document_chunks() -> None:
    async with untenanted_session() as session:
        rows = (
            await session.execute(text("SELECT id FROM document_chunks"))
        ).scalars().all()
    assert rows == []

async def test_seeding_cannot_write_into_the_wrong_tenant() -> None:
    """The WITH CHECK clause constrains writes, including from trusted code."""
    with pytest.raises(Exception, match="policy"):
        async with tenant_session(ORG_A) as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, organization_id, email, role) "
                    "VALUES (:id, :org, 'smuggled@globex.test', CAST('analyst' AS role))"
                ),
                {"id": uuid4(), "org": ORG_B},
            )
