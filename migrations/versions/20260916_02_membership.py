"""Create the TaskPilot memberships table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t022a_membership"
down_revision: str | Sequence[str] | None = "t022_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
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
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name="membership_role_valid"),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_organization"),
        # RESTRICT keeps user/organization removal from silently erasing
        # authorization history; V1 deactivates instead of deleting.
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["taskpilot.organizations.id"],
            name="fk_memberships_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["taskpilot.users.id"],
            name="fk_memberships_user_id_users",
            ondelete="RESTRICT",
        ),
        schema="taskpilot",
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"], schema="taskpilot")
    op.create_index(
        "ix_memberships_organization_id", "memberships", ["organization_id"], schema="taskpilot"
    )


def downgrade() -> None:
    op.drop_index("ix_memberships_organization_id", table_name="memberships", schema="taskpilot")
    op.drop_index("ix_memberships_user_id", table_name="memberships", schema="taskpilot")
    op.drop_table("memberships", schema="taskpilot")
