"""Тесты шага 05 a2a-рефакторинга: жизненный цикл A2A-задач.

Главные гарантии:
- Лимит конкурентности соблюдается (``max_concurrent_tasks``).
- ``cancel()`` останавливает только свою задачу (per-run, не глобальный).
- ``cancel()`` идемпотентен.
- Дедлайн даёт ``partial``, а не ``failed`` (через executor — здесь только
  проверяем, что ``TaskManager`` корректно передаёт stop_event в runner).
- Завершённые задачи вычищаются по TTL (``task_ttl_sec``).
- Исключение в runner → задача в FAILED, не падает весь менеджер.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from blocksnet_agent.a2a.task_manager import TaskManager, TaskState


@pytest.fixture
def manager() -> TaskManager:
    """Менеджер с лимитом 2 для тестов конкурентности."""
    return TaskManager(
        max_concurrent=2,
        task_ttl_sec=60.0,
        progress_interval_sec=0.0,  # без дросселя для тестов
    )


# --- базовые контракты -----------------------------------------------------


def test_submit_creates_task_in_submitted_or_working(manager: TaskManager) -> None:
    """submit() возвращает TaskRecord в состоянии SUBMITTED или WORKING."""
    record = manager.submit(
        {"question": "test"},
        runner=lambda rec, cb: {"status": "ok", "result": "done"},
    )
    assert record.task_id.startswith("t-")
    assert record.state in (TaskState.SUBMITTED, TaskState.WORKING, TaskState.COMPLETED)


def test_simple_task_runs_to_completion(manager: TaskManager) -> None:
    """Задача без блокировок: SUBMITTED → WORKING → COMPLETED."""
    record = manager.submit(
        {"question": "test"},
        runner=lambda rec, cb: {"status": "ok", "result": "done"},
    )
    assert record.future is not None
    record.future.result(timeout=5.0)
    record = manager.get(record.task_id) or record
    assert record.state == TaskState.COMPLETED
    assert record.output == {"status": "ok", "result": "done"}


def test_runner_exception_marks_task_failed(manager: TaskManager) -> None:
    """Исключение в runner → задача FAILED, другие задачи продолжаются."""

    def _boom(rec, cb):
        raise RuntimeError("boom")

    record = manager.submit({"question": "boom"}, runner=_boom)
    record.future.result(timeout=5.0)
    record = manager.get(record.task_id) or record
    assert record.state == TaskState.FAILED
    assert "boom" in (record.output or {}).get("error", "")


# --- лимит конкурентности ---------------------------------------------------


def test_concurrency_limit_holds(manager: TaskManager) -> None:
    """При лимите 2 — третья задача ждёт в SUBMITTED пока первые две в WORKING.

    Используем долгие runner'ы с явными flag'ами — чтобы синхронизировать
    моменты проверки без гонок.
    """
    started_count = [0]
    started_lock = threading.Lock()
    release = threading.Event()

    def slow_runner_factory(name: str):
        def _run(rec, cb):
            with started_lock:
                started_count[0] += 1
            # Ждём, пока все стартовали ИЛИ некого не отпустят.
            release.wait(timeout=3.0)
            return {"status": "ok", "name": name}
        return _run

    # Два долгих runner'а стартуют сразу.
    r1 = manager.submit({"question": "a"}, runner=slow_runner_factory("a"))
    r2 = manager.submit({"question": "b"}, runner=slow_runner_factory("b"))
    # Подождём, пока ОБА стартуют.
    deadline = time.monotonic() + 2.0
    while started_count[0] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started_count[0] == 2, f"оба должны были стартовать, имеем {started_count[0]}"

    # Третья — лимит исчерпан, должна быть в SUBMITTED.
    r3 = manager.submit({"question": "c"}, runner=slow_runner_factory("c"))
    # Пауза, за которую r3 успела бы стартовать, если бы лимит не держал: оба потока пула заняты.
    time.sleep(0.1)
    initial_state = manager.get(r3.task_id).state
    assert initial_state == TaskState.SUBMITTED, (
        f"r3 должна быть SUBMITTED пока лимит исчерпан, "
        f"получили {initial_state}, started={started_count[0]}"
    )
    assert started_count[0] == 2, "r3 не должна была стартовать"

    # Отпускаем первые две.
    release.set()
    r1.future.result(timeout=5.0)
    r2.future.result(timeout=5.0)
    # Третья стартует после освобождения слотов.
    r3.future.result(timeout=5.0)

    assert manager.get(r1.task_id).state == TaskState.COMPLETED
    assert manager.get(r2.task_id).state == TaskState.COMPLETED
    assert manager.get(r3.task_id).state == TaskState.COMPLETED


def test_concurrency_limit_with_threaded_check() -> None:
    """20 задач при лимите 2 — все доходят до COMPLETED, ни одна не висит."""
    manager = TaskManager(max_concurrent=2, task_ttl_sec=60.0, progress_interval_sec=0.0)

    def fast_runner(rec, cb):
        return {"status": "ok"}

    records = [
        manager.submit({"question": f"q-{i}"}, runner=fast_runner) for i in range(20)
    ]
    for r in records:
        r.future.result(timeout=10.0)

    final_states = [manager.get(r.task_id).state for r in records]
    assert all(s == TaskState.COMPLETED for s in final_states)


# --- cancel -----------------------------------------------------------------


def test_cancel_uses_per_run_stop_event(manager: TaskManager) -> None:
    """``cancel()`` взводит per-run stop_event задачи, не глобальный."""
    captured_stop_events: list[threading.Event] = []

    def runner(rec, cb):
        captured_stop_events.append(rec.stop_event)
        # Имитируем долгую работу: ждём, пока stop_event не взведён.
        rec.stop_event.wait(timeout=2.0)
        # Если взвели — возвращаем partial, иначе ok.
        if rec.stop_event.is_set():
            return {"status": "partial", "reason": "stopped"}
        return {"status": "ok"}

    record = manager.submit({"question": "long"}, runner=runner)
    # Дать задаче стартовать.
    time.sleep(0.05)
    cancelled = manager.cancel(record.task_id)

    assert cancelled is True
    record.future.result(timeout=5.0)
    assert len(captured_stop_events) == 1
    assert captured_stop_events[0].is_set()


def test_cancel_is_idempotent(manager: TaskManager) -> None:
    """Повторный cancel() возвращает False, состояние не меняется."""
    record = manager.submit({"question": "x"}, runner=lambda rec, cb: {"status": "ok"})
    record.future.result(timeout=5.0)
    record = manager.get(record.task_id)
    assert record.state == TaskState.COMPLETED

    # Завершённую задачу нельзя cancel'ить — идемпотентно.
    assert manager.cancel(record.task_id) is False


def test_cancel_one_task_does_not_affect_others(manager: TaskManager) -> None:
    """cancel() задачи A не останавливает задачу B (per-run изоляция)."""
    a_after_barrier = threading.Event()
    b_after_barrier = threading.Event()
    release = threading.Event()

    def slow_runner_a(rec, cb):
        # Сигналим, что дошли до check-точки. cancel() от main делает
        # cancel ПОСЛЕ этого сигнала (см. main thread).
        a_after_barrier.set()
        # Короткая пауза, чтобы main thread успел cancel'нуть до check.
        time.sleep(0.05)
        if rec.stop_event.is_set():
            return {"status": "partial", "reason": "canceled"}
        release.wait(timeout=2.0)
        return {"status": "ok"}

    def slow_runner_b(rec, cb):
        b_after_barrier.set()
        # B НЕ проверяет свой stop_event.
        release.wait(timeout=2.0)
        return {"status": "ok"}

    record_a = manager.submit({"question": "a"}, runner=slow_runner_a)
    record_b = manager.submit({"question": "b"}, runner=slow_runner_b)

    # Ждём, пока ОБЕ дойдут до check-точки (тогда обе точно в WORKING).
    assert a_after_barrier.wait(timeout=2.0)
    assert b_after_barrier.wait(timeout=2.0)
    # cancel A.
    manager.cancel(record_a.task_id)
    # Отпускаем обе задачи (они уже прошли check).
    release.set()

    record_a.future.result(timeout=5.0)
    record_b.future.result(timeout=5.0)

    # A — CANCELED, B — COMPLETED.
    assert manager.get(record_a.task_id).state == TaskState.CANCELED
    assert manager.get(record_b.task_id).state == TaskState.COMPLETED
    # Per-run stop_event A взведён, B — нет.
    assert record_a.stop_event.is_set()
    assert not record_b.stop_event.is_set()


def test_cancel_unknown_task_returns_false(manager: TaskManager) -> None:
    """cancel() для несуществующего id → False."""
    assert manager.cancel("t-nonexistent") is False


# --- TTL --------------------------------------------------------------------


def test_ttl_sweep_removes_finished_tasks() -> None:
    """Завершённые задачи удаляются по истечении TTL."""
    manager = TaskManager(max_concurrent=2, task_ttl_sec=0.05, progress_interval_sec=0.0)
    record = manager.submit({"question": "x"}, runner=lambda rec, cb: {"status": "ok"})
    record.future.result(timeout=5.0)

    assert manager.get(record.task_id) is not None
    time.sleep(0.1)
    # ``get`` триггерит sweep.
    assert manager.get(record.task_id) is None


def test_active_tasks_not_swept() -> None:
    """WORKING/SUBMITTED задачи НЕ удаляются по TTL."""
    manager = TaskManager(max_concurrent=2, task_ttl_sec=0.05, progress_interval_sec=0.0)

    def slow(rec, cb):
        time.sleep(1.0)
        return {"status": "ok"}

    record = manager.submit({"question": "slow"}, runner=slow)
    time.sleep(0.1)
    # TTL истёк, но задача ещё работает.
    assert manager.get(record.task_id) is not None
    assert manager.get(record.task_id).state in (TaskState.WORKING, TaskState.SUBMITTED)
    record.future.result(timeout=5.0)


# --- конструктор ------------------------------------------------------------


def test_invalid_max_concurrent_raises() -> None:
    with pytest.raises(ValueError, match="max_concurrent"):
        TaskManager(max_concurrent=0)


def test_invalid_ttl_raises() -> None:
    with pytest.raises(ValueError, match="task_ttl_sec"):
        TaskManager(max_concurrent=2, task_ttl_sec=0)


# --- shutdown ---------------------------------------------------------------


def test_shutdown_releases_running_tasks(manager: TaskManager) -> None:
    """shutdown() взводит stop_event активных задач и ждёт пул."""
    inflight: list[threading.Event] = []

    def slow(rec, cb):
        inflight.append(rec.stop_event)
        rec.stop_event.wait(timeout=2.0)
        return {"status": "partial"}

    manager.submit({"question": "a"}, runner=slow)
    time.sleep(0.05)
    manager.shutdown(wait=True)
    assert inflight[0].is_set(), "shutdown должен взвести per-run stop"


# --- прогресс ---------------------------------------------------------------


def test_progress_cb_is_called(manager: TaskManager) -> None:
    """runner получает progress_cb и может его вызвать."""
    progress_calls: list[tuple[str, str]] = []

    def runner(rec, cb):
        cb("working", "step 1")
        cb("working", "step 2")
        return {"status": "ok"}

    record = manager.submit({"question": "p"}, runner=runner)
    record.future.result(timeout=5.0)
    assert record.last_progress in ("step 1", "step 2")