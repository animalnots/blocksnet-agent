"""Мост между A2A-задачей и ``BlocksNetAgent``.

Шаг 05 a2a-рефакторинга. Главные требования:

- Агент вызывает инструменты **in-process** (не через MCP-клиента). Причина:
  state, который пишут tools (GeoDataFrame кварталов, результаты compute_*),
  читают ``overlay_candidates``, верификация гипотез, ``confidence_basis``,
  ``valid_block_ids``. Граница процесса рвёт это без единого исключения в логах.
- ``start_run()`` вызывается ВНУТРИ рабочего потока, иначе ``ContextVar``
  с ``RunContext`` не долетит (см. шаг 04).
- ``stop_event`` задачи прокидывается через ``agent.run`` — это per-run
  флаг из шага 04.
- Дедлайн: НЕ ``asyncio.wait_for``. Поток не убивать. Агент сам видит
  ``is_deadline_reached()`` и через ``_finalize()`` отдаёт ``status="partial"``.
- Прогресс: ``runtime.report_progress()`` → callback задачи → ``TaskStatusUpdateEvent``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from blocksnet_agent.payload import build_payload

log = logging.getLogger("blocksnet_agent.a2a")


# Колбэк прогресса: ``progress(state: str, message: str)``.
ProgressCallback = Callable[[str, str], None]


def execute_run_pipeline(
    *,
    question: str,
    max_iterations: int | None,
    output_dir: Any,
    data_dir: Any,
    deadline_sec: int | None,
    stop_event: Any,  # ``threading.Event`` из TaskRecord.stop_event
    progress_cb: ProgressCallback,
    scenario_id: str | None = None,
    project_id: str | None = None,
    agent_factory: Callable[..., Any] | None = None,
    agent_settings: Any | None = None,
) -> dict[str, Any]:
    """Запускает ``BlocksNetAgent.run()`` и возвращает общий payload.

    Args:
        question: вопрос пользователя.
        max_iterations: переопределение лимита итераций (None → из settings).
        output_dir: ``Path`` к ``OUTPUT_DIR``.
        data_dir: ``Path`` к ``DATA_DIR``.
        deadline_sec: ``int|None`` — дедлайн в секундах.
        stop_event: per-run стоп-флаг задачи (из ``TaskRecord.stop_event``).
        progress_cb: колбэк прогресса.
        scenario_id: id сценария (шаг 06) — резолвится через ``resolve_context``;
            None → дефолтный сценарий.
        project_id: id проекта (шаг 06).
        agent_factory: опциональный override для тестов.
        agent_settings: опциональный override для ``Settings``.

    Returns:
        ``dict`` в формате ``blocksnet_mcp.serialize.to_json() + status/run_id/...``.
    """
    # Ленивые импорты — agent тяжёлый (langchain/langgraph/tiktoken).
    from blocksnet_agent.runtime import (
        is_stop_requested,
        start_run,
    )
    from blocksnet_agent import BlocksNetAgent

    normalized_question = str(question or "").strip()
    if not normalized_question:
        # Валидация — единый код с MCP-вариантом.
        return build_payload(
            type("R", (), {"output": "", "run_id": None})(),
            "",
            tool="run_pipeline",
            status="failed",
            error="question must be a non-empty string",
            error_code="VALIDATION_ERROR",
        )

    if max_iterations is not None and max_iterations < 1:
        return build_payload(
            type("R", (), {"output": "", "run_id": None})(),
            "",
            tool="run_pipeline",
            status="failed",
            error="max_iterations must be >= 1",
            error_code="VALIDATION_ERROR",
        )

    iterations = max_iterations if max_iterations is not None else 24
    # Читаем agent-настройки (LLM/MAX_ITERATIONS из env/.env). Тесты могут
    # передать ``agent_settings`` напрямую (без чтения .env).
    if agent_settings is None:
        from blocksnet_agent.config import Settings

        agent_settings = Settings()  # type: ignore[call-arg]
    if max_iterations is None:
        iterations = agent_settings.max_iterations

    # a2a/06: если передан scenario_id — резолвим ScenarioContext и переписываем
    # data_dir на подкаталог сценария. Без scenario_id — используем data_dir
    # как есть (текущее поведение полностью сохраняется).
    if scenario_id is not None:
        from blocksnet_agent.context import ContextError, resolve_context

        try:
            scenario_ctx = resolve_context(
                scenario_id=scenario_id,
                project_id=project_id,
                data_dir=data_dir,
                output_dir=output_dir,
            )
        except ContextError as exc:
            # Без materializer'а (UrbanDB не подключён) это **штатный** исход, а
            # не авария: сценарий = имя заранее подготовленного датасета, и
            # клиент вполне может назвать несуществующий. Отдаём машинный код
            # и список доступных датасетов, чтобы вызывающий агент исправился
            # сам, а не упёрся в generic TASK_EXCEPTION.
            return build_payload(
                type("R", (), {"output": "", "run_id": None})(),
                "",
                tool="run_pipeline",
                status="failed",
                error=exc.message,
                error_code=exc.code,
            )
        data_dir = scenario_ctx.data_dir

    # Копируем данные из agent_settings и обновляем data_dir (под scenario_id).
    run_settings = agent_settings.model_copy(update={"data_dir": data_dir})

    # Запускаем run-контекст ВНУТРИ рабочего потока — это требование шага 04.
    # overwrite=True: поток пула хранит RunContext прошлой задачи, без него
    # задача унаследует её каталог и уже истёкший дедлайн.
    # Стоп-флаг задачи и её прогресс — поля этого RunContext, а не подмена функций
    # модуля runtime: подмена одна на весь процесс, и параллельные прогоны видят чужие.
    deadline_for_run = deadline_sec or None

    def _progress(_done: int, _total: int, stage: str) -> None:
        progress_cb("working", stage or "")

    ctx = start_run(
        output_dir,
        progress_callback=_progress,
        deadline_sec=deadline_for_run,
        overwrite=True,
        stop_event=stop_event,
    )

    agent_cls = agent_factory or BlocksNetAgent
    agent = agent_cls(
        settings=run_settings,
        max_iterations=iterations,
    )
    result = agent.run(normalized_question)

    # ``ctx.run_dir`` — это ``run_<timestamp>-<id>``.
    run_dir = str(getattr(result, "run_dir", "") or ctx.run_dir)
    status = "partial" if is_stop_requested() else "ok"

    return build_payload(
        result,
        run_dir,
        tool="run_pipeline",
        status=status,
    )


__all__ = ["execute_run_pipeline", "ProgressCallback"]