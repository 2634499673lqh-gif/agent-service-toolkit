# GeoChange cached Sentinel-2 fixture

Phase 13 Batch B uses `geochange.real-sentinel2.v1`, a checked-in NPZ bundle
under `data/geochange-fixtures/real-sentinel2-v1/`. It contains native uint16
64×64 crops of Sentinel-2 L2A Red (B04) and NIR (B08), at 10 m in EPSG:32650.
The crop window is `[241180, 3387380, 241820, 3388020]` in UTM zone 50N and
lies inside the catalogued Wuhan East Lake AOI. The two accepted items are:

| period | item | acquisition | cloud | fixture SHA-256 |
| --- | --- | --- | ---: | --- |
| A | `S2B_50RKU_20230728_1_L2A` | 2023-07-28 | 22.900553 | `19b4e443dfb7378bfed44979c6dcbe7ef9d610866d5005826ebf9b79877c3e94` |
| B | `S2A_50RKU_20240730_0_L2A` | 2024-07-30 | 8.247093 | `73e41d6a27f471e1409ba955342b077099b9048a6e345cc1a927fbe554187617` |

The complete asset URLs, ETags, byte lengths, STAC item snapshots, AOI, CRS,
dimensions, radiometry, manifest digest, source basis and licensing fields are
in `manifest.json`. The manifest SHA-256 is
`875e22bd8c188e0c0eaf1a1f7c28eef8687454ff62dd4442ce29d4adf0b36b12`.

Runtime processing is offline: it verifies the manifest and each NPZ checksum,
checks exact scene/asset binding, converts the source scale/offset, masks
nodata, and computes NDVI/delta/statistics with NumPy. The runtime mode is
`CACHED_REAL_SENTINEL2_RASTER`. Live STAC metadata, when explicitly enabled,
must identify these same scenes and asset identities; it never downloads or
processes a raster during a run.

The preparation script is opt-in and bounded:

```powershell
python scripts/prepare_geochange_fixture.py --output <new-directory>
```

It uses four HTTP Range reads per scene (COG headers and one tile per band),
requires only preparation-time `tifffile`/`imagecodecs` alongside NumPy, and
fails if the source ETag, geometry, radiometry, or checksum changes. The
repository fixture is below the documented 1 MB bound. Source attribution and
the Copernicus legal notice are recorded verbatim in the manifest; the fixture
is modified Sentinel data and carries no claim of scientific accuracy.
