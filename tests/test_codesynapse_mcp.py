"""Совместимость MCP-сервера с каталогом инструментов CodeSynapse.

Второй канал подключения к MAS: их агент вызывает наши инструменты напрямую,
без нашего LLM. Проверяем то, что ломает регистрацию или рантайм на **их**
стороне, а не нашу аналитику:

* правила идентификаторов (``src/tools/mcp_tool_ids.py``) — точки и пробелы
  в именах запрещены, public id склеивается как ``server.tool``;
* лимит длины wire-имени для LLM (``mcp_llm_function_names.py``, 64 символа);
* поведение при холодной сессии — их исполнитель кэширует stdio-контейнер на
  **проект** (``mcp_executor.py``: ключ ``(project_id, tenant_id, server_id)``),
  поэтому следующий проект получает пустое состояние, и инструменты, читающие
  ``state[result_key]``, обязаны отвечать внятно, а не падать.

План: ``docs/dev/plans/codesynapse/02-mcp-channel.md`` (M2, M4, M5).
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Правила из их src/tools/mcp_tool_ids.py.
MCP_SEGMENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
OPENAI_FUNCTION_NAME_MAX_LEN = 64

#: Предлагаемый Server ID при регистрации (см. M5 / docs/dev/codesynapse_registration.md).
PROPOSED_SERVER_ID = "blocksnet"

#: Инструменты, читающие ``state[result_key]`` — самые уязвимые к холодной сессии.
STATE_READING_TOOLS = (
    "get_analysis_results",
    "get_block_info",
    "get_metric_for_block",
    "get_weakest_services",
    "list_cached_data",
    "render_metric_map",
)


@pytest.fixture(scope="module")
def tool_names() -> List[str]:
    from blocksnet_agent.tools.catalog import build_catalog

    tmp = Path(tempfile.mkdtemp())
    return [spec.name for spec in build_catalog({}, tmp, tmp)]


# --- M5: идентификаторы -----------------------------------------------------


def test_tool_names_satisfy_mcp_segment_rules(tool_names) -> None:
    """Точки в имени сделали бы public id ``server.tool`` неразбираемым."""
    bad = [name for name in tool_names if not MCP_SEGMENT_ID_RE.match(name)]
    assert not bad, f"имена не проходят validate_mcp_segment_id: {bad}"


def test_proposed_server_id_is_valid(tool_names) -> None:
    assert MCP_SEGMENT_ID_RE.match(PROPOSED_SERVER_ID), PROPOSED_SERVER_ID


def test_public_tool_ids_fit_the_llm_function_name_limit(tool_names) -> None:
    """``server.tool`` участвует в генерации wire-имени с лимитом 64 символа."""
    longest = max(tool_names, key=len)
    public_id = f"{PROPOSED_SERVER_ID}.{longest}"
    assert len(public_id) < OPENAI_FUNCTION_NAME_MAX_LEN, (
        f"самый длинный public id {public_id} ({len(public_id)}) не оставляет "
        f"запаса под лимит {OPENAI_FUNCTION_NAME_MAX_LEN}"
    )


# --- M2/M4: реальный обмен по stdio ----------------------------------------


def _mcp_session_calls(calls: List[tuple[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Поднять MCP-сервер по stdio и выполнить вызовы в одной сессии.

    Один процесс на весь список: их исполнитель тоже держит контейнер живым в
    пределах проекта, а не поднимает его на каждый вызов.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run() -> List[Dict[str, Any]]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "blocksnet_mcp"],
            cwd=str(PROJECT_ROOT),
        )
        out: List[Dict[str, Any]] = []
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    text = "".join(
                        getattr(item, "text", "") or "" for item in (result.content or [])
                    )
                    out.append({"name": name, "is_error": result.isError, "text": text})
        return out

    return asyncio.run(_run())


@pytest.fixture(scope="module")
def exposed_tools() -> List[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run() -> List[str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "blocksnet_mcp"],
            cwd=str(PROJECT_ROOT),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [tool.name for tool in (await session.list_tools()).tools]

    return asyncio.run(_run())


def test_stdio_server_lists_tools_without_a_dataset(exposed_tools) -> None:
    """M2: их Discover перечисляет инструменты у образа без смонтированных данных."""
    assert exposed_tools
    assert {"open_session", "close_session", "session_info"} <= set(exposed_tools)


def test_exposed_names_match_the_catalog(exposed_tools, tool_names) -> None:
    """Каталог — источник истины; расхождение означает дрейф документации."""
    missing = sorted(set(tool_names) - set(exposed_tools))
    assert not missing, f"каталожные инструменты не экспонированы по MCP: {missing}"


def test_cold_session_reports_instead_of_crashing() -> None:
    """M4/Л8: следующий проект получает пустое состояние — это не авария.

    Их агент читает текст ошибки и способен исправиться сам, поэтому ответ
    обязан быть структурным, с ``error_code`` и подсказкой, а не исключением.
    """
    results = _mcp_session_calls(
        [
            ("list_cached_data", {"session_id": "cold-1"}),
            (
                "get_analysis_results",
                {"session_id": "cold-1", "result_key": "mean_accessibility"},
            ),
        ]
    )

    for item in results:
        assert not item["is_error"], f"{item['name']} упал вместо структурного ответа"
        assert "Traceback" not in item["text"], item["name"]

    cache, analysis = results
    assert "load_blocks" in cache["text"], (
        "подсказка о следующем шаге пропала — их агент не сможет исправиться сам"
    )
    assert '"error_code"' in analysis["text"] or "не найден" in analysis["text"]


def test_sessions_do_not_share_state() -> None:
    """Два проекта = два состояния; на общее рассчитывать нельзя."""
    results = _mcp_session_calls(
        [
            ("open_session", {"session_id": "proj-a"}),
            ("open_session", {"session_id": "proj-b"}),
            ("list_cached_data", {"session_id": "proj-b"}),
        ]
    )
    assert not results[-1]["is_error"]
    assert "пуст" in results[-1]["text"].lower(), results[-1]["text"]
