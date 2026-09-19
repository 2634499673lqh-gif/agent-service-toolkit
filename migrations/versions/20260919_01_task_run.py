"""Create the TaskPilot task_runs table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t032_task_run"
down_revision: str | Sequence[str] | None = "t031_task"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
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
        sa.CheckConstraint("run_number >= 1", name="task_run_number_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="task_run_status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_runs"),
        sa.UniqueConstraint("task_id", "run_number", name="uq_task_runs_task_run_number"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["taskpilot.tasks.id"],
            name="fk_task_runs_task_id_tasks",
            ondelete="RESTRICT",
        ),
        schema="taskpilot",
    )
    op.create_index("ix_task_runs_task_id", "task_runs", ["task_id"], schema="taskpilot")
    op.create_index("ix_task_runs_status", "task_runs", ["status"], schema="taskpilot")
    op.create_index(
        "uq_task_runs_one_active_per_task",
        "task_runs",
        ["task_id"],
        unique=True,
        schema="taskpilot",
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_task_runs_one_active_per_task", table_name="task_runs", schema="taskpilot")
    op.drop_index("ix_task_runs_status", table_name="task_runs", schema="taskpilot")
    op.drop_index("ix_task_runs_task_id", table_name="task_runs", schema="taskpilot")
    op.drop_table("task_runs", schema="taskpilot")
