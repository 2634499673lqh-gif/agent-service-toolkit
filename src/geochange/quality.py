"""Projection of persisted, verifier-bound Landsat NDVI quality evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Any

from .landsat_ndvi import (
    FINAL_COMMON_COVERAGE_GATE,
    FINAL_NDVI_COVERAGE_GATE,
    MASK_ARTIFACTS,
    NDVI_ARTIFACTS,
    PNG_ARTIFACTS,
)


def build_landsat_quality_report(
    metadata: dict[str, Any], *, canonical_evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return a bounded report, or raise when the persisted evidence is incomplete."""
    if metadata.get("verifier_status") != "passed":
        raise ValueError("quality evidence is not verifier-bound")
    if metadata.get("execution_mode") != "real_stac_landsat_local":
        raise ValueError("quality report requires Landsat execution evidence")
    provenance = metadata.get("provenance")
    metrics = metadata.get("metrics")
    scenes = metadata.get("selected_scene_evidence")
    canonical = canonical_evidence
    if (
        not isinstance(provenance, dict)
        or not isinstance(metrics, dict)
        or not isinstance(scenes, dict)
        or not isinstance(canonical, dict)
    ):
        raise ValueError("quality evidence is incomplete")
    if provenance.get("aoi_id") != "jianghan_district_420103":
        raise ValueError("quality AOI identity is invalid")
    if not isinstance(provenance.get("aoi_hash"), str) or not provenance.get("aoi_hash"):
        raise ValueError("quality AOI hash is missing")
    if not re.fullmatch(r"[0-9a-f]{64}", provenance["aoi_hash"]):
        raise ValueError("quality AOI hash is invalid")
    requested_periods = metadata.get("analysis_periods")
    period_windows: dict[str, tuple[date, date]] = {}
    if not isinstance(requested_periods, dict) or set(requested_periods) != {
        "period_a",
        "period_b",
    }:
        raise ValueError("requested period evidence is incomplete")
    for key in ("a", "b"):
        raw_period = requested_periods.get(f"period_{key}")
        if not isinstance(raw_period, str) or raw_period.count("/") != 1:
            raise ValueError("requested period evidence is invalid")
        start_text, end_text = raw_period.split("/")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_text) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", end_text
            ):
                raise ValueError
            start_date, end_date = date.fromisoformat(start_text), date.fromisoformat(end_text)
        except ValueError:
            raise ValueError("requested period evidence is invalid") from None
        if end_date <= start_date:
            raise ValueError("requested period ordering is invalid")
        period_windows[key] = (start_date, end_date)
    if period_windows["a"][1] > period_windows["b"][0]:
        raise ValueError("requested periods overlap")
    for key in ("analysis_periods", "metrics", "provenance", "selected_scene_evidence"):
        if key not in canonical or metadata.get(key) != canonical[key]:
            raise ValueError("canonical evidence binding is invalid")
    if (
        "artifacts" not in canonical
        or metadata.get("artifact_references") != canonical["artifacts"]
    ):
        raise ValueError("canonical artifact binding is invalid")
    required_metrics = {
        "aoi_rasterized_pixels",
        "preparation_valid_pixels_period_a",
        "preparation_valid_pixels_period_b",
        "final_ndvi_valid_pixels_period_a",
        "final_ndvi_valid_pixels_period_b",
        "final_common_comparison_pixels",
        "preparation_coverage_period_a_pct",
        "preparation_coverage_period_b_pct",
        "final_ndvi_coverage_period_a_pct",
        "final_ndvi_coverage_period_b_pct",
        "final_common_comparison_coverage_pct",
        "mean_ndvi_period_a",
        "mean_ndvi_period_b",
        "mean_delta_ndvi",
    }
    if not required_metrics.issubset(metrics):
        raise ValueError("quality metrics are incomplete")
    denominator = int(metrics["aoi_rasterized_pixels"])
    if denominator <= 0:
        raise ValueError("quality AOI denominator is invalid")
    for key in required_metrics - {"aoi_rasterized_pixels"}:
        value = metrics[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("quality metric is not numeric")
    digest = provenance.get("metrics_sha256")
    expected_digest = hashlib.sha256(
        json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    if not isinstance(digest, str) or digest != expected_digest:
        raise ValueError("quality metrics binding is invalid")
    for key in (
        "preparation_valid_pixels_period_a",
        "preparation_valid_pixels_period_b",
        "final_ndvi_valid_pixels_period_a",
        "final_ndvi_valid_pixels_period_b",
        "final_common_comparison_pixels",
    ):
        if not isinstance(metrics[key], int) or not 0 <= metrics[key] <= denominator:
            raise ValueError("quality pixel count is invalid")
    period_evidence: dict[str, dict[str, Any]] = {}
    for key in ("a", "b"):
        raw = scenes.get(f"period_{key}")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        if isinstance(raw, dict):
            ids = raw.get("scene_ids", raw.get("item_ids", []))
            dates = raw.get("acquisition_dates", raw.get("dates", []))
            if (
                isinstance(ids, list)
                and isinstance(dates, list)
                and ids
                and dates
                and all(isinstance(item, str) and item for item in ids + dates)
            ):
                period_evidence[key] = {
                    "scene_ids": list(ids),
                    "dates": list(dates),
                }
        elif f"period_{key}_item_id" in scenes and f"period_{key}_date" in scenes:
            if not isinstance(scenes[f"period_{key}_item_id"], str) or not isinstance(
                scenes[f"period_{key}_date"], str
            ):
                raise ValueError("scene evidence types are invalid")
            period_evidence[key] = {
                "scene_ids": [scenes[f"period_{key}_item_id"]],
                "dates": [scenes[f"period_{key}_date"]],
            }
    if set(period_evidence) != {"a", "b"}:
        raise ValueError("scene evidence is incomplete")
    canonical_scenes = provenance.get("scene_provenance")
    if not isinstance(canonical_scenes, dict):
        raise ValueError("canonical scene provenance is missing")
    for key in ("a", "b"):
        canonical = canonical_scenes.get(f"period_{key}")
        if (
            not isinstance(canonical, dict)
            or not isinstance(canonical.get("scene_ids"), list)
            or not isinstance(canonical.get("acquisition_dates"), list)
            or not all(
                isinstance(item, str)
                for item in canonical["scene_ids"] + canonical["acquisition_dates"]
            )
            or canonical["scene_ids"] != period_evidence[key]["scene_ids"]
            or canonical["acquisition_dates"] != period_evidence[key]["dates"]
        ):
            raise ValueError("scene identity binding is invalid")
        for value in period_evidence[key]["dates"]:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                acquisition_date = date.fromisoformat(value)
                if not period_windows[key][0] <= acquisition_date <= period_windows[key][1]:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("scene acquisition date is invalid") from None
    target_grid = provenance.get("target_grid")
    dimensions = provenance.get("target_dimensions")
    if not isinstance(target_grid, dict) or not isinstance(dimensions, list):
        raise ValueError("grid evidence is incomplete")
    if len(dimensions) != 2 or any(
        not isinstance(value, int) or value <= 0 for value in dimensions
    ):
        raise ValueError("grid dimensions are invalid")
    if target_grid.get("dimensions") not in (None, dimensions):
        raise ValueError("grid dimensions are inconsistent")
    if target_grid.get("crs") != "EPSG:32649" or provenance.get("target_crs") != target_grid.get(
        "crs"
    ):
        raise ValueError("quality CRS is invalid")
    resolution = target_grid.get("resolution_m")
    if isinstance(resolution, bool) or not isinstance(resolution, (int, float)):
        raise ValueError("quality resolution is invalid")
    if not math.isfinite(float(resolution)) or float(resolution) != 30.0:
        raise ValueError("quality resolution is invalid")
    raw_grid = scenes.get("target_grid")
    if isinstance(raw_grid, str):
        try:
            raw_grid = json.loads(raw_grid)
        except (TypeError, ValueError):
            raw_grid = None
    if (
        not isinstance(raw_grid, dict)
        or raw_grid.get("crs") != target_grid.get("crs")
        or raw_grid.get("transform") != target_grid.get("transform")
        or raw_grid.get("resolution_m") not in (None, resolution)
        or raw_grid.get("dimensions") not in (None, dimensions)
    ):
        raise ValueError("quality grid binding is invalid")
    transform = target_grid.get("transform")
    if (
        not isinstance(transform, (list, tuple))
        or len(transform) != 6
        or not all(
            isinstance(value, (int, float)) and math.isfinite(float(value)) for value in transform
        )
    ):
        raise ValueError("quality affine is invalid")
    provider = provenance.get("provider")
    sensor = provenance.get("sensor")
    if not isinstance(provider, str) or not provider or not isinstance(sensor, str) or not sensor:
        raise ValueError("quality provider/sensor evidence is unavailable")
    required_artifacts = set(NDVI_ARTIFACTS + MASK_ARTIFACTS + PNG_ARTIFACTS)
    artifact_references = metadata.get("artifact_references")
    if (
        not isinstance(artifact_references, dict)
        or set(artifact_references) != required_artifacts
        or any(artifact_references[name] != name for name in required_artifacts)
    ):
        raise ValueError("quality artifact references are incomplete")
    artifact_checksums = provenance.get("artifact_checksums")
    if (
        not isinstance(artifact_checksums, dict)
        or set(artifact_checksums) != required_artifacts
        or any(
            not isinstance(artifact_checksums[name], str)
            or not re.fullmatch(r"[0-9a-f]{64}", artifact_checksums[name])
            for name in required_artifacts
        )
    ):
        raise ValueError("quality artifact integrity evidence is incomplete")
    gates = {
        "preparation_coverage_pct": 70.0,
        "final_ndvi_coverage_pct": FINAL_NDVI_COVERAGE_GATE,
        "common_comparison_coverage_pct": FINAL_COMMON_COVERAGE_GATE,
    }
    prep = {
        period: float(metrics[f"preparation_coverage_period_{period}_pct"]) for period in ("a", "b")
    }
    final = {
        period: float(metrics[f"final_ndvi_coverage_period_{period}_pct"]) for period in ("a", "b")
    }
    common = float(metrics["final_common_comparison_coverage_pct"])
    for count, pct in (
        (metrics["preparation_valid_pixels_period_a"], prep["a"]),
        (metrics["preparation_valid_pixels_period_b"], prep["b"]),
        (metrics["final_ndvi_valid_pixels_period_a"], final["a"]),
        (metrics["final_ndvi_valid_pixels_period_b"], final["b"]),
        (metrics["final_common_comparison_pixels"], common),
    ):
        if abs(float(count) / denominator * 100.0 - pct) > 1e-5:
            raise ValueError("quality coverage binding is invalid")
    if set(canonical_scenes) != {"period_a", "period_b"}:
        raise ValueError("canonical QA period evidence is incomplete")
    qa_periods: dict[str, dict[str, Any]] = {}
    for key in ("a", "b"):
        value = canonical_scenes[f"period_{key}"]
        if not isinstance(value, dict):
            raise ValueError("canonical QA period evidence is invalid")
        policy_present = "qa_policy" in value and value["qa_policy"] is not None
        if policy_present and (not isinstance(value["qa_policy"], str) or not value["qa_policy"]):
            raise ValueError("canonical QA policy is invalid")
        warnings_present = (
            "best_effort_warnings" in value and value["best_effort_warnings"] is not None
        )
        if warnings_present:
            warnings_value = value["best_effort_warnings"]
            if not isinstance(warnings_value, list) or any(
                not isinstance(item, str) for item in warnings_value
            ):
                raise ValueError("canonical QA warnings are invalid")
        else:
            warnings_value = None
        policy_available = isinstance(value.get("qa_policy"), str) and bool(value["qa_policy"])
        warnings_available = isinstance(warnings_value, list)
        qa_periods[key] = {
            "period": key,
            "policy": value.get("qa_policy") if policy_available else None,
            "policy_available": policy_available,
            "warnings": list(warnings_value) if warnings_available else None,
            "warnings_available": warnings_available,
            "status": "verified"
            if policy_available and warnings_available
            else "partial"
            if policy_available or warnings_available
            else "unavailable",
        }
    top_warnings = provenance.get("best_effort_warnings")
    if top_warnings is not None and (
        not isinstance(top_warnings, list)
        or any(not isinstance(item, str) for item in top_warnings)
    ):
        raise ValueError("top-level QA warnings are invalid")
    available_period_warnings = [
        qa_periods[key]["warnings"] for key in ("a", "b") if qa_periods[key]["warnings_available"]
    ]
    expected_top_warnings = [item for values in available_period_warnings for item in values]
    if (
        top_warnings is not None
        and available_period_warnings
        and top_warnings != expected_top_warnings
    ):
        raise ValueError("QA warning evidence is contradictory")
    warnings = expected_top_warnings if len(available_period_warnings) == 2 else None
    policies = [
        qa_periods[key]["policy"] for key in ("a", "b") if qa_periods[key]["policy_available"]
    ]
    top_policy = provenance.get("qa_policy")
    if top_policy is not None and (not isinstance(top_policy, str) or not top_policy):
        raise ValueError("top-level QA policy is invalid")
    if top_policy is not None and policies:
        if len(policies) == 1 and top_policy != policies[0]:
            raise ValueError("QA policy evidence is contradictory")
        if len(policies) == 2 and (policies[0] != policies[1] or top_policy != policies[0]):
            raise ValueError("QA policy evidence is contradictory")
    qa_policy = policies[0] if len(policies) == 2 and policies[0] == policies[1] else None
    policy_available = all(qa_periods[key]["policy_available"] for key in ("a", "b"))
    warnings_available = all(qa_periods[key]["warnings_available"] for key in ("a", "b"))
    qa_status = (
        "verified"
        if policy_available and warnings_available
        else "partial"
        if policy_available or warnings_available
        else "unavailable"
    )
    return {
        "status": "available",
        "aoi": {"id": provenance["aoi_id"], "rasterized_pixels": denominator},
        "requested_periods": metadata.get("analysis_periods", {}),
        "actual_scenes": period_evidence,
        "provider": provider,
        "sensor": sensor,
        "crs": provenance.get("target_crs"),
        "resolution_m": target_grid.get("resolution_m"),
        "grid_dimensions": dimensions,
        "target_transform": [float(value) for value in transform],
        "coverage": {
            "preparation": {
                "period_a": {
                    "pixels": int(metrics["preparation_valid_pixels_period_a"]),
                    "pct": prep["a"],
                    "gate_pct": gates["preparation_coverage_pct"],
                    "passes": prep["a"] >= 70.0,
                },
                "period_b": {
                    "pixels": int(metrics["preparation_valid_pixels_period_b"]),
                    "pct": prep["b"],
                    "gate_pct": gates["preparation_coverage_pct"],
                    "passes": prep["b"] >= 70.0,
                },
            },
            "final_ndvi": {
                "period_a": {
                    "pixels": int(metrics["final_ndvi_valid_pixels_period_a"]),
                    "pct": final["a"],
                    "gate_pct": gates["final_ndvi_coverage_pct"],
                    "passes": final["a"] >= gates["final_ndvi_coverage_pct"],
                },
                "period_b": {
                    "pixels": int(metrics["final_ndvi_valid_pixels_period_b"]),
                    "pct": final["b"],
                    "gate_pct": gates["final_ndvi_coverage_pct"],
                    "passes": final["b"] >= gates["final_ndvi_coverage_pct"],
                },
            },
            "common_comparison": {
                "pixels": int(metrics["final_common_comparison_pixels"]),
                "pct": common,
                "gate_pct": gates["common_comparison_coverage_pct"],
                "passes": common >= gates["common_comparison_coverage_pct"],
            },
        },
        "common_mask_means": {
            "period_a": float(metrics["mean_ndvi_period_a"]),
            "period_b": float(metrics["mean_ndvi_period_b"]),
            "delta": float(metrics["mean_delta_ndvi"]),
        },
        "metrics_digest": digest,
        "artifact_integrity": dict(artifact_checksums),
        "qa": {
            "policy": qa_policy,
            "warnings": warnings,
            "policy_available": policy_available,
            "warnings_available": warnings_available,
            "periods": qa_periods,
            "provenance_status": qa_status,
            "canonical_evidence_status": "verifier-passed",
        },
        "qa_policy": qa_policy,
        "qa_warnings": warnings,
        "qa_status": qa_status,
        "qa_warnings_available": warnings_available,
        "qa_periods": qa_periods,
        "verification": {
            "existing": "server verifier passed",
            "independent_numerical_agreement": "not asserted by this API report",
            "external_accuracy": "not assessed",
        },
        "limitations": [
            "Valid-data coverage is a data-quality measure, not vegetation coverage, FVC, or accuracy.",
            "A small historical mean NDVI delta is not evidence of statistically significant ecological change.",
            "External remote-sensing accuracy requires independent reference data and is not assessed here.",
        ],
    }
