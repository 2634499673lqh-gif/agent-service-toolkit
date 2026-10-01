# T145 Live Product Smoke Evidence

Date: 2026-10-01  
Environment: rebuilt Docker Compose stack (`postgres`, `agent_service`,
`streamlit_app`) with authenticated Product API  
Live switches: `USE_FAKE_MODEL=false`, `GEOCHANGE_LIVE_LLM=true`,
`GEOCHANGE_LIVE_STAC=true`  
Execution mode: `CACHED_REAL_SENTINEL2_RASTER`

This is evidence of one bounded Product execution. It is not scientific
validation, a production benchmark, or evidence of remote raster processing.

## TaskRun

- Task ID: `86cab770-6674-4e86-8c17-f41ad3040487`
- TaskRun ID: `d0a58c5f-1bec-4323-a41b-32612b972c4e`
- Terminal status: `succeeded`
- Stage status: `planner=passed`, `execution=passed`, `verifier=passed`
- Verifier result: `passed`
- Runtime profile: `provider=deepseek`, `model=deepseek-v4-flash`,
  `live_provider=false`

The real DeepSeek provider was enabled and the live LLM planner path completed
successfully. The persisted `live_provider` flag remains `false` because its
frozen runtime contract means that an external provider was identified in a
runtime observation's bounded provider metadata; the planner model call is not
attached to that observation stream. This recording preserves the persisted
value instead of inferring a stronger runtime claim.

## Live STAC metadata

The Product run had `GEOCHANGE_LIVE_STAC=true`; its `search_sentinel2` trace
event succeeded. The live Earth Search endpoint was:
`https://earth-search.aws.element84.com/v1/search`.

The selected metadata was:

| Period | Live STAC item | Acquisition date | Collection | Cloud cover |
|---|---|---|---|---:|
| A | `S2B_50RKU_20230728_1_L2A` | 2023-07-28 | `sentinel-2-l2a` | 22.900553 |
| B | `S2A_50RKU_20240730_0_L2A` | 2024-07-30 | `sentinel-2-l2a` | 8.247093 |

The live metadata asset URL hashes matched the fixture-bound evidence hashes.
This confirms metadata identity; it does not mean remote assets were opened.

## Cached raster provenance and metrics

The runtime processed only the repository's cached, checksum-verified real
Sentinel-2 B04/B08 pixels. Fixture manifest checksum:
`875e22bd8c188e0c0eaf1a1f7c28eef8687454ff62dd4442ce29d4adf0b36b12`.
The live STAC query supplied scene metadata only; no remote raster or COG
pixels were downloaded or processed.

| Metric | Value |
|---|---:|
| Valid pixels | 914 |
| Mean NDVI, period A | 0.4131954610 |
| Mean NDVI, period B | 0.0769188181 |
| Mean delta NDVI | -0.3362765908 |
| Decline threshold | -0.15 |
| Decline percentage | 68.0525164114 |
| Valid analysis area (m²) | 91,400 |
| Significant decline area (m²) | 62,200 |

The trace contained eight successful events: four executor agent events and
four capability calls (`resolve_aoi`, `search_sentinel2`,
`compute_vegetation_change`, `summarize_change`). Authenticated artifact reads
returned HTTP 200 and valid PNG bytes: `ndvi_before` 1,595 bytes,
`ndvi_after` 1,737 bytes, and `ndvi_change` 1,447 bytes.

No credentials, tokens, raw provider payloads, or unrestricted trace contents
are recorded. The machine-readable sanitized capture is
[`T145_LIVE_PRODUCT_SMOKE.json`](T145_LIVE_PRODUCT_SMOKE.json).
