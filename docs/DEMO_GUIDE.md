# GeoChange portfolio demo

Use the Docker/local path in [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md), then run the deterministic evaluation and the existing authenticated Product flow. The Product view preserves login, organization selection, task creation, Start Run, persisted TaskRun reads, result metadata, authorized artifacts, trace, and logout.

## Screenshot checklist

Capture manually from the actual running UI under `docs/assets/` only when needed:

- authenticated organization and task view;
- TaskRun status and execution mode;
- NDVI before/after/change comparison;
- metric cards and final explanation;
- trace timeline, including a visible replan event when the controlled fixture is enabled.

Do not fabricate screenshots or commit files from `data/geochange-artifacts/`.

## 60–120 second recording checklist

1. Log in and select the organization.
2. Open the Wuhan East Lake vegetation-change task.
3. Start one run and show the persisted TaskRun status.
4. Show Period A/Period B, verifier status, metrics, and the three NDVI images.
5. Read the final explanation.
6. Open the trace and point out Planner → tools → verifier → success.
7. If enabled, show the single bounded replan.
8. Explain that the run uses cached real Sentinel-2 Red/NIR pixels bound to two manifest-verified scenes; live STAC metadata is optional and never downloads a raster per run.
9. Mention tenant-scoped artifact authorization and current MVP limitations.
