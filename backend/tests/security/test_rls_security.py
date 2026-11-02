"""PostgreSQL RLS, runtime role privilege, and schema security regressions.

Exercises the real PostgreSQL database using the ``cloudguard_app`` role
(``NOBYPASSRLS``, ``NOSUPERUSER``). When ``REQUIRE_SECURITY_DB_TESTS=1`` is set
(e.g., in blocking CI), missing database connectivity fails closed instead of
silently skipping.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from app.models.enums import Role
from app.repositories.database import (
    dispose_engine,
    tenant_session,
    untenanted_session,
)
from sqlalchemy import text

from conftest import database_admin_session  # type: ignore[import-not-found]


def require_security_database() -> None:
    """Ensure PostgreSQL-backed security regressions never skip silently when required."""
    if os.getenv("RUN_DB_TESTS") == "1":
        return
    if os.getenv("REQUIRE_SECURITY_DB_TESTS") == "1":
        pytest.fail(
            "REQUIRE_SECURITY_DB_TESTS=1 requires RUN_DB_TESTS=1 with PostgreSQL running; "
            "refusing to silently skip database security regressions."
        )
    pytest.skip("set RUN_DB_TESTS=1 with PostgreSQL running")

pytestmark = [pytest.mark.integration, pytest.mark.security]

TENANT_A = uuid4()
TENANT_B = uuid4()
USER_A = uuid4()
USER_B = uuid4()
DOC_A = uuid4()
DOC_B = uuid4()
APPROVAL_A = uuid4()
APPROVAL_B = uuid4()

EXPECTED_FORCED_RLS_TENANT_TABLES: frozenset[str] = frozenset(
    {
        "users",
        "documents",
        "document_chunks",
        "compliance_assessments",
        "control_assessments",
        "evidence_references",
        "assessment_score_snapshots",
        "score_overrides",
        "approvals",
        "security_reviews",
        "approval_audit_events",
    }
)

EXPECTED_MIGRATION_0005_INDEXES: set[tuple[str, str]] = {
    ("documents", "uq_documents_org_id"),
    ("document_chunks", "uq_document_chunks_org_id"),
    ("compliance_frameworks", "uq_compliance_frameworks_code_version"),
    ("compliance_controls", "uq_compliance_controls_framework_code"),
    ("compliance_controls", "ix_compliance_controls_framework_id"),
    ("compliance_assessments", "uq_compliance_assessments_org_id"),
    ("compliance_assessments", "ix_compliance_assessments_org_status"),
    ("compliance_assessments", "ix_compliance_assessments_org_framework"),
    ("control_assessments", "uq_control_assessments_org_id"),
    ("control_assessments", "uq_control_assessments_assessment_control"),
    ("control_assessments", "ix_control_assessments_org_assessment"),
    ("evidence_references", "ix_evidence_references_org_control"),
    ("evidence_references", "ix_evidence_references_org_document"),
    ("evidence_references", "uq_evidence_references_control_doc_chunk"),
    ("evidence_references", "uq_evidence_references_control_doc_nochunk"),
    ("assessment_score_snapshots", "uq_assessment_score_snapshots_assessment_rev"),
    ("assessment_score_snapshots", "ix_assessment_score_snapshots_org_assessment"),
}


@pytest.fixture(scope="module", autouse=True)
async def _seed_security_tenants() -> AsyncIterator[None]:
    require_security_database()

    async with database_admin_session() as session:
        for org_id, label in ((TENANT_A, "sec-acme"), (TENANT_B, "sec-globex")):
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (:id, :name, :slug)"
                ),
                {
                    "id": org_id,
                    "name": label,
                    "slug": f"{label}-{org_id.hex[:12]}",
                },
            )

    for org_id, user_id, doc_id, approval_id in (
        (TENANT_A, USER_A, DOC_A, APPROVAL_A),
        (TENANT_B, USER_B, DOC_B, APPROVAL_B),
    ):
        async with tenant_session(org_id) as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, organization_id, email, role) "
                    "VALUES (:id, :org, :email, CAST(:role AS role))"
                ),
                {
                    "id": user_id,
                    "org": org_id,
                    "email": f"user-{org_id.hex[:8]}@security.test",
                    "role": Role.MANAGER.value,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO documents "
                    "(id, organization_id, filename, storage_key, content_type, uploader_id) "
                    "VALUES (:id, :org, :name, :key, 'application/pdf', :uploader)"
                ),
                {
                    "id": doc_id,
                    "org": org_id,
                    "name": "policy.pdf",
                    "key": f"org/{org_id}/documents/{doc_id}/policy.pdf",
                    "uploader": user_id,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO approvals ("
                    "id, organization_id, workflow_execution_id, recommendation_id, "
                    "proposed_action, evidence, agent_trace_ids, task_token"
                    ") VALUES ("
                    ":id, :org, :wf_id, :rec_id, "
                    "CAST(:action AS jsonb), '[]'::jsonb, '[]'::jsonb, :token"
                    ")"
                ),
                {
                    "id": approval_id,
                    "org": org_id,
                    "wf_id": f"wf-{approval_id}",
                    "rec_id": uuid4(),
                    "action": (
                        '{"action_type":"create_security_review",'
                        '"resource_type":"workflow_execution",'
                        f'"resource_id":"{uuid4()}",'
                        '"summary":"Security review"}'
                    ),
                    "token": f"token-{approval_id}",
                },
            )

    yield
    await dispose_engine()


@pytest.mark.asyncio
async def test_runtime_role_is_nobypassrls_and_non_superuser() -> None:
    async with untenanted_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                        current_user,
                        rolsuper,
                        rolbypassrls,
                        rolcreatedb,
                        rolcreaterole
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            )
        ).one()

    assert row == ("cloudguard_app", False, False, False, False)


@pytest.mark.asyncio
async def test_every_tenant_table_has_enabled_and_forced_rls_in_catalog() -> None:
    async with untenanted_session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT
                        c.relname,
                        c.relrowsecurity,
                        c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind = 'r'
                      AND c.relname NOT IN (
                          'alembic_version',
                          'organizations',
                          'compliance_frameworks',
                          'compliance_controls'
                      )
                    """
                )
            )
        ).all()

    actual_tables = {str(r[0]) for r in rows}
    assert actual_tables == EXPECTED_FORCED_RLS_TENANT_TABLES
    for table_name, rls_enabled, rls_forced in rows:
        assert rls_enabled is True, f"{table_name} does not have relrowsecurity=true"
        assert rls_forced is True, f"{table_name} does not have relforcerowsecurity=true"


@pytest.mark.asyncio
async def test_runtime_role_is_denied_truncate_and_audit_sequence_usage() -> None:
    with pytest.raises(Exception, match="permission denied"):
        async with tenant_session(TENANT_A) as session:
            await session.execute(text("TRUNCATE TABLE documents"))

    with pytest.raises(Exception, match="permission denied"):
        async with tenant_session(TENANT_A) as session:
            await session.execute(
                text("SELECT nextval('approval_audit_events_id_seq')")
            )

    with pytest.raises(Exception, match=r"append-only|immutable|permission denied"):
        async with tenant_session(TENANT_A) as session:
            await session.execute(
                text("DELETE FROM approval_audit_events")
            )


@pytest.mark.asyncio
async def test_rls_blocks_untenanted_and_cross_tenant_access() -> None:
    async with untenanted_session() as session:
        docs = (await session.execute(text("SELECT id FROM documents"))).scalars().all()
        approvals = (
            await session.execute(text("SELECT id FROM approvals"))
        ).scalars().all()
    assert docs == []
    assert approvals == []

    async with tenant_session(TENANT_A) as session:
        cross_doc = (
            await session.execute(
                text("SELECT id FROM documents WHERE id = :id"),
                {"id": DOC_B},
            )
        ).scalar_one_or_none()
        cross_approval = (
            await session.execute(
                text("SELECT id FROM approvals WHERE id = :id"),
                {"id": APPROVAL_B},
            )
        ).scalar_one_or_none()
    assert cross_doc is None
    assert cross_approval is None


@pytest.mark.asyncio
async def test_rls_with_check_rejects_cross_tenant_writes() -> None:
    with pytest.raises(Exception, match="policy"):
        async with tenant_session(TENANT_A) as session:
            await session.execute(
                text(
                    "INSERT INTO documents "
                    "(id, organization_id, filename, storage_key, content_type, uploader_id) "
                    "VALUES (:id, :org, 'forged.pdf', 'k', 'application/pdf', :uploader)"
                ),
                {"id": uuid4(), "org": TENANT_B, "uploader": USER_B},
            )


@pytest.mark.asyncio
async def test_approval_context_immutability_and_delete_prohibition() -> None:
    with pytest.raises(Exception, match="immutable"):
        async with tenant_session(TENANT_A) as session:
            await session.execute(
                text(
                    "UPDATE approvals SET task_token = 'forged-token' "
                    "WHERE id = :id"
                ),
                {"id": APPROVAL_A},
            )

    with pytest.raises(Exception, match="permission denied"):
        async with tenant_session(TENANT_A) as session:
            await session.execute(
                text("DELETE FROM approvals WHERE id = :id"),
                {"id": APPROVAL_A},
            )


@pytest.mark.asyncio
async def test_migration_0005_expected_indexes_exist_in_postgresql() -> None:
    async with untenanted_session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT tablename, indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                    """
                )
            )
        ).all()

    present = {(str(row[0]), str(row[1])) for row in rows}
    missing = EXPECTED_MIGRATION_0005_INDEXES - present
    assert not missing, f"Missing expected migration 0005 indexes: {sorted(missing)}"


def test_published_migrations_sequence_is_intact() -> None:
    migrations_dir = Path(__file__).parents[2] / "migrations" / "versions"
    expected = [
        "0001_initial_schema.py",
        "0002_user_last_login.py",
        "0003_document_chunks.py",
        "0004_pgvector.py",
        "0005_compliance_scoring.py",
        "0006_score_overrides.py",
        "0007_approvals.py",
        "0008_approval_privilege_hardening.py",
        "0009_compliance_privilege_hardening.py",
        "0010_approval_callback_recovery.py",
        "0011_approval_execution_audit.py",
        "0012_runtime_role_privileges.py",
    ]
    actual = sorted(
        f.name
        for f in migrations_dir.iterdir()
        if f.is_file() and f.suffix == ".py" and not f.name.startswith("_")
    )
    assert actual == expected
