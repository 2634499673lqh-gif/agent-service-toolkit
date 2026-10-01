# T144 Live Product Smoke Evidence

Date: 2026-10-01  
Environment: rebuilt Docker Compose stack (`postgres`, `agent_service`,
`streamlit_app`), authenticated Product API path  
Model/provider mode: fake model (`provider=fake`, `model=fake`)  
Live switches: `GEOCHANGE_LIVE_LLM=false`, `GEOCHANGE_LIVE_STAC=false`

This is a bounded Product workflow smoke, not scientific validation, a
production benchmark, or production-scale GIS evidence.

## Task and run

- Request: `Compare Wuhan East Lake vegetation change` for July 2023 versus
  July 2024, with a maximum Sentinel-2 cloud threshold of 30% and NDVI decline
  threshold of `-0.15`.
- Task ID: `ab5b1978-bee8-48ee-a161-47ae126569ac`
- TaskRun ID: `1eab9bd0-f1ae-44ba-89a1-586acd39da57`
- Terminal status: `succeeded`
- Execution mode: `CACHED_REAL_SENTINEL2_RASTER`
- Runtime profile: `provider=fake`, `model=fake`, `live_provider=false`
- Verifier status: `passed`

## Stage and trace evidence

Persisted terminal `stage_status` was:

```json
{"planner":"passed","execution":"passed","verifier":"passed"}
```

The authenticated trace contained 8 events. The capability events were all
`succeeded` for, in order: `resolve_aoi`, `search_sentinel2`,
`compute_vegetation_change`, and `summarize_change`.

## Scene and fixture evidence

The terminal `selected_scene_evidence` bound the run to:

| Period | Scene | Acquisition date | Collection | Cloud cover |
|---|---|---|---|---:|
| A | `S2B_50RKU_20230728_1_L2A` | 2023-07-28 | `sentinel-2-l2a` | 22.900553 |
| B | `S2A_50RKU_20240730_0_L2A` | 2024-07-30 | `sentinel-2-l2a` | 8.247093 |

The fixture manifest checksum was
`875e22bd8c188e0c0eaf1a1f7c28eef8687454ff62dd4442ce29d4adf0b36b12`.
The four selected Red/NIR asset hashes were present in the same bounded
evidence object. The explicit live STAC switch was off, so these scene IDs are
fixture-bound metadata and the pixels were processed locally from the cached
real Sentinel-2 fixture; no remote raster or COG pixels were processed.

## GeoChange metrics

| Metric | Value |
|---|---:|
| Valid pixels | 914 |
| Mean NDVI, period A | 0.4131954610 |
| Mean NDVI, period B | 0.0769188181 |
| Mean delta NDVI | -0.3362765908 |
| Decline threshold | -0.2 (server default) |
| Decline percentage | 59.5185995624 |
| Valid analysis area (m²) | 91,400 |
| Significant decline area (m²) | 54,400 |

## Authenticated artifacts

Each request used the authenticated TaskPilot client and returned HTTP 200 with
`image/png` content and a valid PNG signature:

| Artifact | Bytes | Result |
|---|---:|---|
| `ndvi_before` | 1,595 | present and readable |
| `ndvi_after` | 1,737 | present and readable |
| `ndvi_change` | 1,447 | present and readable |

No credentials, tokens, raw provider payloads, or unrestricted trace contents
are recorded here. Deterministic provider-free evaluation remains recorded
separately in `docs/EVALUATION.md`.

## Corrected smoke after T144 focused review

The original smoke above is preserved as historical evidence of the defect: its
runtime used the cached mode but persisted the omitted-value default
`decline_threshold=-0.2` despite the explicit request text. After the offline
planner fix, a second authenticated Product run completed with:

- Task ID: `24e3eadd-6ea6-4733-85b0-4706d868be34`
- TaskRun ID: `84bfd841-5a04-4877-a2f2-c12f0d20c85d`
- Status: `succeeded`
- Stage status: planner/execution/verifier all `passed`
- Verifier: `passed`
- Execution mode: `CACHED_REAL_SENTINEL2_RASTER`
- Runtime profile: `provider=fake`, `model=fake`, `live_provider=false`
- Effective decline threshold: `-0.15`
- Decline percentage: `68.0525164114`
- Significant decline area: `62,200 m²`
- Trace: 8 events; the four GeoChange capabilities all succeeded
- Artifacts: `ndvi_before`, `ndvi_after`, and `ndvi_change` each returned
  authenticated HTTP 200 with valid PNG content (1,595 / 1,737 / 1,447 bytes)

The corrected run used the same fixture-bound scene IDs and manifest checksum as
the original run. Live LLM and live STAC were disabled; the scene metadata is
fixture evidence and raster computation remained local.
