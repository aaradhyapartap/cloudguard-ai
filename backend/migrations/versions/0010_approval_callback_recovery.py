"""Add durable Approval callback recovery bookkeeping.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-06

Human decisions remain immutable. Internal callback fields allow trusted
post-commit recovery workers to lease an already-decided Approval, resume its
Step Functions task, and record successful callback delivery.

Published migrations 0007-0009 remain untouched.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _install_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_approval_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval rows cannot be deleted';
            END IF;

            -- Exact no-op updates are permitted for idempotent workflow retry.
            -- No persisted value may change through this path.
            IF NEW IS NOT DISTINCT FROM OLD THEN
                RETURN NEW;
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
                    OR NEW.callback_claim_id IS NOT NULL
                    OR NEW.callback_claimed_at IS NOT NULL
                    OR NEW.callback_delivered_at IS NOT NULL
                    OR NEW.callback_attempt_count <> 0
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

                IF NEW.status = 'decided' THEN
                    IF OLD.callback_delivered_at IS NOT NULL
                        AND (
                            NEW.callback_claim_id
                                IS DISTINCT FROM OLD.callback_claim_id
                            OR NEW.callback_claimed_at
                                IS DISTINCT FROM OLD.callback_claimed_at
                            OR NEW.callback_delivered_at
                                IS DISTINCT FROM OLD.callback_delivered_at
                            OR NEW.callback_attempt_count
                                IS DISTINCT FROM OLD.callback_attempt_count
                        )
                    THEN
                        RAISE EXCEPTION 'delivered approval callback is immutable';
                    END IF;

                    IF NEW.callback_attempt_count < OLD.callback_attempt_count
                        OR NEW.callback_attempt_count
                            > OLD.callback_attempt_count + 1
                    THEN
                        RAISE EXCEPTION 'invalid approval callback attempt transition';
                    END IF;

                ELSIF NEW.status IN (
                    'execution_succeeded',
                    'execution_failed'
                ) THEN
                    IF NEW.callback_delivered_at IS NULL THEN
                        RAISE EXCEPTION
                            'approval execution requires delivered workflow callback';
                    END IF;

                ELSE
                    RAISE EXCEPTION 'invalid approval decided-to-execution transition';
                END IF;

            ELSE
                RAISE EXCEPTION 'terminal approval state is immutable';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def _restore_previous_guard() -> None:
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

                IF NEW.status NOT IN (
                    'execution_succeeded',
                    'execution_failed'
                ) THEN
                    RAISE EXCEPTION 'invalid approval decided-to-execution transition';
                END IF;

            ELSE
                RAISE EXCEPTION 'terminal approval state is immutable';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "callback_claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "callback_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "callback_delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "callback_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.create_check_constraint(
        "ck_approvals_callback_attempt_count_nonnegative",
        "approvals",
        "callback_attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_approvals_callback_claim_pair",
        "approvals",
        (
            "(callback_claim_id IS NULL AND callback_claimed_at IS NULL) "
            "OR "
            "(callback_claim_id IS NOT NULL AND callback_claimed_at IS NOT NULL)"
        ),
    )
    op.create_check_constraint(
        "ck_approvals_pending_callback_state",
        "approvals",
        (
            "status <> 'pending' OR "
            "("
            "callback_claim_id IS NULL "
            "AND callback_claimed_at IS NULL "
            "AND callback_delivered_at IS NULL "
            "AND callback_attempt_count = 0"
            ")"
        ),
    )
    op.create_check_constraint(
        "ck_approvals_delivered_callback_unclaimed",
        "approvals",
        (
            "callback_delivered_at IS NULL OR "
            "("
            "callback_claim_id IS NULL "
            "AND callback_claimed_at IS NULL"
            ")"
        ),
    )

    op.create_index(
        "ix_approvals_callback_recovery",
        "approvals",
        [
            "organization_id",
            "callback_claimed_at",
            "decided_at",
        ],
        postgresql_where=sa.text(
            "status = 'decided' AND callback_delivered_at IS NULL"
        ),
    )

    _install_guard()


def downgrade() -> None:
    _restore_previous_guard()

    op.drop_index(
        "ix_approvals_callback_recovery",
        table_name="approvals",
    )
    op.drop_constraint(
        "ck_approvals_delivered_callback_unclaimed",
        "approvals",
        type_="check",
    )
    op.drop_constraint(
        "ck_approvals_pending_callback_state",
        "approvals",
        type_="check",
    )
    op.drop_constraint(
        "ck_approvals_callback_claim_pair",
        "approvals",
        type_="check",
    )
    op.drop_constraint(
        "ck_approvals_callback_attempt_count_nonnegative",
        "approvals",
        type_="check",
    )

    op.drop_column("approvals", "callback_attempt_count")
    op.drop_column("approvals", "callback_delivered_at")
    op.drop_column("approvals", "callback_claimed_at")
    op.drop_column("approvals", "callback_claim_id")
