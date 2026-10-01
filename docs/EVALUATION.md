# MVP deterministic evaluation evidence

The GeoChange evaluation is a small, provider-free portfolio smoke. It uses the existing `geochange` models, deterministic NumPy raster path, artifact generator, and verifier. It does not claim model accuracy, production performance, or scientific validation.

Run it with:

```powershell
uv run python scripts/evaluation_geochange.py
```

The eight cases cover normal success, validated parameter propagation, unsupported AOI rejection, invalid configuration rejection, quality failure with one bounded replan, exhausted replan budget, incomplete evidence rejection, and three-artifact completeness. On 2026-10-01 the command completed with `8/8` passing (`pass_rate=1.0000`); rerun it to obtain fresh evidence. Values are not hard-coded into the evaluator.

The supported data boundary is `CACHED_REAL_SENTINEL2_RASTER`: evaluation reads two 64×64, 10 m, provenance-bound B04/B08 crops cached under `data/geochange-fixtures/real-sentinel2-v1/`. When the opt-in live STAC switch is enabled, metadata must still identify those exact cached scenes; pixels remain local. Optional live DeepSeek or STAC smoke tests are separate from this deterministic suite. This is workflow/regression evidence, not a scientific accuracy study, production benchmark, or production-scale GIS evaluation.
