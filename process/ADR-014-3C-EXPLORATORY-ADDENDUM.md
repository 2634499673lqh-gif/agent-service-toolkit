# ADR-014 3C exploratory scope addendum

Status: **APPROVED FOR LIMITED EXPLORATORY IMPLEMENTATION (2026-10-03)**

The independent evidence gate approved the bounded NDBI workflow described
below. This approval authorizes the exploratory `urban_change`/`NDBI` runtime,
artifact and UI path only; the deferred built-up threshold and all confirmed
urban-expansion claims remain blocked.

This addendum records the evidence-gated replacement scope for 3C. It does
not amend or overwrite the accepted/frozen `process/ADR-014.md`.

## 1. Scope revision

| Original 3C requirement | Disposition | Contract consequence |
|---|---|---|
| Real B11/B08/SCL, coverage, checksum, provenance and grid evidence | **RETAINED** | The independent `real-sentinel2-ndbi-v1` fixture and its source manifest are mandatory inputs. |
| Deterministic B11-to-B08 resampling and common-valid mask | **RETAINED** | Native B11 remains 20 m; nearest-neighbour 2×2 expansion is frozen for the target grid. |
| NDBI continuous before/after/change statistics | **REPLACED BY EXPLORATORY SCOPE** | Only bounded index metrics and maps are authorized. |
| Validated built-up classification threshold | **DEFERRED** | No threshold is server policy or a confirmed class label. |
| Confirmed built-up area metrics | **DEFERRED** | No authoritative built-up-area field is emitted. It must not be encoded as zero. |
| Confirmed urban expansion or construction-land conversion | **NOT AUTHORIZED** | No result, summary, UI label, artifact or verifier output may make these claims. |
| Runtime security, lifecycle, resume, replan, Verifier, tenant isolation and NDVI/NDWI compatibility | **RETAINED** | Existing server authority and legacy paths remain unchanged. |

## 2. Implementation-ready exploratory contract

### 2.1 Static Skill

The server-owned pairing is:

```text
analysis_type = urban_change
indicator     = NDBI
```

The only capability sequence is:

```text
resolve_aoi → search_sentinel2 → compute_urban_change → summarize_change
```

The sequence is a separate allowlisted Skill contract. The server derives it
from validated confirmed intent on every start and resume. Planner output,
checkpoint data, replan output and capability output cannot select or replace
the Skill.

### 2.2 Confirmed intent

The strict branch is bounded to 4096 UTF-8 bytes and uses `extra=forbid`:

```json
{
  "analysis_type": "urban_change",
  "indicator": "NDBI",
  "analysis_area": "wuhan_east_lake",
  "period_a": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "period_b": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "parameters": {"source": "Sentinel-2", "cloud_threshold": 30.0}
}
```

The AOI, ordered non-overlapping periods, source and cloud policy are
validated server-side. Fixture selection, item IDs, URLs, crop windows,
resampling, SCL classes, masks and any threshold are server-owned. No
`decline_threshold` or caller-supplied classification threshold is accepted.

### 2.3 Raster and masks

The calculation uses scaled B11/B08 reflectance and the formula
`(B11 - B08) / (B11 + B08)`. B11 is retained at native 20 m and expanded to
the aligned EPSG:32650, 10 m target grid with nearest-neighbour 2×2 blocks.
SCL is mapped categorically by the same block rule. Coverage, SCL classes 4/5/6,
finite non-negative reflectance and a non-zero finite denominator define each
period mask. The temporal mask is the intersection of both period masks, and
pixel area is derived from the accepted 10 m transform (100 m² here).

### 2.4 Result contract

The single versioned result uses `schema_version = geochange.v1`,
`analysis_type = urban_change`, `indicator = NDBI`, and
`verifier_status = passed`. Required continuous metrics are:

| Field | Type/range | Meaning |
|---|---|---|
| `valid_pixels` | positive integer | count on the common-valid mask |
| `valid_analysis_area_m2` | finite non-negative number | `valid_pixels × 100` for this target transform |
| `mean_ndbi_period_a` | finite float in `[-1, 1]` | common-mask period-A mean |
| `mean_ndbi_period_b` | finite float in `[-1, 1]` | common-mask period-B mean |
| `mean_delta_ndbi` | finite float in `[-2, 2]` | common-mask mean of period-B minus period-A |

No `built_up_area_*`, `urban_expansion`, `construction_land_conversion` or
similar field exists in the authoritative result. An optional candidate
threshold diagnostic, if ever displayed, must be separately marked
`unvalidated: true` and cannot feed summaries, policy or verifier success.

### 2.5 Artifacts and provenance

The only artifact names are `ndbi_before`, `ndbi_after` and `ndbi_change`.
They remain tenant-bound through the existing TaskRun/run artifact
authorization path. Successful results must bind the canonical AOI and
periods, both item IDs and collections, the pinned NDBI fixture manifest,
B08/B11/SCL identities, target/native transforms, resampling rule, coverage,
common-valid mask, execution mode and checksums. Checkpoint/resume/replan and
Verifier paths must revalidate this server evidence before persistence.

### 2.6 Scientific summary

The summary is generated only from the server-verified continuous metrics and
must use exploratory NDBI wording. It may say that the mean NDBI changed on
the common-valid analysis area. It must not say that urban expansion,
built-up-area increase or construction-land conversion was confirmed. A
threshold is not frozen until independent date-matched reference validation is
available.

### 2.7 Compatibility

The existing NDVI and exploratory NDWI contracts, NULL-intent legacy behavior,
historical checkpoints, TaskRun metadata limits, artifact authorization,
tenant checks and accepted ADR-014 architecture remain unchanged.

## 3. Future implementation batch acceptance

After independent approval, one bounded implementation batch must cover:

- static Skill and strict confirmed-intent routing;
- deterministic NDBI calculation, target-grid/resampling and common-mask tests;
- source identity, checksum, coverage, nodata and provenance binding;
- zero-denominator, empty-mask, invalid-grid, cross-Skill and forged-result negatives;
- start, resume, replan, checkpoint and Verifier fail-closed behavior;
- `ndbi_before/after/change` artifact authorization and tenant isolation;
- exploratory Streamlit rendering without confirmed urban claims;
- PostgreSQL persistence and reload evidence;
- affected GeoChange/runtime/service tests, Ruff, Pyrefly, formatting and diff checks.

Until that review returns `APPROVED`, 3C remains evidence/design only.
