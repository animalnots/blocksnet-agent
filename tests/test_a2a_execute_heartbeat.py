"""``execute()`` runs the pipeline in the task-manager pool. Meanwhile the server loop
must keep serving (the SSE stream itself, /health, other clients), and a streaming
client must see ``working`` heartbeats between the initial Task and the terminal status,
otherwise its read timeout fires on any run longer than that timeout.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from a2a.types import Message, Part, Role, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict

HEARTBEAT_SEC = 0.1
RUN_SEC = 0.45


@pytest.fixture(autouse=True)
def _server_env(monkeypatch):
    monkeypatch.setenv("CHAT_URL", "http://llm.invalid/v1")
    monkeypatch.setenv("API_KEY", "test")
    monkeypatch.setenv("A2A_PROGRESS_INTERVAL_SEC", str(HEARTBEAT_SEC))


class _Queue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def enqueue_event(self, event):
        self.events.append(event)

        async def _noop():
            return None

        return _noop()


def _bridge(monkeypatch, runner):
    import blocksnet_agent.a2a.server as server_mod
    from blocksnet_agent.a2a.settings import A2ASettings
    from blocksnet_agent.a2a.task_manager import TaskManager

    monkeypatch.setattr(
        server_mod, "get_skill", lambda _skill_id: SimpleNamespace(id="run_pipeline", runner=runner)
    )
    settings = A2ASettings()
    return server_mod._A2ATaskBridge(
        task_manager=TaskManager(
            max_concurrent=1,
            task_ttl_sec=5.0,
            progress_interval_sec=settings.progress_interval_sec,
        ),
        settings=settings,
    )


def _context():
    message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(text="Вопрос")])
    return SimpleNamespace(message=message, task_id="task-1", context_id="ctx-1")


def _status_updates(queue: _Queue) -> list[dict]:
    return [
        MessageToDict(e)["status"]
        for e in queue.events
        if isinstance(e, TaskStatusUpdateEvent)
    ]


def _ok_after(seconds: float, progress: str | None = None):
    def runner(*, progress_cb, **_kwargs):
        if progress is not None:
            progress_cb("working", progress)
        time.sleep(seconds)
        return {"status": "ok", "tool": "run_pipeline", "output": "stub"}

    return runner


def test_execute_keeps_the_loop_serving_while_the_run_is_in_progress(monkeypatch) -> None:
    bridge = _bridge(monkeypatch, _ok_after(RUN_SEC))

    async def main() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(HEARTBEAT_SEC / 2)
                ticks += 1

        task = asyncio.create_task(ticker())
        await bridge.execute(_context(), _Queue())
        # Sampled before the loop can run the ticker again: a blocked loop leaves it at 0.
        seen = ticks
        task.cancel()
        return seen

    assert asyncio.run(main()) > 0


def test_execute_emits_working_heartbeats_carrying_the_latest_progress(monkeypatch) -> None:
    bridge = _bridge(monkeypatch, _ok_after(RUN_SEC, progress="loading blocks"))
    queue = _Queue()

    asyncio.run(bridge.execute(_context(), queue))

    updates = _status_updates(queue)
    states = [u["state"] for u in updates]
    assert "TASK_STATE_WORKING" in states
    assert states[-1] == "TASK_STATE_COMPLETED"
    last_heartbeat = [u for u in updates if u["state"] == "TASK_STATE_WORKING"][-1]
    assert last_heartbeat["message"]["parts"][0]["text"] == "loading blocks"


def test_zero_progress_interval_does_not_flood_the_stream(monkeypatch) -> None:
    monkeypatch.setenv("A2A_PROGRESS_INTERVAL_SEC", "0")
    bridge = _bridge(monkeypatch, _ok_after(0.2))
    queue = _Queue()

    asyncio.run(bridge.execute(_context(), queue))

    working = [u for u in _status_updates(queue) if u["state"] == "TASK_STATE_WORKING"]
    # A busy loop would produce thousands of events in 0.2 s.
    assert len(working) < 10


@pytest.mark.parametrize("pool", [1, 2])
def test_requests_beyond_the_pool_wait_for_a_slot_and_all_finish(monkeypatch, pool) -> None:
    import blocksnet_agent.a2a.executor as executor_mod
    import blocksnet_agent.a2a.server as server_mod
    from blocksnet_agent.a2a.settings import A2ASettings
    from blocksnet_agent.a2a.task_manager import TaskManager

    lock = threading.Lock()
    running = peak = 0
    first_running = threading.Event()
    pool_full = threading.Event()
    release = threading.Event()

    def pipeline(**_kwargs):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
            if running == pool:
                pool_full.set()
        first_running.set()
        release.wait(timeout=5)
        with lock:
            running -= 1
        return {"status": "ok", "tool": "run_pipeline", "output": "stub"}

    monkeypatch.setattr(executor_mod, "execute_run_pipeline", pipeline)
    manager = TaskManager(max_concurrent=pool, task_ttl_sec=5.0, progress_interval_sec=HEARTBEAT_SEC)
    bridge = server_mod._A2ATaskBridge(task_manager=manager, settings=A2ASettings())
    queues = [_Queue() for _ in range(pool + 1)]

    async def main() -> set:
        tasks = [asyncio.create_task(bridge.execute(_context(), queues[0]))]
        await asyncio.to_thread(first_running.wait, 2)
        tasks += [asyncio.create_task(bridge.execute(_context(), q)) for q in queues[1:]]
        await asyncio.to_thread(pool_full.wait, 2)
        release.set()
        _done, pending = await asyncio.wait(tasks, timeout=5)
        for task in pending:
            task.cancel()
        return pending

    try:
        pending = asyncio.run(main())
    finally:
        # A regression leaves pool threads waiting on queued work forever; dropping that
        # work lets them exit, so the suite fails here instead of hanging at exit.
        manager._executor.shutdown(wait=False, cancel_futures=True)

    assert not pending
    assert [_status_updates(q)[-1]["state"] for q in queues] == ["TASK_STATE_COMPLETED"] * (pool + 1)
    assert peak == pool
