import json

import pytest
from pydantic import ValidationError

from runtime import FailureClassification, FailureClassifier, RuntimeFailure


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("deterministic_execution_failed", "RETRY"),
        ("recoverable_plan_inadequacy", "REPLAN"),
        ("recoverable_verifier_inadequacy", "REPLAN"),
        ("unknown", "TERMINAL"),
        ("unsafe", "TERMINAL"),
        ("unsupported", "TERMINAL"),
        ("policy_invalid", "TERMINAL"),
        ("planner_output_invalid", "TERMINAL"),
        ("verifier_output_invalid", "TERMINAL"),
    ],
)
def test_classifies_each_frozen_failure_category(
    code: str, expected: FailureClassification
) -> None:
    failure = FailureClassifier().classify(code, "The normalized failure.")

    assert failure.classification == expected
    assert failure.code == code
    assert failure.sanitized_message == "The normalized failure."


def test_unknown_code_is_fail_closed_and_not_recoverable() -> None:
    failure = FailureClassifier().classify("future_failure_code")

    assert failure.classification == "TERMINAL"
    assert failure.classification not in {"RETRY", "REPLAN"}


def test_classification_is_deterministic_and_does_not_mutate_result() -> None:
    classifier = FailureClassifier()

    first = classifier.classify("deterministic_execution_failed", "Retryable.")
    second = classifier.classify("deterministic_execution_failed", "Retryable.")

    assert first == second
    assert first.model_dump(mode="json") == {
        "classification": "RETRY",
        "code": "deterministic_execution_failed",
        "sanitized_message": "Retryable.",
    }


def test_runtime_failure_json_round_trip_is_checkpoint_safe() -> None:
    failure = FailureClassifier().classify("verifier_output_invalid", None)

    payload = failure.model_dump(mode="json")
    restored = RuntimeFailure.model_validate_json(json.dumps(payload))

    assert payload == {
        "classification": "TERMINAL",
        "code": "verifier_output_invalid",
        "sanitized_message": None,
    }
    assert restored == failure


@pytest.mark.parametrize(
    ("code", "message"),
    [("", "message"), ("   ", "message"), ("c" * 65, "message"), ("code", "m" * 501)],
)
def test_runtime_failure_enforces_sanitized_bounds(code: str, message: str) -> None:
    with pytest.raises(ValidationError):
        FailureClassifier().classify(code, message)


def test_runtime_failure_rejects_unsupported_classification_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RuntimeFailure(
            classification="UNKNOWN",
            code="unsupported",
            sanitized_message=None,
        )

    with pytest.raises(ValidationError):
        RuntimeFailure(
            classification="TERMINAL",
            code="unsupported",
            sanitized_message=None,
            organization_id="org-1",
        )
