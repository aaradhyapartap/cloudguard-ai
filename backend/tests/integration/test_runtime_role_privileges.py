"""Real PostgreSQL checks for the exact cloudguard_app privilege boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.repositories.database import dispose_engine, untenanted_session
from sqlalchemy import text

from conftest import skip_without_database  # type: ignore[import-not-found]

pytestmark = [pytest.mark.integration, skip_without_database]

EXPECTED: dict[str, tuple[bool, bool, bool, bool, bool, bool, bool]] = {
    "organizations": (True, False, False, False, False, False, False),
    "users": (True, True, True, False, False, False, False),
    "documents": (True, True, True, False, False, False, False),
    "document_chunks": (True, True, True, False, False, False, False),
    "compliance_frameworks": (True, False, False, False, False, False, False),
    "compliance_controls": (True, False, False, False, False, False, False),
    "compliance_assessments": (True, True, True, False, False, False, False),
    "control_assessments": (True, True, True, False, False, False, False),
    "evidence_references": (True, True, False, False, False, False, False),
    "assessment_score_snapshots": (True, True, False, False, False, False, False),
    "score_overrides": (True, True, False, False, False, False, False),
    "approvals": (True, True, True, False, False, False, False),
    "security_reviews": (True, True, False, False, False, False, False),
    "approval_audit_events": (True, False, False, False, False, False, False),
}


@pytest.fixture(scope="module", autouse=True)
async def _dispose_runtime_engine_after_module() -> AsyncIterator[None]:
    """Keep pooled asyncpg connections on the event loop that created them."""
    yield
    await dispose_engine()


@pytest.mark.asyncio
async def test_runtime_role_cannot_bypass_database_boundaries() -> None:
    async with untenanted_session() as session:
        role = (
            await session.execute(
                text(
                    """
                    SELECT
                        rolsuper,
                        rolcreatedb,
                        rolcreaterole,
                        rolreplication,
                        rolbypassrls,
                        NOT EXISTS (
                            SELECT 1
                            FROM pg_auth_members
                            WHERE member = pg_roles.oid
                        ) AS has_no_memberships
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            )
        ).one()

    assert role == (False, False, False, False, False, True)


@pytest.mark.asyncio
async def test_runtime_role_has_exact_table_privileges() -> None:
    async with untenanted_session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT
                        current_user,
                        c.relname,
                        has_table_privilege(current_user, c.oid, 'SELECT'),
                        has_table_privilege(current_user, c.oid, 'INSERT'),
                        has_table_privilege(current_user, c.oid, 'UPDATE'),
                        has_table_privilege(current_user, c.oid, 'DELETE'),
                        has_table_privilege(current_user, c.oid, 'TRUNCATE'),
                        has_table_privilege(current_user, c.oid, 'REFERENCES'),
                        has_table_privilege(current_user, c.oid, 'TRIGGER')
                    FROM pg_class AS c
                    JOIN pg_namespace AS n
                        ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p')
                      AND c.relname <> 'alembic_version'
                    ORDER BY c.relname
                    """
                )
            )
        ).all()

    assert {str(row[0]) for row in rows} == {"cloudguard_app"}
    actual = {str(row[1]): tuple(bool(value) for value in row[2:]) for row in rows}
    assert actual == EXPECTED


@pytest.mark.asyncio
async def test_runtime_cannot_use_audit_sequence() -> None:
    async with untenanted_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                        current_user,
                        has_sequence_privilege(
                            current_user,
                            'approval_audit_events_id_seq',
                            'USAGE'
                        ),
                        has_sequence_privilege(
                            current_user,
                            'approval_audit_events_id_seq',
                            'SELECT'
                        ),
                        has_sequence_privilege(
                            current_user,
                            'approval_audit_events_id_seq',
                            'UPDATE'
                        )
                    """
                )
            )
        ).one()

    assert row == ("cloudguard_app", False, False, False)
