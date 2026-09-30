"""Narrow optional LLM boundary for GeoChange task parsing and explanation."""

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .models import GeoChangeTask


@dataclass(frozen=True, slots=True)
class ExplicitParameterValues:
    cloud_threshold: float | None
    decline_threshold: float | None


_CLOUD_RE = re.compile(
    r"\bmaximum\s+(?:sentinel[- ]2\s+)?cloud(?:\s+cover)?\s+threshold\s*(?:of|=|:)\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*%(?![0-9A-Za-z_%+-]|\.(?=\d))",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(
    r"\bNDVI\s+decline\s+threshold\s*(?:of|=|:)\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))(?![0-9A-Za-z_%+-]|\.(?=\d)|\s*(?:%|percent\b))",
    re.IGNORECASE,
)


def extract_explicit_parameters(text: str) -> ExplicitParameterValues:
    """Extract only the two frozen numeric constraints from original user text."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("task text is empty")

    def one(pattern: re.Pattern[str], label: str) -> float | None:
        values = [float(match.group(1)) for match in pattern.finditer(text)]
        if len(values) > 1 and any(value != values[0] for value in values[1:]):
            raise ValueError(f"conflicting explicit {label} values")
        return values[0] if values else None

    cloud = one(_CLOUD_RE, "cloud threshold")
    decline = one(_DECLINE_RE, "decline threshold")
    cloud_phrases = re.findall(r"\bcloud(?:\s+cover)?\s+threshold\b", text, re.I)
    if cloud_phrases and len(cloud_phrases) != len(_CLOUD_RE.findall(text)):
        raise ValueError("malformed or unsupported cloud threshold")
    decline_phrases = re.findall(r"\bndvi\s+decline\s+threshold\b", text, re.I)
    if decline_phrases and len(decline_phrases) != len(_DECLINE_RE.findall(text)):
        raise ValueError("malformed or unsupported decline threshold")
    if cloud is not None and not 0 <= cloud <= 100:
        raise ValueError("cloud threshold is out of range")
    if decline is not None and not -1 <= decline <= 0:
        raise ValueError("decline threshold is out of range")
    return ExplicitParameterValues(cloud, decline)


class StructuredModel(Protocol):
    async def ainvoke(self, input: Any) -> Any: ...


class GeoChangeLLM:
    def __init__(self, model: Any):
        self._model = model

    async def parse_task(self, text: str) -> GeoChangeTask:
        if not text or len(text) > 2000:
            raise ValueError("task text is empty or too long")
        explicit = extract_explicit_parameters(text)
        prompt = (
            "Return JSON only for this bounded schema: "
            "analysis_type=vegetation_change, version=1, aoi_key=wuhan_east_lake, "
            "period_a and period_b each with ISO start/end, cloud_threshold 0..100, "
            "decline_threshold -1..0, data_mode=local_real_raster_fixture. User task: " + text
        )
        try:
            structured = self._model.with_structured_output(GeoChangeTask)
            raw = await structured.ainvoke(text)
        except Exception:
            result = await self._model.ainvoke(prompt)
            content = getattr(result, "content", result)
            if not isinstance(content, str):
                raise ValueError("model structured output is invalid") from None
            raw = json.loads(content[content.find("{") : content.rfind("}") + 1])
        if isinstance(raw, GeoChangeTask):
            raw = raw.model_dump(mode="python")
        if isinstance(raw, dict) and raw.get("version") == 1:
            raw = {**raw, "version": "1"}
        if not isinstance(raw, dict):
            raise ValueError("model structured output is invalid")
        model_data_mode = raw.get("data_mode")
        if model_data_mode is not None and model_data_mode != "local_real_raster_fixture":
            raise ValueError("model data_mode conflicts with server-owned runtime policy")
        # Numeric constraints, mode, and provider policy are server-owned.
        raw = {
            **raw,
            "cloud_threshold": 30.0 if explicit.cloud_threshold is None else explicit.cloud_threshold,
            "decline_threshold": -0.2 if explicit.decline_threshold is None else explicit.decline_threshold,
            "cloud_threshold_source": "server_default" if explicit.cloud_threshold is None else "user_text",
            "decline_threshold_source": "server_default" if explicit.decline_threshold is None else "user_text",
            "data_mode": "local_real_raster_fixture",
            "version": "1",
        }
        return GeoChangeTask.model_validate(raw)

    async def explain(self, evidence: dict[str, Any]) -> str:
        """Ask the model for prose only after deterministic evidence validation."""
        bounded = json.dumps(evidence, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(bounded) > 4000:
            raise ValueError("evidence is too large")
        result = await self._model.ainvoke(
            "Write a concise explanation using only this validated evidence:\n" + bounded
        )
        content = getattr(result, "content", result)
        if not isinstance(content, str) or not content.strip() or len(content) > 1200:
            raise ValueError("model explanation is invalid")
        return content.strip()


__all__ = ["ExplicitParameterValues", "GeoChangeLLM", "extract_explicit_parameters"]
