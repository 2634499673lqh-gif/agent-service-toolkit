"""Add durable worker lease fields to TaskRun."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t038_task_run_worker"
down_revision: str | Sequence[str] | None = "t037_proposal_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_runs",
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        schema="taskpilot",
    )
    op.add_column(
        "task_runs",
        sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="taskpilot",
    )
    op.add_column(
        "task_runs",
        sa.Column(
            "worker_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema="taskpilot",
    )
    op.create_check_constraint(
        "task_run_worker_attempts_nonnegative",
        "task_runs",
        "worker_attempts >= 0",
        schema="taskpilot",
    )
    op.create_index(
        "ix_task_runs_worker_lease",
        "task_runs",
        ["status", "worker_lease_expires_at", "created_at"],
        schema="taskpilot",
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_task_runs_worker_lease", table_name="task_runs", schema="taskpilot")
    op.drop_constraint(
        "task_run_worker_attempts_nonnegative",
        "task_runs",
        schema="taskpilot",
        type_="check",
    )
    op.drop_column("task_runs", "worker_attempts", schema="taskpilot")
    op.drop_column("task_runs", "worker_lease_expires_at", schema="taskpilot")
    op.drop_column("task_runs", "worker_id", schema="taskpilot")
