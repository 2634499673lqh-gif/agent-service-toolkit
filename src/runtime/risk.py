"""The frozen four-level runtime risk classification boundary for Phase 6."""

from enum import StrEnum

from .capability import CapabilityMetadata
from .context import ContextEnvelope


class RiskRoute(StrEnum):
    """One of the only runtime routing outcomes defined by ADR-008."""

    AUTO_ALLOW = "auto_allow"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"
    INVALID = "invalid"


def classify_action(
    metadata: object,
    arguments: object,
    *,
    expected_name: str,
) -> RiskRoute:
    """Classify one trusted server-selected action and validated arguments.

    The action name/version and risk level come from static server wiring. The
    model-produced PlanStep is only part of the already validated argument
    envelope; it cannot choose the action or its risk. Unknown or malformed
    inputs fail closed.
    """

    try:
        action = CapabilityMetadata.model_validate(metadata)
        ContextEnvelope.model_validate(arguments)
    except Exception:
        return RiskRoute.INVALID
    if not isinstance(expected_name, str) or action.name != expected_name:
        return RiskRoute.INVALID
    if action.risk_level in {"L0", "L1"}:
        return RiskRoute.AUTO_ALLOW
    if action.risk_level == "L2":
        return RiskRoute.APPROVAL_REQUIRED
    if action.risk_level == "L3":
        return RiskRoute.BLOCKED
    return RiskRoute.INVALID  # pragma: no cover - Literal validates this


__all__ = ["RiskRoute", "classify_action"]
