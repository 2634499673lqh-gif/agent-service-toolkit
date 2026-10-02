# ADR-014 3B exploratory scope addendum

Status: **APPROVED for limited exploratory implementation (2026-10-02)**

This document records the independent architecture and scientific review
approval for the limited exploratory scope. It does not amend, overwrite, or silently revise the
accepted/frozen decisions in `process/ADR-014.md`. The original ADR-014 3B
scope remains blocked until its scientific threshold gate is satisfied.

## 1. Explicit scope revision

| Original 3B concern | Addendum disposition | Contract consequence |
|---|---|---|
| NDWI water-class threshold validation | **DEFERRED** | No validated water-class policy is authorized. |
| Confirmed water-area metrics | **DEFERRED** | Core results omit confirmed water area fields. |
| Confirmed water expansion/contraction claims | **NOT AUTHORIZED** | No result, summary, UI label or artifact may make this claim. |
| Water-area change classification threshold | **DEFERRED** | Candidate thresholds may appear only in explicitly exploratory data. |
| Real B03/B08/SCL, coverage mask, checksum, provenance, grid and nodata validation | **RETAINED / REQUIRED** | The v5 fixture and its evidence remain mandatory inputs. |
| Runtime security, lifecycle, resume, replan, Verifier, artifact authorization and NDVI compatibility | **RETAINED / REQUIRED** | The existing Runtime security and lifecycle contract remains unchanged. |
| Exploratory NDWI before/after/change, continuous statistics and maps | **PROPOSED REPLACEMENT SCOPE** | May be implemented only after this addendum is independently approved. |

Approval of this addendum authorizes only the replacement exploratory scope.
It does not authorize confirmed water-area claims, and it does not unlock 3C.

## 2. Implementation-ready exploratory contract

The following is the complete minimum contract for this implementation batch.

### 2.1 Static Skill

The server-owned Skill pairing is:

```text
analysis_type = water_change
indicator     = NDWI
```

The only authorized capability sequence is:

```text
resolve_aoi
→ search_sentinel2
→ compute_water_change
→ summarize_change
```

`compute_water_change` is the water-specific terminal calculation replacing
the NDVI-only `compute_vegetation_change`; `resolve_aoi`, Sentinel-2 search,
plan validation, checkpoint handling, Verifier authority and lifecycle rules
are reused from the existing framework. `summarize_change` produces only the
continuous NDWI statistics defined below. The NDVI sequence remains:

```text
resolve_aoi → search_sentinel2 → compute_vegetation_change → summarize_change
```

The two sequences are separate allowlisted Skill contracts. Planner output,
checkpoint data, capability output and candidate terminal output cannot select
or replace a Skill. The server derives the Skill from validated confirmed
intent on every start and resume.

### 2.2 Confirmed intent

`water_change` uses an independent strict intent branch:

```json
{
  "analysis_type": "water_change",
  "indicator": "NDWI",
  "analysis_area": "wuhan_east_lake",
  "period_a": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "period_b": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "parameters": {
    "source": "Sentinel-2",
    "cloud_threshold": 30.0
  }
}
```

The branch is strict (`extra=forbid`) and bounded to 4096 UTF-8 bytes. It
requires the approved Wuhan East Lake AOI, ordered and non-overlapping periods,
Sentinel-2 as the source, and the server-owned cloud-quality policy. Fixture
selection is server-owned; callers cannot provide a local path, remote URL,
manifest, scene, mask or threshold that changes the selected evidence.

There is no `decline_threshold` in this branch. An NDVI decline threshold is
not a water threshold. No unvalidated water threshold is an authorized policy
field. The existing `vegetation_change`/`NDVI` JSON shape, defaults, and
`confirmed_intent = NULL` legacy path remain unchanged.

### 2.3 Raster, mask and area

The deterministic index is:

```text
NDWI = (B03 - B08) / (B03 + B08)
```

B03 and B08 use their actual asset scale/offset before calculation. The
exploratory contract uses the validated v5 evidence:

- both bands on the same 10 m EPSG:32650 grid and transform;
- SCL retained at native 20 m and mapped categorically to 10 m by nearest
  neighbour (each source pixel covers a 2×2 target block);
- period-specific scene-footprint coverage mask;
- nodata exclusion;
- exclusion of SCL classes 0, 1, 2, 3, 7, 8, 9, 10 and 11;
- finite, non-negative scaled reflectance;
- finite positive denominator;
- the intersection of both period masks for temporal comparison;
- pixel area derived from the accepted target transform, currently 100 m².

Pixels outside a scene footprint are invalid. Single-period coverage cannot be
used as a substitute for the common-valid comparison area. If the common-valid
mask is empty, calculation fails closed.

### 2.4 Result schema and metrics

The result uses the existing bounded shared result envelope with:

```text
schema_version = geochange.v1
analysis_type  = water_change
indicator     = NDWI
verifier_status = passed
```

The required core metrics are exactly:

| Metric | Type / unit | Required validation |
|---|---|---|
| `valid_pixels` | positive integer, pixels | equals the common-valid mask count; cannot be zero |
| `valid_analysis_area_m2` | finite non-negative number, m² | equals `valid_pixels × transform-derived pixel area` |
| `mean_ndwi_period_a` | finite float, dimensionless | mean over the common-valid mask; bounded to `[-1, 1]` |
| `mean_ndwi_period_b` | finite float, dimensionless | mean over the same common-valid mask; bounded to `[-1, 1]` |
| `mean_delta_ndwi` | finite float, dimensionless | `mean(ndwi_b - ndwi_a)` over the same mask; bounded to `[-2, 2]` |

The authoritative metrics omit:

```text
confirmed_water_area_period_a
confirmed_water_area_period_b
confirmed_water_area_delta
confirmed_water_expansion
```

These fields must not be encoded as zero. If a future exploratory view shows a
candidate threshold or candidate area, it must live in a separate bounded
`exploratory` object containing an explicit `unvalidated: true` marker. It is
not part of the authoritative metrics and cannot enter summaries or policy
decisions automatically.

### 2.5 Provenance and artifacts

The artifact allowlist is:

```text
ndwi_before → ndwi_before.png
ndwi_after  → ndwi_after.png
ndwi_change → ndwi_change.png
```

Artifacts remain tenant-bound through the existing TaskRun/task/run artifact
path and authorization checks. The terminal result must bind to:

- canonical AOI and period identity;
- item ID and collection for both periods;
- pinned v5 fixture manifest;
- B03, B08 and SCL identities;
- scene-footprint and mask evidence;
- execution mode;
- validated provenance summary.

Candidate terminal output cannot establish any of these authorities. Stored
result metadata must be created only after the server revalidates this binding.

### 2.6 Verifier and lifecycle failures

The future batch must fail closed for:

- unsupported or wrong Skill/indicator;
- malformed or cross-Skill confirmed intent;
- invalid plan, step order or replan;
- checkpoint evidence mismatch;
- invalid raster, scene, item, collection, fixture or mask provenance;
- invalid scene geometry or coverage evidence;
- zero common-valid pixels;
- invalid or non-finite required metrics;
- missing, unauthorized or mismatched artifacts;
- malformed, non-object or unauthorized terminal output;
- invalid stored `execution_result` during resume;
- any confirmed water-area or confirmed water-expansion claim.

The Verifier stage remains authoritative. Start and resume derive the same Skill
contract. Successful stored execution results are revalidated before lifecycle
success metadata persistence. Cross-tenant resources remain inaccessible.
Historical NDVI tasks, old checkpoints, existing artifacts and NULL-intent
legacy execution remain unchanged.

### 2.7 Acceptance for the future implementation batch

One complete implementation batch must cover:

- NDWI computation and deterministic fixture integration;
- static Skill and strict confirmed intent;
- Runtime start, resume and replan validation;
- mask, provenance and common-valid area validation;
- Verifier and terminal-result validation;
- `ndwi_before/after/change` artifact authorization;
- tenant-safe access and Streamlit exploratory visualization;
- negative and recovery tests;
- PostgreSQL persistence evidence;
- affected GeoChange regression;
- Ruff, Pyrefly, formatting and diff checks.

This is one bounded implementation batch authorized by the approved addendum.

## 3. Independent approval boundary

The original ADR-014 3B contract remains blocked. This addendum authorizes only
the exploratory replacement scope; it does not validate the deferred water
thresholds, authorize confirmed water areas, authorize confirmed
expansion/contraction claims, or unlock 3C.

The v5 fixture, preparation script, scientific assessment and evidence report
remain retained inputs. The exploratory Runtime implementation may consume
these inputs under the bounded contract; NDVI behavior, migrations and the
accepted ADR-014 architecture remain unchanged.
