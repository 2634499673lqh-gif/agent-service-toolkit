import pytest

from runtime import CapabilityMetadata, ContextBuilder, RiskRoute, classify_action
from schema import PlanStep


def _context():
    return ContextBuilder().build(
        task_input={"title": "Prepare the report", "description": None},
        current_step=PlanStep(position=1, instruction="Inspect the requested material"),
    )


@pytest.mark.parametrize(
    ("risk_level", "expected"),
    [
        ("L0", RiskRoute.AUTO_ALLOW),
        ("L1", RiskRoute.AUTO_ALLOW),
        ("L2", RiskRoute.APPROVAL_REQUIRED),
        ("L3", RiskRoute.BLOCKED),
    ],
)
def test_classifier_routes_only_the_server_metadata_risk(risk_level, expected) -> None:
    metadata = CapabilityMetadata(
        name="fixed_server_action",
        risk_level=risk_level,
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    assert classify_action(metadata, _context(), expected_name="fixed_server_action") is expected


@pytest.mark.parametrize(
    ("metadata", "arguments", "expected_name"),
    [
        ({"name": "action", "risk_level": "L9"}, _context(), "action"),
        ({"name": "action", "action_version": "2"}, _context(), "action"),
        (
            CapabilityMetadata(
                name="action",
                risk_level="L2",
                read_only=True,
                deterministic=True,
                side_effect_free=True,
            ),
            {},
            "action",
        ),
        (
            CapabilityMetadata(
                name="action",
                risk_level="L2",
                read_only=True,
                deterministic=True,
                side_effect_free=True,
            ),
            _context(),
            "different_action",
        ),
    ],
)
def test_classifier_fails_closed_for_unknown_or_malformed_inputs(
    metadata, arguments, expected_name
) -> None:
    assert classify_action(metadata, arguments, expected_name=expected_name) is RiskRoute.INVALID
