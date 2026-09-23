"""A2A-сервис для BlocksNetAgent.

Шаг 05 a2a-рефакторинга. ``python -m blocksnet_agent.a2a`` (или
``python -m blocksnet_agent``) поднимает FastAPI-приложение с A2A-роутами.

Главные компоненты:
- ``/health`` — liveness для compose (шаг 07).
- ``/{...}`` — A2A JSON-RPC: SendMessage, GetTask, ListTasks, CancelTask.
- ``/.well-known/agent-card.json`` — карточка агента.

Стек:
- ``a2a-sdk==1.1.1`` (из шага 00.6 спайка).
- uvicorn — ASGI-сервер.
- FastAPI — обёртка.
- TaskManager — лимит конкурентности, TTL, per-run отмена (шаг 04).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from fastapi import FastAPI

from blocksnet_agent.a2a.agent_card import build_agent_card
from blocksnet_agent.a2a.artifacts import build_artifacts
from blocksnet_agent.a2a.auth import auth_middleware, configure_auth
from blocksnet_agent.a2a.executor import execute_run_pipeline
from blocksnet_agent.a2a.params import ParamValidationError, parse_message_params
from blocksnet_agent.a2a.settings import A2ASettings
from blocksnet_agent.a2a.skills import SKILLS, get_skill
from blocksnet_agent.context import ERROR_VALIDATION_ERROR
from blocksnet_agent.a2a.task_manager import TaskManager, TaskState as A2ATaskState

log = logging.getLogger("blocksnet_agent.a2a")


# Нулевой интервал из настроек превратил бы ожидание в execute() в busy-loop
# статусных событий.
MIN_HEARTBEAT_SEC = 0.1


def _agent_message(text: str) -> Message:
    """Сообщение агента для ``TaskStatus.message``.

    ``messageId`` обязателен по схеме CodeSynapse ($defs.message): без него
    Task не проходит их валидацию, хотя SDK такое сообщение соберёт молча.
    """
    return Message(
        message_id=uuid.uuid4().hex,
        role=Role.ROLE_AGENT,
        parts=[Part(text=text or "")],
    )



# --- мост между A2A SDK и TaskManager ---------------------------------------


class _A2ATaskBridge(AgentExecutor):
    """Реализация ``AgentExecutor`` для a2a-sdk 1.1.1.

    Принимает ``message/send`` с текстом вопроса, диспатчит в нужный skill,
    стримит ``TaskStatusUpdateEvent`` через ``EventQueue``.
    """

    def __init__(
        self,
        *,
        task_manager: TaskManager,
        settings: A2ASettings,
    ) -> None:
        self._task_manager = task_manager
        self._settings = settings

    async def _fail(
        self,
        event_queue: EventQueue,
        *,
        context: RequestContext,
        ctx_id: str,
        error_code: str,
        message: str,
    ) -> None:
        """Терминальный отказ до запуска расчёта.

        Причина уходит в ``TaskStatus.message``, потому что делегирование
        CodeSynapse (``src/agents/a2a_delegate.py``) читает именно её и
        показывает оркестратору вместо generic-метки. Traceback наружу не
        уходит — в MAS-ответе ему не место.

        Задача заводится и здесь: SDK требует Task до любого статусного
        события, поэтому отказ «до расчёта» — всё равно полноценная задача с
        терминальным состоянием, а не сообщение об ошибке.
        """
        await event_queue.enqueue_event(
            Task(
                id=context.task_id or "",
                context_id=ctx_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id or "",
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_FAILED,
                    message=_agent_message(f"{error_code}: {message}"),
                ),
            )
        )

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Обрабатывает SendMessage."""
        ctx_id = getattr(context, "context_id", None) or ""
        # Идентификатор задачи в событиях — **SDK-шный**, а не наш внутренний
        # record.task_id: их TaskManager сверяет id и отвергает событие с
        # чужим ("Task in event doesn't match TaskManager").
        task_id = context.task_id or ""

        # Приоритет источников: DataPart > metadata > дефолты. CodeSynapse
        # заполняет только DataPart (их ADR-0006), metadata остаётся ради
        # обратной совместимости. См. blocksnet_agent/a2a/params.py.
        try:
            params = parse_message_params(
                context.message.parts,
                context.message.metadata,
            )
        except ParamValidationError as exc:
            # Значение структурно доехало, но недопустимо. Считать на дефолтах
            # нельзя — это ровно то молчаливо неверное поведение, от которого
            # уходим. Причина уезжает в TaskStatus.message: их a2a_delegate
            # читает именно её вместо generic-метки.
            await self._fail(
                event_queue,
                context=context,
                ctx_id=ctx_id,
                error_code=ERROR_VALIDATION_ERROR,
                message=str(exc),
            )
            return

        user_text = params.question

        # Определяем skill — пока по skill_id из контекста или дефолт run_pipeline.
        skill_id = "run_pipeline"
        spec = get_skill(skill_id) or SKILLS[0]

        # Статусное событие → TaskStatusUpdateEvent.
        #
        # Два требования профиля 1.0, каждое из которых раньше молча гасило
        # весь поток статусов (конструктор падал, а except ниже это глотал):
        #   * значения TaskState — protobuf-имена TASK_STATE_*, у SDK 1.1.1 нет
        #     ни TaskState.working, ни прочих коротких алиасов;
        #   * legacy-поля ``final`` в 1.0 нет — ни в их схеме
        #     ($defs.taskStatusUpdate закрыта), ни в самом SDK.
        # Мы объявляем ``streaming: true``, так что поток статусов — часть
        # контракта, а не внутренняя деталь.
        async def _emit_status(state: str, message: str) -> None:
            try:
                a2a_state = {
                    "submitted": TaskState.TASK_STATE_SUBMITTED,
                    "working": TaskState.TASK_STATE_WORKING,
                    "completed": TaskState.TASK_STATE_COMPLETED,
                    "failed": TaskState.TASK_STATE_FAILED,
                    "canceled": TaskState.TASK_STATE_CANCELED,
                }[state]
            except KeyError:
                # Неизвестное состояние не должно деградировать молча: клиент
                # ждёт терминального статуса и на working будет ждать дальше.
                log.warning(
                    "unknown progress state %r, reported as TASK_STATE_WORKING", state
                )
                a2a_state = TaskState.TASK_STATE_WORKING
            try:
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=context.task_id or "",
                        context_id=ctx_id,
                        status=TaskStatus(state=a2a_state, message=_agent_message(message)),
                    )
                )
            except Exception:
                log.warning("failed to enqueue progress event", exc_info=True)

        # SDK требует, чтобы задача была заведена **до** любых task-событий
        # ("Agent should enqueue Task before TaskStatusUpdateEvent"), иначе
        # весь ответ схлопывается в INVALID_AGENT_RESPONSE.
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=ctx_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )

        # Запускаем через TaskManager (он сам стартует в пуле).
        input_payload = params.as_input_payload()
        if params.sources:
            log.info(
                "run params: %s",
                ", ".join(f"{k}={params.sources[k]}" for k in sorted(params.sources)),
            )
        record = self._task_manager.submit(
            input_payload,
            runner=lambda rec, cb: spec.runner(
                input_payload=input_payload,
                output_dir=self._settings.output_dir,
                data_dir=self._settings.data_dir,
                deadline_sec=self._settings.deadline_sec,
                progress_cb=cb,
                stop_event=rec.stop_event,
            ),
        )

        # Ждём future пула через loop, а не через future.result(): блокирующее
        # ожидание замораживало loop сервера на весь прогон — до клиента не доходил
        # даже начальный Task, /health переставал отвечать. Между шагами пайплайна
        # шлём heartbeat ``working`` с последним прогрессом runner'а, иначе read-timeout
        # стримингового клиента срабатывает на любом прогоне длиннее этого таймаута.
        if record.future is not None:
            finished = asyncio.wrap_future(record.future)
            heartbeat_sec = max(self._settings.progress_interval_sec, MIN_HEARTBEAT_SEC)
            while True:
                done, _ = await asyncio.wait({finished}, timeout=heartbeat_sec)
                if done:
                    break
                await _emit_status("working", record.last_progress or "working")
            try:
                finished.result()
            except Exception:
                log.exception("task failed during execute()")

        # Итог отдаём **задачей**, а не сообщением. Раньше здесь эмитился
        # голый Message: SDK переводил обмен в "message mode", и следующий же
        # TaskArtifactUpdateEvent ронял весь ответ ошибкой
        # INVALID_AGENT_RESPONSE ("Received TaskArtifactUpdateEvent in message
        # mode"). CodeSynapse при этом ждёт именно Task — их делегирование
        # читает Task.artifacts и TaskStatus.message.
        record = self._task_manager.get(record.task_id) or record
        output = record.output or {"status": "failed", "error_code": "NO_OUTPUT"}

        # Артефакты — до терминального статуса: после него задача закрыта.
        for artifact in build_artifacts(output):
            try:
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=task_id,
                        context_id=ctx_id,
                        artifact=artifact,
                    )
                )
            except Exception:
                log.warning("failed to enqueue artifact event", exc_info=True)

        status = str(output.get("status") or "ok")
        failed = status not in ("ok", "partial") or bool(output.get("error"))
        if record.state == A2ATaskState.CANCELED:
            terminal = TaskState.TASK_STATE_CANCELED
        elif failed:
            terminal = TaskState.TASK_STATE_FAILED
        else:
            terminal = TaskState.TASK_STATE_COMPLETED

        # Причина отказа — в TaskStatus.message: их a2a_delegate показывает
        # оркестратору именно её, а не generic-метку. Traceback не отдаём.
        if failed:
            code = output.get("error_code") or "AGENT_ERROR"
            summary = f"{code}: {output.get('error') or 'run failed'}"
        else:
            summary = f"{status}: {output.get('result') or output.get('output') or 'done'}"

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=terminal,
                    message=_agent_message(summary[:2000]),
                ),
            )
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Обрабатывает CancelTask."""
        task_id = context.task_id or ""
        cancelled = self._task_manager.cancel(task_id)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=getattr(context, "context_id", None) or "",
                status=TaskStatus(
                    state=TaskState.TASK_STATE_CANCELED if cancelled else TaskState.TASK_STATE_FAILED,
                    message=_agent_message("canceled" if cancelled else "no such task"),
                ),
            )
        )


# --- сборка приложения -----------------------------------------------------


def _build_task_manager(settings: A2ASettings) -> TaskManager:
    return TaskManager(
        max_concurrent=settings.max_concurrent_tasks,
        task_ttl_sec=settings.task_ttl_sec,
        progress_interval_sec=settings.progress_interval_sec,
    )


def build_app(settings: A2ASettings | None = None) -> FastAPI:
    """Собирает FastAPI с A2A-роутами и /health.

    Args:
        settings: если None — создаётся ``A2ASettings()`` (читает env/.env).
    """
    if settings is None:
        settings = A2ASettings()  # type: ignore[call-arg]

    # a2a/06: инициализация auth перед регистрацией роутов (fail-fast если
    # ``AUTH_ENABLED=true`` без ``MAS_BEARER_TOKEN``).
    configure_auth(
        auth_enabled=settings.auth_enabled,
        mas_bearer_token=settings.mas_bearer_token,
    )

    task_manager = _build_task_manager(settings)
    card = build_agent_card(
        host=settings.host,
        port=settings.port,
        public_url=settings.public_url,
    )

    handler = DefaultRequestHandler(
        agent_executor=_A2ATaskBridge(task_manager=task_manager, settings=settings),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    app = FastAPI(title="blocksnet-mcp-a2a", version=card.version)
    # a2a/06: middleware auth (если ``AUTH_ENABLED=true`` — пропускает запрос
    # без валидного токена).
    app.middleware("http")(auth_middleware)

    # /health для compose/Docker (шаг 07).
    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "name": card.name,
            "version": card.version,
            "skills": [s.id for s in card.skills],
        }

    # A2A-роуты.
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app


# Синглтон-приложение для удобства тестов и dev-сервера.
app: FastAPI | None = None


def get_app() -> FastAPI:
    """Ленивая инициализация — для тестов и ``uvicorn blocksnet_agent.a2a.server:app``."""
    global app
    if app is None:
        app = build_app()
    return app


def main() -> None:
    """``python -m blocksnet_agent.a2a`` — uvicorn ASGI-сервер."""
    import uvicorn

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    settings = A2ASettings()  # type: ignore[call-arg]
    application = build_app(settings)
    log.info(
        "starting A2A service on %s:%s (public=%s)",
        settings.host,
        settings.port,
        settings.public_url,
    )
    uvicorn.run(application, host=settings.host, port=settings.port, log_level="warning")


__all__ = ["build_app", "get_app", "main", "A2ATaskState"]