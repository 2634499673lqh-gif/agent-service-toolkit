# Phase 14 / 3C NDBI evidence package

Prepared 2026-10-02 for the 3C evidence and design gate. The evidence gate
approved the limited exploratory `urban_change`/`NDBI` implementation on
2026-10-03. The package remains the source of truth for the pinned fixture,
grid, mask and continuous-index boundaries; it does not authorize a built-up
threshold or any confirmed urban-expansion claim. ADR-014 remains
accepted/frozen.

## Fixture and source evidence

The independent fixture is under
`data/geochange-fixtures/real-sentinel2-ndbi-v1/`. It contains two NPZ crops,
the copied STAC item snapshots, a manifest, and a manifest checksum. The
existing `real-sentinel2-ndwi-v5` fixture was not modified. Its B08, native SCL
and footprint arrays are copied byte-for-byte into the new fixture; only the
real B11 crops are newly prepared.

The preparation script is `scripts/prepare_geochange_ndbi_fixture.py`. It
performs HEAD checks and bounded HTTP Range reads for the B11 COG tile. The two
preparations read 1,792,904 bytes in total; no complete COG was downloaded.
Each manifest binds item ID, acquisition date, collection, URL, ETag,
Content-Length, source CRS/transform/resolution/shape, scale, offset, nodata,
crop origin/shape, crop checksum, STAC checksum, AOI, coverage and license
metadata.

`scripts/validate_ndbi_science.py` pins the independently verified SHA-256
values for the NDBI manifest, both NDBI NPZ files, both STAC snapshots, the
reused NDWI manifest and both reused NDWI NPZ files in code-owned constants.
It checks those digests before loading raster data. Regenerating
`manifest.sha256` cannot bless a changed manifest. The validator also compares
the copied B08, SCL and coverage arrays with the pinned NDWI v5 arrays, so the
reused inputs cannot silently drift.

| period | item/date | B11 ETag | Content-Length | B11 crop SHA-256 | NPZ SHA-256 |
|---|---|---|---:|---|---|
| A | `S2B_50RKU_20230728_1_L2A` / 2023-07-28 | `b1adb6e3431ed7519414d8ad7c68cce8-3` | 18,365,153 | `819a4b66534e6df9681eec06750fef72f10a00de1c4fa811dd4b784a1c81092c` | `e7a3ecb6b936706c94c0d94899c9c2aac23bddc4369a759b115ea87c12655b56` |
| B | `S2A_50RKU_20240730_0_L2A` / 2024-07-30 | `e126f28831b13502580bd20c6a4d0748-3` | 17,025,962 | `0d2d305c6eabc42d432c128404273bcbde4bca3e595570a9b9c142049ecde507` | `e08d009765786ff887c536849a238d9552f12fea9dc96dcd5780fa08d9c9ffe8` |

The new manifest SHA-256 is
`253b37ed24d81f3c869653155579e9eeb9f3a63cb3eb5ac257eefc67d6071e6a`.
Attribution and the Sentinel data legal notice are retained in the manifest;
no permissive license is inferred.

## Grid, resampling and mask evidence

B08 is native 10 m with transform `[10, 0, 199980, 0, -10, 3400020]`. B11 is
native 20 m with transform `[20, 0, 199980, 0, -20, 3400020]`. Both use
EPSG:32650 and the B11 crop origin `(2060, 620)` maps exactly to the B08
target origin `(4120, 1240)`. The frozen evidence rule is nearest-neighbour
2×2 block expansion from the 12×12 native B11 crop to a 24×24 target grid.
This creates no independent 10 m B11 information; the native 20 m resolution
remains part of the provenance.

The SCL crop remains native 20 m and is expanded categorically to 10 m by the
same 2×2 block rule. Valid pixels require scene-footprint coverage, SCL class
4, 5 or 6, finite scaled reflectance, non-negative reflectance, and a finite
non-zero denominator. Period comparison uses the intersection of both period
masks. The common mask has 322 pixels (32,200 m²); an empty common mask is a
terminal failure. Cloud-shadow, cloud, cirrus, saturated/defective,
unclassified and SCL no-data classes are excluded.

## Deterministic NDBI evidence

`NDBI = (B11 - B08) / (B11 + B08)` after applying each asset's scale and
offset. `scripts/validate_ndbi_science.py` recomputes the result from the
fixture and reports:

| metric | period A | period B | common comparison |
|---|---:|---:|---:|
| valid pixels | 338 | 395 | 322 |
| mean NDBI | -0.2790341 | -0.1061997 | A -0.2691168 / B -0.1286449 |
| mean delta NDBI | — | — | 0.1404719 |
| delta range | — | — | -0.5782405 to 0.7900824 |

The result is reproducible and bounded. Candidate counts are highly sensitive
to the chosen threshold: on the common mask, period-B minus period-A counts
are 120 at -0.2, 78 at -0.1, 12 at 0.0, 4 at 0.1, 3 at 0.2, 1 at 0.3 and 2
at 0.4. These are index-count sensitivities only, not built-up area changes.

Published Sentinel-2 studies report study-specific thresholds, including 0.31
in an automated global method and -0.08 in an index-ensemble infrastructure
method ([Automated Global Method](https://www.mdpi.com/2073-445X/14/5/1061),
[Automated Built-Up Infrastructure Extraction](https://www.mdpi.com/2072-4292/16/5/868)).
Those values use different regions, reference data and classifiers; they do
not validate a Wuhan East Lake threshold. No local date-matched independent
built-up reference is available in this batch. Therefore thresholded
built-up-area metrics and urban-expansion claims remain deferred.

The scientific validator binds every calculation to the manifest and STAC
asset contracts before computing: item/date/collection, source CRS and
dimensions, affine transforms, native resolutions, scale/offset/nodata, crop
origins and shapes, target-grid alignment, categorical SCL alignment, scene
coverage, array names/dtypes, and checksums. Missing files, changed bytes,
metadata mismatches, array geometry changes and altered reused inputs fail
closed at the scientific entry point.

## Scope conclusion

The fixture, grid/resampling rule, masks, deterministic calculation and
provenance are sufficient to propose a bounded exploratory NDBI contract. The
evidence does not support confirmed urban expansion, confirmed built-up area
increase, construction-land conversion, or a validated classification
threshold.
