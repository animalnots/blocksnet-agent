"""P0.2: in-memory MCP contract-тест — analyze_urban_question не падает транспортным ExceptionGroup.

Тесты не делают реальных LLM-вызовов: agent.run() заглушается через monkeypatch.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from blocksnet_agent import AgentResult, runtime
from blocksnet_mcp import agent_tool
from blocksnet_mcp.settings import MCPSettings


def test_analyze_urban_question_validation_returns_structured_response() -> None:
    """P0.2: ошибка валидации → структурированный dict, не raise."""
    from blocksnet_mcp.tools_mcp import analyze_urban_question

    result = analyze_urban_question("")
    assert isinstance(result, dict)
    assert result["status"] == "failed"
    assert result["error_code"] == "VALIDATION_ERROR"
    assert "question" in result["error"]


def test_analyze_urban_question_requires_positive_iterations() -> None:
    """P0.2: max_iterations=0 → структурированный failed, не raise."""
    from blocksnet_mcp.tools_mcp import analyze_urban_question

    result = analyze_urban_question("test", max_iterations=0)
    assert isinstance(result, dict)
    assert result["status"] == "failed"
    assert result["error_code"] == "VALIDATION_ERROR"


def test_progress_callback_accepted_without_llm(monkeypatch) -> None:
    """P0.2: progress callback регистрируется в start_run без реального LLM-вызова.

    Заглушаем BlocksNetAgent.run чтобы вернуть фиктивный результат — проверяем,
    что start_run принимает callback и не падает.
    """
    from blocksnet_mcp import tools_mcp
    from blocksnet_agent import BlocksNetAgent

    seen: list[tuple[int, int, str]] = []

    def cb(done: int, total: int, message: str) -> None:
        seen.append((done, total, message))

    # Заглушка: agent.run возвращает фиктивный AgentResult-like dict.
    class _FakeResult:
        output = "Fake result"
        run_id = "test-fake"
        run_dir = ""

    def _fake_run(self, task):
        return _FakeResult()

    monkeypatch.setattr(BlocksNetAgent, "run", _fake_run)

    result = tools_mcp.analyze_urban_question(
        "что разместить в квартале 3442?",
        max_iterations=1,
        progress_callback=cb,
    )
    assert isinstance(result, dict)
    assert "status" in result
    # callback мог не сработать (заглушка не вызывает инструменты), но регистрация прошла.
    # Если сработал — проверяем формат.
    for done, total, message in seen:
        assert isinstance(done, int)
        assert isinstance(message, str)


def test_agent_exception_returns_structured_failed(monkeypatch) -> None:
    """P0.2: исключение внутри агента → status=failed с error_code, не голая строка в isError."""
    from blocksnet_mcp import tools_mcp
    from blocksnet_agent import BlocksNetAgent

    def _boom_run(self, task):
        raise RuntimeError("LLM connection refused")

    monkeypatch.setattr(BlocksNetAgent, "run", _boom_run)

    result = tools_mcp.analyze_urban_question("test question", max_iterations=1)
    assert isinstance(result, dict)
    assert result["status"] == "failed"
    assert result["error_code"] == "AGENT_EXCEPTION"
    assert "LLM connection refused" in result["error"]


def test_agent_tool_on_a_reused_worker_thread_starts_its_own_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Оба вызова — на одном потоке, как у ``run_in_executor`` в server.py: RunContext
    лежит в ContextVar потока и переживает вызов, а дедлайн первого истекает до второго.
    """
    deadline_sec = 0.3
    settings = MCPSettings.model_construct(
        chat_url="http://test",
        api_key="test",
        model="test-model",
        data_dir=tmp_path,
        output_dir=tmp_path,
        max_iterations=1,
        deadline_sec=deadline_sec,
    )
    monkeypatch.setattr(agent_tool, "get_mcp_settings", lambda: settings)
    deadline_passed_at_start: list[bool] = []

    def _recording_run(self: Any, task: str) -> AgentResult:
        deadline_passed_at_start.append(runtime.is_deadline_reached())
        return AgentResult(output="готово", run_dir=str(runtime.get_run_context().run_dir))

    monkeypatch.setattr("blocksnet_agent.BlocksNetAgent.run", _recording_run)

    question = "Где не хватает школ?"
    with ThreadPoolExecutor(max_workers=1) as worker:
        first = worker.submit(agent_tool.analyze_urban_question, question).result(timeout=60.0)
        time.sleep(deadline_sec * 2)
        second = worker.submit(agent_tool.analyze_urban_question, question).result(timeout=60.0)

    assert second["run_dir"] != first["run_dir"]
    assert deadline_passed_at_start == [False, False]
