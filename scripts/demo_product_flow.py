"""Run the deterministic, public TaskPilot Product API demo flow."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from client.taskpilot import TaskPilotClient, TaskPilotClientError  # noqa: E402

DEMO_TITLE = "T135 deterministic demo task"
DEMO_DESCRIPTION = "Safe, repeatable Product UI/API walkthrough task."


class DemoReuseError(RuntimeError):
    """The deterministic demo cannot choose among duplicate demo tasks."""


def select_or_create_task(client: TaskPilotClient) -> tuple[dict[str, object], bool]:
    """Return the sole demo task, or create it when no exact match exists."""

    matches = [x for x in client.list_tasks() if x.get("title") == DEMO_TITLE]
    if len(matches) > 1:
        raise DemoReuseError(
            "multiple deterministic demo Tasks exist; clean/reset duplicate demo state "
            "using the supported demo instructions before rerunning"
        )
    if matches:
        return matches[0], False
    return client.create_task(DEMO_TITLE, DEMO_DESCRIPTION), True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    client = TaskPilotClient(args.base_url)
    try:
        login = client.login(args.email, getpass.getpass("TaskPilot password: "))
        if login.requires_organization:
            choice = input("Organization ID: ").strip()
            client.login(args.email, getpass.getpass("TaskPilot password: "), UUID(choice))
        session = client.session()
        task, created = select_or_create_task(client)
        if created:
            print(f"created task {task['id']}")
        else:
            print(f"reusing task {task['id']}")
        task_id = task["id"]
        runs = client.list_task_runs(task_id)
        run = runs[0] if runs else client.start_task_run(task_id)
        persisted = client.get_task_run(task_id, run["id"])
        print(f"organization {session['organization_id']}")
        print(f"task {task_id} status={task.get('status', 'unknown')}")
        print(f"run {run['id']} status={persisted.get('status', 'unknown')}")
        print("Product UI can inspect the persisted state; runtime execution is not public.")
        return 0
    except (DemoReuseError, TaskPilotClientError, KeyError, ValueError) as error:
        print(f"demo failed: {error}", file=sys.stderr)
        return 1
    finally:
        if client.token:
            try:
                client.logout()
            except TaskPilotClientError:
                print("warning: local demo state was retained; logout request failed", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
