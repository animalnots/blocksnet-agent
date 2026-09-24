"""Жизненный цикл A2A-задач: submitted → working → completed/failed/canceled.

Шаг 05 a2a-рефакторинга. Главные требования:

- Лимит конкурентности (``max_concurrent_tasks``) — размер пула потоков. Превышение
  → задача остаётся в ``submitted`` пока не освободится слот.
- ``start_run()`` вызывается ВНУТРИ рабочего потока (см. шаг 04 — ``ContextVar``
  с ``RunContext`` не долетит через ``loop.run_in_executor``). Иначе дедлайн
  и per-run стоп-флаг будут от чужого прогона.
- Прогресс: ``progress_callback(done, total, message)`` →
  ``TaskStatusUpdateEvent``. Инвал не чаще ``PROGRESS_INTERVAL_SEC``.
- Отмена: ``cancel(task_id)`` → ``stop_run()`` этой задачи (per-run из шага 04),
  не глобальный.
- TTL: завершённые задачи вычищаются по ``task_ttl_sec`` (лениво при
  ``get_task``/``list_tasks``).
- Дедлайн: НЕ через ``asyncio.wait_for`` (убил бы поток до финализации → вместо
  ``partial`` получаем ``failed``). Агент сам проверяет ``is_deadline_reached``
  и вызывает ``_finalize()``.

``submit_answer`` (input_required) — НЕ реализуем (см. 09-deferred.md).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("blocksnet_agent.a2a")


class TaskState(str, Enum):
    """Состояния задачи в A2A-сервере.

    Упрощённый набор: ``submitted`` → ``working`` → ``completed``/``failed``/
    ``canceled``. ``input_required`` НЕ реализован (агент не задаёт уточняющих
    вопросов — см. 09-deferred.md).
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class TaskRecord:
    """Состояние одной A2A-задачи."""

    task_id: str
    state: TaskState
    # ``input`` — это dict с полями skill-а (question, max_iterations, ...).
    input: dict[str, Any]
    # Финальный payload (тот же dict, что отдаёт MCP-tool) — заполняется
    # при COMPLETED/FAILED.
    output: dict[str, Any] | None = None
    # ``Future`` живёт пока задача в WORKING/SUBMITTED. По нему трекаем отмену.
    future: Future | None = field(default=None, repr=False)
    # Per-run stop_event — чтобы ``cancel()`` мог дёрнуть именно эту задачу,
    # а не глобальный флаг.
    stop_event: threading.Event = field(default_factory=threading.Event)
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    # Последнее сообщение прогресса (для отладки + отчёта при cancel).
    last_progress: str = ""


class TaskManager:
    """Управляет жизненным циклом A2A-задач с лимитом конкурентности.

    Потокобезопасный (RLock вокруг всех мутаций ``_tasks``).
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        task_ttl_sec: float = 3600.0,
        progress_interval_sec: float = 10.0,
    ) -> None:
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be > 0, got {max_concurrent}")
        if task_ttl_sec <= 0:
            raise ValueError(f"task_ttl_sec must be > 0, got {task_ttl_sec}")
        self._max_concurrent = max_concurrent
        self._task_ttl_sec = task_ttl_sec
        self._progress_interval_sec = progress_interval_sec
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.RLock()
        # Пул потоков для фактического исполнения.
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent,
            thread_name_prefix="a2a-task-",
        )

    # --- публичные операции -----------------------------------------------

    def submit(
        self,
        input_payload: dict[str, Any],
        runner: Callable[[TaskRecord, Callable[[str, str], None]], dict[str, Any]],
    ) -> TaskRecord:
        """Создаёт задачу и запускает в пуле (если есть слот) или ставит в очередь.

        Args:
            input_payload: входные данные (поля skill-а).
            runner: callable ``runner(record, progress_cb) -> payload``.
                Вызывается в рабочем потоке. ``progress_cb(state, message)`` —
                колбэк для эмиссии ``TaskStatusUpdateEvent``.

        Returns:
            ``TaskRecord`` в состоянии SUBMITTED или WORKING.
        """
        task_id = f"t-{uuid.uuid4().hex[:10]}"
        record = TaskRecord(task_id=task_id, state=TaskState.SUBMITTED, input=input_payload)
        with self._lock:
            self._tasks[task_id] = record

        def _run() -> None:
            try:
                self._start(record, runner)
            except Exception as exc:  # noqa: BLE001
                log.exception("task %s crashed in _start", task_id)
                self._finish(record, TaskState.FAILED, error=str(exc), error_code="TASK_EXCEPTION")

        record.future = self._executor.submit(_run)
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        """Возвращает задачу по id или None. Ленивая очистка TTL."""
        with self._lock:
            self._sweep_locked()
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """Отменяет задачу. Возвращает ``True``, если задача была в WORKING/SUBMITTED.

        Использует per-run ``stop_event`` задачи (НЕ глобальный — иначе отмена
        одной задачи валит соседние, см. шаг 04).
        """
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return False
            if record.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED):
                return False  # идемпотентная отмена
            record.state = TaskState.CANCELED
            record.finished_at = time.monotonic()
            # Per-run stop — не глобальный.
            record.stop_event.set()
            # Future не отменяем через .cancel() — пусть дойдёт до логической
            # точки и сам разрулит state. Но помечаем, чтобы _start вышел чисто.
            record.last_progress = "canceled by client"
            return True

    def shutdown(self, wait: bool = True) -> None:
        """Останавливает пул потоков. ``wait=True`` — дождаться завершения."""
        # Отменяем все активные задачи (per-run, не глобальный).
        with self._lock:
            for record in self._tasks.values():
                if record.state in (TaskState.WORKING, TaskState.SUBMITTED):
                    record.stop_event.set()
        self._executor.shutdown(wait=wait)

    # --- внутренние -------------------------------------------------------

    def _start(
        self,
        record: TaskRecord,
        runner: Callable[[TaskRecord, Callable[[str, str], None]], dict[str, Any]],
    ) -> None:
        """Запускает задачу в текущем потоке пула.

        Состояние переходит SUBMITTED → WORKING → (COMPLETED|FAILED|CANCELED).
        """
        with self._lock:
            if record.state == TaskState.CANCELED:
                # Отменили пока ждали слота — не запускаем.
                return
            record.state = TaskState.WORKING

        last_emit = [0.0]  # в списке, чтобы mutable в closure

        def progress_cb(state: str, message: str) -> None:
            record.last_progress = message
            now = time.monotonic()
            # Дросселирование — не чаще progress_interval_sec.
            if now - last_emit[0] < self._progress_interval_sec:
                return
            last_emit[0] = now
            # Внешний наблюдатель (SDK executor) подписывается на статус через
            # свой callback; здесь мы только обновляем ``last_progress``.
            # Полная интеграция с TaskStatusUpdateEvent — в executor.py.

        try:
            output = runner(record, progress_cb)
            # Различаем CANCELED (через record.state) от FAILED.
            with self._lock:
                if record.state == TaskState.CANCELED:
                    return  # уже отменено
            self._finish(record, TaskState.COMPLETED, output=output)
        except Exception as exc:  # noqa: BLE001
            log.exception("task %s raised", record.task_id)
            self._finish(
                record, TaskState.FAILED, error=str(exc), error_code="TASK_EXCEPTION"
            )

    def _finish(
        self,
        record: TaskRecord,
        state: TaskState,
        *,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Финализирует задачу: ``state``, ``output``/``error``, ``finished_at``.

        ``CANCELED`` имеет приоритет: если ``cancel()`` уже взвёл состояние
        до CANCELED, runner-уровневый финал (COMPLETED/FAILED) НЕ перезаписывает
        его. Это лечит «cancel после завершения» — клиент увидит CANCELED.
        """
        with self._lock:
            if record.state == TaskState.CANCELED and state != TaskState.CANCELED:
                # cancel() уже отметил как CANCELED — уважаем.
                return
            record.state = state
            record.finished_at = time.monotonic()
            if output is not None:
                record.output = output
            elif error is not None:
                record.output = {
                    "status": "failed",
                    "error": error,
                    "error_code": error_code or "UNKNOWN",
                }

    def _sweep_locked(self) -> None:
        """Удаляет задачи, у которых ``idle_sec > task_ttl_sec``. Требует ``_lock``."""
        now = time.monotonic()
        expired = [
            tid
            for tid, r in self._tasks.items()
            if r.finished_at is not None and now - r.finished_at > self._task_ttl_sec
        ]
        for tid in expired:
            self._tasks.pop(tid, None)


__all__ = [
    "TaskManager",
    "TaskRecord",
    "TaskState",
]