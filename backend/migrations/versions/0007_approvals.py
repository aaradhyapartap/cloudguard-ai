"""Add tenant-scoped human approvals and guarded lifecycle persistence.

Revision ID: 0007
Revises: 0006
Create Date: Phase 7.2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TENANT_SETTING = "app.current_organization_id"

APPROVAL_DECISION_VALUES = ("approved", "rejected", "modified")
APPROVAL_STATUS_VALUES = (
    "pending",
    "decided",
    "execution_succeeded",
    "execution_failed",
)


def _create_enum_type(values: tuple[str, ...], name: str) -> postgresql.ENUM:
    postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    approval_decision_enum = _create_enum_type(
        APPROVAL_DECISION_VALUES,
        "approval_decision",
    )
    approval_status_enum = _create_enum_type(
        APPROVAL_STATUS_VALUES,
        "approval_status",
    )

    # Tenant-coupled approver FK target.
    op.create_unique_constraint(
        "uq_users_org_id",
        "users",
        ["organization_id", "id"],
    )

    op.create_table(
        "approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_execution_id",
            sa.String(256),
            nullable=False,
        ),
        sa.Column(
            "recommendation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "proposed_action",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "score_context",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "agent_trace_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "generator_model_id",
            sa.String(256),
            nullable=True,
        ),
        sa.Column(
            "reviewer_model_id",
            sa.String(256),
            nullable=True,
        ),
        sa.Column(
            "status",
            approval_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "decision",
            approval_decision_enum,
            nullable=True,
        ),
        sa.Column(
            "approver_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "justification",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "comment",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "modified_action",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "task_token",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approver_id"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_approvals_approver_org",
        ),
        sa.UniqueConstraint(
            "task_token",
            name="uq_approvals_task_token",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "workflow_execution_id",
            name="uq_approvals_org_workflow_execution",
        ),
        sa.CheckConstraint(
            "length(btrim(workflow_execution_id)) > 0",
            name="ck_approvals_workflow_execution_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(task_token)) > 0",
            name="ck_approvals_task_token_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proposed_action) = 'object'",
            name="ck_approvals_proposed_action_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array' "
            "AND jsonb_array_length(evidence) <= 10",
            name="ck_approvals_evidence_array",
        ),
        sa.CheckConstraint(
            "score_context IS NULL "
            "OR jsonb_typeof(score_context) = 'object'",
            name="ck_approvals_score_context_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(agent_trace_ids) = 'array' "
            "AND jsonb_array_length(agent_trace_ids) <= 16",
            name="ck_approvals_agent_trace_ids_array",
        ),
        sa.CheckConstraint(
            "modified_action IS NULL "
            "OR jsonb_typeof(modified_action) = 'object'",
            name="ck_approvals_modified_action_object",
        ),
        sa.CheckConstraint(
            "("
            "status = 'pending' "
            "AND decision IS NULL "
            "AND approver_id IS NULL "
            "AND decided_at IS NULL "
            "AND justification IS NULL "
            "AND comment IS NULL "
            "AND modified_action IS NULL"
            ") OR ("
            "status IN ('decided', 'execution_succeeded', 'execution_failed') "
            "AND decision IS NOT NULL "
            "AND approver_id IS NOT NULL "
            "AND decided_at IS NOT NULL"
            ")",
            name="ck_approvals_lifecycle_shape",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR "
            "("
            "decision = 'approved' "
            "AND modified_action IS NULL"
            ") OR ("
            "decision = 'rejected' "
            "AND justification IS NOT NULL "
            "AND length(btrim(justification)) > 0 "
            "AND modified_action IS NULL"
            ") OR ("
            "decision = 'modified' "
            "AND justification IS NOT NULL "
            "AND length(btrim(justification)) > 0 "
            "AND modified_action IS NOT NULL"
            ")",
            name="ck_approvals_decision_shape",
        ),
    )

    op.create_index(
        "ix_approvals_org_id",
        "approvals",
        ["organization_id", "id"],
    )
    op.create_index(
        "ix_approvals_pending_queue",
        "approvals",
        ["organization_id", "created_at"],
        postgresql_where=sa.text("decision IS NULL"),
    )
    op.create_index(
        "ix_approvals_decided_lookup",
        "approvals",
        ["organization_id", "decision", "decided_at"],
        postgresql_where=sa.text("decision IS NOT NULL"),
    )

    # Tenant RLS. Missing tenant context fails closed.
    op.execute("ALTER TABLE approvals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approvals FORCE ROW LEVEL SECURITY")

    predicate = (
        "organization_id = "
        f"NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    )

    op.execute(
        f"CREATE POLICY approvals_tenant_select ON approvals "
        f"FOR SELECT USING ({predicate})"
    )
    op.execute(
        f"CREATE POLICY approvals_tenant_insert ON approvals "
        f"FOR INSERT WITH CHECK ({predicate})"
    )
    op.execute(
        f"CREATE POLICY approvals_tenant_update ON approvals "
        f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
    )

    # Guard frozen recommendation context and the one-way lifecycle.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_approval_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval rows cannot be deleted';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
                OR NEW.workflow_execution_id IS DISTINCT FROM OLD.workflow_execution_id
                OR NEW.recommendation_id IS DISTINCT FROM OLD.recommendation_id
                OR NEW.proposed_action IS DISTINCT FROM OLD.proposed_action
                OR NEW.evidence IS DISTINCT FROM OLD.evidence
                OR NEW.score_context IS DISTINCT FROM OLD.score_context
                OR NEW.agent_trace_ids IS DISTINCT FROM OLD.agent_trace_ids
                OR NEW.generator_model_id IS DISTINCT FROM OLD.generator_model_id
                OR NEW.reviewer_model_id IS DISTINCT FROM OLD.reviewer_model_id
                OR NEW.task_token IS DISTINCT FROM OLD.task_token
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'approval recommendation context is immutable';
            END IF;

            IF OLD.status = 'pending' THEN
                IF NEW.status <> 'decided'
                    OR OLD.decision IS NOT NULL
                    OR NEW.decision IS NULL
                    OR NEW.approver_id IS NULL
                    OR NEW.decided_at IS NULL
                THEN
                    RAISE EXCEPTION 'invalid approval pending-to-decided transition';
                END IF;

            ELSIF OLD.status = 'decided' THEN
                IF NEW.decision IS DISTINCT FROM OLD.decision
                    OR NEW.approver_id IS DISTINCT FROM OLD.approver_id
                    OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
                    OR NEW.justification IS DISTINCT FROM OLD.justification
                    OR NEW.comment IS DISTINCT FROM OLD.comment
                    OR NEW.modified_action IS DISTINCT FROM OLD.modified_action
                THEN
                    RAISE EXCEPTION 'approval human decision is immutable';
                END IF;

                IF NEW.status NOT IN ('execution_succeeded', 'execution_failed') THEN
                    RAISE EXCEPTION 'invalid approval decided-to-execution transition';
                END IF;

            ELSE
                RAISE EXCEPTION 'terminal approval state is immutable';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_guard_approval_mutation
        BEFORE UPDATE OR DELETE ON approvals
        FOR EACH ROW
        EXECUTE FUNCTION guard_approval_mutation();
        """
    )

    # Application role intentionally receives no DELETE privilege.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'cloudguard_app'
            ) THEN
                GRANT SELECT, INSERT, UPDATE
                ON TABLE approvals
                TO cloudguard_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_approval_mutation ON approvals"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_approval_mutation()")

    op.execute(
        "DROP POLICY IF EXISTS approvals_tenant_update ON approvals"
    )
    op.execute(
        "DROP POLICY IF EXISTS approvals_tenant_insert ON approvals"
    )
    op.execute(
        "DROP POLICY IF EXISTS approvals_tenant_select ON approvals"
    )

    op.drop_index(
        "ix_approvals_decided_lookup",
        table_name="approvals",
    )
    op.drop_index(
        "ix_approvals_pending_queue",
        table_name="approvals",
    )
    op.drop_index(
        "ix_approvals_org_id",
        table_name="approvals",
    )

    op.drop_table("approvals")

    op.execute("DROP TYPE IF EXISTS approval_status")
    op.execute("DROP TYPE IF EXISTS approval_decision")

    op.drop_constraint(
        "uq_users_org_id",
        "users",
        type_="unique",
    )
