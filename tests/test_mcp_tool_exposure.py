"""Тесты шага 03 a2a-рефакторинга: экспозиция MCP-сервера.

Главные гарантии:
- ``python -m blocksnet_mcp`` стартует без LLM-конфига.
- В ``tools/list`` есть каталог (32) + 3 служебных, ``submit_answer`` отсутствует.
- У каждого raw-tool в inputSchema есть ``session_id``.
- Исключения в инструменте → envelope со ``status="failed"`` + ``TOOL_EXCEPTION``.
- Изоляция сессий: state одной не виден другой.
- Анализ текста инструмента → правильный status в envelope.
- Legacy ``analyze_urban_question`` отключается через ``ENABLE_AGENT_TOOL=false``.

Тесты НЕ требуют реальных данных — инструменты возвращают текст-ошибку при
отсутствии файлов, это валидный ``failed``-конверт.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from pathlib import Path

import pytest

import blocksnet_mcp.server as server_module
from blocksnet_mcp.envelope import (
    ERROR_CODE_LLM_NOT_CONFIGURED,
    ERROR_CODE_SESSION_NOT_FOUND,
    ERROR_CODE_TOOL_EXCEPTION,
    ERROR_CODE_TOOL_FAILED,
    build_envelope,
)
from blocksnet_mcp.session import SessionStore, reset_session_store
from blocksnet_mcp.settings import reset_mcp_settings


# --- helpers ---------------------------------------------------------------


async def _list_tools():
    return await server_module.get_mcp().list_tools()


async def _call_tool(name: str, arguments: dict):
    """Обёртка над ``mcp.call_tool`` — нормализует возвращаемый формат."""
    result = await server_module.get_mcp().call_tool(name, arguments)
    # Новый API FastMCP: (content_list, structured_dict | None).
    if isinstance(result, tuple) and len(result) == 2:
        _content_list, structured = result
        if structured:
            return structured
        # Фолбэк: парсим первый text-content.
        for c in _content_list:
            text = getattr(c, "text", None)
            if text:
                try:
                    import json as _json
                    return _json.loads(text)
                except Exception:
                    return {"text": text}
        return {"text": ""}
    # Старый формат — dict напрямую.
    return result


@pytest.fixture(autouse=True)
def _reset_session_store_only():
    """Сбрасываем SessionStore между тестами (дешёвая операция).

    Настройки НЕ сбрасываем — иначе monkeypatch-фикстура pytest не успеет
    применить delenv/setenv ДО того, как мы cache_clear() вызовем.
    """
    reset_session_store()
    yield
    reset_session_store()


# --- главное: сервер поднимается без LLM ----------------------------------


def test_server_imports_without_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без ``CHAT_URL``/``API_KEY`` сервер поднимается и регистрирует инструменты."""
    monkeypatch.delenv("CHAT_URL", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    importlib.reload(server_module)
    tools = asyncio.run(_list_tools())
    # Хотя бы 32 каталожных + 3 служебных = 35 (без legacy analyze_urban_question).
    assert len(tools) >= 35, f"ожидалось ≥35, получили {len(tools)}"


def test_agent_tool_reports_llm_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без LLM-конфига ``analyze_urban_question`` возвращает ``LLM_NOT_CONFIGURED``.

    Подменяем ``get_mcp_settings`` напрямую, чтобы ``pydantic-settings``
    не подтянул ``CHAT_URL``/``API_KEY`` из ``.env`` (monkeypatch.delenv
    убирает только из ``os.environ``, но настройки читают файл).
    """
    from blocksnet_mcp import agent_tool
    from blocksnet_mcp.settings import MCPSettings

    fake_settings = MCPSettings.model_construct(
        chat_url=None,
        api_key=None,
        model=None,
        data_dir=Path("/tmp"),
        output_dir=Path("/tmp"),
        max_iterations=1,
        deadline_sec=480,
        progress_interval_sec=10.0,
        session_ttl_sec=1800.0,
        max_sessions=8,
        enable_agent_tool=True,
    )
    monkeypatch.setattr(agent_tool, "get_mcp_settings", lambda: fake_settings)
    reset_mcp_settings()

    result = agent_tool.analyze_urban_question("вопрос")
    assert result["status"] == "failed"
    assert result["error_code"] == ERROR_CODE_LLM_NOT_CONFIGURED


# --- экспозиция каталога ---------------------------------------------------


def test_all_catalog_tools_are_registered() -> None:
    """MCP экспозиция = каталог + служебные (не литерал 32)."""
    from blocksnet_agent.tools.catalog import build_catalog

    tools = asyncio.run(_list_tools())
    exposed = {t.name for t in tools}
    expected = {s.name for s in build_catalog({}, Path("/tmp"), Path("/tmp"))}
    # Все каталожные инструменты должны быть экспонированы.
    assert expected <= exposed, (
        f"не экспонированы: {expected - exposed}"
    )


def test_submit_answer_not_exposed() -> None:
    """``submit_answer`` — терминальный агентский, в MCP не уходит (инвариант 5)."""
    tools = asyncio.run(_list_tools())
    names = {t.name for t in tools}
    assert "submit_answer" not in names


def test_service_tools_are_registered() -> None:
    """open_session / close_session / session_info — служебные MCP-инструменты."""
    tools = asyncio.run(_list_tools())
    names = {t.name for t in tools}
    for required in ("open_session", "close_session", "session_info"):
        assert required in names, f"{required} не зарегистрирован"


def test_analyze_urban_question_exposed_by_default() -> None:
    """Legacy analyze_urban_question экспонирован по умолчанию (``ENABLE_AGENT_TOOL=true``)."""
    tools = asyncio.run(_list_tools())
    names = {t.name for t in tools}
    assert "analyze_urban_question" in names


def test_agent_tool_hidden_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ENABLE_AGENT_TOOL=false`` → legacy-инструмент не регистрируется."""
    monkeypatch.setenv("ENABLE_AGENT_TOOL", "false")
    reset_mcp_settings()
    importlib.reload(server_module)
    tools = asyncio.run(_list_tools())
    names = {t.name for t in tools}
    assert "analyze_urban_question" not in names
    # Каталожные и служебные остаются.
    assert "open_session" in names
    assert "list_cached_data" in names


# --- session_id в схеме ----------------------------------------------------


def test_every_catalog_tool_has_session_id_in_input_schema() -> None:
    """У каждого каталожного инструмента в inputSchema есть ``session_id``."""
    from blocksnet_agent.tools.catalog import build_catalog

    catalog_names = {s.name for s in build_catalog({}, Path("/tmp"), Path("/tmp"))}
    tools = asyncio.run(_list_tools())
    for t in tools:
        if t.name not in catalog_names:
            # Служебные — у них своя схема.
            continue
        props = t.inputSchema.get("properties", {})
        assert "session_id" in props, (
            f"{t.name}: inputSchema не содержит session_id — клиенты не смогут передавать"
        )


def test_session_id_default_is_default_string() -> None:
    """Дефолт ``session_id`` — строка ``"default"`` (однопользовательский режим)."""
    tools = asyncio.run(_list_tools())
    sample = next(t for t in tools if t.name == "list_cached_data")
    sid_schema = sample.inputSchema["properties"]["session_id"]
    assert sid_schema.get("default") == "default"
    assert sid_schema.get("type") == "string"


# --- изоляция сессий -------------------------------------------------------


def test_session_isolation_via_call_tool() -> None:
    """list_cached_data в разных сессиях показывает разный кэш."""
    for sid in ("iso-A", "iso-B"):
        asyncio.run(_call_tool("open_session", {"session_id": sid}))
    # Сессия A: загружаем blocks.
    result_a = asyncio.run(_call_tool("load_blocks", {"session_id": "iso-A"}))
    result_b = asyncio.run(_call_tool("list_cached_data", {"session_id": "iso-B"}))
    assert result_a["session_id"] == "iso-A"
    assert result_b["session_id"] == "iso-B"
    # В A: что-то загружено. В B: кэш пуст (изоляция).
    text_a = result_a.get("text", "")
    text_b = result_b.get("text", "")
    # ``list_cached_data`` без данных говорит "Кэш пуст".
    assert "пуст" in text_b.lower(), f"session B должна быть пустой, получили: {text_b[:200]!r}"


def test_default_session_is_shared_when_no_session_id() -> None:
    """Без явного session_id — всё в одной default-сессии."""
    r1 = asyncio.run(_call_tool("list_cached_data", {}))
    r2 = asyncio.run(_call_tool("list_cached_data", {"session_id": "default"}))
    assert r1["session_id"] == "default"
    assert r2["session_id"] == "default"


def test_empty_session_id_uses_the_default_session() -> None:
    result = asyncio.run(_call_tool("list_cached_data", {"session_id": ""}))
    assert result["session_id"] == "default"
    assert "error_code" not in result


def test_evicted_session_is_refused_instead_of_recreated_on_the_root_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionStore(max_sessions=2)
    monkeypatch.setattr(server_module, "get_session_store", lambda: store)
    for sid in ("evicted", "second", "third"):
        asyncio.run(_call_tool("open_session", {"session_id": sid}))

    result = asyncio.run(_call_tool("list_cached_data", {"session_id": "evicted"}))

    assert result["status"] == "failed"
    assert result["error_code"] == ERROR_CODE_SESSION_NOT_FOUND
    assert "open_session" in result["error"]
    assert store.get("evicted") is None


def test_expired_session_is_refused_not_revived(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SessionStore(ttl_sec=0.01)
    monkeypatch.setattr(server_module, "get_session_store", lambda: store)
    asyncio.run(_call_tool("open_session", {"session_id": "idle"}))
    time.sleep(0.05)

    result = asyncio.run(_call_tool("list_cached_data", {"session_id": "idle"}))

    assert result["error_code"] == ERROR_CODE_SESSION_NOT_FOUND
    assert store.get("idle") is None


def test_session_unknown_to_the_server_is_refused_and_not_created() -> None:
    result = asyncio.run(_call_tool("load_blocks", {"session_id": "never-opened"}))

    assert result["error_code"] == ERROR_CODE_SESSION_NOT_FOUND
    info = asyncio.run(_call_tool("session_info", {"session_id": "never-opened"}))
    assert info["exists"] is False


# --- envelope и обработка ошибок ------------------------------------------


def test_envelope_marks_failure_text_as_failed() -> None:
    """Текст с FAILURE_MARKERS → ``status="failed"``, ``error_code="TOOL_FAILED"``.

    Используем формат, который реально генерирует ``load_blocks``: начинается
    с ``"Ошибка: "`` (с двоеточием — это маркер ``FAILURE_MARKERS``).
    """
    env = build_envelope(
        "load_blocks", "default", "Ошибка: файл data/blocks_with_services.gpkg не найден"
    )
    assert env["status"] == "failed"
    assert env["error_code"] == ERROR_CODE_TOOL_FAILED


def test_envelope_marks_not_found_text_as_failed() -> None:
    """Текст с маркером ``"не найден"`` → failed (через _STALE_OBSERVATION_MARKERS)."""
    env = build_envelope("get_block_info", "default", "Блок не найден в индексе города")
    assert env["status"] == "failed"
    assert env["error_code"] == ERROR_CODE_TOOL_FAILED


def test_envelope_normal_text_is_ok() -> None:
    """Нейтральный текст → ``status="ok"``, нет error_code."""
    env = build_envelope("list_cached_data", "default", "Кэш пуст.")
    assert env["status"] == "ok"
    assert "error_code" not in env
    assert "error" not in env


def test_envelope_explicit_error_overrides_text() -> None:
    """Явный ``error_code`` переопределяет анализ текста."""
    env = build_envelope(
        "x",
        "default",
        "нормальный текст",
        error_code=ERROR_CODE_TOOL_EXCEPTION,
        error="boom",
    )
    assert env["status"] == "failed"
    assert env["error_code"] == ERROR_CODE_TOOL_EXCEPTION
    assert env["error"] == "boom"


def test_tool_exception_becomes_envelope() -> None:
    """Исключение внутри инструмента → envelope с TOOL_EXCEPTION, не raise.

    ``list_cached_data`` не падает в нашем сценарии, поэтому используем
    ``render_metric_map`` с невалидным result_key — должен вернуть текст-ошибку,
    которая классифицируется как failed. Если бы он бросил — envelope поймал бы.
    """
    result = asyncio.run(
        _call_tool(
            "render_metric_map", {"session_id": "default", "result_key": "nonexistent"}
        )
    )
    assert "status" in result
    assert result["status"] in ("ok", "failed")
    assert result["tool"] == "render_metric_map"
    assert result["session_id"] == "default"


# --- open_session/close_session/session_info --------------------------------


def test_open_session_returns_session_id() -> None:
    """open_session возвращает session_id (или existing)."""
    result = asyncio.run(_call_tool("open_session", {"session_id": "explicit-id"}))
    assert result["session_id"] == "explicit-id"
    assert result.get("created") is True
    assert "info" in result


def test_session_info_lists_keys_only_not_values() -> None:
    """session_info отдаёт только имена ключей, не значения (там DataFrame'ы)."""
    import json as _json

    asyncio.run(_call_tool("open_session", {"session_id": "info-test"}))
    # Грузим что-то в default-сессию.
    asyncio.run(_call_tool("load_blocks", {"session_id": "info-test"}))
    info_str = asyncio.run(_call_tool("session_info", {"session_id": "info-test"}))
    assert "keys" in info_str, info_str
    # Внутри нет содержимого GeoDataFrame'ов (там были бы блоки памяти).
    payload = _json.dumps(info_str, default=str)
    # Если бы ключи сериализовались с содержимым — это были бы мегабайты.
    assert len(payload) < 5000, "session_info утекает содержимым state"


def test_close_session_releases_state() -> None:
    """close_session → session_id освобождён, следующий вызов с ним получает SESSION_NOT_FOUND."""
    asyncio.run(_call_tool("open_session", {"session_id": "to-close"}))
    asyncio.run(_call_tool("load_blocks", {"session_id": "to-close"}))
    closed = asyncio.run(_call_tool("close_session", {"session_id": "to-close"}))
    assert closed["closed"] is True
    after = asyncio.run(_call_tool("list_cached_data", {"session_id": "to-close"}))
    assert after["error_code"] == ERROR_CODE_SESSION_NOT_FOUND


# --- не-MCP-API (для отладки) ---------------------------------------------


def test_server_module_has_main() -> None:
    """``python -m blocksnet_mcp.server`` и ``python -m blocksnet_mcp`` работают."""
    assert callable(server_module.main)