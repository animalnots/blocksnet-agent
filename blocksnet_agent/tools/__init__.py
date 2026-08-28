import json
from pathlib import Path

from langchain_core.tools import BaseTool

from blocksnet_agent.metrics import FAILURE_MARKERS
from blocksnet_agent.tools.data import make_data_tools
from blocksnet_agent.tools.indicators import make_indicators_tools
from blocksnet_agent.tools.network import make_network_tools
from blocksnet_agent.tools.optimize import make_optimize_tools
from blocksnet_agent.tools.preparation import make_preparation_tools
from blocksnet_agent.tools.provision import make_provision_tools
from blocksnet_agent.tools.registry import build_tool_registry, make_help_tools
from blocksnet_agent.tools.services import make_services_tools
from blocksnet_agent.tools.viz import make_viz_tools

# T1.2: инструменты, для которых повторный вызов с теми же аргументами в рамках ОДНОГО запуска
# идемпотентен — результат можно отдать из кэша, не пересчитывая и не пересохраняя артефакты.
# Сюда НЕ входят генераторы/оптимизаторы (TPE стохастичен), viz (render_*) и meta-инструменты RAG.
_MEMOIZABLE_PREFIXES = ("compute_", "list_", "load_")
_NON_MEMOIZABLE_TOOLS = {"compute_scenario_provision", "list_cached_data"}
# P0.5: RAG-инструменты для самопомощи агента. Лимит нужен, чтобы модель не сжигала
# бюджет итераций на поиск документации вместо расчётов (см. отчёт 20260709 §4.3).
_RAG_TOOLS = {"find_tools", "get_tool_help"}
_RAG_MAX_CONSECUTIVE = 3
_STALE_OBSERVATION_MARKERS = (
    "нет кэшированных",
    "Сначала вызови",
    "сначала вызови",
    "не найден",
    "не удалось",
)
_REPEATED_FAILED_PREFIX = "REPEATED_FAILED_CALL"


def _memoize_tools(tools: list[BaseTool], state: dict | None = None) -> list[BaseTool]:
    """Оборачивает идемпотентные инструменты кэшем (tool, args)->observation на время запуска.

    Убирает дубли вызовов, которые порождает слой согласованности при реентри: повторный
    идентичный вызов возвращает прошлое наблюдение и не плодит повторные CSV/карты.

    P0.5: если передан ``state``, отслеживает streak RAG-вызовов (find_tools/get_tool_help).
    При превышении ``_RAG_MAX_CONSECUTIVE`` подряд — возвращает подсказку «хватит искать,
    переходи к расчёту» вместо выполнения, чтобы модель не сжигала бюджет итераций.
    """
    cache: dict[str, str] = {}
    failed_cache: dict[str, str | int] = {}
    # P0.4: версия state для инвалидации failed_cache — растёт при добавлении новых ключей.
    state_version = [0]

    # P0.5: streak-счётчик RAG в state (если state передан).
    _rag_streak = [0]

    def _bump_state_version():
        state_version[0] += 1

    wrapped: list[BaseTool] = []
    for tool in tools:
        original_func = getattr(tool, "func", None)
        # P0.5: оборачиваем и memo-инструменты (compute_/list_/load_), и RAG-инструменты
        # (find_tools/get_tool_help) — для memo — кэш, для RAG — только streak-счётчик.
        # Остальные (submit_answer, viz/render_*, suggest_target_blocks и пр.) — без обёртки.
        is_memo = (
            original_func is not None
            and tool.name not in _NON_MEMOIZABLE_TOOLS
            and any(tool.name.startswith(p) for p in _MEMOIZABLE_PREFIXES)
        )
        is_rag = tool.name in _RAG_TOOLS and original_func is not None
        if not (is_memo or is_rag):
            wrapped.append(tool)
            continue

        def make_wrapped(func, name, do_memo):
            def wrapped_call(*args, **kwargs):
                # P0.2: если стоп-флаг или дедлайн — не выполняем инструмент.
                try:
                    from blocksnet_agent.runtime import is_stop_requested, is_deadline_reached

                    if is_stop_requested() or is_deadline_reached():
                        return "STOP: запуск прерван по дедлайну; дальнейшие вызовы не выполняются."
                except Exception:
                    pass
                # P0.5: ограничение streak RAG-вызовов подряд. Если превышен лимит —
                # возвращаем подсказку вместо выполнения (агент должен перейти к расчётам).
                if name in _RAG_TOOLS and state is not None:
                    prev = _rag_streak[0]
                    if prev >= _RAG_MAX_CONSECUTIVE:
                        return (
                            f"STOP_RAG_STREAK: уже {_RAG_MAX_CONSECUTIVE} RAG-вызовов подряд "
                            f"({sorted(_RAG_TOOLS)}); дальнейшие find_tools/get_tool_help в этом ране "
                            f"не выполняются. Переходи к расчётам (compute_*, get_block_info, "
                            f"submit_answer) — у тебя осталось ограниченное число итераций."
                        )
                    _rag_streak[0] = prev + 1
                else:
                    # Не-RAG-вызов сбрасывает streak.
                    _rag_streak[0] = 0
                if not do_memo:
                    return func(*args, **kwargs)
                try:
                    # P0.1: канонизируем ключ — подстановка дефолтов убирает рассинхрон
                    # ('school', 15) vs ('school') — теперь одинаковый cache-key.
                    canonical_k = {
                        key: value for key, value in kwargs.items() if value is not None
                    }
                    key = name + "|" + json.dumps({"a": args, "k": canonical_k}, sort_keys=True, default=str)
                except Exception:
                    return func(*args, **kwargs)
                # P0.4: failed_cache инвалидируется при изменении ключей state
                # (новые данные могут сделать ранее неудачный вызов успешным).
                cached_version = failed_cache.get(f"__version__{key}")
                if cached_version is not None and cached_version != state_version[0]:
                    failed_cache.pop(key, None)
                    failed_cache.pop(f"__version__{key}", None)
                if key in failed_cache:
                    previous = failed_cache[key]
                    return (
                        f"{_REPEATED_FAILED_PREFIX}: this exact call already failed with: "
                        f"{str(previous)[:500]}; choose a different tool, different arguments, or inspect available options."
                    )
                if key in cache:
                    return cache[key]
                result = func(*args, **kwargs)
                if isinstance(result, str) and _cacheable_observation(result):
                    cache[key] = result
                    _bump_state_version()
                elif isinstance(result, str) and _failed_observation(result):
                    failed_cache[key] = result.strip()
                    failed_cache[f"__version__{key}"] = state_version[0]
                return result

            return wrapped_call

        try:
            wrapped.append(tool.model_copy(update={"func": make_wrapped(original_func, tool.name, is_memo)}))
        except Exception:
            wrapped.append(tool)
    return wrapped


def _cacheable_observation(result: str) -> bool:
    text = result.strip()
    if text.startswith(FAILURE_MARKERS):
        return False
    return not any(marker in text for marker in _STALE_OBSERVATION_MARKERS)


def _failed_observation(result: str) -> bool:
    text = result.strip()
    return text.startswith(FAILURE_MARKERS) or any(marker in text for marker in _STALE_OBSERVATION_MARKERS)


# a2a/03: публичный re-export — переиспользуется в ``blocksnet_mcp.envelope``
# для конверта ответа. Поведение не меняется, просто поднимаем наружу.
is_failed_observation = _failed_observation


def _tool_error_handler(exc: Exception) -> str:
    """Возвращает текст ошибки агенту вместо выброса исключения.

    Pydantic validation errors (LLM передал неверный тип аргумента) и другие
    исключения на входе инструмента попадают сюда — агент получает читаемое
    сообщение и может скорректировать вызов, вместо падения всего run.
    """
    return f"Ошибка вызова инструмента: {exc}"


def make_tools(
    state: dict,
    data_dir: Path,
    output_dir: Path,
    *,
    registry_out: dict[str, dict[str, str]] | None = None,
) -> list[BaseTool]:
    ctx = {"state": state, "data_dir": data_dir, "output_dir": output_dir}
    domain_tools = (
        make_data_tools(ctx)
        + make_network_tools(ctx)
        + make_provision_tools(ctx)
        + make_services_tools(ctx)
        + make_indicators_tools(ctx)
        + make_optimize_tools(ctx)
        + make_viz_tools(ctx)
        + make_preparation_tools(ctx)
    )
    # P1.1: терминальный ``submit_answer`` — финальный структурный ответ. Добавляется
    # в общий набор инструментов, агент вызывает его в самом конце. Без этого
    # ответа ``to_json`` пойдёт по fallback-пути (regex-парсинг + ``salvaged: True``).
    try:
        from blocksnet_agent.agent import _build_submit_answer_tool

        domain_tools.append(_build_submit_answer_tool(state))
    except Exception:
        # Если импорт не удался — fallback останется в ``to_json``.
        pass
    # Двухуровневые доки: LLM видит короткое описание (.description ← первая строка docstring),
    # полное — через get_tool_help/find_tools (RAG по инструментам, без заданных workflow).
    short_tools, registry = build_tool_registry(domain_tools)
    # a2a/01: если вызывающий передал ``registry_out``, отдаём ему полный реестр
    # (short+full) — это позволяет MCP-серверу построить каталог инструментов
    # из одного источника правды. Сигнатура обратно совместима (новый параметр
    # имеет дефолт None, существующие вызовы в ``agent.py`` не меняются).
    if registry_out is not None:
        registry_out.update(registry)
    tools = short_tools + make_help_tools(registry)
    # Pydantic validation errors (неверный тип аргумента от LLM) не должны ронять агент —
    # LangChain handle_tool_error возвращает сообщение обратно в reasoning loop.
    for t in tools:
        t.handle_tool_error = True
    return _memoize_tools(tools, state=state)


__all__ = ["make_tools"]
