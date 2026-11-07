"""Normalize least-privilege access for the application database role.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-07

Migrations 0001-0004 predate the cloudguard_app privilege convention, while
the original CI bootstrap granted blanket CRUD after every migration. A clean
database could therefore leave foundational tables unusable and later security
revocations ineffective.

This forward migration makes Alembic the sole owner of table privileges. It
resets direct and PUBLIC access before granting only operations exercised by the
application. Organizations and compliance definitions remain control-plane
data. No runtime table receives DELETE, TRUNCATE, REFERENCES, or TRIGGER.
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        REVOKE ALL
        ON TABLE
            organizations,
            users,
            documents,
            document_chunks,
            compliance_frameworks,
            compliance_controls,
            compliance_assessments,
            control_assessments,
            evidence_references,
            assessment_score_snapshots,
            score_overrides,
            approvals,
            security_reviews,
            approval_audit_events
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
                REVOKE ALL
                ON TABLE
                    organizations,
                    users,
                    documents,
                    document_chunks,
                    compliance_frameworks,
                    compliance_controls,
                    compliance_assessments,
                    control_assessments,
                    evidence_references,
                    assessment_score_snapshots,
                    score_overrides,
                    approvals,
                    security_reviews,
                    approval_audit_events
                FROM cloudguard_app;

                GRANT SELECT
                ON TABLE
                    organizations,
                    compliance_frameworks,
                    compliance_controls,
                    approval_audit_events
                TO cloudguard_app;

                GRANT SELECT, INSERT
                ON TABLE
                    evidence_references,
                    assessment_score_snapshots,
                    score_overrides,
                    security_reviews
                TO cloudguard_app;

                GRANT SELECT, INSERT, UPDATE
                ON TABLE
                    users,
                    documents,
                    document_chunks,
                    compliance_assessments,
                    control_assessments,
                    approvals
                TO cloudguard_app;

                REVOKE ALL
                ON SEQUENCE approval_audit_events_id_seq
                FROM cloudguard_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Restore the table grants produced by migrations through 0011."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'cloudguard_app'
            ) THEN
                REVOKE ALL
                ON TABLE
                    organizations,
                    users,
                    documents,
                    document_chunks,
                    compliance_frameworks,
                    compliance_controls,
                    compliance_assessments,
                    control_assessments,
                    evidence_references,
                    assessment_score_snapshots,
                    score_overrides,
                    approvals,
                    security_reviews,
                    approval_audit_events
                FROM cloudguard_app;

                GRANT SELECT, INSERT, UPDATE, DELETE
                ON TABLE
                    compliance_frameworks,
                    compliance_controls,
                    compliance_assessments,
                    control_assessments,
                    evidence_references
                TO cloudguard_app;

                GRANT SELECT, INSERT
                ON TABLE
                    assessment_score_snapshots,
                    score_overrides,
                    security_reviews
                TO cloudguard_app;

                GRANT SELECT, INSERT, UPDATE
                ON TABLE approvals
                TO cloudguard_app;

                GRANT SELECT
                ON TABLE approval_audit_events
                TO cloudguard_app;

                REVOKE ALL
                ON SEQUENCE approval_audit_events_id_seq
                FROM cloudguard_app;
            END IF;
        END
        $$;
        """
    )
