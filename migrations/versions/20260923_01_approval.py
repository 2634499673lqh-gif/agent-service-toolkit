"""Create the TaskPilot Approval persistence table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t033_approval"
down_revision: str | Sequence[str] | None = "t032_task_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("replan_count", sa.Integer(), nullable=False),
        sa.Column("step_position", sa.Integer(), nullable=False),
        sa.Column("action_name", sa.String(length=128), nullable=False),
        sa.Column("action_version", sa.String(length=64), nullable=False),
        sa.Column("proposed_action", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_level", sa.String(length=2), nullable=False),
        sa.Column("requester_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=8),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("decider_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
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
        sa.Column(
            "action_state",
            sa.String(length=9),
            server_default=sa.text("'available'"),
            nullable=False,
        ),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("action_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "replan_count BETWEEN 0 AND 1",
            name="approval_replan_count_range",
        ),
        sa.CheckConstraint(
            "step_position >= 0",
            name="approval_step_position_nonnegative",
        ),
        sa.CheckConstraint(
            "action_name ~ '[^[:space:]]'",
            name="approval_action_name_not_blank",
        ),
        sa.CheckConstraint(
            "action_version ~ '[^[:space:]]'",
            name="approval_action_version_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(action_name) <= 128 AND char_length(action_version) <= 64",
            name="approval_action_identity_length",
        ),
        sa.CheckConstraint("risk_level IN ('L2')", name="approval_risk_level_valid"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="approval_status_valid",
        ),
        sa.CheckConstraint(
            "action_state IN ('available', 'completed', 'failed')",
            name="approval_action_state_valid",
        ),
        sa.CheckConstraint(
            "decision_reason IS NULL OR char_length(decision_reason) <= 500",
            name="approval_decision_reason_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proposed_action) = 'object'",
            name="approval_proposed_action_object",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR jsonb_typeof(outcome) = 'object'",
            name="approval_outcome_object",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decider_membership_id IS NULL "
            "AND decided_at IS NULL AND decision_reason IS NULL) OR "
            "(status IN ('approved', 'rejected') AND "
            "decider_membership_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="approval_decision_fields_consistent",
        ),
        sa.CheckConstraint(
            "(action_state = 'available' AND outcome IS NULL "
            "AND action_finished_at IS NULL) OR "
            "(action_state = 'completed' AND status = 'approved' "
            "AND outcome IS NOT NULL AND action_finished_at IS NOT NULL "
            "AND outcome ? 'step_position' AND outcome ? 'success' "
            "AND (outcome - ARRAY['step_position', 'success', 'output', "
            "'error_code', 'error_message']::text[]) = '{}'::jsonb "
            "AND jsonb_typeof(outcome->'step_position') = 'number' "
            "AND outcome->'step_position' = to_jsonb(step_position + 1) "
            "AND jsonb_typeof(outcome->'success') = 'boolean' "
            "AND outcome->'success' = 'true'::jsonb "
            "AND jsonb_typeof(outcome->'output') = 'string' "
            "AND char_length(outcome->>'output') <= 2000 "
            "AND (outcome->'error_code' IS NULL "
            "OR outcome->'error_code' = 'null'::jsonb) "
            "AND (outcome->'error_message' IS NULL "
            "OR outcome->'error_message' = 'null'::jsonb)) OR "
            "(action_state = 'failed' AND status = 'approved' "
            "AND outcome IS NOT NULL AND action_finished_at IS NOT NULL "
            "AND outcome ? 'step_position' AND outcome ? 'success' "
            "AND (outcome - ARRAY['step_position', 'success', 'output', "
            "'error_code', 'error_message']::text[]) = '{}'::jsonb "
            "AND jsonb_typeof(outcome->'step_position') = 'number' "
            "AND outcome->'step_position' = to_jsonb(step_position + 1) "
            "AND jsonb_typeof(outcome->'success') = 'boolean' "
            "AND outcome->'success' = 'false'::jsonb "
            "AND (outcome->'output' IS NULL "
            "OR outcome->'output' = 'null'::jsonb) "
            "AND jsonb_typeof(outcome->'error_code') = 'string' "
            "AND outcome->>'error_code' ~ '[^[:space:]]' "
            "AND char_length(outcome->>'error_code') <= 64 "
            "AND (outcome->'error_message' IS NULL "
            "OR outcome->'error_message' = 'null'::jsonb "
            "OR (jsonb_typeof(outcome->'error_message') = 'string' "
            "AND char_length(outcome->>'error_message') <= 500)))",
            name="approval_action_outcome_consistent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.UniqueConstraint(
            "task_run_id",
            "replan_count",
            "step_position",
            name="uq_approvals_run_replan_step",
        ),
        sa.ForeignKeyConstraint(
            ["task_run_id"],
            ["taskpilot.task_runs.id"],
            name="fk_approvals_task_run_id_task_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requester_membership_id"],
            ["taskpilot.memberships.id"],
            name="fk_approvals_requester_membership_id_memberships",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decider_membership_id"],
            ["taskpilot.memberships.id"],
            name="fk_approvals_decider_membership_id_memberships",
            ondelete="RESTRICT",
        ),
        schema="taskpilot",
    )
    op.create_index(
        "ix_approvals_task_run_id_status",
        "approvals",
        ["task_run_id", "status"],
        schema="taskpilot",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approvals_task_run_id_status",
        table_name="approvals",
        schema="taskpilot",
    )
    op.drop_table("approvals", schema="taskpilot")
