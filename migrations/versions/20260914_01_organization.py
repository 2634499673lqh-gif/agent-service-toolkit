"""Create TaskPilot schema and organizations table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t021_organization"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS taskpilot"))
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint("name ~ '[^[:space:]]'", name="organization_name_not_blank"),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        schema="taskpilot",
    )
    op.create_index(
        "ix_taskpilot_organizations_is_active",
        "organizations",
        ["is_active"],
        schema="taskpilot",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_taskpilot_organizations_is_active", table_name="organizations", schema="taskpilot"
    )
    op.drop_table("organizations", schema="taskpilot")
