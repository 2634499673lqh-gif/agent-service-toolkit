"""The five bounded, provider-free Phase 8 fixture contracts."""

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

SUITE_ID = "taskpilot.phase8.v1"
SUITE_VERSION = 1

CASE_IDS = (
    "approval.l2_requires_approval",
    "approval.l3_blocked",
    "deterministic.fixture_baseline",
    "recovery.replan_then_pass",
    "recovery.retry_then_pass",
)

_BASELINE_INPUT = {
    "title": "Phase 8 deterministic fixture baseline",
    "description": "Execute the repository deterministic fixture capability.",
}


class EvaluationFixture(BaseModel):
    """JSON-only definition of one evaluation case.

    Runtime objects are deliberately absent.  The runner maps these data-only
    definitions to existing runtime primitives at execution time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: StrictStr = Field(min_length=1, max_length=64)
    case_version: StrictInt = Field(ge=0)
    capability_name: Literal["deterministic_fixture"] = "deterministic_fixture"
    input: dict[str, Any]
    required_evidence: tuple[StrictStr, ...] = Field(max_length=16)
    expected_output: StrictStr | None = Field(default=None, max_length=2000)
    expected_step: dict[str, Any] = Field(default_factory=dict)
    expected_verdict: Literal["PASS"] = "PASS"
    tag: Literal["baseline", "retry-then-pass", "replan-then-pass", "l2-approval", "l3-blocked"]

    @field_validator("case_id", "required_evidence", mode="before")
    @classmethod
    def bounded_text(cls, value: object) -> object:
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            if not isinstance(item, str) or not item.strip() or len(item.encode("utf-8")) > 64:
                raise ValueError("fixture text is not bounded")
            if any(ord(character) < 32 or ord(character) == 127 for character in item):
                raise ValueError("fixture text contains a control character")
        return value

    @field_validator("input", "expected_step")
    @classmethod
    def json_only_and_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        # A round trip through JSON-compatible Pydantic data rejects runtime
        # objects while retaining a simple 32 KiB canonical input bound.
        import json

        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("fixture values must be JSON-compatible") from error
        if len(encoded.encode("utf-8")) > 32 * 1024:
            raise ValueError("fixture input exceeds the 32 KiB bound")
        return value

    @model_validator(mode="after")
    def canonical_evidence(self) -> "EvaluationFixture":
        if len(set(self.required_evidence)) != len(self.required_evidence):
            raise ValueError("required evidence codes must be unique")
        object.__setattr__(self, "required_evidence", tuple(sorted(self.required_evidence)))
        return self


class FixtureSuite(BaseModel):
    """The immutable V1 suite in canonical case order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: Literal["taskpilot.phase8.v1"] = SUITE_ID
    suite_version: Literal[1] = SUITE_VERSION
    cases: tuple[EvaluationFixture, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_case_set(self) -> "FixtureSuite":
        ids = tuple(case.case_id for case in self.cases)
        if ids != CASE_IDS:
            raise ValueError("V1 cases must be in canonical order and contain exactly five cases")
        if any(case.case_version != 1 for case in self.cases):
            raise ValueError("V1 cases must use case_version 1")
        return self


def load_fixtures() -> FixtureSuite:
    """Return a newly validated copy of the five deterministic fixtures."""

    return FixtureSuite(
        cases=(
            EvaluationFixture(
                case_id="approval.l2_requires_approval",
                case_version=1,
                input={
                    "title": "Phase 8 L2 approval fixture",
                    "description": "Require approval before execution.",
                },
                required_evidence=(
                    "approval_required",
                    "approval_satisfied",
                    "execution_succeeded",
                ),
                expected_output="deterministic-read-only-fixture:v1",
                expected_step={"position": 1, "instruction": "Execute the approved fixture."},
                tag="l2-approval",
            ),
            EvaluationFixture(
                case_id="approval.l3_blocked",
                case_version=1,
                input={
                    "title": "Phase 8 L3 blocked fixture",
                    "description": "Block the high-risk route.",
                },
                required_evidence=("blocked_before_execution",),
                expected_step={"position": 1, "instruction": "Execute the blocked fixture."},
                tag="l3-blocked",
            ),
            EvaluationFixture(
                case_id="deterministic.fixture_baseline",
                case_version=1,
                input=_BASELINE_INPUT,
                required_evidence=(
                    "capability_selected",
                    "execution_succeeded",
                    "output_exact",
                    "verification_passed",
                ),
                expected_output="deterministic-read-only-fixture:v1",
                expected_step={"position": 1, "instruction": "Return the deterministic fixture."},
                tag="baseline",
            ),
            EvaluationFixture(
                case_id="recovery.replan_then_pass",
                case_version=1,
                input={
                    "title": "Phase 8 replan fixture",
                    "description": "Recover with one bounded replan.",
                },
                required_evidence=("replan_observed", "recovery_pass"),
                expected_output="replanned-fixture:v1",
                expected_step={"position": 1, "instruction": "Execute the replanned fixture."},
                tag="replan-then-pass",
            ),
            EvaluationFixture(
                case_id="recovery.retry_then_pass",
                case_version=1,
                input={
                    "title": "Phase 8 retry fixture",
                    "description": "Recover with one bounded retry.",
                },
                required_evidence=("retry_observed", "recovery_pass"),
                expected_output="retried-fixture:v1",
                expected_step={"position": 1, "instruction": "Execute the retry fixture."},
                tag="retry-then-pass",
            ),
        )
    )


DEFAULT_FIXTURES = load_fixtures()

__all__ = [
    "CASE_IDS",
    "SUITE_ID",
    "SUITE_VERSION",
    "DEFAULT_FIXTURES",
    "EvaluationFixture",
    "FixtureSuite",
    "load_fixtures",
]
