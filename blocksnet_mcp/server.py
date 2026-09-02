"""MCP-сервер blocksnet: raw-tools из каталога + сессии.

Шаг 03 a2a-рефакторинга. Главные цели:

1. ``python -m blocksnet_mcp`` стартует без ``CHAT_URL``/``API_KEY``.
2. ``tools/list`` отдаёт весь каталог агента (32 инструмента) +
   3 служебных (``open_session``, ``close_session``, ``session_info``).
3. ``session_id`` — первый параметр в каждом raw-tool, default = ``"default"``.
4. Изоляция сессий: state одного клиента не виден другому.
5. Исключения в инструменте → envelope со ``status="failed"``,
   ``error_code="TOOL_EXCEPTION"``, не транспортная ошибка (P0.2 принцип).

Архитектура:
- Каталог инструментов — ``build_catalog()`` из шага 01, вызывается per-request.
- Session store — ``get_session_store()`` из шага 02.
- Дедлайн на вызов — ``start_run(deadline_sec=...)`` из ``blocksnet_agent.runtime``.
- legacy ``analyze_urban_question`` — отдельным маршрутом, через ``agent_tool``.
- Регистрация инструментов — динамическая, через ``mcp.add_tool()``, сигнатура
  собирается из ``spec.args_schema`` + ``session_id``.

Безопасность (по плану):
- ``start_run()`` выставляет deadline — тяжёлый compute не зависнет.
- Любое исключение → envelope, не raise (чтобы MCP-клиент получил структуру).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP

from blocksnet_mcp.envelope import (
    ERROR_CODE_SESSION_NOT_FOUND,
    ERROR_CODE_TOOL_EXCEPTION,
    build_envelope,
)
from blocksnet_mcp.session import get_session_store
from blocksnet_mcp.settings import get_mcp_settings, reset_mcp_settings

log = logging.getLogger("blocksnet_mcp")

mcp = FastMCP("blocksnet")


# --- утилиты для построения динамической сигнатуры -------------------------


def _python_type_for_json_schema_type(json_type: str | None) -> Any:
    """Грубое JSON-Schema → Python type для inspect.Signature.

    Используется только чтобы FastMCP корректно сгенерировал inputSchema.
    Если не получится — фолбэк на ``str``.
    """
    mapping = {
        "integer": int,
        "number": float,
        "boolean": bool,
        "string": str,
        "array": list,
        "object": dict,
    }
    return mapping.get(json_type or "string", str)


def _build_tool_wrapper(
    spec_name: str,
    spec_short: str,
    spec_full: str,
    args_schema: dict[str, Any],
) -> Callable[..., Any]:
    """Создаёт обёртку инструмента с динамической сигнатурой.

    Сигнатура = параметры из ``args_schema["properties"]`` + ``session_id``.
    Каждый параметр получает дефолт, если в схеме он не required.
    Реальный вызов идёт в ``original_tool.invoke({...})``.
    """
    properties: dict[str, Any] = args_schema.get("properties") or {}
    required: list[str] = list(args_schema.get("required") or [])

    new_params: list[inspect.Parameter] = [
        inspect.Parameter(
            name="session_id",
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=str,
            default="default",
        )
    ]
    for prop_name, prop_schema in properties.items():
        py_type = _python_type_for_json_schema_type(prop_schema.get("type"))
        if prop_name in required:
            default = inspect.Parameter.empty
        else:
            default = prop_schema.get("default", None)
        new_params.append(
            inspect.Parameter(
                name=prop_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                annotation=py_type,
                default=default,
            )
        )

    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        session_id = kwargs.pop("session_id", "default")
        store = get_session_store()
        session = store.get_or_create(session_id)
        # a2a/07 fix: создаём tools per-request с state конкретной сессии.
        # Раньше обёртка держала ссылку на ``ref_tool`` с пустым state —
        # все сессии делили один набор tools (регрессия изоляции сессий,
        # поймана в test_session_isolation_via_call_tool 22.07).
        from blocksnet_agent.tools import make_tools

        data_dir = session.data_dir or get_mcp_settings().data_dir
        output_dir = session.output_dir or get_mcp_settings().output_dir
        tools = make_tools(session.state, data_dir, output_dir)
        tool = next((t for t in tools if t.name == spec_name), None)
        if tool is None:
            return build_envelope(
                tool=spec_name,
                session_id=session.session_id,
                text="",
                artifacts=[],
                error_code="TOOL_NOT_FOUND",
                error=f"инструмент {spec_name!r} не найден в каталоге",
            )
        return _invoke_with_envelope(
            tool=tool,
            tool_name=spec_name,
            session_id=session.session_id,
            args=kwargs,
            data_dir=data_dir,
            output_dir=output_dir,
        )

    # FastMCP читает __signature__ для построения inputSchema.
    wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=new_params, return_annotation=dict[str, Any]
    )
    wrapper.__name__ = f"tool_{spec_name}"
    wrapper.__qualname__ = f"tool_{spec_name}"
    wrapper.__doc__ = spec_short or spec_full
    return wrapper


def _invoke_with_envelope(
    tool, tool_name: str, session_id: str, args: dict[str, Any],
    data_dir: Path | None = None, output_dir: Path | None = None,
) -> dict[str, Any]:
    """Вызов инструмента с обёрткой в envelope и обработкой исключений.

    Дедлайн на вызов выставляется через ``start_run`` из blocksnet_agent.runtime —
    инструменты внутри проверяют ``is_deadline_reached()``.
    Артефакты собираются из RunLogger через ``get_run_logger().saved_files``.

    a2a/06: ``data_dir``/``output_dir`` прокидываются явно — если сессия
    привязана к scenario_id, они указывают на подкаталог сценария.
    """
    # Ленивый импорт — нужен только при фактическом вызове.
    from blocksnet_agent.runtime import (
        is_deadline_reached,
        start_run,
    )

    settings = get_mcp_settings()
    deadline_sec = float(settings.deadline_sec or 0) or None

    # a2a/06: используем data_dir/output_dir сессии (если привязаны к scenario_id),
    # иначе — глобальные из настроек. OUTPUT_DIR всегда один — там копятся run_*.
    effective_output_dir = output_dir if output_dir is not None else settings.output_dir

    run_ctx = start_run(effective_output_dir, deadline_sec=deadline_sec)
    logger = run_ctx.logger
    saved_before = len(logger.saved_files)

    try:
        try:
            result_text = tool.invoke(args)
        except Exception as exc:  # noqa: BLE001 — все исключения в envelope
            log.warning("tool %s raised: %s", tool_name, exc)
            return build_envelope(
                tool=tool_name,
                session_id=session_id,
                text="",
                artifacts=[],
                error_code=ERROR_CODE_TOOL_EXCEPTION,
                error=f"{type(exc).__name__}: {exc}",
            )
        # Артефакты: только новые, записанные этим вызовом.
        new_artifacts = [
            entry["path"]
            for entry in logger.saved_files[saved_before:]
            if entry.get("path")
        ]
        # Проверка дедлайна — если инструмент вернул ok, но время вышло,
        # помечаем как partial (не failed).
        if is_deadline_reached() and "STOP:" in (result_text or ""):
            envelope = build_envelope(
                tool=tool_name,
                session_id=session_id,
                text=result_text,
                artifacts=new_artifacts,
            )
            envelope["status"] = "partial"
            return envelope
        return build_envelope(
            tool=tool_name,
            session_id=session_id,
            text=result_text,
            artifacts=new_artifacts,
        )
    finally:
        # Не закрываем run-context глобально — он переиспользуется между вызовами
        # в той же сессии.
        _ = run_ctx  # подавляем unused


# --- регистрация каталога --------------------------------------------------


def _register_catalog_tools(mcp: FastMCP) -> None:
    """Регистрирует инструменты каталога (32 доменных + RAG).

    Строит каталог на пустом state — нужны только имена, описания, схемы.
    Живые объекты ``tool`` будут создаваться per-request через ``build_catalog``
    с реальным state сессии — это гарантирует изоляцию между сессиями
    (блок #1 ревизии 22.07: ``original_tool`` имел закрытие на пустой state,
    все сессии делили один набор tools и один state).
    """
    from blocksnet_agent.tools.catalog import build_catalog, get_spec

    settings = get_mcp_settings()
    # Построение каталога "для схем" — на временном state.
    # Используется только для метаданных (имена, описания, args_schema).
    probe_specs = build_catalog({}, settings.data_dir, settings.output_dir)

    for spec in probe_specs:
        wrapper = _build_tool_wrapper(
            spec_name=spec.name,
            spec_short=spec.short,
            spec_full=spec.full,
            args_schema=spec.args_schema,
        )
        mcp.add_tool(
            wrapper,
            name=spec.name,
            description=spec.short,
        )

    # ``get_spec`` оставлен как fallback — но в текущей реализации не нужен,
    # потому что каждая обёртка хранит spec и строит tools per-request.
    _ = get_spec  # noqa: F841 — keep import visible for tooling


# --- служебные MCP-инструменты (только на уровне MCP, не в каталоге) -------


def _register_session_tools(mcp: FastMCP) -> None:
    """``open_session``/``close_session``/``session_info`` — обёртки SessionStore."""

    def open_session(
        session_id: str = "default",
        scenario_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Создаёт или возвращает сессию MCP. Возвращает session_id и сводку.

        a2a/06: ``scenario_id``/``project_id`` привязываются к сессии.
        При первом вызове резолвится ``data_dir`` через ``resolve_context``
        (с дефолтным materializer=None — сценарий должен быть предварительно
        материализован, иначе ``SCENARIO_NOT_MATERIALIZED``).
        """
        from blocksnet_agent.context import resolve_context

        settings = get_mcp_settings()
        try:
            scenario_ctx = resolve_context(
                scenario_id=scenario_id,
                project_id=project_id,
                data_dir=settings.data_dir,
                output_dir=settings.output_dir,
            )
        except Exception as exc:
            # Конвертируем ContextError в envelope.
            from blocksnet_mcp.envelope import build_envelope
            return build_envelope(
                tool="open_session",
                session_id=session_id,
                text="",
                artifacts=[],
                error_code=getattr(exc, "code", "CONTEXT_ERROR"),
                error=str(exc),
            )

        store = get_session_store()
        session = store.get_or_create(
            session_id,
            scenario_id=scenario_id,
            project_id=project_id,
            data_dir=scenario_ctx.data_dir,
            output_dir=scenario_ctx.output_dir,
        )
        return {
            "session_id": session.session_id,
            "created": True,
            "scenario_id": scenario_id,
            "project_id": project_id,
            "data_dir": str(session.data_dir) if session.data_dir else None,
            "output_dir": str(session.output_dir) if session.output_dir else None,
            "info": store.info(session.session_id),
        }

    def close_session(session_id: str) -> dict[str, Any]:
        """Закрывает сессию: освобождает state и удаляет из стора."""
        store = get_session_store()
        closed = store.close(session_id)
        return {"session_id": session_id, "closed": closed}

    def session_info(session_id: str = "default") -> dict[str, Any]:
        """Диагностика: возраст, idle, список ключей в state (без значений)."""
        store = get_session_store()
        return store.info(session_id)

    for fn in (open_session, close_session, session_info):
        mcp.add_tool(fn)


# --- legacy: analyze_urban_question ----------------------------------------


def _register_agent_tool(mcp: FastMCP) -> None:
    """Legacy ``analyze_urban_question`` — отдельно, потому что у него async-progress."""
    from blocksnet_mcp.agent_tool import analyze_urban_question as _sync_agent

    @mcp.tool()
    async def analyze_urban_question(
        question: str,
        max_iterations: int | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """[DEPRECATED] Run BlocksNetAgent on local data and return structured JSON.

        a2a/03: сохранён для обратной совместимости. Используйте A2A-skill
        ``run_pipeline`` для новых интеграций. Возвращает ``LLM_NOT_CONFIGURED``
        если ``CHAT_URL``/``API_KEY`` не заданы.
        """
        # Прогресс — если есть MCP Context, отправляем уведомления.
        progress_state: dict[str, Any] = {"count": 0, "last_msg": ""}

        def progress_callback(done: int, total: int, message: str) -> None:
            progress_state["count"] = int(done)
            progress_state["last_msg"] = str(message or "")

        settings = get_mcp_settings()
        deadline_sec = float(settings.deadline_sec or 0) or None
        progress_interval = max(0.1, float(settings.progress_interval_sec or 0))

        loop = asyncio.get_running_loop()

        def run_in_thread() -> dict[str, Any]:
            return _sync_agent(
                question=question,
                max_iterations=max_iterations,
                progress_callback=progress_callback,
            )

        if ctx is None:
            future = loop.run_in_executor(None, run_in_thread)
            try:
                return await future
            except Exception as exc:
                return {
                    "status": "failed",
                    "error_code": "AGENT_EXCEPTION",
                    "error": str(exc),
                }

        async def progress_reporter() -> None:
            try:
                while True:
                    await asyncio.sleep(progress_interval)
                    try:
                        await ctx.report_progress(
                            progress=progress_state["count"],
                            message=f"tool_calls={progress_state['count']}: {progress_state['last_msg']}",
                        )
                    except Exception:
                        return
            except asyncio.CancelledError:
                return

        reporter = asyncio.create_task(progress_reporter())
        try:
            future = loop.run_in_executor(None, run_in_thread)
            return await future
        except Exception as exc:
            log.warning("analyze_urban_question: agent thread failed: %s", exc)
            return {
                "status": "failed",
                "error_code": "AGENT_EXCEPTION",
                "error": str(exc),
                "tool_calls_done": progress_state["count"],
            }
        finally:
            reporter.cancel()
            try:
                await reporter
            except (asyncio.CancelledError, Exception):
                pass


# --- entry point -----------------------------------------------------------


def _build_server() -> FastMCP:
    """Создаёт и настраивает FastMCP с зарегистрированными инструментами."""
    settings = get_mcp_settings()
    # host/port/path нужны только streamable-http; для stdio FastMCP их игнорирует.
    mcp_instance = FastMCP(
        "blocksnet",
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.mcp_path,
    )
    _register_catalog_tools(mcp_instance)
    _register_session_tools(mcp_instance)
    if settings.enable_agent_tool:
        _register_agent_tool(mcp_instance)
    _register_health_route(mcp_instance)
    return mcp_instance


# --- HTTP-транспорт: /health и выбор транспорта ---------------------------


def health_payload() -> dict[str, Any]:
    """Ответ ``GET /health`` (только streamable-http).

    ``data_ready`` — оба базовых файла датасета на месте; без них
    ``analyze_urban_question`` и ``load_*`` вернут failed-envelope, поэтому
    статус ``degraded``, а не ``ok``.
    """
    settings = get_mcp_settings()
    data_dir = settings.data_dir
    # Датасет лежит либо прямо в DATA_DIR (одиночный город), либо в
    # подкаталогах-сценариях (``data_dir/<scenario_id>``, см. session.py).
    datasets = sorted(
        candidate.name
        for candidate in ([data_dir] + [c for c in data_dir.iterdir() if c.is_dir()])
        if _has_base_dataset(candidate)
    ) if data_dir.is_dir() else []
    data_ready = bool(datasets)
    return {
        "status": "ok" if data_ready else "degraded",
        "server": "blocksnet",
        "transport": resolve_transport(settings.transport),
        "data_ready": data_ready,
        "data_dir": str(data_dir),
        "datasets": datasets,
    }


def _has_base_dataset(path: Path) -> bool:
    return (path / "blocks_with_services.gpkg").is_file() and (path / "acc_mx.pickle").is_file()


def _register_health_route(mcp: FastMCP) -> None:
    """``GET /health`` для compose/оркестратора; в stdio-режиме маршрут не виден."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(health_payload())


def resolve_transport(raw: str | None) -> str:
    """``TRANSPORT`` → имя транспорта FastMCP (``stdio`` | ``streamable-http``)."""
    value = (raw or "stdio").strip().lower()
    if value == "stdio":
        return "stdio"
    if value in {"http", "streamable-http", "streamable_http"}:
        return "streamable-http"
    raise ValueError(f"TRANSPORT must be 'stdio' or 'http', got {raw!r}")


# ``mcp`` как lazy singleton — инициализируется при первом обращении
# (``get_mcp()``). ``import blocksnet_mcp.server`` НЕ должен тянуть
# ``blocksnet_agent`` (нужно для MCP-образа без LLM-зависимостей,
# см. ``tests/test_image_deps.py``).
# Тесты обращаются через ``get_mcp()`` или через ``mcp`` напрямую —
# оба пути триггерят ленивую инициализацию.
_mcp_instance: FastMCP | None = None
mcp: FastMCP | None = None  # back-compat: старое имя используется в тестах и smoke


def get_mcp() -> FastMCP:
    """Lazy singleton — инициализирует ``_build_server()`` при первом вызове.

    Это даёт гарантию, что ``import blocksnet_mcp.server`` НЕ тянет
    ``blocksnet_agent`` (а через него — ``langgraph``/``tiktoken``).
    """
    global _mcp_instance, mcp
    if _mcp_instance is None:
        _mcp_instance = _build_server()
        mcp = _mcp_instance  # back-compat
    return _mcp_instance


def main() -> None:
    """``python -m blocksnet_mcp`` — MCP-сервер: stdio (default) или streamable-http.

    ``TRANSPORT=http`` поднимает streamable-http на ``HOST:PORT`` (``MCP_PATH``)
    с ``GET /health``; любое другое значение кроме ``stdio`` — ошибка на старте,
    а не тихий откат к stdio (иначе сетевой деплой молча слушает только stdin).
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    transport = resolve_transport(get_mcp_settings().transport)
    # main() идёт через lazy singleton — триггерит инициализацию здесь,
    # а не на этапе ``import blocksnet_mcp.server``.
    get_mcp().run(transport=transport)


if __name__ == "__main__":
    main()


__all__ = ["build_app", "get_mcp", "main", "A2ATaskState"]