"""Static, code-owned Skill constraints for supported GeoChange analyses."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Final

from geochange.aoi import TrustedAOI, resolve_aoi
from geochange.fixture import scene_evidence, validate_binding
from geochange.models import GeoChangeTask
from geochange.ndbi import scene_evidence as ndbi_scene_evidence
from geochange.ndbi import validate_binding as validate_ndbi_binding
from geochange.ndwi import scene_evidence as ndwi_scene_evidence
from geochange.ndwi import validate_binding as validate_ndwi_binding
from schema.planner import Plan, PlanStep


class SkillValidationError(ValueError):
    """A planner, checkpoint, or result value violates the selected Skill."""


def exploratory_ndwi_summary(metrics: Mapping[str, object]) -> str:
    """Build the only server-authorized summary for exploratory NDWI."""

    period_a = metrics["mean_ndwi_period_a"]
    period_b = metrics["mean_ndwi_period_b"]
    if not isinstance(period_a, (int, float)) or not isinstance(period_b, (int, float)):
        raise SkillValidationError("NDWI summary metrics are invalid")
    return (
        "Exploratory NDWI comparison: mean NDWI changed from "
        f"{period_a:.3f} to "
        f"{period_b:.3f}; "
        "continuous index statistics over the common-valid pixels only; "
        "this does not establish confirmed water area or expansion/contraction."
    )


def exploratory_ndbi_summary(metrics: Mapping[str, object]) -> str:
    """Build the only server-authorized summary for exploratory NDBI."""

    period_a = metrics["mean_ndbi_period_a"]
    period_b = metrics["mean_ndbi_period_b"]
    if not isinstance(period_a, (int, float)) or not isinstance(period_b, (int, float)):
        raise SkillValidationError("NDBI summary metrics are invalid")
    return (
        "Exploratory NDBI index comparison: mean NDBI changed from "
        f"{period_a:.3f} to {period_b:.3f}; "
        "continuous index statistics over the common-valid pixels only; "
        "this does not establish confirmed built-up area or urban expansion."
    )


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """The complete runtime contract for one supported analysis Skill."""

    analysis_type: str
    indicator: str
    capabilities: tuple[str, ...]
    result_type: str
    artifact_names: tuple[str, ...]
    required_metrics: tuple[str, ...] = ()
    required_provenance: tuple[str, ...] = ()

    def validate_plan(self, plan: Plan) -> None:
        """Require the exact allowlisted capability sequence and order."""
        if len(plan.steps) != len(self.capabilities):
            raise SkillValidationError("plan does not match the selected Skill")
        for position, (step, capability) in enumerate(
            zip(plan.steps, self.capabilities, strict=True), 1
        ):
            if step.position != position or step.instruction.strip() != capability:
                raise SkillValidationError("plan does not match the selected Skill")

    def validate_step(self, step: PlanStep, *, plan_position: int) -> str:
        """Validate the current step and return its only authorized capability."""
        if not 0 <= plan_position < len(self.capabilities):
            raise SkillValidationError("runtime plan position is outside the selected Skill")
        capability = self.capabilities[plan_position]
        if step.position != plan_position + 1 or step.instruction.strip() != capability:
            raise SkillValidationError("runtime step does not match the selected Skill")
        return capability

    def validate_result(
        self,
        payload: object,
        *,
        task: GeoChangeTask | None = None,
        aoi_evidence: Mapping[str, str] | None = None,
        scene_evidence: Mapping[str, str] | None = None,
        require_trusted_evidence: bool = False,
    ) -> None:
        """Validate the complete terminal result against this Skill and task."""
        if not isinstance(payload, dict):
            raise SkillValidationError("terminal result must be an object")
        allowed = {
            "schema_version",
            "analysis_type",
            "indicator",
            "mode",
            "summary",
            "metrics",
            "analysis_area",
            "analysis_periods",
            "data_source",
            "provenance_summary",
            "provenance",
            "artifacts",
            "verifier_status",
            "execution_mode",
            "selected_scene_evidence",
        }
        if set(payload) - allowed:
            raise SkillValidationError("terminal result contains unauthorized fields")
        required = {
            "schema_version",
            "analysis_type",
            "mode",
            "summary",
            "metrics",
            "analysis_area",
            "analysis_periods",
            "data_source",
            "provenance_summary",
            "provenance",
            "artifacts",
            "verifier_status",
        }
        if not required.issubset(payload):
            raise SkillValidationError("terminal result is incomplete")
        if (
            payload["schema_version"] != "geochange.v1"
            or payload["analysis_type"] != self.result_type
            or payload["verifier_status"] != "passed"
        ):
            raise SkillValidationError("terminal result type or verifier status is invalid")
        if payload.get("indicator") is not None and payload["indicator"] != self.indicator:
            raise SkillValidationError(
                "terminal result indicator does not match the selected Skill"
            )
        for key in ("mode", "summary", "analysis_area", "data_source", "provenance_summary"):
            if not isinstance(payload[key], str) or not payload[key].strip():
                raise SkillValidationError("terminal result text field is invalid")
        periods = payload["analysis_periods"]
        if (
            not isinstance(periods, dict)
            or set(periods) != {"period_a", "period_b"}
            or any(not isinstance(value, str) or not value.strip() for value in periods.values())
        ):
            raise SkillValidationError("terminal result periods are invalid")
        execution_mode = payload.get("execution_mode")
        provenance = payload["provenance"]
        if (
            not isinstance(provenance, dict)
            or (
                execution_mode != "real_stac_landsat_local"
                and set(provenance) != set(self.required_provenance)
            )
            or (
                execution_mode != "real_stac_landsat_local"
                and any(
                    not isinstance(value, str) or not value.strip() for value in provenance.values()
                )
            )
        ):
            raise SkillValidationError("terminal result provenance is invalid")
        if execution_mode is not None and (
            not isinstance(execution_mode, str)
            or execution_mode
            not in {
                "CACHED_REAL_SENTINEL2_RASTER",
                "REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE",
                "REAL_STAC_LOCAL_FIXTURE",
                "CACHED_REAL_METADATA",
                "CACHED_REAL_SENTINEL2_NDWI_FIXTURE",
                "CACHED_REAL_SENTINEL2_NDBI_FIXTURE",
                "real_stac_landsat_local",
            }
        ):
            raise SkillValidationError("terminal execution mode is invalid")
        if execution_mode == "real_stac_landsat_local":
            if (
                payload.get("analysis_type") != "vegetation_change"
                or payload.get("indicator") != "NDVI"
            ):
                raise SkillValidationError("Landsat execution requires NDVI vegetation change")
            if payload.get("analysis_area") not in {"jianghan_district_420103", "武汉市江汉区"}:
                raise SkillValidationError("Landsat execution requires the trusted Jianghan AOI")
            if task is not None:
                expected_periods = {
                    "period_a": f"{task.period_a.start.isoformat()}/{task.period_a.end.isoformat()}",
                    "period_b": f"{task.period_b.start.isoformat()}/{task.period_b.end.isoformat()}",
                }
                if payload.get("analysis_periods") != expected_periods:
                    raise SkillValidationError("Landsat periods do not match confirmed intent")
            metrics = payload.get("metrics")
            if not isinstance(metrics, dict) or not {
                "mean_ndvi_period_a",
                "mean_ndvi_period_b",
                "mean_delta_ndvi",
                "final_common_comparison_pixels",
            }.issubset(metrics):
                raise SkillValidationError("Landsat metrics are incomplete")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                for value in metrics.values()
            ):
                raise SkillValidationError("Landsat metric is invalid")
            artifacts = payload.get("artifacts")
            if not isinstance(artifacts, dict) or not {
                "ndvi_before_raster",
                "ndvi_after_raster",
                "ndvi_change_raster",
                "ndvi_valid_before",
                "ndvi_valid_after",
                "ndvi_common_comparison",
            }.issubset(artifacts):
                raise SkillValidationError("Landsat numeric artifacts are incomplete")
            if payload.get("verifier_status") != "passed":
                raise SkillValidationError("Landsat verifier status is invalid")
            provenance = payload.get("provenance")
            if (
                not isinstance(provenance, dict)
                or provenance.get("aoi_id") != "jianghan_district_420103"
                or not isinstance(provenance.get("aoi_hash"), str)
                or provenance.get("target_crs") != "EPSG:32649"
                or provenance.get("target_dimensions") != [296, 264]
                or not isinstance(provenance.get("scene_provenance"), dict)
            ):
                raise SkillValidationError("Landsat provenance is incomplete")
            selected_scene = payload.get("selected_scene_evidence")
            if not isinstance(selected_scene, dict) or not all(
                isinstance(selected_scene.get(key), str)
                for key in ("period_a", "period_b", "target_grid")
            ):
                raise SkillValidationError("Landsat scene evidence is incomplete")
            checksums = provenance.get("artifact_checksums")
            if not isinstance(checksums, dict) or set(checksums) != set(artifacts):
                raise SkillValidationError("Landsat artifact checksums are incomplete")
            if any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in checksums.values()
            ):
                raise SkillValidationError("Landsat artifact checksums are invalid")
            metrics_digest = hashlib.sha256(
                json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
            if provenance.get("metrics_sha256") != metrics_digest:
                raise SkillValidationError("Landsat metrics are not verifier-bound")
            return
        selected_scene = payload.get("selected_scene_evidence")
        if require_trusted_evidence and (execution_mode is None or selected_scene is None):
            raise SkillValidationError("terminal trusted evidence is incomplete")
        if selected_scene is not None:
            if (
                not isinstance(selected_scene, dict)
                or len(selected_scene) > 32
                or any(
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    or not key.strip()
                    or not value.strip()
                    or len(key) > 64
                    or len(value) > 256
                    for key, value in selected_scene.items()
                )
            ):
                raise SkillValidationError("terminal scene evidence is invalid")
            if scene_evidence is None or dict(selected_scene) != dict(scene_evidence):
                raise SkillValidationError("terminal scene evidence is not server-authorized")
        artifacts = payload["artifacts"]
        if (
            not isinstance(artifacts, dict)
            or set(artifacts) != set(self.artifact_names)
            or any(not isinstance(value, str) or value != name for name, value in artifacts.items())
        ):
            raise SkillValidationError("terminal result artifacts are invalid")
        metrics = payload["metrics"]
        if not isinstance(metrics, dict) or set(metrics) != set(self.required_metrics):
            raise SkillValidationError("terminal result metrics are incomplete or unauthorized")
        for name, value in metrics.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise SkillValidationError(f"terminal metric {name} is invalid")
        if not isinstance(metrics["valid_pixels"], int) or metrics["valid_pixels"] <= 0:
            raise SkillValidationError("terminal valid pixel count is invalid")
        if self.result_type == "vegetation_change":
            if not all(
                -1.00001 <= metrics[name] <= 1.00001
                for name in ("mean_ndvi_period_a", "mean_ndvi_period_b", "mean_delta_ndvi")
            ):
                raise SkillValidationError("terminal NDVI range is invalid")
            if (
                metrics["valid_analysis_area_m2"] <= 0
                or metrics["significant_decline_area_m2"] < 0
                or metrics["significant_decline_area_m2"] > metrics["valid_analysis_area_m2"]
                or not 0 <= metrics["decline_percentage"] <= 100
                or not -1 <= metrics["decline_threshold"] <= 0
            ):
                raise SkillValidationError("terminal NDVI area or threshold is invalid")
        elif self.result_type == "water_change":
            if (
                not all(
                    -1.00001 <= metrics[name] <= 1.00001
                    for name in ("mean_ndwi_period_a", "mean_ndwi_period_b")
                )
                or not -2.00001 <= metrics["mean_delta_ndwi"] <= 2.00001
            ):
                raise SkillValidationError("terminal NDWI range is invalid")
            if metrics["valid_analysis_area_m2"] <= 0:
                raise SkillValidationError("terminal NDWI area is invalid")
            if (
                payload["mode"] != "CACHED_REAL_SENTINEL2_NDWI_FIXTURE"
                or payload["data_source"] != "cached_real_sentinel2_ndwi_fixture"
                or payload["provenance_summary"]
                != "Exploratory NDWI over verified common-valid Sentinel-2 coverage."
                or payload["summary"] != exploratory_ndwi_summary(metrics)
            ):
                raise SkillValidationError("terminal NDWI summary is not server-authorized")
        else:
            if (
                not all(
                    -1.00001 <= metrics[name] <= 1.00001
                    for name in ("mean_ndbi_period_a", "mean_ndbi_period_b")
                )
                or not -2.00001 <= metrics["mean_delta_ndbi"] <= 2.00001
            ):
                raise SkillValidationError("terminal NDBI range is invalid")
            if metrics["valid_analysis_area_m2"] <= 0:
                raise SkillValidationError("terminal NDBI area is invalid")
            if (
                payload["mode"] != "CACHED_REAL_SENTINEL2_NDBI_FIXTURE"
                or payload["data_source"] != "cached_real_sentinel2_ndbi_fixture"
                or payload["provenance_summary"]
                != "Exploratory NDBI over verified common-valid Sentinel-2 coverage; B11 native resolution is 20 m."
                or payload["summary"] != exploratory_ndbi_summary(metrics)
            ):
                raise SkillValidationError("terminal NDBI summary is not server-authorized")
        if task is not None:
            expected_periods = {
                "period_a": f"{task.period_a.start.isoformat()}/{task.period_a.end.isoformat()}",
                "period_b": f"{task.period_b.start.isoformat()}/{task.period_b.end.isoformat()}",
            }
            if payload["analysis_area"] != task.aoi_key or periods != expected_periods:
                raise SkillValidationError("terminal result does not match trusted intent")
            if (
                self.result_type == "vegetation_change"
                and metrics["decline_threshold"] != task.decline_threshold
            ):
                raise SkillValidationError("terminal threshold does not match trusted intent")
        if aoi_evidence is not None:
            if (
                not isinstance(aoi_evidence, Mapping)
                or provenance["aoi_key"] != aoi_evidence.get("catalog_key")
                or provenance["aoi_crs"] != aoi_evidence.get("crs")
                or provenance["aoi_source"] != aoi_evidence.get("source")
            ):
                raise SkillValidationError("terminal AOI provenance is not server-authorized")
        if scene_evidence is not None:
            expected_scene = dict(scene_evidence)
            if provenance["fixture_manifest"] != expected_scene.get("fixture_manifest"):
                raise SkillValidationError("terminal fixture provenance is not server-authorized")
            for period in ("a", "b"):
                if provenance[f"period_{period}_collection"] != expected_scene.get(
                    f"period_{period}_collection"
                ):
                    raise SkillValidationError("terminal scene provenance is not server-authorized")


def validate_terminal_result(
    skill: SkillSpec,
    payload: object,
    *,
    task: GeoChangeTask,
    aoi_evidence: Mapping[str, str],
    scene_evidence_values: Mapping[str, str],
) -> None:
    """Validate terminal output against canonical server-owned evidence."""

    aoi = resolve_aoi(task.aoi_key)
    canonical_aoi = {
        "catalog_key": aoi.catalog_key,
        "crs": aoi.crs,
        "source": aoi.source,
    }
    canonical_scene = (
        ndwi_scene_evidence(task)
        if skill.result_type == "water_change"
        else ndbi_scene_evidence(task)
        if skill.result_type == "urban_change"
        else scene_evidence(task)
    )
    if dict(aoi_evidence) != canonical_aoi:
        raise SkillValidationError("AOI evidence is not server-authorized")
    if isinstance(payload, dict) and payload.get("execution_mode") == "real_stac_landsat_local":
        provenance = payload.get("provenance")
        if (
            not isinstance(aoi, TrustedAOI)
            or not isinstance(provenance, dict)
            or provenance.get("aoi_hash") != aoi.source_hash
        ):
            raise SkillValidationError("dynamic AOI hash is not server-authorized")
        skill.validate_result(
            payload, task=task, aoi_evidence=canonical_aoi, require_trusted_evidence=False
        )
        return
    if dict(scene_evidence_values) != canonical_scene:
        raise SkillValidationError("scene evidence is not server-authorized")
    if skill.result_type == "water_change":
        validate_ndwi_binding(task, dict(scene_evidence_values))
    elif skill.result_type == "urban_change":
        validate_ndbi_binding(task, dict(scene_evidence_values))
    else:
        validate_binding(task, dict(scene_evidence_values))
    skill.validate_result(
        payload,
        task=task,
        aoi_evidence=canonical_aoi,
        scene_evidence=canonical_scene,
        require_trusted_evidence=True,
    )


VEGETATION_CHANGE_NDVI: Final[SkillSpec] = SkillSpec(
    analysis_type="vegetation_change",
    indicator="NDVI",
    capabilities=(
        "resolve_aoi",
        "search_sentinel2",
        "compute_vegetation_change",
        "summarize_change",
    ),
    result_type="vegetation_change",
    artifact_names=("ndvi_before", "ndvi_after", "ndvi_change"),
    required_metrics=(
        "valid_pixels",
        "valid_analysis_area_m2",
        "mean_ndvi_period_a",
        "mean_ndvi_period_b",
        "mean_delta_ndvi",
        "significant_decline_area_m2",
        "decline_percentage",
        "decline_threshold",
    ),
    required_provenance=(
        "aoi_key",
        "aoi_crs",
        "aoi_source",
        "raster_source",
        "fixture_manifest",
        "period_a_collection",
        "period_b_collection",
    ),
)

WATER_CHANGE_NDWI: Final[SkillSpec] = SkillSpec(
    analysis_type="water_change",
    indicator="NDWI",
    capabilities=(
        "resolve_aoi",
        "search_sentinel2",
        "compute_water_change",
        "summarize_change",
    ),
    result_type="water_change",
    artifact_names=("ndwi_before", "ndwi_after", "ndwi_change"),
    required_metrics=(
        "valid_pixels",
        "valid_analysis_area_m2",
        "mean_ndwi_period_a",
        "mean_ndwi_period_b",
        "mean_delta_ndwi",
    ),
    required_provenance=(
        "aoi_key",
        "aoi_crs",
        "aoi_source",
        "raster_source",
        "fixture_manifest",
        "period_a_collection",
        "period_b_collection",
    ),
)

URBAN_CHANGE_NDBI: Final[SkillSpec] = SkillSpec(
    analysis_type="urban_change",
    indicator="NDBI",
    capabilities=(
        "resolve_aoi",
        "search_sentinel2",
        "compute_urban_change",
        "summarize_change",
    ),
    result_type="urban_change",
    artifact_names=("ndbi_before", "ndbi_after", "ndbi_change"),
    required_metrics=(
        "valid_pixels",
        "valid_analysis_area_m2",
        "mean_ndbi_period_a",
        "mean_ndbi_period_b",
        "mean_delta_ndbi",
    ),
    required_provenance=(
        "aoi_key",
        "aoi_crs",
        "aoi_source",
        "raster_source",
        "fixture_manifest",
        "period_a_collection",
        "period_b_collection",
    ),
)


def resolve_skill(analysis_type: str, indicator: str) -> SkillSpec:
    """Resolve only the frozen supported analysis/indicator pairing."""
    pair = (analysis_type, indicator)
    if pair == (VEGETATION_CHANGE_NDVI.analysis_type, VEGETATION_CHANGE_NDVI.indicator):
        return VEGETATION_CHANGE_NDVI
    if pair == (WATER_CHANGE_NDWI.analysis_type, WATER_CHANGE_NDWI.indicator):
        return WATER_CHANGE_NDWI
    if pair == (URBAN_CHANGE_NDBI.analysis_type, URBAN_CHANGE_NDBI.indicator):
        return URBAN_CHANGE_NDBI
    raise SkillValidationError("analysis type or indicator is unsupported")


__all__ = [
    "SkillSpec",
    "SkillValidationError",
    "VEGETATION_CHANGE_NDVI",
    "WATER_CHANGE_NDWI",
    "URBAN_CHANGE_NDBI",
    "exploratory_ndbi_summary",
    "resolve_skill",
    "validate_terminal_result",
]


def validate_unconfirmed_geochange_route(
    task: GeoChangeTask | None,
    plan: Plan | None,
    *,
    title: str,
    description: str | None,
) -> None:
    """Reject new-index requests; text never grants Skill authority.

    Identifier boundaries avoid treating incidental substrings as requests.
    The named water/urban analysis expressions mirror supported request forms;
    ordinary vegetation comparisons mentioning water or city context survive.
    """

    text = f"{title} {description or ''}"
    negation = re.compile(
        r"(?:do not|don't|not|without|exclude|excluding|skip|不|不要|不需要|无需|排除|不计算|不做)",
        re.I,
    )

    conjunction = re.compile(r"(?:\bbut\b|\band\b|\bor\b|但是|但|而且|并且|和|以及)", re.I)

    def is_negated(match: re.Match[str]) -> bool:
        clause_start = (
            max(
                text.rfind(mark, 0, match.start())
                for mark in (".", ";", ",", "!", "?", "。", "；", "，", "！", "？")
            )
            + 1
        )
        clause_end_candidates = [
            text.find(mark, match.end())
            for mark in (".", ";", ",", "!", "?", "。", "；", "，", "！", "？")
        ]
        clause_end = min(
            (value for value in clause_end_candidates if value >= 0), default=len(text)
        )
        clause = text[clause_start:clause_end]
        relative_start = match.start() - clause_start
        relative_end = match.end() - clause_start
        before = clause[:relative_start]
        previous_connector = (
            list(conjunction.finditer(before))[-1] if conjunction.search(before) else None
        )
        local_before = before[(previous_connector.end() if previous_connector else 0) :]
        following = clause[relative_end:]
        next_connector = conjunction.search(following)
        local_after = following[: next_connector.start() if next_connector else None]
        if negation.search(local_before):
            return True
        if negation.search(local_after) and re.match(
            r"\s*(?:is\s+)?(?:not requested|not needed|excluded|不要|不需要|无需|不计算|不做)",
            local_after,
            re.I,
        ):
            return True
        if previous_connector and previous_connector.group(0).casefold() in {"or", "和", "以及"}:
            prior = clause[: previous_connector.start()]
            return bool(negation.search(prior)) and not re.search(
                r"\b(?:calculate|分析|比较|计算)\b", local_before, re.I
            )
        return False

    index_mentions = re.finditer(
        r"(?<![A-Za-z0-9_])(?P<indicator>NDWI|NDBI)(?![A-Za-z0-9_])", text, re.I
    )
    explicit_index = any(not is_negated(match) for match in index_mentions)
    analysis_matches = re.finditer(
        r"\b(?:water|urban|built[- ]up)[ _-]+(?:change|analysis|index|expansion|area|comparison)\b"
        r"|\b(?:analy[sz]e|compare|calculate)[ ]+(?:the[ ]+)?(?:water|urban|built[- ]up)\b"
        r"|(?:水体|水域|建成区|城市)(?:的)?(?:变化|分析|指数|扩张|面积)"
        r"|(?:分析|比较|计算)(?:水体|水域|建成区|城市)",
        text,
        re.I,
    )
    analysis_expression = any(not is_negated(match) for match in analysis_matches)
    newer_capabilities = (
        set(WATER_CHANGE_NDWI.capabilities) | set(URBAN_CHANGE_NDBI.capabilities)
    ) - set(VEGETATION_CHANGE_NDVI.capabilities)
    if (
        explicit_index
        or analysis_expression
        or (
            task is not None
            and (task.analysis_type, task.indicator) != ("vegetation_change", "NDVI")
        )
        or (
            plan is not None
            and any(
                step.instruction.strip().split()[0] in newer_capabilities for step in plan.steps
            )
        )
    ):
        raise SkillValidationError("confirmed intent is required for this Skill")
