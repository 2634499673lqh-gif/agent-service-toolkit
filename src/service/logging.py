"""Small stdlib-only helpers for structured, request-correlated logging."""

from __future__ import annotations

import json
import logging
import re
import traceback
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"

_request_id: ContextVar[str | None] = ContextVar("taskpilot_request_id", default=None)
_registered_secrets: set[str] = set()
_original_record_factory: Callable[..., logging.LogRecord] | None = None

_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|"
    r"credentials?|dsn|connection[_-]?(?:string|url)|password|private[_-]?key|"
    r"secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(r"(?i)(\bauthorization\s*[:=]\s*bearer\s+)([^\s,;]+)")
_URL_CREDENTIAL = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)([^\s/@]+)(@)", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|bearer|password|"
    r"private[_-]?key|secret|token)\b\s*[:=]\s*[\"']?)([^\"'\s,};]+)"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|password|secret|token)=)([^&\s]+)"
)


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind a request ID to the current async context and return its reset token."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request context."""
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def redact_text(value: str) -> str:
    """Redact known secret values and common credential-bearing string forms."""
    redacted = value
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        redacted = redacted.replace(secret, REDACTED)
    redacted = _AUTHORIZATION.sub(rf"\1{REDACTED}", redacted)
    redacted = _URL_CREDENTIAL.sub(rf"\1{REDACTED}\3", redacted)
    redacted = _KEY_VALUE.sub(rf"\1{REDACTED}", redacted)
    return _QUERY_SECRET.sub(rf"\1{REDACTED}", redacted)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact sensitive mapping values, lists, and strings."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if hasattr(value, "get_secret_value"):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def register_secrets(values: Iterable[str]) -> None:
    """Register non-empty secret values without exposing them in logs."""
    _registered_secrets.update(value for value in values if value)


def _secrets_from_settings(settings: Any) -> list[str]:
    values: list[str] = []
    for value in vars(settings).values():
        getter = getattr(value, "get_secret_value", None)
        if callable(getter):
            secret = getter()
            if isinstance(secret, str):
                values.append(secret)
    return values


class StructuredFormatter(logging.Formatter):
    """Emit one JSON object per log record with safe diagnostic fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": getattr(record, "request_id", None),
        }
        for field in ("event", "method", "path", "status_code"):
            if hasattr(record, field):
                payload[field] = redact_value(getattr(record, field), key=field)
        if record.exc_info:
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Exception",
                "message": redact_text(self.formatException(record.exc_info)),
            }
        return json.dumps(payload, ensure_ascii=False, default=str)


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    assert _original_record_factory is not None
    record = _original_record_factory(*args, **kwargs)
    record.request_id = current_request_id()
    # Keep the original format string intact; redacting it before interpolation
    # can remove a literal format value such as ``Bearer`` and break ``%s``.
    # Arguments are sanitized before interpolation, and the formatter performs
    # a final pass over the rendered message.
    if record.args:
        record.args = redact_value(record.args)
    else:
        record.msg = redact_value(record.msg)
    if record.exc_info:
        # Populate the standard LogRecord cache with a safe traceback so a
        # handler added later cannot bypass exception redaction.
        record.exc_text = redact_text("".join(traceback.format_exception(*record.exc_info)))
    return record


def configure_logging(settings: Any | None = None, *, secret_values: Iterable[str] = ()) -> None:
    """Install the minimal structured logging policy on existing root handlers."""
    global _original_record_factory
    if settings is not None:
        register_secrets(_secrets_from_settings(settings))
    register_secrets(secret_values)

    if _original_record_factory is None:
        _original_record_factory = logging.getLogRecordFactory()
        logging.setLogRecordFactory(_record_factory)

    formatter = StructuredFormatter()
    for handler in logging.getLogger().handlers:
        if not getattr(handler, "_taskpilot_structured", False):
            handler.setFormatter(formatter)
            setattr(handler, "_taskpilot_structured", True)
