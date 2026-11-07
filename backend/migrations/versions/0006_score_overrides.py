"""Add score_overrides table with immutability triggers and tenant RLS.

Revision ID: 0006
Revises: 0005
Create Date: Phase 5.4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_SETTING = "app.current_organization_id"


def upgrade() -> None:
    risk_classification_enum = postgresql.ENUM(
        "not_scored", "low", "medium", "high", "critical",
        name="risk_classification",
        create_type=False,
    )

    # 1. Add unique constraint to assessment_score_snapshots for exact snapshot triple reference
    op.create_unique_constraint(
        "uq_assessment_snapshots_assessment_rev_id",
        "assessment_score_snapshots",
        ["assessment_id", "revision_number", "id"],
    )

    # 2. Create score_overrides table
    op.create_table(
        "score_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_revision_number", sa.Integer(), nullable=False),
        sa.Column("original_overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "original_risk_classification",
            risk_classification_enum,
            nullable=False,
        ),
        sa.Column("override_overall_score", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "override_risk_classification",
            risk_classification_enum,
            nullable=False,
        ),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "overridden_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "overridden_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assessment_id"],
            ["compliance_assessments.organization_id", "compliance_assessments.id"],
            ondelete="CASCADE",
            name="fk_score_overrides_assessment_org",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "source_revision_number", "snapshot_id"],
            [
                "assessment_score_snapshots.assessment_id",
                "assessment_score_snapshots.revision_number",
                "assessment_score_snapshots.id",
            ],
            ondelete="RESTRICT",
            name="fk_score_overrides_snapshot_triple",
        ),
    )

    op.create_index(
        "ix_score_overrides_org_assessment",
        "score_overrides",
        ["organization_id", "assessment_id"],
    )
    op.create_index(
        "ix_score_overrides_org_assessment_created",
        "score_overrides",
        ["organization_id", "assessment_id", "overridden_at"],
    )

    # 3. Append-only RLS Policy for score_overrides (SELECT and INSERT only)
    op.execute("ALTER TABLE score_overrides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE score_overrides FORCE ROW LEVEL SECURITY")
    predicate = f"organization_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    op.execute(
        f"CREATE POLICY score_overrides_tenant_select ON score_overrides "
        f"FOR SELECT USING ({predicate})"
    )
    op.execute(
        f"CREATE POLICY score_overrides_tenant_insert ON score_overrides "
        f"FOR INSERT WITH CHECK ({predicate})"
    )

    # 4. Immutability Trigger (Reject UPDATE and DELETE on score_overrides)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_score_override_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'score_overrides rows are immutable';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_score_override_mutation
        BEFORE UPDATE OR DELETE ON score_overrides
        FOR EACH ROW
        EXECUTE FUNCTION prevent_score_override_mutation();
        """
    )

    # 5. Table Privileges for application role
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cloudguard_app') THEN
                GRANT SELECT, INSERT ON TABLE score_overrides TO cloudguard_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # 1. Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_score_override_mutation ON score_overrides")
    op.execute("DROP FUNCTION IF EXISTS prevent_score_override_mutation()")

    # 2. Drop RLS policies
    op.execute("DROP POLICY IF EXISTS score_overrides_tenant_select ON score_overrides")
    op.execute("DROP POLICY IF EXISTS score_overrides_tenant_insert ON score_overrides")

    # 3. Drop indexes and table
    op.drop_index("ix_score_overrides_org_assessment_created", table_name="score_overrides")
    op.drop_index("ix_score_overrides_org_assessment", table_name="score_overrides")
    op.drop_table("score_overrides")

    # 4. Drop unique constraint on assessment_score_snapshots
    op.drop_constraint(
        "uq_assessment_snapshots_assessment_rev_id",
        "assessment_score_snapshots",
        type_="unique",
    )
