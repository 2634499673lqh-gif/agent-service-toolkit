"""Application service for the tenant-scoped execution timeline read."""

import json
import re
from collections.abc import Mapping
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.repositories import TaskRunRepository, TraceRepository
from runtime.observability import PricingTable, estimate_cost
from schema.trace_api import TraceEventResponse
from service.logging import redact_text, redact_value
from service.session import CurrentPrincipal

_TRACE_BEARER_TOKEN = re.compile(r"(?i)(\bBearer\s+)([^\s,;]+)")


class TraceService:
    """Read existing observability evidence without adding business authority."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        pricing: PricingTable | None = None,
    ) -> None:
        self.session = session
        self.runs = TaskRunRepository(session)
        self.traces = TraceRepository(session)
        self.pricing = pricing if pricing is not None else PricingTable()

    async def get_trace(
        self,
        principal: CurrentPrincipal,
        task_id: UUID,
        run_id: UUID,
        *,
        limit: int = TraceRepository.DEFAULT_LIMIT,
    ) -> list[TraceEventResponse] | None:
        """Return ``None`` for an invisible run and ``[]`` for an empty trace."""

        visible_run = await self.runs.get_for_task_in_principal_tenant(
            task_id, run_id, principal.organization_id
        )
        if visible_run is None:
            return None
        rows = await self.traces.list_for_task_run_in_principal_tenant(
            task_id,
            run_id,
            principal.organization_id,
            limit=limit,
        )
        return [self._to_response(row) for row in rows]

    def _to_response(self, row: Mapping[str, Any]) -> TraceEventResponse:
        metadata = self._sanitize_metadata(row.get("metadata"))
        usage = self._sanitize_json_object(row.get("usage"))
        estimate = self._estimate(usage, metadata)
        event_kind_value = str(row["event_kind"])
        if event_kind_value not in {"agent_run", "tool_call"}:
            raise ValueError("trace query returned an unknown event kind")
        event_kind = cast(Literal["agent_run", "tool_call"], event_kind_value)
        status = row.get("status")
        if hasattr(status, "value"):
            status = status.value
        if not isinstance(status, str):
            status = "unknown"
        error_class = row.get("error_class")
        if hasattr(error_class, "value"):
            error_class = error_class.value
        return TraceEventResponse(
            event_id=row["event_id"],
            event_kind=event_kind,
            task_id=row["task_id"],
            task_run_id=row["task_run_id"],
            request_id=row.get("request_id"),
            replan_count=row["replan_count"],
            step_position=row["step_position"],
            retry_count=row["retry_count"],
            approval_id=row.get("approval_id"),
            agent_run_id=row["agent_run_id"],
            agent_name=self._sanitize_text(row["agent_name"]) or "[REDACTED]",
            tool_call_id=row["event_id"] if event_kind == "tool_call" else None,
            call_index=row.get("call_index"),
            tool_name=self._sanitize_text(row.get("tool_name")),
            tool_version=self._sanitize_text(row.get("tool_version")),
            status=status,
            started_at=row["started_at"],
            finished_at=row.get("finished_at"),
            duration_ms=row.get("duration_ms"),
            error_class=error_class,
            error_code=self._sanitize_text(row.get("error_code")),
            error_message=self._sanitize_text(row.get("error_message")),
            usage=usage,
            estimate=estimate,
            metadata=metadata,
        )

    def _estimate(
        self,
        usage: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, str] | None:
        if usage is None:
            return None
        metadata = metadata or {}
        identifiers = {
            key: value if isinstance(value, str) else ""
            for key, value in metadata.items()
            if key in {"provider", "model", "version"}
        }
        return estimate_cost(
            usage,
            self.pricing,
            provider=identifiers.get("provider", ""),
            model=identifiers.get("model", ""),
            version=identifiers.get("version", ""),
        )

    @staticmethod
    def _sanitize_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        return _TRACE_BEARER_TOKEN.sub(r"\1[REDACTED]", redact_text(value))

    @staticmethod
    def _sanitize_json_object(value: object) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        sanitized = redact_value(value)
        if not isinstance(sanitized, dict):
            return None

        def redact_bearer(candidate: object) -> object:
            if isinstance(candidate, Mapping):
                return {str(key): redact_bearer(item) for key, item in candidate.items()}
            if isinstance(candidate, list):
                return [redact_bearer(item) for item in candidate]
            if isinstance(candidate, str):
                return _TRACE_BEARER_TOKEN.sub(r"\1[REDACTED]", candidate)
            return candidate

        bounded = redact_bearer(sanitized)
        return bounded if isinstance(bounded, dict) else None

    @classmethod
    def _sanitize_metadata(cls, value: object) -> dict[str, Any] | None:
        sanitized = cls._sanitize_json_object(value)
        if sanitized is None:
            return None
        allowed_keys = {
            "provider",
            "model",
            "version",
            "response_id",
            "request_id",
            "provider_request_id",
        }
        sanitized = {
            key: item
            for key, item in sanitized.items()
            if key in allowed_keys and isinstance(item, str)
        }
        try:
            encoded = json.dumps(
                sanitized,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            return None
        return sanitized if len(encoded) <= 2_048 else None


__all__ = ["TraceService"]
