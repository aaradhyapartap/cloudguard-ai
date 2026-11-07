"""Add compliance frameworks, controls, assessments, evidence references, and score snapshots.

Revision ID: 0005
Revises: 0004
Create Date: Phase 5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TENANT_SETTING = "app.current_organization_id"

RISK_CLASSIFICATION_VALUES = ("not_scored", "low", "medium", "high", "critical")
CONTROL_STATUS_VALUES = (
    "satisfied",
    "partially_satisfied",
    "deficient",
    "unassessed",
    "not_applicable",
)
ASSESSMENT_STATUS_VALUES = ("draft", "in_progress", "completed", "archived")

TENANT_MUTABLE_TABLES = (
    "compliance_assessments",
    "control_assessments",
    "evidence_references",
)


def _create_enum_type(values: tuple[str, ...], name: str) -> postgresql.ENUM:
    postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    risk_classification_enum = _create_enum_type(
        RISK_CLASSIFICATION_VALUES, "risk_classification"
    )
    control_status_enum = _create_enum_type(CONTROL_STATUS_VALUES, "control_status")
    assessment_status_enum = _create_enum_type(
        ASSESSMENT_STATUS_VALUES, "assessment_status"
    )
    confidentiality_enum = postgresql.ENUM(
        "public", "internal", "confidential", "restricted",
        name="confidentiality_level",
        create_type=False,
    )

    # 1. Ensure composite uniqueness on existing tenant tables for composite FK targets
    op.create_unique_constraint(
        "uq_documents_org_id", "documents", ["organization_id", "id"]
    )
    op.create_unique_constraint(
        "uq_document_chunks_org_id", "document_chunks", ["organization_id", "id"]
    )

    # 2. Global Compliance Frameworks
    op.create_table(
        "compliance_frameworks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "code", "version", name="uq_compliance_frameworks_code_version"
        ),
    )

    # 3. Global Compliance Controls
    op.create_table(
        "compliance_controls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "framework_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("compliance_frameworks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("control_code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(120), nullable=False, server_default=""),
        sa.Column(
            "default_weight",
            sa.Numeric(3, 1),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "framework_id",
            "control_code",
            name="uq_compliance_controls_framework_code",
        ),
    )
    op.create_index(
        "ix_compliance_controls_framework_id",
        "compliance_controls",
        ["framework_id"],
    )

    # 4. Tenant Compliance Assessments
    op.create_table(
        "compliance_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "framework_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("compliance_frameworks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column(
            "status",
            assessment_status_enum,
            nullable=False,
            server_default="draft",
        ),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "risk_classification",
            risk_classification_enum,
            nullable=False,
            server_default="not_scored",
        ),
        sa.Column("scoring_version", sa.String(40), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_compliance_assessments_org_id"
        ),
    )
    op.create_index(
        "ix_compliance_assessments_org_status",
        "compliance_assessments",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_compliance_assessments_org_framework",
        "compliance_assessments",
        ["organization_id", "framework_id"],
    )

    # 5. Tenant Control Assessments
    op.create_table(
        "control_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "control_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("compliance_controls.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            control_status_enum,
            nullable=False,
            server_default="unassessed",
        ),
        sa.Column(
            "effective_weight",
            sa.Numeric(3, 1),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_control_assessments_org_id"
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "control_id",
            name="uq_control_assessments_assessment_control",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assessment_id"],
            ["compliance_assessments.organization_id", "compliance_assessments.id"],
            ondelete="CASCADE",
            name="fk_control_assessments_assessment_org",
        ),
    )
    op.create_index(
        "ix_control_assessments_org_assessment",
        "control_assessments",
        ["organization_id", "assessment_id"],
    )

    # 6. Tenant Evidence References
    op.create_table(
        "evidence_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "control_assessment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "confidentiality_level",
            confidentiality_enum,
            nullable=False,
            server_default="internal",
        ),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "control_assessment_id"],
            ["control_assessments.organization_id", "control_assessments.id"],
            ondelete="CASCADE",
            name="fk_evidence_references_control_assessment_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_evidence_references_document_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "chunk_id"],
            ["document_chunks.organization_id", "document_chunks.id"],
            ondelete="SET NULL (chunk_id)",
            name="fk_evidence_references_chunk_org",
        ),
    )
    op.create_index(
        "ix_evidence_references_org_control",
        "evidence_references",
        ["organization_id", "control_assessment_id"],
    )
    op.create_index(
        "ix_evidence_references_org_document",
        "evidence_references",
        ["organization_id", "document_id"],
    )
    op.create_index(
        "uq_evidence_references_control_doc_chunk",
        "evidence_references",
        ["organization_id", "control_assessment_id", "document_id", "chunk_id"],
        unique=True,
        postgresql_where=sa.text("chunk_id IS NOT NULL"),
    )
    op.create_index(
        "uq_evidence_references_control_doc_nochunk",
        "evidence_references",
        ["organization_id", "control_assessment_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("chunk_id IS NULL"),
    )

    # 7. Tenant Assessment Score Snapshots (Immutable History)
    op.create_table(
        "assessment_score_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scoring_version", sa.String(40), nullable=False),
        sa.Column("framework_version", sa.String(40), nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "raw_scores",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "risk_classification",
            risk_classification_enum,
            nullable=False,
        ),
        sa.Column(
            "computed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "revision_number",
            name="uq_assessment_score_snapshots_assessment_rev",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assessment_id"],
            ["compliance_assessments.organization_id", "compliance_assessments.id"],
            ondelete="CASCADE",
            name="fk_assessment_score_snapshots_assessment_org",
        ),
    )
    op.create_index(
        "ix_assessment_score_snapshots_org_assessment",
        "assessment_score_snapshots",
        ["organization_id", "assessment_id"],
    )

    # 8. RLS Policies
    # Standard CRUD tenant isolation for mutable tenant tables
    for table in TENANT_MUTABLE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        predicate = f"organization_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )

    # Append-only RLS policy for snapshots (SELECT and INSERT only, NO UPDATE/DELETE)
    op.execute("ALTER TABLE assessment_score_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assessment_score_snapshots FORCE ROW LEVEL SECURITY")
    snapshot_predicate = (
        f"organization_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    )
    op.execute(
        f"CREATE POLICY assessment_score_snapshots_tenant_select ON assessment_score_snapshots "
        f"FOR SELECT USING ({snapshot_predicate})"
    )
    op.execute(
        f"CREATE POLICY assessment_score_snapshots_tenant_insert ON assessment_score_snapshots "
        f"FOR INSERT WITH CHECK ({snapshot_predicate})"
    )

    # 9. Immutability Trigger (Reject UPDATE and DELETE on snapshots)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_assessment_score_snapshot_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'assessment_score_snapshots rows are immutable';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_assessment_score_snapshot_mutation
        BEFORE UPDATE OR DELETE ON assessment_score_snapshots
        FOR EACH ROW
        EXECUTE FUNCTION prevent_assessment_score_snapshot_mutation();
        """
    )

    # 10. Table Privileges for local/production application role
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cloudguard_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
                    compliance_frameworks,
                    compliance_controls,
                    compliance_assessments,
                    control_assessments,
                    evidence_references
                TO cloudguard_app;

                GRANT SELECT, INSERT ON TABLE
                    assessment_score_snapshots
                TO cloudguard_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # 1. Drop trigger and function
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_assessment_score_snapshot_mutation "
        "ON assessment_score_snapshots"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_assessment_score_snapshot_mutation()")

    # 2. Drop RLS policies
    op.execute(
        "DROP POLICY IF EXISTS assessment_score_snapshots_tenant_select "
        "ON assessment_score_snapshots"
    )
    op.execute(
        "DROP POLICY IF EXISTS assessment_score_snapshots_tenant_insert "
        "ON assessment_score_snapshots"
    )
    op.execute("ALTER TABLE assessment_score_snapshots DISABLE ROW LEVEL SECURITY")

    for table in TENANT_MUTABLE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # 3. Drop tables in reverse dependency order
    op.drop_table("assessment_score_snapshots")
    op.drop_table("evidence_references")
    op.drop_table("control_assessments")
    op.drop_table("compliance_assessments")
    op.drop_table("compliance_controls")
    op.drop_table("compliance_frameworks")

    # 4. Drop composite unique constraints on existing tables
    op.execute("ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS uq_document_chunks_org_id")
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS uq_documents_org_id")

    # 5. Drop enum types
    op.execute("DROP TYPE IF EXISTS assessment_status")
    op.execute("DROP TYPE IF EXISTS control_status")
    op.execute("DROP TYPE IF EXISTS risk_classification")
