"""Create the TaskPilot auth_sessions table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t023_auth_session"
down_revision: str | Sequence[str] | None = "t022a_membership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        # RESTRICT keeps revocation/audit rows from disappearing silently when a
        # user or membership is removed; V1 revokes and deactivates instead.
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["taskpilot.memberships.id"],
            name="fk_auth_sessions_membership_id_memberships",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["taskpilot.users.id"],
            name="fk_auth_sessions_user_id_users",
            ondelete="RESTRICT",
        ),
        schema="taskpilot",
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], schema="taskpilot")
    op.create_index(
        "ix_auth_sessions_membership_id", "auth_sessions", ["membership_id"], schema="taskpilot"
    )
    op.create_index(
        "ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"], schema="taskpilot"
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions", schema="taskpilot")
    op.drop_index("ix_auth_sessions_membership_id", table_name="auth_sessions", schema="taskpilot")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions", schema="taskpilot")
    op.drop_table("auth_sessions", schema="taskpilot")
