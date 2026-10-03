# Implementation B Handoff

## A 交付

Implementation A adds a Vite React/TypeScript frontend under `web/`, stable GeoChange capability and verified map metadata endpoints, fixture-root resolution for local and Docker layouts, and bounded RuntimeDispatch recovery for queued/pending orphan runs. Streamlit remains available as the fallback. The deterministic conversation bridge remains in `src/service/conversation_api.py`; it is intentionally not a real LLM.

## Run and API contract

- Frontend: `pnpm --dir web install && pnpm --dir web dev -- --host 127.0.0.1`; browser URL `http://localhost:5173/`.
- Backend: existing `python src/run_service.py` / Compose `agent_service` on port 8080.
- `GET /api/v1/geochange/capabilities` requires the existing opaque bearer session and returns only supported indicators, periods, source labels, and scientific limits.
- `GET /api/v1/tasks/{task_id}/runs/{run_id}/map` is tenant-scoped and requires a succeeded, verifier-passed run. It returns sanitized result metadata and never trusts client paths, bounds, or indicator values.

## B replacement points

1. Replace `proposal_from_message` in `src/service/conversation_service.py` with a real structured LLM intent parser while preserving server-side Pydantic validation and explicit confirmation.
2. Replace the deterministic bridge in `src/service/conversation_api.py` only after adding provider failure classification, authorization tests, and redacted observability.
3. Add real map artifact URL loading in `web/src/main.tsx` using the verified artifact route and server-returned bounds; do not infer geometry from user input.

Known limitation: Docker and real browser E2E require a local Docker daemon and configured database; this A run verified frontend build and Vite HTTP startup, but did not claim a public deployment URL or real LLM execution.
