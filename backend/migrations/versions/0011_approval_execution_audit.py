"""Add Phase 7.5 approved-action execution and immutable audit ledger.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Phase 7.5 owns the first legitimate transition into execution terminal
    # states. Refuse to invent execution metadata for any pre-existing terminal
    # row created outside this contract.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM approvals
                WHERE status IN (
                    'execution_succeeded',
                    'execution_failed'
                )
            ) THEN
                RAISE EXCEPTION
                    'Phase 7.5 migration requires reconciliation of '
                    'pre-existing terminal approvals';
            END IF;
        END;
        $$;
        """
    )

    op.add_column(
        "approvals",
        sa.Column(
            "execution_idempotency_key",
            sa.String(length=256),
            nullable=True,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "execution_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "execution_reference",
            sa.String(length=256),
            nullable=True,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "execution_error_code",
            sa.String(length=128),
            nullable=True,
        ),
    )

    op.create_index(
        "uq_approvals_execution_idempotency_key",
        "approvals",
        ["execution_idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "execution_idempotency_key IS NOT NULL"
        ),
    )

    op.create_check_constraint(
        "ck_approvals_execution_result_state",
        "approvals",
        """
        (
            status NOT IN (
                'execution_succeeded',
                'execution_failed'
            )
            AND execution_idempotency_key IS NULL
            AND execution_completed_at IS NULL
            AND execution_reference IS NULL
            AND execution_error_code IS NULL
        )
        OR
        (
            status = 'execution_succeeded'
            AND execution_idempotency_key IS NOT NULL
            AND execution_completed_at IS NOT NULL
            AND execution_reference IS NOT NULL
            AND execution_error_code IS NULL
        )
        OR
        (
            status = 'execution_failed'
            AND execution_idempotency_key IS NOT NULL
            AND execution_completed_at IS NOT NULL
            AND execution_reference IS NULL
            AND execution_error_code IS NOT NULL
        )
        """,
    )

    # ------------------------------------------------------------------
    # The single bounded consequential Phase 7 demo side effect.
    # ------------------------------------------------------------------

    op.create_table(
        "security_reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "organizations.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "approvals.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "workflow_execution_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "action_type",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=256),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "approval_id",
            name="uq_security_reviews_approval_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_security_reviews_idempotency_key",
        ),
    )

    op.create_index(
        "ix_security_reviews_org_created",
        "security_reviews",
        ["organization_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # Append-only Approval audit ledger.
    # ------------------------------------------------------------------

    op.create_table(
        "approval_audit_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "organizations.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "approvals.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "approval_id",
            "event_type",
            name="uq_approval_audit_event_type",
        ),
        sa.CheckConstraint(
            """
            event_type IN (
                'approval_created',
                'approval_decided',
                'callback_delivered',
                'execution_succeeded',
                'execution_failed'
            )
            """,
            name="ck_approval_audit_event_type",
        ),
    )

    op.create_index(
        "ix_approval_audit_events_org_time",
        "approval_audit_events",
        ["organization_id", "occurred_at"],
    )

    # Backfill the durable history that already exists before the trigger
    # is installed. No task token is projected into any payload.
    op.execute(
        """
        INSERT INTO approval_audit_events (
            organization_id,
            approval_id,
            event_type,
            actor_id,
            occurred_at,
            payload
        )
        SELECT
            organization_id,
            id,
            'approval_created',
            NULL,
            created_at,
            jsonb_build_object(
                'workflow_execution_id',
                workflow_execution_id,
                'recommendation_id',
                recommendation_id,
                'proposed_action',
                proposed_action
            )
        FROM approvals
        ON CONFLICT (approval_id, event_type) DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO approval_audit_events (
            organization_id,
            approval_id,
            event_type,
            actor_id,
            occurred_at,
            payload
        )
        SELECT
            organization_id,
            id,
            'approval_decided',
            approver_id,
            decided_at,
            jsonb_build_object(
                'decision',
                decision,
                'approver_id',
                approver_id,
                'justification',
                justification,
                'comment',
                comment,
                'modified_action',
                modified_action
            )
        FROM approvals
        WHERE decision IS NOT NULL
          AND decided_at IS NOT NULL
        ON CONFLICT (approval_id, event_type) DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO approval_audit_events (
            organization_id,
            approval_id,
            event_type,
            actor_id,
            occurred_at,
            payload
        )
        SELECT
            organization_id,
            id,
            'callback_delivered',
            NULL,
            callback_delivered_at,
            jsonb_build_object(
                'callback_delivered_at',
                callback_delivered_at,
                'callback_attempt_count',
                callback_attempt_count
            )
        FROM approvals
        WHERE callback_delivered_at IS NOT NULL
        ON CONFLICT (approval_id, event_type) DO NOTHING;
        """
    )

    # ------------------------------------------------------------------
    # Tenant isolation.
    # ------------------------------------------------------------------

    for table_name, policy_name in (
        (
            "security_reviews",
            "security_reviews_tenant_policy",
        ),
        (
            "approval_audit_events",
            "approval_audit_events_tenant_policy",
        ),
    ):
        op.execute(
            f"ALTER TABLE {table_name} "
            "ENABLE ROW LEVEL SECURITY"
        )
        op.execute(
            f"ALTER TABLE {table_name} "
            "FORCE ROW LEVEL SECURITY"
        )
        op.execute(
            f"""
            CREATE POLICY {policy_name}
            ON {table_name}
            USING (
                organization_id =
                    NULLIF(
                        current_setting(
                            'app.current_organization_id',
                            true
                        ),
                        ''
                    )::uuid
            )
            WITH CHECK (
                organization_id =
                    NULLIF(
                        current_setting(
                            'app.current_organization_id',
                            true
                        ),
                        ''
                    )::uuid
            );
            """
        )

    # ------------------------------------------------------------------
    # Append-only side-effect/audit records.
    # ------------------------------------------------------------------

    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_security_review_insert()
        RETURNS TRIGGER AS $$
        DECLARE
            approval_status_value approval_status;
            approval_decision_value approval_decision;
            approval_callback_time TIMESTAMPTZ;
            approval_workflow_execution_id TEXT;
            effective_action JSONB;
        BEGIN
            SELECT
                status,
                decision,
                callback_delivered_at,
                workflow_execution_id::text,
                CASE
                    WHEN decision = 'modified'
                    THEN modified_action
                    ELSE proposed_action
                END
            INTO
                approval_status_value,
                approval_decision_value,
                approval_callback_time,
                approval_workflow_execution_id,
                effective_action
            FROM approvals
            WHERE id = NEW.approval_id
              AND organization_id = NEW.organization_id
            FOR SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'security review requires a tenant-owned approval';
            END IF;

            IF approval_status_value <> 'decided'
                OR approval_callback_time IS NULL
                OR approval_decision_value NOT IN (
                    'approved',
                    'modified'
                )
            THEN
                RAISE EXCEPTION
                    'security review requires a delivered '
                    'human-approved decision';
            END IF;

            IF effective_action IS NULL
                OR effective_action ->> 'action_type'
                    <> 'create_security_review'
                OR effective_action ->> 'resource_type'
                    <> 'workflow_execution'
                OR effective_action ->> 'resource_id'
                    <> NEW.workflow_execution_id::text
                OR approval_workflow_execution_id
                    <> NEW.workflow_execution_id::text
                OR NEW.action_type <> 'create_security_review'
                OR NEW.action_type
                    <> effective_action ->> 'action_type'
                OR NEW.summary
                    <> effective_action ->> 'summary'
            THEN
                RAISE EXCEPTION
                    'security review does not match the exact '
                    'approved bounded action';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER guard_security_review_insert_trigger
        BEFORE INSERT ON security_reviews
        FOR EACH ROW
        EXECUTE FUNCTION guard_security_review_insert();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_phase7_immutable_record()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'Phase 7 execution and audit records are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER guard_security_reviews_immutable
        BEFORE UPDATE OR DELETE ON security_reviews
        FOR EACH ROW
        EXECUTE FUNCTION guard_phase7_immutable_record();
        """
    )

    op.execute(
        """
        CREATE TRIGGER guard_approval_audit_events_immutable
        BEFORE UPDATE OR DELETE ON approval_audit_events
        FOR EACH ROW
        EXECUTE FUNCTION guard_phase7_immutable_record();
        """
    )

    op.execute(
        "REVOKE UPDATE, DELETE "
        "ON security_reviews FROM PUBLIC"
    )
    op.execute(
        "REVOKE UPDATE, DELETE "
        "ON approval_audit_events FROM PUBLIC"
    )

    # ------------------------------------------------------------------
    # Runtime-role least privilege.
    #
    # security_reviews is the bounded side effect, so the runtime needs
    # SELECT for exact-retry reconciliation and INSERT for first execution.
    #
    # approval_audit_events is emitted only by the trusted trigger. The
    # runtime may read the ledger but cannot forge, update, or delete events.
    # ------------------------------------------------------------------

    op.execute(
        """
        REVOKE ALL
        ON TABLE security_reviews
        FROM PUBLIC;

        REVOKE ALL
        ON TABLE approval_audit_events
        FROM PUBLIC;

        REVOKE ALL
        ON SEQUENCE approval_audit_events_id_seq
        FROM PUBLIC;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'cloudguard_app'
            ) THEN
                GRANT SELECT, INSERT
                ON TABLE security_reviews
                TO cloudguard_app;

                REVOKE UPDATE, DELETE
                ON TABLE security_reviews
                FROM cloudguard_app;

                GRANT SELECT
                ON TABLE approval_audit_events
                TO cloudguard_app;

                REVOKE INSERT, UPDATE, DELETE
                ON TABLE approval_audit_events
                FROM cloudguard_app;

                REVOKE ALL
                ON SEQUENCE approval_audit_events_id_seq
                FROM cloudguard_app;
            END IF;
        END
        $$;
        """
    )

    # ------------------------------------------------------------------
    # New execution-field guard. 0010's published lifecycle guard remains
    # untouched; this trigger supplements it for fields introduced here.
    # ------------------------------------------------------------------

    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_approval_execution_fields()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.status IN ('pending', 'decided')
                AND NEW.status NOT IN (
                    'execution_succeeded',
                    'execution_failed'
                )
            THEN
                IF NEW.execution_idempotency_key
                        IS DISTINCT FROM OLD.execution_idempotency_key
                    OR NEW.execution_completed_at
                        IS DISTINCT FROM OLD.execution_completed_at
                    OR NEW.execution_reference
                        IS DISTINCT FROM OLD.execution_reference
                    OR NEW.execution_error_code
                        IS DISTINCT FROM OLD.execution_error_code
                THEN
                    RAISE EXCEPTION
                        'approval execution result fields cannot be '
                        'written before terminal execution';
                END IF;

            ELSIF OLD.status = 'decided'
                AND NEW.status IN (
                    'execution_succeeded',
                    'execution_failed'
                )
            THEN
                IF OLD.execution_idempotency_key IS NOT NULL
                    OR OLD.execution_completed_at IS NOT NULL
                    OR OLD.execution_reference IS NOT NULL
                    OR OLD.execution_error_code IS NOT NULL
                THEN
                    RAISE EXCEPTION
                        'approval execution result already exists';
                END IF;

                IF NEW.callback_delivered_at IS NULL THEN
                    RAISE EXCEPTION
                        'approval execution requires delivered callback';
                END IF;

                IF OLD.decision NOT IN ('approved', 'modified') THEN
                    RAISE EXCEPTION
                        'rejected approval cannot enter execution state';
                END IF;

                IF NEW.execution_idempotency_key IS NULL
                    OR NEW.execution_completed_at IS NULL
                THEN
                    RAISE EXCEPTION
                        'terminal execution requires durable result metadata';
                END IF;

                IF NEW.status = 'execution_succeeded'
                    AND (
                        NEW.execution_reference IS NULL
                        OR NEW.execution_error_code IS NOT NULL
                    )
                THEN
                    RAISE EXCEPTION
                        'successful execution requires a reference '
                        'and no error code';
                END IF;

                IF NEW.status = 'execution_succeeded'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM security_reviews AS review
                        WHERE review.organization_id =
                            NEW.organization_id
                          AND review.approval_id = NEW.id
                          AND review.idempotency_key =
                            NEW.execution_idempotency_key
                          AND review.workflow_execution_id::text =
                            CASE
                                WHEN NEW.decision = 'modified'
                                THEN NEW.modified_action ->> 'resource_id'
                                ELSE NEW.proposed_action ->> 'resource_id'
                            END
                          AND review.action_type =
                            CASE
                                WHEN NEW.decision = 'modified'
                                THEN NEW.modified_action ->> 'action_type'
                                ELSE NEW.proposed_action ->> 'action_type'
                            END
                          AND review.summary =
                            CASE
                                WHEN NEW.decision = 'modified'
                                THEN NEW.modified_action ->> 'summary'
                                ELSE NEW.proposed_action ->> 'summary'
                            END
                          AND NEW.execution_reference =
                            'security-review:' || review.id::text
                    )
                THEN
                    RAISE EXCEPTION
                        'successful execution requires the exact '
                        'durable approved side effect';
                END IF;

                IF NEW.status = 'execution_failed'
                    AND (
                        NEW.execution_reference IS NOT NULL
                        OR NEW.execution_error_code IS NULL
                    )
                THEN
                    RAISE EXCEPTION
                        'failed execution requires an error code '
                        'and no reference';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER guard_approval_execution_fields_trigger
        BEFORE UPDATE ON approvals
        FOR EACH ROW
        EXECUTE FUNCTION guard_approval_execution_fields();
        """
    )

    # ------------------------------------------------------------------
    # Audit-event emission. Every payload intentionally omits workflow secrets.
    # ------------------------------------------------------------------

    op.execute(
        """
        CREATE OR REPLACE FUNCTION emit_approval_audit_event()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO approval_audit_events (
                    organization_id,
                    approval_id,
                    event_type,
                    actor_id,
                    occurred_at,
                    payload
                )
                VALUES (
                    NEW.organization_id,
                    NEW.id,
                    'approval_created',
                    NULL,
                    NEW.created_at,
                    jsonb_build_object(
                        'workflow_execution_id',
                        NEW.workflow_execution_id,
                        'recommendation_id',
                        NEW.recommendation_id,
                        'proposed_action',
                        NEW.proposed_action
                    )
                )
                ON CONFLICT (approval_id, event_type) DO NOTHING;

                RETURN NEW;
            END IF;

            IF OLD.status = 'pending'
                AND NEW.status = 'decided'
            THEN
                INSERT INTO approval_audit_events (
                    organization_id,
                    approval_id,
                    event_type,
                    actor_id,
                    occurred_at,
                    payload
                )
                VALUES (
                    NEW.organization_id,
                    NEW.id,
                    'approval_decided',
                    NEW.approver_id,
                    NEW.decided_at,
                    jsonb_build_object(
                        'decision',
                        NEW.decision,
                        'approver_id',
                        NEW.approver_id,
                        'justification',
                        NEW.justification,
                        'comment',
                        NEW.comment,
                        'modified_action',
                        NEW.modified_action
                    )
                )
                ON CONFLICT (approval_id, event_type) DO NOTHING;
            END IF;

            IF OLD.callback_delivered_at IS NULL
                AND NEW.callback_delivered_at IS NOT NULL
            THEN
                INSERT INTO approval_audit_events (
                    organization_id,
                    approval_id,
                    event_type,
                    actor_id,
                    occurred_at,
                    payload
                )
                VALUES (
                    NEW.organization_id,
                    NEW.id,
                    'callback_delivered',
                    NULL,
                    NEW.callback_delivered_at,
                    jsonb_build_object(
                        'callback_delivered_at',
                        NEW.callback_delivered_at,
                        'callback_attempt_count',
                        NEW.callback_attempt_count
                    )
                )
                ON CONFLICT (approval_id, event_type) DO NOTHING;
            END IF;

            IF OLD.status = 'decided'
                AND NEW.status IN (
                    'execution_succeeded',
                    'execution_failed'
                )
            THEN
                INSERT INTO approval_audit_events (
                    organization_id,
                    approval_id,
                    event_type,
                    actor_id,
                    occurred_at,
                    payload
                )
                VALUES (
                    NEW.organization_id,
                    NEW.id,
                    NEW.status::text,
                    NULL,
                    NEW.execution_completed_at,
                    jsonb_build_object(
                        'decision',
                        NEW.decision,
                        'execution_idempotency_key',
                        NEW.execution_idempotency_key,
                        'execution_reference',
                        NEW.execution_reference,
                        'execution_error_code',
                        NEW.execution_error_code
                    )
                )
                ON CONFLICT (approval_id, event_type) DO NOTHING;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public;
        """
    )

    op.execute(
        """
        CREATE TRIGGER emit_approval_audit_event_trigger
        AFTER INSERT OR UPDATE ON approvals
        FOR EACH ROW
        EXECUTE FUNCTION emit_approval_audit_event();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "emit_approval_audit_event_trigger ON approvals"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS emit_approval_audit_event()"
    )

    op.execute(
        "DROP TRIGGER IF EXISTS "
        "guard_approval_execution_fields_trigger ON approvals"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS guard_approval_execution_fields()"
    )

    op.execute(
        "DROP TRIGGER IF EXISTS "
        "guard_security_review_insert_trigger "
        "ON security_reviews"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS guard_security_review_insert()"
    )

    op.execute(
        "DROP TRIGGER IF EXISTS "
        "guard_approval_audit_events_immutable "
        "ON approval_audit_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "guard_security_reviews_immutable "
        "ON security_reviews"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS guard_phase7_immutable_record()"
    )

    op.drop_index(
        "ix_approval_audit_events_org_time",
        table_name="approval_audit_events",
    )
    op.drop_table("approval_audit_events")

    op.drop_index(
        "ix_security_reviews_org_created",
        table_name="security_reviews",
    )
    op.drop_table("security_reviews")

    op.drop_constraint(
        "ck_approvals_execution_result_state",
        "approvals",
        type_="check",
    )
    op.drop_index(
        "uq_approvals_execution_idempotency_key",
        table_name="approvals",
    )

    op.drop_column(
        "approvals",
        "execution_error_code",
    )
    op.drop_column(
        "approvals",
        "execution_reference",
    )
    op.drop_column(
        "approvals",
        "execution_completed_at",
    )
    op.drop_column(
        "approvals",
        "execution_idempotency_key",
    )
