from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "demo_product_flow", Path(__file__).parents[2] / "scripts" / "demo_product_flow.py"
)
assert _SPEC and _SPEC.loader
demo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(demo)


def test_select_or_create_task_creates_when_no_exact_match() -> None:
    client = Mock()
    client.list_tasks.return_value = [{"title": "other"}]
    client.create_task.return_value = {"id": "new", "title": demo.DEMO_TITLE}

    task, created = demo.select_or_create_task(client)

    assert created is True
    assert task["id"] == "new"


def test_select_or_create_task_reuses_one_exact_match() -> None:
    client = Mock()
    existing = {"id": "existing", "title": demo.DEMO_TITLE}
    client.list_tasks.return_value = [existing]

    task, created = demo.select_or_create_task(client)

    assert (task, created) == (existing, False)
    client.create_task.assert_not_called()


def test_select_or_create_task_rejects_duplicate_exact_matches() -> None:
    client = Mock()
    client.list_tasks.return_value = [
        {"id": "one", "title": demo.DEMO_TITLE},
        {"id": "two", "title": demo.DEMO_TITLE},
    ]

    with pytest.raises(demo.DemoReuseError, match="multiple deterministic demo Tasks"):
        demo.select_or_create_task(client)

    client.create_task.assert_not_called()
