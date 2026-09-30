# MVP deterministic evaluation evidence

The GeoChange evaluation is a small, provider-free portfolio smoke. It uses the existing `geochange` models, deterministic NumPy raster path, artifact generator, and verifier. It does not claim model accuracy, production performance, or scientific validation.

Run it with:

```powershell
uv run python scripts/evaluation_geochange.py
```

The eight cases cover normal success, validated parameter propagation, unsupported AOI rejection, invalid configuration rejection, quality failure with one bounded replan, exhausted replan budget, incomplete evidence rejection, and three-artifact completeness. The command prints measured pass/fail counts; values are not hard-coded into this report.

The approved data boundary remains `REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE`: live STAC metadata is optional, while evaluation raster pixels are controlled local arrays. Optional live DeepSeek or STAC smoke tests are separate from this deterministic suite.
