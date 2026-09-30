"""Narrow optional LLM boundary for GeoChange task parsing and explanation."""

import json
from typing import Any, Protocol

from .models import GeoChangeTask


class StructuredModel(Protocol):
    async def ainvoke(self, input: Any) -> Any: ...


class GeoChangeLLM:
    def __init__(self, model: Any):
        self._model = model

    async def parse_task(self, text: str) -> GeoChangeTask:
        if not text or len(text) > 2000:
            raise ValueError("task text is empty or too long")
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
        if isinstance(raw, dict) and raw.get("version") == 1:
            raw = {**raw, "version": "1"}
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


__all__ = ["GeoChangeLLM"]
