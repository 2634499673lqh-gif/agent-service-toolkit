import json

import pytest
from pydantic import ValidationError

from schema import VerificationResult, VerificationVerdict


@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
def test_accepts_each_frozen_verdict(verdict: VerificationVerdict) -> None:
    result = VerificationResult(
        verdict=verdict, reason="The output meets the task criteria.", evidence=[]
    )

    assert result.verdict == verdict
    assert result.evidence == []


def test_accepts_reason_at_maximum_length() -> None:
    result = VerificationResult(verdict="PASS", reason="r" * 500, evidence=[])

    assert len(result.reason) == 500


def test_accepts_evidence_item_at_maximum_length() -> None:
    result = VerificationResult(
        verdict="FAIL", reason="The output is incomplete.", evidence=["e" * 500]
    )

    assert len(result.evidence[0]) == 500


def test_accepts_exactly_eight_evidence_items() -> None:
    result = VerificationResult(
        verdict="PASS",
        reason="All checks passed.",
        evidence=[f"Evidence {index}" for index in range(1, 9)],
    )

    assert len(result.evidence) == 8


def test_verification_result_json_round_trip_is_json_compatible() -> None:
    result = VerificationResult(
        verdict="FAIL",
        reason="The result did not satisfy the acceptance criteria.",
        evidence=["The required output was missing."],
    )

    payload = result.model_dump(mode="json")
    restored = VerificationResult.model_validate_json(json.dumps(payload))

    assert payload == {
        "verdict": "FAIL",
        "reason": "The result did not satisfy the acceptance criteria.",
        "evidence": ["The required output was missing."],
    }
    assert restored == result


def test_rejects_unsupported_verdict() -> None:
    with pytest.raises(ValidationError):
        VerificationResult(verdict="RETRY", reason="Not a verifier verdict.", evidence=[])


@pytest.mark.parametrize("reason", ["", "   ", "\t\n"])
def test_rejects_empty_or_whitespace_only_reason(reason: str) -> None:
    with pytest.raises(ValidationError):
        VerificationResult(verdict="PASS", reason=reason, evidence=[])


def test_rejects_overlong_reason() -> None:
    with pytest.raises(ValidationError):
        VerificationResult(verdict="PASS", reason="r" * 501, evidence=[])


def test_rejects_more_than_eight_evidence_items() -> None:
    with pytest.raises(ValidationError):
        VerificationResult(
            verdict="PASS",
            reason="The result passed.",
            evidence=[f"Evidence {index}" for index in range(1, 10)],
        )


@pytest.mark.parametrize("evidence_item", ["", "   ", "\t\n"])
def test_accepts_empty_or_whitespace_only_evidence_item(evidence_item: str) -> None:
    result = VerificationResult(
        verdict="FAIL", reason="The result failed.", evidence=[evidence_item]
    )

    assert result.evidence == [evidence_item]


def test_rejects_overlong_evidence_item() -> None:
    with pytest.raises(ValidationError):
        VerificationResult(verdict="FAIL", reason="The result failed.", evidence=["e" * 501])


def test_rejects_unexpected_extra_fields() -> None:
    with pytest.raises(ValidationError):
        VerificationResult(
            verdict="PASS",
            reason="The result passed.",
            evidence=[],
            criteria="must not be accepted",
        )


def test_rejects_missing_evidence_field() -> None:
    with pytest.raises(ValidationError):
        VerificationResult(verdict="PASS", reason="The result passed.")
