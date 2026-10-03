"""Add trusted nullable execution intent to Task."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t036_confirmed_intent"
down_revision: str | Sequence[str] | None = "t035_task_run_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("confirmed_intent", postgresql.JSONB(none_as_null=True), nullable=True),
        schema="taskpilot",
    )
    op.create_check_constraint(
        "task_confirmed_intent_bounds",
        "tasks",
        "confirmed_intent IS NULL OR (jsonb_typeof(confirmed_intent) = 'object' "
        "AND octet_length(confirmed_intent::text) <= 4096)",
        schema="taskpilot",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_task_confirmed_intent_bounds", "tasks", schema="taskpilot")
    op.drop_column("tasks", "confirmed_intent", schema="taskpilot")
