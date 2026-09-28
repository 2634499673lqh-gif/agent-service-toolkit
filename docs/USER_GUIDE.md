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

The underlying protected API is `/api/v1/tasks` (`POST`/`GET`), `/api/v1/tasks/{task_id}` (`GET`), and `/api/v1/tasks/{task_id}/runs` plus `/runs/{run_id}` (`POST`/`GET`). A task or run outside the current organization is not exposed as a visible resource.

Starting a run creates persisted queued or pending state. The Product UI does not execute the internal Planner/Executor/Verifier runtime, poll live progress, or invent a result. There is no TaskStep view.

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
