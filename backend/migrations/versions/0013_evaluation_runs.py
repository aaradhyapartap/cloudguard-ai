"""Create tenant-isolated evaluation_runs table with forced RLS and least privilege.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-02

Phase 9 adds persistent, tenant-scoped evaluation run storage (`evaluation_runs`).
In accordance with the repository's least-privilege and tenant-isolation model:
1. Row-Level Security is enabled and forced (`ENABLE` + `FORCE ROW LEVEL SECURITY`).
2. `evaluation_runs_tenant_isolation` restricts `USING` and `WITH CHECK` to
   `organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid`.
3. `REVOKE ALL ON TABLE evaluation_runs FROM PUBLIC;` is applied.
4. `cloudguard_app` receives only `SELECT, INSERT` (append-only; no `UPDATE` or `DELETE`).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TENANT_SETTING = "app.current_organization_id"


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "triggered_by",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("execution_mode", sa.String(64), nullable=False),
        sa.Column("embedding_model_id", sa.String(256), nullable=False),
        sa.Column("generation_model_id", sa.String(256), nullable=False),
        sa.Column("reviewer_model_id", sa.String(256), nullable=False),
        sa.Column("judge_model_id", sa.String(256), nullable=False),
        sa.Column(
            "security_gate_passed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "quality_gate_advisory_passed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "metrics_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "category_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "case_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "provenance_notes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_evaluation_runs_org_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "triggered_by"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_evaluation_runs_triggered_by_org",
        ),
    )

    op.create_index(
        "ix_evaluation_runs_org_created",
        "evaluation_runs",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_evaluation_runs_org_triggered_by",
        "evaluation_runs",
        ["organization_id", "triggered_by", "created_at"],
    )

    op.execute("ALTER TABLE evaluation_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evaluation_runs FORCE ROW LEVEL SECURITY")
    predicate = (
        "organization_id = "
        f"NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    )
    op.execute(
        f"CREATE POLICY evaluation_runs_tenant_isolation ON evaluation_runs "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )

    op.execute("REVOKE ALL ON TABLE evaluation_runs FROM PUBLIC;")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'cloudguard_app'
            ) THEN
                REVOKE ALL ON TABLE evaluation_runs FROM cloudguard_app;
                GRANT SELECT, INSERT ON TABLE evaluation_runs TO cloudguard_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS evaluation_runs_tenant_isolation ON evaluation_runs"
    )
    op.execute("ALTER TABLE evaluation_runs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_evaluation_runs_org_triggered_by", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_org_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")

