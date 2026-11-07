"""Harden approval-table privileges for the application role.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05

Existing environments may already have granted DELETE broadly to
cloudguard_app before the approvals table was created. This forward
migration explicitly removes that privilege without modifying the
published 0007 migration.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
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
                REVOKE DELETE
                ON TABLE approvals
                FROM cloudguard_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Security hardening is intentionally not reversed.
    pass
