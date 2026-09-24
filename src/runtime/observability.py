"""Small pure normalizers shared by runtime observations and provider adapters."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from persistence.models import _redact_observability_text

MAX_USAGE_TOKENS = 1_000_000_000_000
MAX_DURATION_MS = 86_400_000
PROVIDER_METADATA_MAX_BYTES = 2_048

UsageUnavailableReason = Literal["not_returned", "unsupported", "malformed"]


class ProviderUsage(BaseModel):
    """Normalized provider usage returned by :func:`normalize_provider_usage`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["known"]
    input_tokens: int = Field(ge=0, le=MAX_USAGE_TOKENS)
    output_tokens: int = Field(ge=0, le=MAX_USAGE_TOKENS)
    total_tokens: int = Field(ge=0, le=MAX_USAGE_TOKENS)
    cached_input_tokens: int | None = Field(default=None, ge=0, le=MAX_USAGE_TOKENS)


class ProviderUsageUnavailable(BaseModel):
    """Explicitly unavailable provider usage; it is never a numeric zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["unavailable"]
    reason: UsageUnavailableReason


NormalizedProviderUsage = ProviderUsage | ProviderUsageUnavailable


def unavailable_usage(reason: UsageUnavailableReason) -> dict[str, Any]:
    """Return the canonical unavailable shape as plain JSON data."""

    return {"status": "unavailable", "reason": reason}


def _valid_token(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_USAGE_TOKENS


def normalize_provider_usage(
    raw_usage: object,
    provider_supported: bool = True,
    *,
    supported: bool | None = None,
) -> dict[str, Any]:
    """Normalize one provider usage payload without inventing missing numbers.

    Providers may use the common ``prompt_tokens``/``completion_tokens`` names;
    they are translated to the canonical input/output names. A missing total is
    derived only after both required counts have passed strict bounds.
    """

    if supported is not None:
        provider_supported = supported
    if not provider_supported:
        return unavailable_usage("unsupported")
    if raw_usage is None:
        return unavailable_usage("not_returned")
    if not isinstance(raw_usage, Mapping):
        return unavailable_usage("malformed")

    data: dict[str, Any] = dict(raw_usage)
    nested = data.get("usage")
    if set(data) == {"usage"}:
        if nested is None:
            return unavailable_usage("not_returned")
        if not isinstance(nested, Mapping):
            return unavailable_usage("malformed")
        data = dict(nested)

    aliases = {"prompt_tokens": "input_tokens", "completion_tokens": "output_tokens"}
    for alias, canonical in aliases.items():
        if alias in data:
            if canonical in data:
                return unavailable_usage("malformed")
            data[canonical] = data.pop(alias)

    # Accept an already-normalized unavailable shape from an adapter boundary.
    if data.get("status") == "unavailable":
        if set(data) == {"status", "reason"} and data["reason"] in {
            "not_returned",
            "unsupported",
            "malformed",
        }:
            return {"status": "unavailable", "reason": data["reason"]}
        return unavailable_usage("malformed")
    if data.get("status") == "known":
        data.pop("status")

    allowed = {"input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"}
    if set(data) - allowed or "input_tokens" not in data or "output_tokens" not in data:
        return unavailable_usage("malformed")
    if not _valid_token(data["input_tokens"]) or not _valid_token(data["output_tokens"]):
        return unavailable_usage("malformed")
    if "cached_input_tokens" in data and not _valid_token(data["cached_input_tokens"]):
        return unavailable_usage("malformed")

    if "total_tokens" in data:
        if not _valid_token(data["total_tokens"]):
            return unavailable_usage("malformed")
        total = data["total_tokens"]
    else:
        total = data["input_tokens"] + data["output_tokens"]
        if total > MAX_USAGE_TOKENS:
            return unavailable_usage("malformed")

    normalized: dict[str, Any] = {
        "status": "known",
        "input_tokens": data["input_tokens"],
        "output_tokens": data["output_tokens"],
        "total_tokens": total,
    }
    if "cached_input_tokens" in data:
        normalized["cached_input_tokens"] = data["cached_input_tokens"]
    return normalized


# Short aliases make the adapter boundary discoverable without introducing a
# provider registry or framework.
normalize_usage = normalize_provider_usage
adapt_provider_usage = normalize_provider_usage


def normalize_provider_metadata(value: object) -> dict[str, str] | None:
    """Validate the compact allowlisted provider identifier object."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("provider_metadata must be an object")
    allowed = {"provider", "model", "version", "response_id", "request_id", "provider_request_id"}
    if set(value) - allowed:
        raise ValueError("provider_metadata contains a non-allowlisted key")
    if any(not isinstance(item, str) for item in value.values()):
        raise ValueError("provider_metadata values must be strings")
    result = dict(value)
    sanitized = {key: _redact_observability_text(item) for key, item in result.items()}
    if sanitized != result:
        raise ValueError("provider_metadata contains credential-bearing text")
    try:
        encoded = json.dumps(
            result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise ValueError("provider_metadata must be compact JSON") from error
    if len(encoded) > PROVIDER_METADATA_MAX_BYTES:
        raise ValueError(f"provider_metadata exceeds {PROVIDER_METADATA_MAX_BYTES} UTF-8 bytes")
    return result


def normalize_utc_timestamp(value: object, field_name: str) -> datetime:
    """Require an aware timestamp and canonicalize it to UTC."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def duration_ms(started_at: datetime, finished_at: datetime | None) -> int | None:
    """Derive bounded milliseconds, preserving NULL for an open observation."""

    if finished_at is None:
        return None
    if finished_at < started_at:
        raise ValueError("finished_at must not precede started_at")
    result = int((finished_at - started_at).total_seconds() * 1000)
    if not 0 <= result <= MAX_DURATION_MS:
        raise ValueError(f"duration_ms must be between 0 and {MAX_DURATION_MS}")
    return result


def sanitize_error_text(value: str | None, *, field_name: str, maximum: int) -> str | None:
    """Reject raw multiline/traceback text while preserving bounded messages."""

    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    if "traceback" in value.casefold() or "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must be a sanitized single-line message")
    return value


__all__ = [
    "MAX_DURATION_MS",
    "MAX_USAGE_TOKENS",
    "NormalizedProviderUsage",
    "ProviderUsage",
    "ProviderUsageUnavailable",
    "adapt_provider_usage",
    "duration_ms",
    "normalize_provider_metadata",
    "normalize_provider_usage",
    "normalize_usage",
    "normalize_utc_timestamp",
    "sanitize_error_text",
    "unavailable_usage",
]
