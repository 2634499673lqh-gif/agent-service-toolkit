"""Small pure normalizers shared by runtime observations and provider adapters."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import (
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
    localcontext,
)
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from persistence.models import _redact_observability_text

MAX_USAGE_TOKENS = 1_000_000_000_000
MAX_DURATION_MS = 86_400_000
PROVIDER_METADATA_MAX_BYTES = 2_048
MAX_PRICE_INTEGER_DIGITS = 12
MAX_PRICE_FRACTIONAL_DIGITS = 6
COST_ESTIMATE_QUANTUM = Decimal("0.000001")
MAX_COST_UNITS = Decimal("1000000000000000000")

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

CostUnknownReason = Literal[
    "missing_price",
    "unsupported_model",
    "usage_unavailable",
    "overflow",
]
PricingKey = tuple[str, str, str]
CostEstimate = dict[str, str]


class PricingEntry(BaseModel):
    """One bounded in-process price for a provider model version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    currency: str = Field(min_length=1, max_length=8)

    @field_validator("provider", "model", "version", "currency")
    @classmethod
    def identifiers_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pricing identifiers and currency must not be blank")
        return value

    @field_validator("input_price_per_1k", "output_price_per_1k", mode="before")
    @classmethod
    def prices_must_be_bounded_decimals(cls, value: object) -> Decimal:
        if not isinstance(value, Decimal):
            raise ValueError("pricing values must be Decimal instances")
        if not value.is_finite() or value < 0:
            raise ValueError("pricing values must be finite and non-negative")
        decimal_tuple = value.as_tuple()
        digits = decimal_tuple.digits
        exponent = decimal_tuple.exponent
        if not isinstance(exponent, int):
            raise ValueError("pricing values must be finite Decimal instances")
        fractional_digits = max(-exponent, 0)
        integer_digits = max(len(digits) + exponent, 0)
        if integer_digits > MAX_PRICE_INTEGER_DIGITS:
            raise ValueError(
                f"pricing values may have at most {MAX_PRICE_INTEGER_DIGITS} integer digits"
            )
        if fractional_digits > MAX_PRICE_FRACTIONAL_DIGITS:
            raise ValueError(
                f"pricing values may have at most {MAX_PRICE_FRACTIONAL_DIGITS} fractional digits"
            )
        return value

    @property
    def key(self) -> tuple[str, str, str]:
        """Return the canonical lookup key."""

        return (self.provider, self.model, self.version)


class PricingTable:
    """Immutable lookup table for deterministic in-process pricing."""

    def __init__(
        self,
        entries: Mapping[tuple[str, str, str], PricingEntry | Mapping[str, object]]
        | list[PricingEntry | Mapping[str, object]]
        | tuple[PricingEntry | Mapping[str, object], ...] = (),
    ) -> None:
        if isinstance(entries, Mapping):
            source = []
            for key, value in entries.items():
                if (
                    not isinstance(key, tuple)
                    or len(key) != 3
                    or not all(isinstance(item, str) for item in key)
                ):
                    raise ValueError("pricing table keys must be (provider, model, version) tuples")
                entry = (
                    value if isinstance(value, PricingEntry) else PricingEntry.model_validate(value)
                )
                if entry.key != key:
                    raise ValueError("pricing table key does not match its pricing entry")
                source.append(entry)
        else:
            source = [
                entry if isinstance(entry, PricingEntry) else PricingEntry.model_validate(entry)
                for entry in entries
            ]
        table: dict[tuple[str, str, str], PricingEntry] = {}
        for entry in source:
            if entry.key in table:
                raise ValueError(f"duplicate pricing entry for {entry.key!r}")
            table[entry.key] = entry
        self._entries = MappingProxyType(table)

    def lookup(
        self, provider: str, model: str, version: str
    ) -> tuple[PricingEntry | None, CostUnknownReason | None]:
        """Resolve an exact version and distinguish unsupported from missing price."""

        if not all(isinstance(value, str) and value.strip() for value in (provider, model)):
            return None, "unsupported_model"
        model_is_configured = any(
            entry.provider == provider and entry.model == model for entry in self._entries.values()
        )
        if not isinstance(version, str) or not version.strip():
            return None, "missing_price" if model_is_configured else "unsupported_model"
        exact = self._entries.get((provider, model, version))
        if exact is not None:
            return exact, None
        if model_is_configured:
            return None, "missing_price"
        return None, "unsupported_model"

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __getitem__(self, key: PricingKey) -> PricingEntry:
        return self._entries[key]


PricingConfiguration = PricingTable


def _unknown_cost(reason: CostUnknownReason) -> CostEstimate:
    """Create the canonical non-numeric estimate shape."""

    return {"status": "unknown", "reason": reason}


def _coerce_pricing_table(
    pricing: PricingTable
    | Mapping[tuple[str, str, str], PricingEntry | Mapping[str, object]]
    | list[PricingEntry | Mapping[str, object]]
    | tuple[PricingEntry | Mapping[str, object], ...],
) -> PricingTable:
    return pricing if isinstance(pricing, PricingTable) else PricingTable(pricing)


def estimate_cost(
    usage: object,
    pricing: PricingTable
    | Mapping[tuple[str, str, str], PricingEntry | Mapping[str, object]]
    | list[PricingEntry | Mapping[str, object]]
    | tuple[PricingEntry | Mapping[str, object], ...],
    *,
    provider: str,
    model: str,
    version: str,
) -> CostEstimate:
    """Estimate informational cost from normalized usage and explicit pricing.

    The estimator deliberately returns a plain JSON-shaped mapping. Unknown
    values contain a reason and never fabricate a numeric zero.
    """

    if isinstance(usage, (ProviderUsage, ProviderUsageUnavailable)):
        normalized_usage = usage.model_dump(mode="python")
    elif isinstance(usage, Mapping) and usage.get("status") == "known":
        if "total_tokens" not in usage:
            return _unknown_cost("usage_unavailable")
        normalized_usage = normalize_provider_usage(usage)
    else:
        normalized_usage = unavailable_usage("malformed")
    if normalized_usage.get("status") != "known":
        return _unknown_cost("usage_unavailable")

    table = _coerce_pricing_table(pricing)
    entry, lookup_reason = table.lookup(provider, model, version)
    if entry is None:
        return _unknown_cost(lookup_reason or "missing_price")

    input_tokens = normalized_usage["input_tokens"]
    output_tokens = normalized_usage["output_tokens"]
    try:
        with localcontext() as context:
            context.prec = 50
            total = (Decimal(input_tokens) / Decimal(1000)) * entry.input_price_per_1k + (
                Decimal(output_tokens) / Decimal(1000)
            ) * entry.output_price_per_1k
            if total > MAX_COST_UNITS:
                return _unknown_cost("overflow")
            rounded = total.quantize(COST_ESTIMATE_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, OverflowError):
        return _unknown_cost("overflow")
    return {"status": "known", "currency": entry.currency, "amount": f"{rounded:.6f}"}


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
    "COST_ESTIMATE_QUANTUM",
    "MAX_COST_UNITS",
    "MAX_DURATION_MS",
    "MAX_PRICE_FRACTIONAL_DIGITS",
    "MAX_PRICE_INTEGER_DIGITS",
    "MAX_USAGE_TOKENS",
    "CostEstimate",
    "CostUnknownReason",
    "NormalizedProviderUsage",
    "PricingConfiguration",
    "PricingEntry",
    "PricingKey",
    "PricingTable",
    "ProviderUsage",
    "ProviderUsageUnavailable",
    "adapt_provider_usage",
    "duration_ms",
    "estimate_cost",
    "normalize_provider_metadata",
    "normalize_provider_usage",
    "normalize_usage",
    "normalize_utc_timestamp",
    "sanitize_error_text",
    "unavailable_usage",
]
