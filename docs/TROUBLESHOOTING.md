# Troubleshooting

Codex should append real issues encountered during development.

## Windows: Streamlit tests time out before app code runs

Symptom: `tests/app/test_streamlit_app.py` fails with `RuntimeError: Could not determine home directory` inside `Path.home()` or an AppTest timeout.

Cause: the shared `mock_env` fixture clears the process environment. On Windows, Streamlit needs `USERPROFILE` (and sometimes `HOMEDRIVE`/`HOMEPATH`) to find its config directory.

Resolution in this repository: `tests/conftest.py` preserves only those three OS path variables while continuing to clear project configuration and secrets. Do not put API keys or other application settings into that fixture.

## Docker command is unavailable

Symptom: `docker --version` and `docker compose version` cannot run; neither Docker Desktop nor the `com.docker.service` service is present.

Resolution: install and start Docker Desktop using the owner's normal Windows setup. This requires user-controlled system software installation and may require WSL2; do not replace it with ad-hoc local PostgreSQL changes. After Docker is running, execute `docker compose config`, then `docker compose watch` or the relevant `scripts/smoke_test.sh` target.

## Docker Desktop opens but Engine does not answer

Symptom: the Docker Desktop process and named pipes exist and `docker --version` / `docker compose version` work, but `docker info` or `docker version` never return. Docker Desktop backend logs may say that the backend is not running.

Cause: this is a Docker Desktop/WSL runtime startup issue, not a repository Compose configuration issue. Do not change `compose.yaml`, install a second PostgreSQL, or remove volumes to work around it.

Resolution: use Docker Desktop's Troubleshoot screen to restart Docker Desktop and wait for its Engine-ready status. If it remains stuck, follow Docker Desktop's displayed WSL2/virtualization repair guidance. Once `docker info` succeeds in a newly opened terminal, rerun `docker compose config` before starting the repository stack.

## Compose starts PostgreSQL but agent service uses SQLite

Symptom: the PostgreSQL container is healthy, but service logs or behavior show the SQLite checkpointer instead.

Cause: an empty `DATABASE_TYPE` selects the settings default of SQLite. A plain `env_file` does not automatically supply the internal Compose hostname `postgres` to the application.

Resolution: the repository's `compose.yaml` explicitly sets the `agent_service` database type and PostgreSQL connection values to match its `postgres` service. Keep local `uv` development on SQLite unless you intentionally provide a reachable PostgreSQL configuration.

## Docker services are running but show `unhealthy`

Symptom: FastAPI and Streamlit answer their mapped host ports, but `docker compose ps` reports them as unhealthy.

Cause: the service images are based on `python:slim`, which does not include `curl`. A Compose healthcheck that runs `curl` therefore fails even when the Python application is ready.

Resolution in this repository: `compose.yaml` uses Python's built-in `urllib.request.urlopen` for both healthchecks. Do not add an operating-system package solely for a one-line health probe.

## Windows host FastAPI cannot open an async PostgreSQL connection

Symptom: the server logs `Psycopg cannot use the 'ProactorEventLoop' to run in async mode` and startup eventually fails with a pool timeout.

Cause: psycopg's async driver needs a Selector event loop on Windows. The installed Uvicorn version's `loop="auto"` chooses Proactor on Windows even if an earlier event-loop policy was set.

Resolution in this repository: `src/run_service.py` sets `WindowsSelectorEventLoopPolicy` and passes `loop="none"` to Uvicorn on Windows, allowing asyncio to create the compatible loop. Start the API through that file; this is not a reason to remove PostgreSQL or fall back to SQLite.

## PostgreSQL smoke script would remove local data

Symptom: `scripts/smoke_test.sh postgres` looks attractive for local verification, but its cleanup includes `docker compose down -v`.

Resolution: do not run that wrapper against a reusable development volume. Start the existing Compose stack, then use `uv run pytest tests/smoke/test_persistence.py -v --run-docker` with `AGENT_URL` and a unique `SMOKE_THREAD_ID`. Query the matching PostgreSQL checkpoints if an explicit backend proof is needed.

## Service does not start

Check:

- `.env`
- Docker
- port conflicts
- DB health
- dependency sync
- migration state

## LLM call fails

Check:

- API key exists
- model/provider configured
- network allowed
- rate limit
- provider status
- request payload size

Do not print API keys while debugging.

For local service/test verification without a provider account, create ignored `.env` from `.env.example` and set `USE_FAKE_MODEL=true`. This validates FastAPI, LangGraph and persistence wiring, but does not validate a real provider call.

## DB migration fails

Do not delete the database immediately.

Check:

- current revision
- migration history
- whether local data can be recreated
- incompatible schema changes

## Agent loops

Check:

- retry budget
- replan budget
- terminal condition
- invalid verifier routing
- tool error classification

All loops must be bounded.

## Approval stuck

Check:

- approval status
- checkpoint exists
- run/task status alignment
- authorization
- resume event consumed exactly once
