"""Track last login on users.

Revision ID: 0002
Revises: 0001
Create Date: Phase 2

Small, but it earns its place: "when did this account last authenticate?" is a
standard access-review question, and a compliance platform that cannot answer it
about its own users is not making a good first impression. It is also the first
real exercise of the migration workflow — an incremental, reversible change
rather than a rewrite of 0001.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index: dormant-account queries look for recent logins, and rows
    # that have never logged in are not interesting to them.
    op.create_index(
        "ix_users_org_last_login",
        "users",
        ["organization_id", "last_login_at"],
        postgresql_where=sa.text("last_login_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_org_last_login", table_name="users")
    op.drop_column("users", "last_login_at")
