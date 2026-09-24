"""TaskPilot business ORM models."""

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates

from persistence.base import Base
from persistence.identity import canonicalize_email


def utc_now() -> datetime:
    """Return an aware UTC timestamp for application-managed fields."""

    return datetime.now(UTC)


APPROVAL_JSON_MAX_BYTES = 8192
OBSERVABILITY_JSON_MAX_BYTES = 8192
PROVIDER_METADATA_MAX_BYTES = 2048
MAX_DURATION_MS = 86_400_000
MAX_USAGE_TOKENS = 1_000_000_000_000

_OBSERVABILITY_REDACTED = "[REDACTED]"
_OBSERVABILITY_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|"
    r"credentials?|dsn|connection[_-]?(?:string|url)|password|private[_-]?key|"
    r"secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_OBSERVABILITY_AUTHORIZATION = re.compile(r"(?i)(\bauthorization\s*[:=]\s*bearer\s+)([^\s,;]+)")
_OBSERVABILITY_URL_CREDENTIAL = re.compile(
    r"(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)([^\s/@]+)(@)", re.IGNORECASE
)
_OBSERVABILITY_KEY_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|bearer|password|"
    r"private[_-]?key|secret|token)\b\s*[:=]\s*[\"']?)([^\"'\s,};]+)"
)


def _is_sensitive_observability_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return (
        normalized
        in {
            "apikey",
            "accesstoken",
            "authorization",
            "bearer",
            "credential",
            "credentials",
            "dsn",
            "password",
            "privatekey",
            "secret",
            "token",
        }
        or _OBSERVABILITY_SENSITIVE_KEY.search(key) is not None
    )


def _redact_observability_text(value: str) -> str:
    """Mask common credential forms before an observation reaches JSONB."""

    redacted = _OBSERVABILITY_AUTHORIZATION.sub(rf"\1{_OBSERVABILITY_REDACTED}", value)
    redacted = _OBSERVABILITY_URL_CREDENTIAL.sub(rf"\1{_OBSERVABILITY_REDACTED}\3", redacted)
    return _OBSERVABILITY_KEY_VALUE.sub(rf"\1{_OBSERVABILITY_REDACTED}", redacted)


def _redact_observability_value(value: object, *, key: str | None = None) -> object:
    """Recursively redact secret-bearing payload fields without mutating input."""

    if key is not None and _is_sensitive_observability_key(key):
        return _OBSERVABILITY_REDACTED
    if hasattr(value, "get_secret_value"):
        return _OBSERVABILITY_REDACTED
    if isinstance(value, Mapping):
        return {
            item_key: _redact_observability_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_observability_value(item) for item in value]
    if isinstance(value, str):
        return _redact_observability_text(value)
    return value


def _validate_observability_json_object(value: object, field_name: str) -> dict[str, Any]:
    """Redact and validate one compact JSON object for observation storage."""

    try:
        redacted = _redact_observability_value(value)
    except RecursionError:
        raise ValueError(f"{field_name} must be a JSON object") from None

    def is_json_value(candidate: object) -> bool:
        if candidate is None or isinstance(candidate, (str, bool, int)):
            return True
        if isinstance(candidate, float):
            return math.isfinite(candidate)
        if isinstance(candidate, list):
            return all(is_json_value(item) for item in candidate)
        if isinstance(candidate, dict):
            return all(
                isinstance(key, str) and is_json_value(item) for key, item in candidate.items()
            )
        return False

    if not isinstance(redacted, dict) or not is_json_value(redacted):
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        encoded = json.dumps(
            redacted,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError(f"{field_name} must be a JSON object") from None
    if len(encoded) > OBSERVABILITY_JSON_MAX_BYTES:
        raise ValueError(f"{field_name} exceeds {OBSERVABILITY_JSON_MAX_BYTES} UTF-8 bytes")
    return redacted


def _validate_usage(value: object, field_name: str = "usage") -> dict[str, Any] | None:
    """Validate the normalized T095 usage shape without inventing zero values."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a normalized usage object")
    status = value.get("status")
    if status == "unavailable":
        if set(value) != {"status", "reason"} or value.get("reason") not in {
            "not_returned",
            "unsupported",
            "malformed",
        }:
            raise ValueError(f"{field_name} has an invalid unavailable shape")
        return dict(value)
    if status != "known":
        raise ValueError(f"{field_name} has an invalid status")
    required = {"status", "input_tokens", "output_tokens", "total_tokens"}
    if not required.issubset(value) or set(value) - required - {"cached_input_tokens"}:
        raise ValueError(f"{field_name} has an invalid known shape")
    for key in required - {"status"} | (
        {"cached_input_tokens"} if "cached_input_tokens" in value else set()
    ):
        token_count = value[key]
        if (
            isinstance(token_count, bool)
            or not isinstance(token_count, int)
            or not 0 <= token_count <= MAX_USAGE_TOKENS
        ):
            raise ValueError(f"{field_name}.{key} is out of bounds")
    return dict(value)


def _usage_check_sql(column: str) -> str:
    """Return the database shape check shared by both observation tables."""

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


def _validate_provider_metadata(value: object) -> dict[str, Any] | None:
    """Keep only compact provider/model/response identifiers in metadata."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("provider_metadata must be a JSON object")
    allowed_keys = {
        "provider",
        "model",
        "version",
        "response_id",
        "request_id",
        "provider_request_id",
    }
    if set(value) - allowed_keys:
        raise ValueError("provider_metadata contains a non-allowlisted key")
    sanitized = _validate_observability_json_object(value, "provider_metadata")
    if any(not isinstance(item, str) for item in sanitized.values()):
        raise ValueError("provider_metadata values must be strings")
    try:
        encoded = json.dumps(
            sanitized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError("provider_metadata must be a JSON object") from None
    if len(encoded) > PROVIDER_METADATA_MAX_BYTES:
        raise ValueError(f"provider_metadata exceeds {PROVIDER_METADATA_MAX_BYTES} UTF-8 bytes")
    return sanitized


def _normalize_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _derive_duration_ms(started_at: datetime, finished_at: datetime | None) -> int | None:
    if finished_at is None:
        return None
    if finished_at < started_at:
        raise ValueError("finished_at must not precede started_at")
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    if not 0 <= duration_ms <= MAX_DURATION_MS:
        raise ValueError(f"duration_ms must be between 0 and {MAX_DURATION_MS}")
    return duration_ms


def _validate_approval_json_object(value: object, field_name: str) -> dict[str, Any]:
    """Validate one bounded, canonical-JSON-compatible Approval object."""

    def is_json_value(candidate: object) -> bool:
        if candidate is None or isinstance(candidate, (str, bool, int, float)):
            return True
        if isinstance(candidate, list):
            return all(is_json_value(item) for item in candidate)
        if isinstance(candidate, dict):
            return all(
                isinstance(key, str) and is_json_value(item) for key, item in candidate.items()
            )
        return False

    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        is_valid_object = is_json_value(value)
    except RecursionError:
        is_valid_object = False
    if not is_valid_object:
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        canonical_json = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError(f"{field_name} must be a JSON object") from None
    if len(canonical_json) > APPROVAL_JSON_MAX_BYTES:
        raise ValueError(f"{field_name} exceeds {APPROVAL_JSON_MAX_BYTES} UTF-8 bytes")
    return value


class Role(Enum):
    """Frozen V1 membership roles.

    The stored value is the lowercase role name, so the database column stays
    readable and the PostgreSQL check constraint can enforce the same set.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class TaskStatus(Enum):
    """User-visible overall lifecycle state of a Task."""

    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Organization(Base):
    """Root tenant record for TaskPilot.

    IDs are generated by the application.  Business deletion is intentionally
    represented by ``is_active``; no relationship cascade is defined here.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("name ~ '[^[:space:]]'", name="organization_name_not_blank"),
        Index("ix_taskpilot_organizations_is_active", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class User(Base):
    """TaskPilot user identity and opaque password-hash storage."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email ~ '[^[:space:]]'", name="user_email_not_blank"),
        UniqueConstraint("normalized_email", name="uq_users_normalized_email"),
        Index("ix_taskpilot_users_is_active", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __init__(self, **kwargs: Any) -> None:
        """Construct a user while deriving identity from the application helper."""

        raw_email = kwargs.pop("email", None)
        if not isinstance(raw_email, str):
            raise TypeError("User email must be a string")
        if "normalized_email" in kwargs:
            raise TypeError("User normalized_email is derived from email by canonicalize_email")
        super().__init__(email=raw_email, **kwargs)

    @validates("email")
    def _canonicalize_email(self, key: str, value: str) -> str:
        del key
        display_email, normalized_email = canonicalize_email(value)
        self.normalized_email = normalized_email
        return display_email

    def __repr__(self) -> str:
        """Return a safe representation that never includes the password hash."""

        return f"User(id={self.id!r}, email={self.email!r}, is_active={self.is_active!r})"


class Membership(Base):
    """Formal User-to-Organization link carrying the V1 role.

    A user may belong to several organizations, but the pair is unique and a
    membership is deactivated rather than deleted. Foreign keys are
    non-destructive (``RESTRICT``) so removing a user or organization can never
    silently erase audit-relevant authorization rows.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="membership_role_valid"),
        UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_organization"),
        Index("ix_memberships_user_id", "user_id"),
        Index("ix_memberships_organization_id", "organization_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[Role] = mapped_column(
        sa.Enum(
            Role,
            name="membership_role",
            native_enum=False,
            length=16,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __init__(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        role: Role | str,
        is_active: bool = True,
        **kwargs: Any,
    ) -> None:
        """Build a membership with a validated role and explicit tenant pair."""

        if not isinstance(role, Role):
            role = Role(role)
        super().__init__(
            user_id=user_id,
            organization_id=organization_id,
            role=role,
            is_active=is_active,
            **kwargs,
        )

    def __repr__(self) -> str:
        """Return an inspectable representation; no credential data is involved."""

        return (
            f"Membership(id={self.id!r}, user_id={self.user_id!r}, "
            f"organization_id={self.organization_id!r}, role={self.role!r}, "
            f"is_active={self.is_active!r})"
        )


class AuthSession(Base):
    """Opaque server-side session bound to one user and one membership.

    Only the SHA-256 digest of the raw token is stored.  A session carries no
    authorization claims: organization and role are always re-read from the
    current Membership/Organization rows.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_membership_id", "membership_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    membership_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __init__(
        self,
        *,
        user_id: UUID,
        membership_id: UUID,
        token_hash: str,
        expires_at: datetime,
        revoked_at: datetime | None = None,
        **kwargs: Any,
    ) -> None:
        """Build a session row; the raw token never reaches the model."""

        super().__init__(
            user_id=user_id,
            membership_id=membership_id,
            token_hash=token_hash,
            expires_at=expires_at,
            revoked_at=revoked_at,
            **kwargs,
        )

    def __repr__(self) -> str:
        """Describe the session without exposing the raw token or token digest."""

        return (
            f"AuthSession(id={self.id!r}, user_id={self.user_id!r}, "
            f"membership_id={self.membership_id!r}, expires_at={self.expires_at!r}, "
            f"revoked_at={self.revoked_at!r})"
        )


class Task(Base):
    """Tenant-owned user task; execution attempts are stored by T032."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("title ~ '[^[:space:]]'", name="task_title_not_blank"),
        CheckConstraint(
            "status IN ('draft', 'queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="task_status_valid",
        ),
        Index("ix_tasks_organization_id", "organization_id"),
        Index("ix_tasks_created_by_user_id", "created_by_user_id"),
        Index("ix_tasks_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        sa.Enum(
            TaskStatus,
            name="task_status",
            native_enum=False,
            length=16,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=TaskStatus.DRAFT,
        server_default=text("'draft'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __init__(
        self,
        *,
        organization_id: UUID,
        created_by_user_id: UUID,
        title: str,
        description: str | None = None,
        status: TaskStatus | str = TaskStatus.DRAFT,
        **kwargs: Any,
    ) -> None:
        """Build a task with explicit server-derived ownership fields."""

        if not isinstance(status, TaskStatus):
            status = TaskStatus(status)
        super().__init__(
            organization_id=organization_id,
            created_by_user_id=created_by_user_id,
            title=title,
            description=description,
            status=status,
            **kwargs,
        )


class TaskRunStatus(Enum):
    """Lifecycle state of one durable Task execution attempt."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskRun(Base):
    """One ordered execution attempt belonging to exactly one Task."""

    __tablename__ = "task_runs"
    __table_args__ = (
        CheckConstraint("run_number >= 1", name="task_run_number_positive"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="task_run_status_valid",
        ),
        UniqueConstraint("task_id", "run_number", name="uq_task_runs_task_run_number"),
        Index("ix_task_runs_task_id", "task_id"),
        Index("ix_task_runs_status", "status"),
        Index(
            "uq_task_runs_one_active_per_task",
            "task_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TaskRunStatus] = mapped_column(
        sa.Enum(
            TaskRunStatus,
            name="task_run_status",
            native_enum=False,
            length=16,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=TaskRunStatus.PENDING,
        server_default=text("'pending'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __init__(
        self,
        *,
        task_id: UUID,
        run_number: int,
        status: TaskRunStatus | str = TaskRunStatus.PENDING,
        **kwargs: Any,
    ) -> None:
        """Build a run with an explicit task owner and ordered number."""

        if not isinstance(status, TaskRunStatus):
            status = TaskRunStatus(status)
        super().__init__(
            task_id=task_id,
            run_number=run_number,
            status=status,
            **kwargs,
        )


class AgentRunStatus(Enum):
    """Observational status for one server-selected agent invocation."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WAITING_APPROVAL = "waiting_approval"
    UNKNOWN = "unknown"


class ToolCallStatus(Enum):
    """Observational status for one tool dispatch attempt."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ObservabilityErrorClass(Enum):
    """Normalized runtime error classification stored on observation rows."""

    RETRY = "RETRY"
    REPLAN = "REPLAN"
    TERMINAL = "TERMINAL"
    UNKNOWN = "UNKNOWN"


# Both rows use the same frozen error vocabulary; the aliases keep the model
# names discoverable for callers that reason from either table.
AgentRunErrorClass = ObservabilityErrorClass
ToolCallErrorClass = ObservabilityErrorClass


def _validate_observation_name(value: str, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be non-empty and at most {maximum} characters")
    return value


def _validate_observation_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError("error_code must be non-empty and at most 64 characters")
    return _redact_observability_text(value)


def _validate_observation_error_message(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("error_message must be at most 500 characters")
    if "traceback" in value.casefold() or "\n" in value or "\r" in value:
        raise ValueError("error_message must be a sanitized single-line message")
    return _redact_observability_text(value)


class AgentRun(Base):
    """One immutable observation of a server-selected agent/node invocation."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("replan_count BETWEEN 0 AND 1", name="agent_run_replan_count_range"),
        CheckConstraint("step_position BETWEEN 0 AND 7", name="agent_run_step_position_range"),
        CheckConstraint("retry_count BETWEEN 0 AND 1", name="agent_run_retry_count_range"),
        CheckConstraint("agent_name ~ '[^[:space:]]'", name="agent_run_name_not_blank"),
        CheckConstraint("char_length(agent_name) <= 128", name="agent_run_name_length"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'waiting_approval', 'unknown')",
            name="agent_run_status_valid",
        ),
        CheckConstraint(
            "error_class IS NULL OR error_class IN ('RETRY', 'REPLAN', 'TERMINAL', 'UNKNOWN')",
            name="agent_run_error_class_valid",
        ),
        CheckConstraint(
            "error_code IS NULL OR (error_code ~ '[^[:space:]]' AND char_length(error_code) <= 64)",
            name="agent_run_error_code_bounds",
        ),
        CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 500",
            name="agent_run_error_message_length",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="agent_run_finish_not_before_start",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR (finished_at IS NOT NULL "
            "AND duration_ms BETWEEN 0 AND 86400000 "
            "AND duration_ms = floor(extract(epoch FROM (finished_at - started_at)) * 1000)::integer)",
            name="agent_run_duration_bounds",
        ),
        CheckConstraint(
            "(finished_at IS NULL AND duration_ms IS NULL) OR finished_at IS NOT NULL",
            name="agent_run_duration_requires_finish",
        ),
        CheckConstraint(
            "(status IN ('running', 'succeeded', 'waiting_approval') AND error_class IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "status IN ('failed', 'unknown')",
            name="agent_run_error_consistency",
        ),
        CheckConstraint(_usage_check_sql("usage"), name="agent_run_usage_shape"),
        CheckConstraint(
            "provider_metadata IS NULL OR (jsonb_typeof(provider_metadata) = 'object' "
            "AND octet_length(provider_metadata::text) <= 2048)",
            name="agent_run_provider_metadata_bounds",
        ),
        UniqueConstraint(
            "task_run_id",
            "replan_count",
            "step_position",
            "retry_count",
            "agent_name",
            name="uq_agent_runs_invocation",
        ),
        Index("ix_agent_runs_task_run_id", "task_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.task_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    replan_count: Mapped[int] = mapped_column(Integer, nullable=False)
    step_position: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(
        sa.Enum(
            AgentRunStatus,
            name="agent_run_status",
            native_enum=False,
            create_constraint=False,
            length=16,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=AgentRunStatus.UNKNOWN,
        server_default=text("'unknown'"),
    )
    error_class: Mapped[ObservabilityErrorClass | None] = mapped_column(
        sa.Enum(
            ObservabilityErrorClass,
            name="observability_error_class",
            native_enum=False,
            create_constraint=False,
            length=8,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __init__(
        self,
        *,
        task_run_id: UUID,
        replan_count: int,
        step_position: int,
        retry_count: int,
        agent_name: str,
        started_at: datetime,
        request_id: UUID | None = None,
        status: AgentRunStatus | str = AgentRunStatus.UNKNOWN,
        error_class: ObservabilityErrorClass | str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: datetime | None = None,
        duration_ms: int | None = None,
        usage: dict[str, Any] | None = None,
        provider_metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(status, AgentRunStatus):
            status = AgentRunStatus(status)
        if error_class is not None and not isinstance(error_class, ObservabilityErrorClass):
            error_class = ObservabilityErrorClass(error_class)
        started_at = _normalize_utc(started_at, "started_at")
        finished_at = None if finished_at is None else _normalize_utc(finished_at, "finished_at")
        derived_duration = _derive_duration_ms(started_at, finished_at)
        if duration_ms is not None and duration_ms != derived_duration:
            raise ValueError("duration_ms must be derived from started_at and finished_at")
        agent_name = _validate_observation_name(agent_name, "agent_name", 128)
        error_code = _validate_observation_error_code(error_code)
        error_message = _validate_observation_error_message(error_message)
        if status in {
            AgentRunStatus.RUNNING,
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.WAITING_APPROVAL,
        } and (error_class is not None or error_code is not None or error_message is not None):
            raise ValueError("normal AgentRun observations cannot carry error fields")
        super().__init__(
            task_run_id=task_run_id,
            request_id=request_id,
            replan_count=replan_count,
            step_position=step_position,
            retry_count=retry_count,
            agent_name=agent_name,
            status=status,
            error_class=error_class,
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=derived_duration,
            usage=_validate_usage(usage),
            provider_metadata=_validate_provider_metadata(provider_metadata),
            **kwargs,
        )

    @validates("agent_name")
    def validate_agent_name(self, _key: str, value: str) -> str:
        return _validate_observation_name(value, "agent_name", 128)

    @validates("duration_ms")
    def validate_duration(self, _key: str, value: int | None) -> int | None:
        started_at = getattr(self, "started_at", None)
        finished_at = getattr(self, "finished_at", None)
        expected = None if started_at is None else _derive_duration_ms(started_at, finished_at)
        if value != expected:
            raise ValueError("duration_ms must be derived from started_at and finished_at")
        return value

    @validates("error_code")
    def validate_error_code(self, _key: str, value: str | None) -> str | None:
        return _validate_observation_error_code(value)

    @validates("error_message")
    def validate_error_message(self, _key: str, value: str | None) -> str | None:
        return _validate_observation_error_message(value)

    @validates("usage")
    def validate_usage(self, _key: str, value: object) -> dict[str, Any] | None:
        return _validate_usage(value)

    @validates("provider_metadata")
    def validate_provider_metadata(self, _key: str, value: object) -> dict[str, Any] | None:
        return _validate_provider_metadata(value)

    def __repr__(self) -> str:
        return (
            f"AgentRun(id={self.id!r}, task_run_id={self.task_run_id!r}, "
            f"agent_name={self.agent_name!r}, status={self.status!r})"
        )


class ToolCall(Base):
    """One immutable, sanitized tool-dispatch observation beneath an AgentRun."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint("call_index BETWEEN 0 AND 999", name="tool_call_index_range"),
        CheckConstraint("tool_name ~ '[^[:space:]]'", name="tool_call_name_not_blank"),
        CheckConstraint("char_length(tool_name) <= 128", name="tool_call_name_length"),
        CheckConstraint(
            "tool_version IS NULL OR (tool_version ~ '[^[:space:]]' AND char_length(tool_version) <= 64)",
            name="tool_call_version_bounds",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'unknown')",
            name="tool_call_status_valid",
        ),
        CheckConstraint(
            "error_class IS NULL OR error_class IN ('RETRY', 'REPLAN', 'TERMINAL', 'UNKNOWN')",
            name="tool_call_error_class_valid",
        ),
        CheckConstraint(
            "error_code IS NULL OR (error_code ~ '[^[:space:]]' AND char_length(error_code) <= 64)",
            name="tool_call_error_code_bounds",
        ),
        CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 500",
            name="tool_call_error_message_length",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="tool_call_finish_not_before_start",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR (finished_at IS NOT NULL "
            "AND duration_ms BETWEEN 0 AND 86400000 "
            "AND duration_ms = floor(extract(epoch FROM (finished_at - started_at)) * 1000)::integer)",
            name="tool_call_duration_bounds",
        ),
        CheckConstraint(
            "(finished_at IS NULL AND duration_ms IS NULL) OR finished_at IS NOT NULL",
            name="tool_call_duration_requires_finish",
        ),
        CheckConstraint(
            "(status IN ('running', 'succeeded') AND error_class IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR status IN ('failed', 'unknown')",
            name="tool_call_error_consistency",
        ),
        CheckConstraint("jsonb_typeof(arguments) = 'object'", name="tool_call_arguments_object"),
        CheckConstraint("octet_length(arguments::text) <= 8192", name="tool_call_arguments_size"),
        CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'", name="tool_call_result_object"
        ),
        CheckConstraint(
            "result IS NULL OR octet_length(result::text) <= 8192",
            name="tool_call_result_size",
        ),
        CheckConstraint(_usage_check_sql("usage"), name="tool_call_usage_shape"),
        UniqueConstraint("agent_run_id", "call_index", name="uq_tool_calls_agent_run_index"),
        Index("ix_tool_calls_agent_run_id", "agent_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.agent_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[ToolCallStatus] = mapped_column(
        sa.Enum(
            ToolCallStatus,
            name="tool_call_status",
            native_enum=False,
            create_constraint=False,
            length=9,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ToolCallStatus.UNKNOWN,
        server_default=text("'unknown'"),
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error_class: Mapped[ObservabilityErrorClass | None] = mapped_column(
        sa.Enum(
            ObservabilityErrorClass,
            name="observability_error_class",
            native_enum=False,
            create_constraint=False,
            length=8,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __init__(
        self,
        *,
        agent_run_id: UUID,
        call_index: int,
        tool_name: str,
        started_at: datetime,
        arguments: dict[str, Any],
        tool_version: str | None = None,
        status: ToolCallStatus | str = ToolCallStatus.UNKNOWN,
        finished_at: datetime | None = None,
        duration_ms: int | None = None,
        result: dict[str, Any] | None = None,
        error_class: ObservabilityErrorClass | str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(status, ToolCallStatus):
            status = ToolCallStatus(status)
        if error_class is not None and not isinstance(error_class, ObservabilityErrorClass):
            error_class = ObservabilityErrorClass(error_class)
        started_at = _normalize_utc(started_at, "started_at")
        finished_at = None if finished_at is None else _normalize_utc(finished_at, "finished_at")
        derived_duration = _derive_duration_ms(started_at, finished_at)
        if duration_ms is not None and duration_ms != derived_duration:
            raise ValueError("duration_ms must be derived from started_at and finished_at")
        tool_name = _validate_observation_name(tool_name, "tool_name", 128)
        if tool_version is not None:
            tool_version = _validate_observation_name(tool_version, "tool_version", 64)
        error_code = _validate_observation_error_code(error_code)
        error_message = _validate_observation_error_message(error_message)
        if status in {ToolCallStatus.RUNNING, ToolCallStatus.SUCCEEDED} and (
            error_class is not None or error_code is not None or error_message is not None
        ):
            raise ValueError("normal ToolCall observations cannot carry error fields")
        super().__init__(
            agent_run_id=agent_run_id,
            call_index=call_index,
            tool_name=tool_name,
            tool_version=tool_version,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=derived_duration,
            arguments=_validate_observability_json_object(arguments, "arguments"),
            result=(
                None if result is None else _validate_observability_json_object(result, "result")
            ),
            error_class=error_class,
            error_code=error_code,
            error_message=error_message,
            usage=_validate_usage(usage),
            **kwargs,
        )

    @validates("tool_name")
    def validate_tool_name(self, _key: str, value: str) -> str:
        return _validate_observation_name(value, "tool_name", 128)

    @validates("duration_ms")
    def validate_duration(self, _key: str, value: int | None) -> int | None:
        started_at = getattr(self, "started_at", None)
        finished_at = getattr(self, "finished_at", None)
        expected = None if started_at is None else _derive_duration_ms(started_at, finished_at)
        if value != expected:
            raise ValueError("duration_ms must be derived from started_at and finished_at")
        return value

    @validates("tool_version")
    def validate_tool_version(self, _key: str, value: str | None) -> str | None:
        return None if value is None else _validate_observation_name(value, "tool_version", 64)

    @validates("arguments")
    def validate_arguments(self, _key: str, value: object) -> dict[str, Any]:
        return _validate_observability_json_object(value, "arguments")

    @validates("result")
    def validate_result(self, _key: str, value: object) -> dict[str, Any] | None:
        return None if value is None else _validate_observability_json_object(value, "result")

    @validates("error_code")
    def validate_error_code(self, _key: str, value: str | None) -> str | None:
        return _validate_observation_error_code(value)

    @validates("error_message")
    def validate_error_message(self, _key: str, value: str | None) -> str | None:
        return _validate_observation_error_message(value)

    @validates("usage")
    def validate_usage(self, _key: str, value: object) -> dict[str, Any] | None:
        return _validate_usage(value)

    def __repr__(self) -> str:
        return (
            f"ToolCall(id={self.id!r}, agent_run_id={self.agent_run_id!r}, "
            f"tool_name={self.tool_name!r}, call_index={self.call_index!r}, status={self.status!r})"
        )


class ApprovalRiskLevel(Enum):
    """Risk level for persisted approval proposals; only L2 creates a row."""

    L2 = "L2"


class ApprovalStatus(Enum):
    """Terminal human decision state for one approval proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalActionState(Enum):
    """State of the single approved mock action stored on an Approval."""

    AVAILABLE = "available"
    COMPLETED = "completed"
    FAILED = "failed"


class Approval(Base):
    """Tenant-owned approval evidence linked to a TaskRun, without tenant duplication."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "replan_count BETWEEN 0 AND 1",
            name="approval_replan_count_range",
        ),
        CheckConstraint("step_position >= 0", name="approval_step_position_nonnegative"),
        CheckConstraint("action_name ~ '[^[:space:]]'", name="approval_action_name_not_blank"),
        CheckConstraint(
            "action_version ~ '[^[:space:]]'",
            name="approval_action_version_not_blank",
        ),
        CheckConstraint(
            "char_length(action_name) <= 128 AND char_length(action_version) <= 64",
            name="approval_action_identity_length",
        ),
        CheckConstraint("risk_level IN ('L2')", name="approval_risk_level_valid"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="approval_status_valid",
        ),
        CheckConstraint(
            "action_state IN ('available', 'completed', 'failed')",
            name="approval_action_state_valid",
        ),
        CheckConstraint(
            "decision_reason IS NULL OR char_length(decision_reason) <= 500",
            name="approval_decision_reason_length",
        ),
        CheckConstraint(
            "jsonb_typeof(proposed_action) = 'object'",
            name="approval_proposed_action_object",
        ),
        CheckConstraint(
            "outcome IS NULL OR jsonb_typeof(outcome) = 'object'",
            name="approval_outcome_object",
        ),
        CheckConstraint(
            "(status = 'pending' AND decider_membership_id IS NULL "
            "AND decided_at IS NULL AND decision_reason IS NULL) OR "
            "(status IN ('approved', 'rejected') AND "
            "decider_membership_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="approval_decision_fields_consistent",
        ),
        CheckConstraint(
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
        UniqueConstraint(
            "task_run_id",
            "replan_count",
            "step_position",
            name="uq_approvals_run_replan_step",
        ),
        Index("ix_approvals_task_run_id_status", "task_run_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.task_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    replan_count: Mapped[int] = mapped_column(Integer, nullable=False)
    step_position: Mapped[int] = mapped_column(Integer, nullable=False)
    action_name: Mapped[str] = mapped_column(String(128), nullable=False)
    action_version: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_action: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[ApprovalRiskLevel] = mapped_column(
        sa.Enum(
            ApprovalRiskLevel,
            name="approval_risk_level",
            native_enum=False,
            create_constraint=False,
            length=2,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    requester_membership_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        sa.Enum(
            ApprovalStatus,
            name="approval_status",
            native_enum=False,
            create_constraint=False,
            length=8,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ApprovalStatus.PENDING,
        server_default=text("'pending'"),
    )
    decider_membership_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("taskpilot.memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    action_state: Mapped[ApprovalActionState] = mapped_column(
        sa.Enum(
            ApprovalActionState,
            name="approval_action_state",
            native_enum=False,
            create_constraint=False,
            length=9,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ApprovalActionState.AVAILABLE,
        server_default=text("'available'"),
    )
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    action_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __init__(
        self,
        *,
        task_run_id: UUID,
        replan_count: int,
        step_position: int,
        action_name: str,
        action_version: str,
        proposed_action: dict[str, Any],
        requester_membership_id: UUID,
        risk_level: ApprovalRiskLevel | str = ApprovalRiskLevel.L2,
        status: ApprovalStatus | str = ApprovalStatus.PENDING,
        action_state: ApprovalActionState | str = ApprovalActionState.AVAILABLE,
        **kwargs: Any,
    ) -> None:
        """Build one Approval row with explicit run and actor ownership."""

        if not isinstance(risk_level, ApprovalRiskLevel):
            risk_level = ApprovalRiskLevel(risk_level)
        if not isinstance(status, ApprovalStatus):
            status = ApprovalStatus(status)
        if not isinstance(action_state, ApprovalActionState):
            action_state = ApprovalActionState(action_state)
        super().__init__(
            task_run_id=task_run_id,
            replan_count=replan_count,
            step_position=step_position,
            action_name=action_name,
            action_version=action_version,
            proposed_action=proposed_action,
            requester_membership_id=requester_membership_id,
            risk_level=risk_level,
            status=status,
            action_state=action_state,
            **kwargs,
        )

    @validates("action_name")
    def validate_action_name(self, _key: str, value: str) -> str:
        if not value.strip() or len(value) > 128:
            raise ValueError("action_name must be non-empty and at most 128 characters")
        return value

    @validates("action_version")
    def validate_action_version(self, _key: str, value: str) -> str:
        if not value.strip() or len(value) > 64:
            raise ValueError("action_version must be non-empty and at most 64 characters")
        return value

    @validates("decision_reason")
    def validate_decision_reason(self, _key: str, value: str | None) -> str | None:
        if value is not None and len(value) > 500:
            raise ValueError("decision_reason must be at most 500 characters")
        return value

    @validates("proposed_action")
    def validate_proposed_action(self, _key: str, value: object) -> dict[str, Any]:
        return _validate_approval_json_object(value, "proposed_action")

    @validates("outcome")
    def validate_outcome(self, _key: str, value: object | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_approval_json_object(value, "outcome")

    def __repr__(self) -> str:
        """Expose approval identity/state, never its proposed or outcome payload."""

        return (
            f"Approval(id={self.id!r}, task_run_id={self.task_run_id!r}, "
            f"status={self.status!r}, action_state={self.action_state!r})"
        )
