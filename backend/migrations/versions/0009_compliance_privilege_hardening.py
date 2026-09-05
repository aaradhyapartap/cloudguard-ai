"""Harden immutable compliance-table privileges for the application role.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-05

Existing environments may have granted UPDATE or DELETE broadly to
cloudguard_app before the append-only compliance tables were created.
This forward migration restores the intended SELECT/INSERT-only boundary
without modifying published migrations 0005 or 0006.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'cloudguard_app'
            ) THEN
                REVOKE UPDATE, DELETE
                ON TABLE
                    assessment_score_snapshots,
                    score_overrides
                FROM cloudguard_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Security hardening is intentionally not reversed.
    pass
