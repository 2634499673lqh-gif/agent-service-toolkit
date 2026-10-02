# Phase 14 Implementation 3B evidence package

Prepared 2026-10-02 from the frozen Sentinel-2 L2A item snapshots already in
the repository. The approved exploratory `water_change` / `NDWI` workflow now
consumes this package; it does not change the NDVI fixture or authorize
confirmed water-area claims.

## Prepared data

The independent fixture is under
`data/geochange-fixtures/real-sentinel2-ndwi-v5/`:

- `period_a.npz` and `period_b.npz`: B03, B08, native SCL and a scene-footprint
  coverage mask;
- `period_a.item.json` and `period_b.item.json`: copied STAC snapshots;
- `manifest.json` and `manifest.sha256`: asset identity, geometry, radiometry,
  provenance and local checksums.

The common target grid is EPSG:32650, 10 m, 24×24 pixels. B03/B08 use the
same native transform `[10, 0, 199980, 0, -10, 3400020]`. SCL is retained at
its native 20 m, 12×12 grid with transform `[20, 0, 199980, 0, -20, 3400020]`.
The candidate 10 m mask expands each SCL pixel to a 2×2 block; no resampled
SCL data is stored.

The selected crop is `[241180, 3387560, 241420, 3387800]` in UTM 50N. Period A
has 576/576 pixels inside its footprint; period B has 395/576. Pixels outside
the period-specific footprint are explicitly invalid. This resolves the
previous period B footprint mismatch without claiming full-scene coverage.

Fixture checksums:

| period | NPZ SHA-256 | coverage pixels | valid pixels | SCL-water pixels |
|---|---|---:|---:|---:|
| A | `a3a40d907c5e33f633667aafadf0a378f3934c466a30954c533d762f605b6636` | 576 | 218 | 39 |
| B | `8793b491fca151d467764e6aafb20752fdbde3df45c1af104b24bcf910838f54` | 395 | 355 | 47 |

Manifest SHA-256 is
`90416a43c08d127a4ed46b313481d803123d04109c94bb4542e1e4439e867aad`.
Preparation used 3,102,952 bytes of bounded HTTP Range reads. No full COG was
downloaded.

## Mask and radiometry evidence

The reproducible candidate mask keeps scene-footprint pixels with SCL classes
4 (vegetation), 5 (not vegetated) and 6 (water), finite B03/B08 reflectance,
non-negative scaled reflectance, and a positive denominator. It excludes SCL
no-data, saturated/defective, cloud-shadow, unclassified, cloud and cirrus
classes. Zero denominators and non-finite values are invalid.

This is a preparation contract for review. It is not yet an activated runtime
contract. Pixel area is 100 m² only after the target transform is accepted.

## Scientific threshold check

The candidate NDWI calculation uses the ADR-014 formula after applying the
asset scale/offset. Against the local SCL labels in this small crop, pooled
classification F1 scores were:

| candidate threshold | pooled TP | pooled FP | pooled FN | F1 |
|---:|---:|---:|---:|---:|
| 0.00 | 51 | 140 | 35 | 0.368 |
| 0.10 | 44 | 98 | 42 | 0.386 |
| 0.15 | 42 | 60 | 44 | 0.447 |
| 0.20 | 37 | 36 | 49 | 0.465 |
| 0.25 | 18 | 9 | 68 | 0.319 |

The maximum among these candidates is 0.20, but this is only a weak, local
SCL-consistency result: the sample is 573 valid pixels, the period A water
class contains mixed/low or negative reflectance values, and SCL is a product
classification layer rather than independent ground truth. Therefore `0.20`
is a candidate parameter, not a validated scientific threshold. Water-area
change thresholds remain unvalidated. The fixture supports describing NDWI
values and SCL-labelled candidate areas; it does not support claiming confirmed
water expansion.

## Reusable 3C evidence

Both copied STAC snapshots expose `assets.swir16` (B11) at 20 m with scale
`0.0001`, offset `-0.1`, nodata `0`, and source geometry in the item JSON.
This is reusable provenance evidence only. No B11 pixels were prepared, no
resampling rule was frozen, and NDBI remains blocked.

## Validation

Focused fixture tests validate manifest and NPZ checksums, item identity,
transform/CRS/radiometry, SCL-to-10 m expansion, scene coverage, common valid
pixels, water sample presence, and B11 source metadata. The preparation script
rechecks COG ETag, byte length, source shape, transform, tile geometry and
bounded Range reads.

The scientific threshold result and the period-specific mask are reviewable,
but independent scientific validation of classification and change thresholds
is still required by ADR-014.

## Independent reference assessment and classification detail

Two public reference families were assessed without downloading large data:
[Copernicus Water Bodies 2020-present](https://land.copernicus.eu/en/products/water-bodies/water-bodies-global-v1-0-100m)
and [Dynamic World V1](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1).
Copernicus Water Bodies 2020-present is monthly and globally available, but its
100 m grid supplies too few pixels for this 240 m crop and its monthly synthesis
does not provide date-matched 10 m labels. Dynamic World is date-linked to
individual Sentinel-2 acquisitions at 10 m and is CC-BY 4.0, but no local
period-A/period-B export is available here and its labels are model outputs,
not independent ground truth. Neither source is used as a label.

The deterministic exploratory assessment is implemented in
`scripts/validate_ndwi_science.py`. It reports each period separately:

| period | valid | SCL water | SCL non-water | threshold 0.20 TP/FP/FN/TN | F1 |
|---|---:|---:|---:|---|---:|
| A | 218 | 39 | 179 | 7 / 0 / 32 / 179 | 0.304 |
| B | 355 | 47 | 308 | 30 / 36 / 17 / 272 | 0.531 |

The common-valid comparison contains 160 pixels. At thresholds 0.00, 0.10,
0.15, 0.20, 0.25 and 0.30, the candidate period-B-minus-period-A water-pixel
deltas are respectively 82, 48, 28, 20, 4 and −1 pixels (each candidate pixel
is 100 m²). The result is highly threshold-sensitive and does not establish a
scientific change threshold. Period A and B do not support one consistent
validated classification threshold.

Because an independent, date-matched reference is unavailable, the minimal
scope-reduction proposal is recorded in
`process/ADR-014-3B-EXPLORATORY-ADDENDUM.md`. It is not an ADR amendment and
does not authorize runtime work.
