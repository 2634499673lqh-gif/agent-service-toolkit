"""Add bounded terminal result metadata to TaskRun."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t035_task_run_result"
down_revision: str | Sequence[str] | None = "t034_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_runs",
        sa.Column("result_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="taskpilot",
    )
    op.create_check_constraint(
        "task_run_result_metadata_bounds",
        "task_runs",
        "result_metadata IS NULL OR (jsonb_typeof(result_metadata) = 'object' AND octet_length(result_metadata::text) <= 8192)",
        schema="taskpilot",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_runs_task_run_result_metadata_bounds", "task_runs", schema="taskpilot"
    )
    op.drop_column("task_runs", "result_metadata", schema="taskpilot")
