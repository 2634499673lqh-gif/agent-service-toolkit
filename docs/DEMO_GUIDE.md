# GeoChange portfolio demo

Use the Docker/local path in [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md), then run the deterministic evaluation and the authenticated Product flow. The Product view preserves login, organization selection, task creation, Start Run, bounded runtime execution, persisted TaskRun reads, result metadata, authorized artifacts, trace, and logout.

## Reproducible demo flow

1. Prepare `.env`, PostgreSQL, and the owner account as described in
   [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md). Set `USE_FAKE_MODEL=true` for a
   deterministic local run; use a configured provider only for an explicitly
   recorded live smoke.
2. Start the API and Streamlit UI in separate terminals, then open
   `http://localhost:8501`.
3. Sign in, select the organization when prompted, and create a task with a
   title such as `Compare Wuhan East Lake vegetation change` and a description
   containing the July 2023 and July 2024 periods.
4. Open the task and choose **Start run**. The API creates the tenant-scoped
   TaskRun and dispatches the bounded process-local runtime. Refresh while it is
   active; the UI stops automatic refresh after its finite budget and exposes a
   truthful manual refresh.
5. Read the result in this order: interpreted parameters and stage status;
   runtime profile and selected scenes; NDVI metrics and the three artifacts;
   explanation; verifier and provenance evidence; then the expandable trace.
6. Describe the evidence accurately: pixels come from the cached,
   provenance-bound real Sentinel-2 fixture. A live STAC check, when enabled,
   validates metadata for those scenes but does not process remote raster pixels.

The latest bounded Product smoke is recorded in
[`process/T144_LIVE_PRODUCT_SMOKE.md`](../process/T144_LIVE_PRODUCT_SMOKE.md).
It is separate from the provider-free deterministic evaluation in
[`docs/EVALUATION.md`](EVALUATION.md).

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
8. Explain that the run uses cached real Sentinel-2 Red/NIR pixels bound to two manifest-verified scenes; live STAC metadata is optional and never downloads or processes a raster per run.
9. Mention tenant-scoped artifact authorization and current MVP limitations.
