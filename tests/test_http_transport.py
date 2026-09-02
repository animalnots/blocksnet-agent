"""Транспорт MCP-сервера: stdio по умолчанию, streamable-http по ``TRANSPORT=http``.

Гарантии:
- настройки транспорта читаются из ``TRANSPORT``/``HOST``/``PORT``/``MCP_PATH``
  (и синонимов ``MCP_HOST``/``MCP_PORT``), дефолт — stdio на 127.0.0.1:8001;
- ``main()`` выбирает транспорт по настройке, неизвестное значение — ошибка,
  а не тихий stdio;
- ``GET /health`` зарегистрирован в streamable-http приложении и честно
  отражает наличие датасета.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TRANSPORT_ENV = ("TRANSPORT", "HOST", "PORT", "MCP_PATH", "MCP_HOST", "MCP_PORT")


def _reset_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    from blocksnet_mcp.settings import reset_mcp_settings

    for key in _TRANSPORT_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reset_mcp_settings()


def test_default_transport_is_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch)
    from blocksnet_mcp.settings import get_mcp_settings

    settings = get_mcp_settings()
    assert (settings.transport, settings.host, settings.port, settings.mcp_path) == (
        "stdio",
        "127.0.0.1",
        8001,
        "/mcp",
    )


def test_http_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, TRANSPORT="http", HOST="0.0.0.0", PORT="9001", MCP_PATH="/x")
    from blocksnet_mcp.settings import get_mcp_settings

    settings = get_mcp_settings()
    assert (settings.transport, settings.host, settings.port, settings.mcp_path) == (
        "http",
        "0.0.0.0",
        9001,
        "/x",
    )


def test_mcp_prefixed_host_port_are_synonyms(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, MCP_HOST="10.0.0.5", MCP_PORT="8002")
    from blocksnet_mcp.settings import get_mcp_settings

    settings = get_mcp_settings()
    assert (settings.host, settings.port) == ("10.0.0.5", 8002)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("stdio", "stdio"),
        ("", "stdio"),
        (None, "stdio"),
        ("http", "streamable-http"),
        ("HTTP", "streamable-http"),
        (" streamable-http ", "streamable-http"),
        ("streamable_http", "streamable-http"),
    ],
)
def test_resolve_transport(raw: str | None, expected: str) -> None:
    from blocksnet_mcp.server import resolve_transport

    assert resolve_transport(raw) == expected


def test_resolve_transport_rejects_unknown() -> None:
    from blocksnet_mcp.server import resolve_transport

    with pytest.raises(ValueError, match="TRANSPORT"):
        resolve_transport("sse")


class _RunRecorder:
    def __init__(self) -> None:
        self.transports: list[str] = []

    def run(self, transport: str) -> None:
        self.transports.append(transport)


def test_main_runs_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, TRANSPORT="http")
    import blocksnet_mcp.server as server

    recorder = _RunRecorder()
    monkeypatch.setattr(server, "get_mcp", lambda: recorder)
    server.main()
    assert recorder.transports == ["streamable-http"]


def test_main_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch)
    import blocksnet_mcp.server as server

    recorder = _RunRecorder()
    monkeypatch.setattr(server, "get_mcp", lambda: recorder)
    server.main()
    assert recorder.transports == ["stdio"]


def test_main_fails_fast_on_unknown_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, TRANSPORT="sse")
    import blocksnet_mcp.server as server

    recorder = _RunRecorder()
    monkeypatch.setattr(server, "get_mcp", lambda: recorder)
    with pytest.raises(ValueError):
        server.main()
    assert recorder.transports == []


def test_health_payload_reports_data_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset_settings(
        monkeypatch,
        TRANSPORT="http",
        DATA_DIR=str(tmp_path),
        OUTPUT_DIR=str(tmp_path / "outputs"),
    )
    from blocksnet_mcp.server import health_payload

    degraded = health_payload()
    assert degraded["status"] == "degraded"
    assert degraded["data_ready"] is False
    assert degraded["transport"] == "streamable-http"
    assert degraded["data_dir"] == str(tmp_path)

    assert degraded["datasets"] == []

    # Сценарный layout: data_dir/<scenario_id>/{gpkg,pickle}.
    scenario = tmp_path / "saint_petersburg"
    scenario.mkdir()
    (scenario / "blocks_with_services.gpkg").write_bytes(b"gpkg")
    assert health_payload()["data_ready"] is False  # pickle ещё нет
    (scenario / "acc_mx.pickle").write_bytes(b"pickle")
    ready = health_payload()
    assert (ready["status"], ready["data_ready"], ready["datasets"]) == (
        "ok",
        True,
        ["saint_petersburg"],
    )

    # Одиночный layout: файлы прямо в DATA_DIR — тоже считается готовым.
    (tmp_path / "blocks_with_services.gpkg").write_bytes(b"gpkg")
    (tmp_path / "acc_mx.pickle").write_bytes(b"pickle")
    assert health_payload()["datasets"] == sorted([tmp_path.name, "saint_petersburg"])


def test_health_payload_survives_missing_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Несуществующий DATA_DIR — degraded, а не 500 на /health."""
    _reset_settings(
        monkeypatch,
        DATA_DIR=str(tmp_path / "absent"),
        OUTPUT_DIR=str(tmp_path / "outputs"),
    )
    from blocksnet_mcp.server import health_payload

    payload = health_payload()
    assert (payload["status"], payload["data_ready"], payload["datasets"]) == ("degraded", False, [])


def test_health_route_is_part_of_http_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch)
    from mcp.server.fastmcp import FastMCP

    from blocksnet_mcp.server import _register_health_route

    server = FastMCP("blocksnet-test")
    _register_health_route(server)
    app = server.streamable_http_app()
    assert any(getattr(route, "path", None) == "/health" for route in app.routes)


def test_build_server_uses_network_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_build_server`` пробрасывает HOST/PORT/MCP_PATH в FastMCP (иначе http-режим
    слушает дефолтный 127.0.0.1:8000 и деплой по compose не отвечает на 8001)."""
    _reset_settings(monkeypatch, TRANSPORT="http", HOST="0.0.0.0", PORT="8001", MCP_PATH="/mcp")
    import blocksnet_mcp.server as server

    monkeypatch.setattr(server, "_register_catalog_tools", lambda mcp: None)
    monkeypatch.setattr(server, "_register_session_tools", lambda mcp: None)
    monkeypatch.setattr(server, "_register_agent_tool", lambda mcp: None)
    built = server._build_server()
    assert (built.settings.host, built.settings.port, built.settings.streamable_http_path) == (
        "0.0.0.0",
        8001,
        "/mcp",
    )
