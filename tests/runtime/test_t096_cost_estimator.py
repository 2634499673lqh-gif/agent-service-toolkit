"""Deterministic T096 pricing and cost-estimate coverage."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from runtime import PricingEntry, PricingTable, estimate_cost, normalize_provider_usage


def _entry(
    *,
    provider: str = "fixture",
    model: str = "fixture-v1",
    version: str = "2026-09",
    input_price: str = "0.002000",
    output_price: str = "0.003000",
    currency: str = "USD",
) -> PricingEntry:
    return PricingEntry(
        provider=provider,
        model=model,
        version=version,
        input_price_per_1k=Decimal(input_price),
        output_price_per_1k=Decimal(output_price),
        currency=currency,
    )


def _estimate(usage: object, entry: PricingEntry | None = None) -> dict[str, str]:
    selected = entry or _entry()
    return estimate_cost(
        usage,
        PricingTable([selected]),
        provider=selected.provider,
        model=selected.model,
        version=selected.version,
    )


def test_t096_exact_decimal_arithmetic_is_deterministic() -> None:
    usage = {
        "status": "known",
        "input_tokens": 1_234,
        "output_tokens": 567,
        "total_tokens": 1_801,
    }
    expected = {"status": "known", "currency": "USD", "amount": "0.004169"}

    assert _estimate(usage) == expected
    assert _estimate(usage) == expected


def test_t096_rounding_uses_half_up_and_fixed_six_places() -> None:
    entry = _entry(input_price="0.000500", output_price="0")

    assert _estimate(
        {"status": "known", "input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        entry,
    ) == {"status": "known", "currency": "USD", "amount": "0.000001"}


def test_t096_accepts_t095_derived_total_without_fabricating_usage() -> None:
    normalized = normalize_provider_usage({"input_tokens": 4, "output_tokens": 3})
    assert _estimate(normalized) == {
        "status": "known",
        "currency": "USD",
        "amount": "0.000017",
    }


@pytest.mark.parametrize(
    "usage",
    [
        {"status": "unavailable", "reason": "not_returned"},
        {"status": "unavailable", "reason": "malformed"},
        {"status": "known", "input_tokens": 1},
        {"status": "known", "output_tokens": 1},
        {"status": "known", "input_tokens": 1, "output_tokens": 1},
        {"status": "known", "input_tokens": -1, "output_tokens": 1},
        {"status": "known", "input_tokens": 1_000_000_000_001, "output_tokens": 1},
    ],
)
def test_t096_unavailable_or_partial_usage_is_unknown(usage: object) -> None:
    assert _estimate(usage) == {"status": "unknown", "reason": "usage_unavailable"}


def test_t096_lookup_distinguishes_missing_version_from_unsupported_model() -> None:
    table = PricingTable([_entry()])
    usage = {"status": "known", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

    assert estimate_cost(
        usage,
        table,
        provider="fixture",
        model="fixture-v1",
        version="missing-version",
    ) == {"status": "unknown", "reason": "missing_price"}
    assert estimate_cost(
        usage,
        table,
        provider="fixture",
        model="fixture-v1",
        version="",
    ) == {"status": "unknown", "reason": "missing_price"}
    assert estimate_cost(
        usage,
        table,
        provider="fixture",
        model="unknown-model",
        version="2026-09",
    ) == {"status": "unknown", "reason": "unsupported_model"}


def test_t096_exact_boundary_is_allowed_but_above_it_overflows() -> None:
    boundary = _entry(input_price="1000000000", output_price="0")
    overflow = _entry(input_price="1000000000.000001", output_price="0")
    usage = {
        "status": "known",
        "input_tokens": 1_000_000_000_000,
        "output_tokens": 0,
        "total_tokens": 1_000_000_000_000,
    }

    assert _estimate(usage, boundary) == {
        "status": "known",
        "currency": "USD",
        "amount": "1000000000000000000.000000",
    }
    assert _estimate(usage, overflow) == {"status": "unknown", "reason": "overflow"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "", "model": "fixture-v1", "version": "2026-09"},
        {"provider": "fixture", "model": " " * 2, "version": "2026-09"},
        {
            "provider": "fixture",
            "model": "fixture-v1",
            "version": "2026-09",
            "input_price": "1.0000000",
        },
        {
            "provider": "fixture",
            "model": "fixture-v1",
            "version": "2026-09",
            "input_price": "1000000000000.000000",
        },
        {"provider": "fixture", "model": "fixture-v1", "version": "2026-09", "input_price": "-0.1"},
    ],
)
def test_t096_pricing_entry_enforces_identifiers_and_decimal_bounds(
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        _entry(**kwargs)


def test_t096_mapping_configuration_must_match_entry_key() -> None:
    entry = _entry()
    with pytest.raises(ValueError, match="does not match"):
        PricingTable({("other", entry.model, entry.version): entry})


def test_t096_unknown_result_never_contains_numeric_zero() -> None:
    result = _estimate(None)

    assert result == {"status": "unknown", "reason": "usage_unavailable"}
    assert not any(value == "0" or value == "0.000000" for value in result.values())
