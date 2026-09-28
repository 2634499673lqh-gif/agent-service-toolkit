# Deterministic Product demo (T135)

Use the existing bootstrap, API, and Streamlit Product UI with a disposable
local organization. This path does not execute the internal runtime.

Follow [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) for environment, PostgreSQL,
migrations, and startup, then run:

```powershell
uv run python scripts/bootstrap_owner.py --organization-name "T135 Demo" --email demo-owner@example.com
uv run python src/run_service.py
uv run streamlit run src/streamlit_app.py
uv run python scripts/demo_product_flow.py --email demo-owner@example.com
```

The script reads passwords only from hidden prompts, reuses the exact demo-title
task on rerun, creates at most one persisted run, and reads the server state
back. If duplicate exact-title tasks exist, it fails explicitly; remove/reset
the disposable demo database using the supported Compose path before retrying.
Sign in at `http://localhost:8501` to inspect the organization, task,
TaskRun, and logout/session behavior. Use `--base-url http://localhost:8080`
with Compose.

Approvals and trace appear only when naturally persisted by existing behavior.
The demo does not insert TaskStep, ToolCall, approval, trace, progress, result,
or external-effect rows. Run creation currently remains queued/pending because
there is no public runtime execution endpoint or worker.
