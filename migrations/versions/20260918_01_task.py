"""Create the TaskPilot tasks table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t031_task"
down_revision: str | Sequence[str] | None = "t023_auth_session"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False
        ),
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
        sa.CheckConstraint("title ~ '[^[:space:]]'", name="task_title_not_blank"),
        sa.CheckConstraint(
            "status IN ('draft', 'queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="task_status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["taskpilot.organizations.id"],
            name="fk_tasks_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["taskpilot.users.id"],
            name="fk_tasks_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        schema="taskpilot",
    )
    op.create_index("ix_tasks_organization_id", "tasks", ["organization_id"], schema="taskpilot")
    op.create_index(
        "ix_tasks_created_by_user_id", "tasks", ["created_by_user_id"], schema="taskpilot"
    )
    op.create_index("ix_tasks_status", "tasks", ["status"], schema="taskpilot")


def downgrade() -> None:
    op.drop_index("ix_tasks_status", table_name="tasks", schema="taskpilot")
    op.drop_index("ix_tasks_created_by_user_id", table_name="tasks", schema="taskpilot")
    op.drop_index("ix_tasks_organization_id", table_name="tasks", schema="taskpilot")
    op.drop_table("tasks", schema="taskpilot")
