"""[DEPRECATED] Legacy LLM-агент-инструмент для MCP-сервера.

Используйте A2A-skill ``run_pipeline`` (шаг 05) для новых интеграций.
Этот модуль сохранён ради обратной совместимости — клиенты, импортирующие
``from blocksnet_mcp.tools_mcp import analyze_urban_question`` (через shim
``blocksnet_mcp/tools_mcp.py``), продолжают работать без изменений.

a2a/03 изменения:
- Ленивые импорты LLM (``BlocksNetAgent``, ``AgentSettings``) — на уровне модуля
  они больше не импортируются, иначе ``import blocksnet_mcp`` тянет весь агентский
  стек (langchain/langgraph/tiktoken) ради одного лишь legacy-инструмента.
- Явная проверка ``CHAT_URL``/``API_KEY`` в начале вызова — без них возвращаем
  структурированный failed-ответ ``LLM_NOT_CONFIGURED``.
- Формат ответа унифицирован с ``blocksnet_mcp.envelope.build_envelope`` —
  добавилось поле ``tool``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# a2a/03: ``get_mcp_settings`` импортируем сразу — он не тянет LLM-стек
# (pydantic-settings + наш .env). Остальные импорты — ленивые, внутри функции.
from blocksnet_mcp.settings import get_mcp_settings

log = logging.getLogger("blocksnet_mcp")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _trace(message: str) -> None:
    from os import getenv

    if getenv("BLOCKSNET_MCP_TRACE") != "1":
        return
    path = Path(__file__).resolve().parents[1] / "outputs" / "mcp_trace.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{_now_iso()} {message}\n")


def _build_payload(
    result: Any,
    run_dir: str,
    status: str = "ok",
    error: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """P0.2: единая обёртка — структурированный ответ, статус, isError=False на транспорте.

    a2a/05: делегирует в общий ``blocksnet_agent.payload.build_payload``,
    чтобы A2A-сервис и MCP-tool собирали один и тот же формат ответа.
    Старая сигнатура сохранена (tool=analyze_urban_question подставляется
    явно).
    """
    from blocksnet_agent.payload import build_payload

    return build_payload(
        result,
        run_dir,
        tool="analyze_urban_question",
        status=status,
        error=error,
        error_code=error_code,
    )


def analyze_urban_question(
    question: str,
    max_iterations: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Analyze an urban question using local BlocksNet data and return structured JSON.

    P0.2: try/except оборачивает всё — структурированный failed-ответ вместо голой
    строки в isError. Поддерживает progress callback и серверный дедлайн (см. server.py).
    """
    _trace("tool entered")
    normalized_question = str(question or "").strip()
    if not normalized_question:
        _trace("validation failed: empty question")
        return _build_payload(
            type("R", (), {"output": "", "run_id": None})(),
            "",
            status="failed",
            error="question must be a non-empty string",
            error_code="VALIDATION_ERROR",
        )

    if max_iterations is not None and max_iterations < 1:
        _trace("validation failed: max_iterations")
        return _build_payload(
            type("R", (), {"output": "", "run_id": None})(),
            "",
            status="failed",
            error="max_iterations must be >= 1",
            error_code="VALIDATION_ERROR",
        )

    _trace("loading mcp settings")
    settings = get_mcp_settings()
    # a2a/03: явная проверка LLM-конфига. Без CHAT_URL/API_KEY legacy-инструмент
    # не может работать (BlocksNetAgent.run требует LLM) — возвращаем структурированный
    # failed-ответ вместо падения.
    if not (settings.chat_url and settings.api_key):
        _trace("LLM not configured")
        return _build_payload(
            type("R", (), {"output": "", "run_id": None})(),
            "",
            status="failed",
            error="analyze_urban_question требует CHAT_URL и API_KEY в окружении",
            error_code="LLM_NOT_CONFIGURED",
        )
    iterations = max_iterations if max_iterations is not None else settings.max_iterations
    if iterations < 1:
        return _build_payload(
            type("R", (), {"output": "", "run_id": None})(),
            "",
            status="failed",
            error="max_iterations must be >= 1",
            error_code="VALIDATION_ERROR",
        )

    _trace("importing agent (lazy)")
    # a2a/03: ленивые импорты — раньше были на уровне модуля, и тянули весь
    # langchain/langgraph стек при ``import blocksnet_mcp``. Теперь — только
    # когда legacy-инструмент реально вызывается.
    from blocksnet_agent import BlocksNetAgent
    from blocksnet_agent.config import Settings as AgentSettings
    from blocksnet_agent.runtime import start_run, get_run_dir
    from blocksnet_mcp.serialize import to_json

    _trace("building agent settings")
    # a2a/03: model может быть None в новых настройках (LLM-поля optional).
    # Фолбэк на дефолт ``gpt-4o-mini`` — то же поведение, что и раньше.
    agent_settings = AgentSettings(
        chat_url=settings.chat_url,
        api_key=settings.api_key,
        model=settings.model or "gpt-4o-mini",
        data_dir=settings.data_dir,
        output_dir=settings.output_dir,
        max_iterations=iterations,
    )

    _trace("starting run context")
    # P0.2: пробрасываем progress callback и дедлайн в run context.
    from blocksnet_agent.runtime import is_stop_requested

    # overwrite=True: поток пула (server.py → run_in_executor) хранит RunContext
    # прошлого вызова, без него вызов унаследует его каталог, дедлайн и callback.
    ctx = start_run(
        settings.output_dir,
        progress_callback=progress_callback,
        deadline_sec=settings.deadline_sec or None,
        overwrite=True,
    )
    output_dir = get_run_dir(settings.output_dir)
    run_id = ctx.run_id

    _trace("constructing agent")
    agent = BlocksNetAgent(settings=agent_settings, max_iterations=iterations)

    try:
        _trace("running agent")
        result = agent.run(normalized_question)
        status = "partial" if is_stop_requested() else "ok"
        _trace("serializing result")
        run_dir = str(getattr(result, "run_dir", output_dir) or output_dir)
        return _build_payload(result, run_dir, status=status)
    except Exception as exc:
        # P0.2: структурированный failed-ответ, isError=False (ошибка — легитимный результат).
        log.exception("analyze_urban_question failed")
        return _build_payload(
            type("R", (), {"output": str(exc), "run_id": run_id})(),
            str(output_dir),
            status="failed",
            error=str(exc),
            error_code="AGENT_EXCEPTION",
        )
