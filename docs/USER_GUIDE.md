# TaskPilot User Guide

> This guide describes the behavior implemented by the current repository.

## Open the Product UI

The Product UI is the Streamlit view served by `src/streamlit_app.py`. Start the API first, then open `http://localhost:8501` for a local client or the Compose URL when using Docker. The sidebar switches between **TaskPilot Product** and the retained **Legacy chat** view.

## Sign in and organization selection

1. Enter the email and password on the TaskPilot sign-in form.
2. If the account has more than one eligible organization, select one and submit the password again.
3. The API returns an opaque session token. The browser session keeps the token in its own Streamlit session state; it is not a caller-supplied user or organization identity.
4. The sidebar shows the selected organization. **Log out** revokes the server session when possible and always clears local Product state.

A session is checked again while the Product view loads. Expiry, revocation, deactivation, or a membership change sends the user back to sign in. The API applies tenant and role checks server-side; the UI is only a display and input client.

## Tasks

After sign-in, the Product view can:

- create a task with a required title and optional description;
- list the current organization's tasks and refresh the list;
- open a task detail view with its persisted status and timestamps;
- start a run for a draft task or retry a failed task;
- list and open the task's persisted TaskRuns.

The current Product workspace is presented in Chinese as an AI remote-sensing
analysis dashboard. Its landing metrics summarize total, active, completed,
and draft analysis tasks. Current supported analyses are confirmed NDVI
vegetation analysis, exploratory NDWI continuous-index comparison, and
exploratory NDBI continuous-index comparison. A task must be explicitly
confirmed before NDWI or NDBI execution; the server uses the confirmed intent,
AOI and pinned data scope. Results provide before/after/change maps and
continuous metrics. NDWI does not establish confirmed water expansion, and
NDBI does not establish confirmed urban expansion; classification thresholds
remain deferred. Scene selection and provenance are shown as evidence, while
the technical trace remains available in its collapsed developer view.

The current supported AOI is Wuhan East Lake / 武汉东湖. The workflows use the
approved bounded, pinned Sentinel-2 fixture coverage: the repository fixtures
contain the historical scenes dated 2023-07-28 and 2024-07-30 for the two
comparison periods. This small fixture window is not the whole lake and does
not provide arbitrary AOIs, arbitrary acquisition dates, or general live
Sentinel-2 processing.

The underlying protected API is `/api/v1/tasks` (`POST`/`GET`), `/api/v1/tasks/{task_id}` (`GET`), and `/api/v1/tasks/{task_id}/runs` plus `/runs/{run_id}` (`POST`/`GET`). A task or run outside the current organization is not exposed as a visible resource.

Starting a run creates a persisted TaskRun and dispatches the bounded internal Planner/Executor/Verifier runtime through the process-local runtime bridge. The UI performs finite refresh checks while the run is active, then reads the persisted terminal result; it does not invent lifecycle state or results. There is no TaskStep view.

## Approvals

For the selected run, the UI lists persisted approvals and opens an approval detail containing the action name/version, risk, sanitized proposed action, status, membership references, timestamps, reason, replan count, and step position. Owners and admins can approve or reject a pending approval with an optional reason (maximum 500 characters). Members can inspect approvals but cannot decide them.

Approval decisions update the database record only. They do not execute an external side effect or resume a public run from the Product UI. The API endpoints are nested under `/api/v1/tasks/{task_id}/runs/{run_id}/approvals` with `GET` and `POST .../approve|reject` operations.

## Trace

The selected run can show a bounded trace timeline (100 or 500 events). The API returns sanitized trace fields, and the UI applies an additional display projection for sensitive keys and bearer values. Events may include status, timing, node/tool, correlation IDs, approval ID, usage, cost estimate, metadata, and safe error fields. An empty trace is reported as “No trace evidence observed”; reaching the bound is labeled as potentially incomplete.

Trace and approval data are observational evidence. They are not a substitute for database lifecycle state or authorization.

## Current limitations

- No public runtime execution endpoint or background worker is wired to Product run creation.
- No live progress stream, TaskStep persistence/view, fake progress, or fake result is provided.
- No uploads, admin console, registration, SSO, refresh-token flow, or external side-effect workflow is exposed by this UI.
- The legacy chat view remains available for the upstream LangGraph experience and has separate caller-supplied thread behavior; it is not the TaskPilot identity boundary.

## Writing a good task

Prefer a concrete objective and acceptance criteria, for example:

> Compare two supplied documents and list differences in budget, schedule, and owner. Use only directly supported information; mark missing items as “not found”.

Never approve an action you do not understand. Inspect the sanitized proposal, target, risk level, and originating task before deciding.
