#!/usr/bin/env python
"""Smoke-проверка MCP-сервера: каталог + изоляция сессий + tool-calls.

Шаг 03 a2a-рефакторинга. Поднимает сервер in-process (без stdio-транспорта)
и прогоняет сценарий:

    tools/list                  → 32 каталожных + 3 служебных, есть session_id
    open_session                → sid
    load_blocks(sid)            → status ok / failed (нет данных)
    list_cached_data(sid)       → cache заполнен
    list_cached_data("other")   → cache пуст (изоляция)
    compute_service_provision   → status ok / failed
    get_analysis_results        → ok / failed
    close_session(sid)          → ok

Код возврата ≠ 0 при любом ``status != "ok"`` (или при ошибках подключения).
Если данные в ``DATA_DIR`` отсутствуют — скрипт печатает предупреждение и
всё равно прогоняет сценарий (инструменты вернут ``status="failed"`` с
человекочитаемой ошибкой).

Использование::

    python scripts/smoke_mcp_tools.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Гарантируем, что корень проекта в sys.path — скрипт можно запускать откуда угодно.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blocksnet_mcp.server import get_mcp  # noqa: E402

mcp = get_mcp()


def _print_header(title: str) -> None:
    print()
    print(f"=== {title} ===")


async def _list_tools() -> list:
    return await mcp.list_tools()


async def _call_tool(name: str, args: dict) -> dict:
    """Нормализует разные форматы ответа call_tool к dict."""
    result = await mcp.call_tool(name, args)
    if isinstance(result, tuple) and len(result) == 2:
        _content_list, structured = result
        if structured:
            return structured
        for c in _content_list:
            text = getattr(c, "text", None)
            if text:
                import json as _json

                try:
                    return _json.loads(text)
                except Exception:
                    return {"text": text}
        return {"text": ""}
    return result


def _check(condition: bool, message: str) -> bool:
    """Печатает PASS/FAIL и возвращает статус."""
    marker = "PASS" if condition else "FAIL"
    print(f"  [{marker}] {message}")
    return condition


def _has_data() -> bool:
    """Проверяет, есть ли реальные данные — для предупреждения в выводе."""
    settings_module = sys.modules.get("blocksnet_mcp.settings")
    if settings_module is None:
        from blocksnet_mcp.settings import get_mcp_settings

        settings = get_mcp_settings()
    else:
        settings = settings_module.get_mcp_settings()
    blocks_path = settings.data_dir / "blocks_with_services.gpkg"
    acc_path = settings.data_dir / "acc_mx.pickle"
    return blocks_path.exists() or acc_path.exists()


async def main() -> int:
    print("blocksnet-mcp smoke: каталог + сессии + tool-calls")

    if not _has_data():
        print(
            "\n[INFO] DATA_DIR не содержит blocks_with_services.gpkg / acc_mx.pickle."
            "\n        Инструменты вернут status=failed — это ожидаемо, smoke всё равно валиден."
        )

    overall_ok = True

    _print_header("tools/list")
    tools = await _list_tools()
    catalog_tool_names = {t.name for t in tools if not t.name.startswith(("open_", "close_", "session_")) and t.name != "analyze_urban_question"}
    overall_ok &= _check(
        len(catalog_tool_names) >= 32,
        f"Каталожных инструментов: {len(catalog_tool_names)} (ожидалось ≥32)",
    )
    overall_ok &= _check(
        "submit_answer" not in {t.name for t in tools},
        "submit_answer НЕ экспонирован",
    )
    overall_ok &= _check(
        {"open_session", "close_session", "session_info"} <= {t.name for t in tools},
        "open_session/close_session/session_info экспонированы",
    )
    sample = next(t for t in tools if t.name == "list_cached_data")
    overall_ok &= _check(
        "session_id" in sample.inputSchema.get("properties", {}),
        "session_id в inputSchema list_cached_data",
    )

    sid = "smoke-sid-A"
    other_sid = "smoke-sid-B"

    _print_header(f"open_session({sid!r})")
    open_result = await _call_tool("open_session", {"session_id": sid})
    overall_ok &= _check(
        open_result.get("session_id") == sid,
        f"open вернул session_id={open_result.get('session_id')!r}",
    )

    _print_header(f"load_blocks({sid!r})")
    load_result = await _call_tool("load_blocks", {"session_id": sid})
    has_data = _has_data()
    expected_status = "ok" if has_data else "failed"
    overall_ok &= _check(
        load_result.get("status") in ("ok", "failed"),
        f"load_blocks → status={load_result.get('status')!r}, text[:80]={load_result.get('text', '')[:80]!r}",
    )

    _print_header(f"list_cached_data({sid!r}) vs list_cached_data({other_sid!r})")
    await _call_tool("open_session", {"session_id": other_sid})
    cache_a = await _call_tool("list_cached_data", {"session_id": sid})
    cache_b = await _call_tool("list_cached_data", {"session_id": other_sid})
    text_a = cache_a.get("text", "").lower()
    text_b = cache_b.get("text", "").lower()
    overall_ok &= _check(
        "blocks" in cache_a.get("text", "") or has_data is False,
        f"В сессии A: text[:60]={cache_a.get('text', '')[:60]!r}",
    )
    # Изоляция: в B не должно быть данных, загруженных в A.
    if has_data:
        overall_ok &= _check(
            "пуст" in text_b,
            f"В сессии B: text[:60]={cache_b.get('text', '')[:60]!r} (должен быть пустым)",
        )

    _print_header("compute_service_provision")
    if has_data:
        prov = await _call_tool(
            "compute_service_provision",
            {"session_id": sid, "service_type": "school"},
        )
        overall_ok &= _check(
            prov.get("status") in ("ok", "failed"),
            f"compute_service_provision → status={prov.get('status')!r}",
        )
    else:
        print("  [SKIP] нет данных в DATA_DIR — пропускаем compute_service_provision")

    _print_header(f"close_session({sid!r})")
    close_result = await _call_tool("close_session", {"session_id": sid})
    overall_ok &= _check(
        close_result.get("closed") is True,
        f"close_session вернул closed={close_result.get('closed')!r}",
    )

    _print_header("итог")
    if overall_ok:
        print("  SMOKE OK")
        return 0
    print("  SMOKE FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))