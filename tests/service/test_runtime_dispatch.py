import asyncio
from uuid import uuid4

import pytest

from service.runtime_dispatch import RuntimeDispatchService


@pytest.mark.asyncio
async def test_dispatch_uses_a_fresh_session_and_tracks_completion(monkeypatch):
    calls = []

    class Session:
        async def __aenter__(self):
            calls.append("enter")
            return self

        async def __aexit__(self, *_):
            calls.append("exit")

    class Factory:
        def __call__(self):
            return Session()

    class Runtime:
        def __init__(self, _checkpointer):
            pass

        async def execute_run(self, session, **kwargs):
            calls.append((session, kwargs))

    monkeypatch.setattr("service.runtime_dispatch.get_business_session_factory", lambda: Factory())
    monkeypatch.setattr("service.runtime_dispatch.TaskRuntimeService", Runtime)
    dispatcher = RuntimeDispatchService(object())
    dispatcher.dispatch(organization_id=uuid4(), task_id=uuid4(), task_run_id=uuid4())
    await asyncio.sleep(0)
    await dispatcher.close()
    assert calls[0] == "enter"
    assert calls[-1] == "exit"
    assert calls[1][0].__class__ is Session


@pytest.mark.asyncio
async def test_duplicate_dispatch_only_starts_one_runtime(monkeypatch):
    executions = 0
    started = asyncio.Event()
    release = asyncio.Event()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class Runtime:
        def __init__(self, _checkpointer):
            pass

        async def execute_run(self, _session, **_kwargs):
            nonlocal executions
            executions += 1
            started.set()
            await release.wait()

    monkeypatch.setattr(
        "service.runtime_dispatch.get_business_session_factory", lambda: lambda: Session()
    )
    monkeypatch.setattr("service.runtime_dispatch.TaskRuntimeService", Runtime)
    dispatcher = RuntimeDispatchService(object())
    run_id = uuid4()
    dispatcher.dispatch(organization_id=uuid4(), task_id=uuid4(), task_run_id=run_id)
    dispatcher.dispatch(organization_id=uuid4(), task_id=uuid4(), task_run_id=run_id)
    await started.wait()
    assert executions == 1
    release.set()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_owned_dispatch_exception_recovers_running_run(monkeypatch):
    failures = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class Runtime:
        def __init__(self, _checkpointer):
            pass

        async def execute_run(self, _session, **_kwargs):
            raise RuntimeError("runtime failed")

    class Runs:
        def __init__(self, _session):
            pass

        async def get_task_and_run_in_principal_tenant(self, *_args):
            return object(), type("Run", (), {"status": TaskRunStatus.RUNNING})()

    class Lifecycle:
        def __init__(self, _session):
            pass

        async def fail_run(self, *args):
            failures.append(args)

    from persistence.models import TaskRunStatus

    monkeypatch.setattr(
        "service.runtime_dispatch.get_business_session_factory", lambda: lambda: Session()
    )
    monkeypatch.setattr("service.runtime_dispatch.TaskRuntimeService", Runtime)
    monkeypatch.setattr("service.runtime_dispatch.TaskRunRepository", Runs)
    monkeypatch.setattr("service.runtime_dispatch.TaskLifecycleService", Lifecycle)
    dispatcher = RuntimeDispatchService(object())
    dispatcher.dispatch(organization_id=uuid4(), task_id=uuid4(), task_run_id=uuid4())
    await asyncio.sleep(0)
    await dispatcher.close()
    assert len(failures) == 1
