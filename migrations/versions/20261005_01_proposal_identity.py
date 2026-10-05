"""Add a stable identity for one conversational proposal instance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t037_proposal_identity"
down_revision: str | Sequence[str] | None = "t036_confirmed_intent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="taskpilot",
    )
    op.create_index(
        "ix_tasks_proposal_scope",
        "tasks",
        ["organization_id", "created_by_user_id", "proposal_id"],
        schema="taskpilot",
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_proposal_scope", table_name="tasks", schema="taskpilot")
    op.drop_column("tasks", "proposal_id", schema="taskpilot")
