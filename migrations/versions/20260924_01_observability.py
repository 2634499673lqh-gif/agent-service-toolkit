"""Create the TaskPilot AgentRun and ToolCall observability tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t034_observability"
down_revision: str | Sequence[str] | None = "t033_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _usage_check(column: str) -> str:
    def bounded_token(key: str) -> str:
        return (
            f"jsonb_typeof({column}->'{key}') = 'number' AND "
            f"CASE WHEN ({column}->>'{key}') ~ '^[0-9]+$' "
            f"THEN (({column}->>'{key}')::numeric BETWEEN 0 AND 1000000000000) "
            "ELSE FALSE END"
        )

    return (
        f"{column} IS NULL OR (jsonb_typeof({column}) = 'object' AND ("
        f"(({column}->>'status') = 'unavailable' AND "
        f"({column}->>'reason') IN ('not_returned', 'unsupported', 'malformed') AND "
        f"({column} - ARRAY['status', 'reason']::text[]) = '{{}}'::jsonb) OR "
        f"(({column}->>'status') = 'known' AND "
        f"({column} ? 'input_tokens') AND ({column} ? 'output_tokens') AND "
        f"({column} ? 'total_tokens') AND {bounded_token('input_tokens')} AND "
        f"{bounded_token('output_tokens')} AND {bounded_token('total_tokens')} AND "
        f"(NOT ({column} ? 'cached_input_tokens') OR {bounded_token('cached_input_tokens')}) AND "
        f"({column} - ARRAY['status', 'input_tokens', 'output_tokens', 'total_tokens', "
        f"'cached_input_tokens']::text[]) = '{{}}'::jsonb))"
        ")"
    )


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("replan_count", sa.Integer(), nullable=False),
        sa.Column("step_position", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("error_class", sa.String(length=8), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provider_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint("replan_count BETWEEN 0 AND 1", name="agent_run_replan_count_range"),
        sa.CheckConstraint("step_position BETWEEN 0 AND 7", name="agent_run_step_position_range"),
        sa.CheckConstraint("retry_count BETWEEN 0 AND 1", name="agent_run_retry_count_range"),
        sa.CheckConstraint("agent_name ~ '[^[:space:]]'", name="agent_run_name_not_blank"),
        sa.CheckConstraint("char_length(agent_name) <= 128", name="agent_run_name_length"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'waiting_approval', 'unknown')",
            name="agent_run_status_valid",
        ),
        sa.CheckConstraint(
            "error_class IS NULL OR error_class IN ('RETRY', 'REPLAN', 'TERMINAL', 'UNKNOWN')",
            name="agent_run_error_class_valid",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR (error_code ~ '[^[:space:]]' AND char_length(error_code) <= 64)",
            name="agent_run_error_code_bounds",
        ),
        sa.CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 500",
            name="agent_run_error_message_length",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="agent_run_finish_not_before_start",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR (finished_at IS NOT NULL "
            "AND duration_ms BETWEEN 0 AND 86400000 "
            "AND duration_ms = floor(extract(epoch FROM (finished_at - started_at)) * 1000)::integer)",
            name="agent_run_duration_bounds",
        ),
        sa.CheckConstraint(
            "(finished_at IS NULL AND duration_ms IS NULL) OR finished_at IS NOT NULL",
            name="agent_run_duration_requires_finish",
        ),
        sa.CheckConstraint(
            "(status IN ('running', 'succeeded', 'waiting_approval') AND error_class IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "status IN ('failed', 'unknown')",
            name="agent_run_error_consistency",
        ),
        sa.CheckConstraint(_usage_check("usage"), name="agent_run_usage_shape"),
        sa.CheckConstraint(
            "provider_metadata IS NULL OR (jsonb_typeof(provider_metadata) = 'object' "
            "AND octet_length(provider_metadata::text) <= 2048)",
            name="agent_run_provider_metadata_bounds",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.UniqueConstraint(
            "task_run_id",
            "replan_count",
            "step_position",
            "retry_count",
            "agent_name",
            name="uq_agent_runs_invocation",
        ),
        sa.ForeignKeyConstraint(
            ["task_run_id"],
            ["taskpilot.task_runs.id"],
            name="fk_agent_runs_task_run_id_task_runs",
            ondelete="RESTRICT",
        ),
        schema="taskpilot",
    )
    op.create_index("ix_agent_runs_task_run_id", "agent_runs", ["task_run_id"], schema="taskpilot")

    op.create_table(
        "tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=9), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_class", sa.String(length=8), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint("call_index BETWEEN 0 AND 999", name="tool_call_index_range"),
        sa.CheckConstraint("tool_name ~ '[^[:space:]]'", name="tool_call_name_not_blank"),
        sa.CheckConstraint("char_length(tool_name) <= 128", name="tool_call_name_length"),
        sa.CheckConstraint(
            "tool_version IS NULL OR (tool_version ~ '[^[:space:]]' AND char_length(tool_version) <= 64)",
            name="tool_call_version_bounds",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'unknown')",
            name="tool_call_status_valid",
        ),
        sa.CheckConstraint(
            "error_class IS NULL OR error_class IN ('RETRY', 'REPLAN', 'TERMINAL', 'UNKNOWN')",
            name="tool_call_error_class_valid",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR (error_code ~ '[^[:space:]]' AND char_length(error_code) <= 64)",
            name="tool_call_error_code_bounds",
        ),
        sa.CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 500",
            name="tool_call_error_message_length",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="tool_call_finish_not_before_start",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR (finished_at IS NOT NULL "
            "AND duration_ms BETWEEN 0 AND 86400000 "
            "AND duration_ms = floor(extract(epoch FROM (finished_at - started_at)) * 1000)::integer)",
            name="tool_call_duration_bounds",
        ),
        sa.CheckConstraint(
            "(finished_at IS NULL AND duration_ms IS NULL) OR finished_at IS NOT NULL",
            name="tool_call_duration_requires_finish",
        ),
        sa.CheckConstraint(
            "(status IN ('running', 'succeeded') AND error_class IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "status IN ('failed', 'unknown')",
            name="tool_call_error_consistency",
        ),
        sa.CheckConstraint("jsonb_typeof(arguments) = 'object'", name="tool_call_arguments_object"),
        sa.CheckConstraint(
            "octet_length(arguments::text) <= 8192", name="tool_call_arguments_size"
        ),
        sa.CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'", name="tool_call_result_object"
        ),
        sa.CheckConstraint(
            "result IS NULL OR octet_length(result::text) <= 8192", name="tool_call_result_size"
        ),
        sa.CheckConstraint(_usage_check("usage"), name="tool_call_usage_shape"),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
        sa.UniqueConstraint("agent_run_id", "call_index", name="uq_tool_calls_agent_run_index"),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["taskpilot.agent_runs.id"],
            name="fk_tool_calls_agent_run_id_agent_runs",
            ondelete="RESTRICT",
        ),
        schema="taskpilot",
    )
    op.create_index(
        "ix_tool_calls_agent_run_id", "tool_calls", ["agent_run_id"], schema="taskpilot"
    )


def downgrade() -> None:
    op.drop_index("ix_tool_calls_agent_run_id", table_name="tool_calls", schema="taskpilot")
    op.drop_table("tool_calls", schema="taskpilot")
    op.drop_index("ix_agent_runs_task_run_id", table_name="agent_runs", schema="taskpilot")
    op.drop_table("agent_runs", schema="taskpilot")
