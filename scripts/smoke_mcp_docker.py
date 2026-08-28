#!/usr/bin/env python
"""Smoke MCP-образа по сценарию регистрации CodeSynapse (режим ``stdio``).

Повторяет ровно то, что делает их Discover: поднимает образ как
``docker run --rm -i <image>``, говорит по MCP через stdio, забирает
``tools/list`` и делает один безопасный вызов. Портов не публикует, данных не
монтирует — если инструмент требует датасет для простого перечисления, здесь
это и вскроется.

    python scripts/smoke_mcp_docker.py                     # blocksnet-agent/mcp:local
    python scripts/smoke_mcp_docker.py --image registry/blocksnet-mcp:0.2.0

Exit code: 0 — образ пригоден к регистрации; 1 — контракт нарушен;
2 — образ или docker недоступны.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import subprocess
import sys

DEFAULT_IMAGE = "blocksnet-agent/mcp:local"

# Правила их mcp_tool_ids.py: имя инструмента и mcp_server — только эти символы,
# точки запрещены (public id склеивается как ``server.tool``).
SEGMENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
OPENAI_FUNCTION_NAME_MAX_LEN = 64

#: Инструменты, безопасные для холодного вызова: не читают ``state``.
SAFE_CALLS = ("find_tools", "get_tool_help")

EXPECTED_SESSION_TOOLS = {"open_session", "close_session", "session_info"}


def _check(ok: bool, message: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
    return ok


async def _run(image: str) -> bool:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", image],
    )

    overall = True
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=== tools/list ===")
            tools = (await session.list_tools()).tools
            names = [tool.name for tool in tools]
            overall &= _check(bool(names), f"инструментов получено: {len(names)}")
            overall &= _check(
                EXPECTED_SESSION_TOOLS <= set(names),
                f"session-инструменты на месте: {sorted(EXPECTED_SESSION_TOOLS)}",
            )

            bad = [n for n in names if not SEGMENT_ID_RE.match(n)]
            overall &= _check(
                not bad,
                f"имена совместимы с mcp_tool_ids.py (нарушений: {len(bad)}{': ' + str(bad[:5]) if bad else ''})",
            )
            too_long = [n for n in names if len(n) >= OPENAI_FUNCTION_NAME_MAX_LEN]
            overall &= _check(
                not too_long,
                f"имена короче {OPENAI_FUNCTION_NAME_MAX_LEN} символов (нарушений: {len(too_long)})",
            )

            print("=== tools/call (без смонтированных данных) ===")
            candidate = next((c for c in SAFE_CALLS if c in names), None)
            if candidate is None:
                overall &= _check(False, f"нет ни одного из {SAFE_CALLS} для холодного вызова")
            else:
                result = await session.call_tool(candidate, {})
                text = "".join(
                    getattr(item, "text", "") or "" for item in (result.content or [])
                )
                overall &= _check(
                    not result.isError and bool(text),
                    f"{candidate}() отвечает без данных ({len(text)} символов)",
                )
    return overall


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", default=DEFAULT_IMAGE, help=f"тег образа (default: {DEFAULT_IMAGE})")
    args = parser.parse_args(argv)

    print(f"blocksnet MCP docker smoke (stdio): {args.image}\n")

    if shutil.which("docker") is None:
        print("FAIL: docker не найден в PATH", file=sys.stderr)
        return 2
    probe = subprocess.run(
        ["docker", "image", "inspect", args.image],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        print(
            f"FAIL: образ {args.image} недоступен. Соберите его:\n"
            f"    docker build -f Dockerfile.mcp -t {args.image} .",
            file=sys.stderr,
        )
        return 2

    try:
        ok = asyncio.run(_run(args.image))
    except Exception as exc:  # noqa: BLE001 — внешний процесс
        print(f"FAIL: обмен по MCP не состоялся: {exc}", file=sys.stderr)
        return 1

    print("\n=== итог ===")
    print("OK: образ пригоден для регистрации в CodeSynapse (mode stdio)" if ok else "FAIL: контракт нарушен")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
