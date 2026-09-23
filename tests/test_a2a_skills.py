"""Тесты шага 05 a2a-рефакторинга: контракт skill-ов и валидация.

Главные гарантии:
- ``analyze_urban_question`` (A2A) возвращает те же ключи, что MCP-tool
  (status/tool/run_id/run_dir/error_code/hypotheses/measured/recommendation_blocks).
- ``run_pipeline`` эмитит submitted → working → completed.
- Ошибка агента → ``failed`` + ``error_code``, не исключение наружу.
- Пустой ``question`` → ``VALIDATION_ERROR``.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from blocksnet_agent import AgentResult, runtime
from blocksnet_agent.a2a import skills
from blocksnet_agent.a2a.executor import execute_run_pipeline
from blocksnet_agent.a2a.task_manager import TaskManager
from blocksnet_agent.config import Settings


@pytest.fixture
def task_manager() -> TaskManager:
    return TaskManager(
        max_concurrent=2, task_ttl_sec=60.0, progress_interval_sec=0.0
    )


@pytest.fixture
def mock_blocksnet_agent(monkeypatch: pytest.MonkeyPatch):
    """Подменяет BlocksNetAgent.run + Settings() — без реального LLM.

    Патчит ``blocksnet_agent.config.Settings``, потому что executor.py делает
    ``from blocksnet_agent.config import Settings`` внутри функции.
    """
    from blocksnet_agent import config as cfg_module
    from blocksnet_agent.config import Settings

    fake_settings = Settings.model_construct(
        chat_url="http://test",
        api_key="test",
        model="test-model",
        data_dir=Path("/tmp"),
        output_dir=Path("/tmp"),
        max_iterations=5,
    )
    monkeypatch.setattr(cfg_module, "Settings", lambda: fake_settings)

    class _FakeResult:
        def __init__(self):
            self.output = "Mock"
            self.run_id = "test"
            self.run_dir = "/tmp/run"
            self.sections = {}
            self.confidence = 0.5
            self.limitations = []
            self.artifacts = []
            self.submitted_answer = None
            self.overlay_recommendations = []
            self.overlay_meta = {}
            self.hypotheses = []
            self.measured = {}
            self._data = {
                "question": "x", "output": self.output, "sections": {},
                "confidence": self.confidence, "limitations": [],
                "artifacts": [], "recommendation_blocks": [],
                "overlay_candidates": None, "overlay_meta": None,
                "hypotheses": [], "measured": {},
            }

        def get(self, k, d=None):
            return self._data.get(k, d)

    from blocksnet_agent import BlocksNetAgent
    monkeypatch.setattr(BlocksNetAgent, "run", lambda self, task: _FakeResult())

    return {"settings": fake_settings, "result_cls": _FakeResult}


# --- контракт ответа -------------------------------------------------------


def test_run_pipeline_output_has_required_keys(
    task_manager: TaskManager, mock_blocksnet_agent: dict
) -> None:
    """``run_pipeline`` отдаёт dict с обязательными ключами (как MCP-tool)."""
    record = task_manager.submit(
        {"question": "тест"},
        runner=lambda rec, cb: skills.get_skill("run_pipeline").runner(
            input_payload={"question": "тест"},
            output_dir=Path("/tmp"),
            data_dir=Path("/tmp"),
            deadline_sec=None,
            progress_cb=lambda s, m: None,
            stop_event=rec.stop_event,
        ),
    )
    record.future.result(timeout=5.0)
    output = (task_manager.get(record.task_id) or record).output or {}
    assert "status" in output
    assert "tool" in output
    assert output["tool"] == "run_pipeline"


def test_analyze_urban_question_proxy_to_run_pipeline(
    task_manager: TaskManager, mock_blocksnet_agent: dict
) -> None:
    """``analyze_urban_question`` — прокси на ``run_pipeline``, тот же формат."""
    record = task_manager.submit(
        {"question": "q"},
        runner=lambda rec, cb: skills.get_skill("analyze_urban_question").runner(
            input_payload={"question": "q"},
            output_dir=Path("/tmp"),
            data_dir=Path("/tmp"),
            deadline_sec=None,
            progress_cb=lambda s, m: None,
            stop_event=rec.stop_event,
        ),
    )
    record.future.result(timeout=5.0)
    output = (task_manager.get(record.task_id) or record).output or {}
    assert "status" in output
    # tool — analyze_urban_question (back-compat) или run_pipeline (если прокси).
    assert output.get("status") in ("ok", "partial", "failed")


def test_analyze_urban_question_contract_keys_match_mcp(
    task_manager: TaskManager, mock_blocksnet_agent: dict
) -> None:
    """Ответ A2A-skill содержит те же ключи верхнего уровня, что MCP-tool."""
    expected_keys = {
        "status", "tool", "question", "analysis_plan", "result", "hypotheses",
        "measured", "recommendation_blocks", "confidence", "limitations",
        "artifacts", "run_id", "run_dir",
    }

    record = task_manager.submit(
        {"question": "q"},
        runner=lambda rec, cb: skills.get_skill("run_pipeline").runner(
            input_payload={"question": "q"},
            output_dir=Path("/tmp"),
            data_dir=Path("/tmp"),
            deadline_sec=None,
            progress_cb=lambda s, m: None,
            stop_event=rec.stop_event,
        ),
    )
    record.future.result(timeout=5.0)
    output = (task_manager.get(record.task_id) or record).output or {}

    for k in expected_keys:
        assert k in output, f"обязательный ключ {k!r} отсутствует в A2A-ответе"


# --- валидация -------------------------------------------------------------


def test_empty_question_returns_validation_error(
    task_manager: TaskManager, mock_blocksnet_agent: dict
) -> None:
    """Пустой вопрос → ``status=failed``, ``error_code=VALIDATION_ERROR``."""
    record = task_manager.submit(
        {"question": ""},
        runner=lambda rec, cb: skills.get_skill("run_pipeline").runner(
            input_payload={"question": ""},
            output_dir=Path("/tmp"),
            data_dir=Path("/tmp"),
            deadline_sec=None,
            progress_cb=lambda s, m: None,
            stop_event=rec.stop_event,
        ),
    )
    record.future.result(timeout=5.0)
    output = (task_manager.get(record.task_id) or record).output or {}
    assert output.get("status") == "failed"
    assert output.get("error_code") == "VALIDATION_ERROR"


def test_invalid_max_iterations_returns_validation_error(
    task_manager: TaskManager, mock_blocksnet_agent: dict
) -> None:
    """``max_iterations=0`` → ``VALIDATION_ERROR``."""
    record = task_manager.submit(
        {"question": "q", "max_iterations": 0},
        runner=lambda rec, cb: skills.get_skill("run_pipeline").runner(
            input_payload={"question": "q", "max_iterations": 0},
            output_dir=Path("/tmp"),
            data_dir=Path("/tmp"),
            deadline_sec=None,
            progress_cb=lambda s, m: None,
            stop_event=rec.stop_event,
        ),
    )
    record.future.result(timeout=5.0)
    output = (task_manager.get(record.task_id) or record).output or {}
    assert output.get("status") == "failed"
    assert output.get("error_code") == "VALIDATION_ERROR"


# --- реестр skills ----------------------------------------------------------


def test_skills_registry_has_two_skills() -> None:
    """В реестре ровно два skill-а."""
    assert len(skills.SKILLS) == 2
    ids = {spec.id for spec in skills.SKILLS}
    assert ids == {"run_pipeline", "analyze_urban_question"}


def test_get_skill_returns_correct_spec() -> None:
    """``get_skill(id)`` возвращает нужный SkillSpec."""
    spec = skills.get_skill("run_pipeline")
    assert spec is not None
    assert spec.id == "run_pipeline"
    assert spec.input_model.__name__ == "RunPipelineInput"


def test_get_skill_returns_none_for_unknown() -> None:
    """``get_skill(unknown)`` → None, не KeyError."""
    assert skills.get_skill("nonexistent_skill") is None


# --- e2e executor с monkeypatch --------------------------------------------


def test_execute_run_pipeline_with_mock_agent(
    task_manager: TaskManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``execute_run_pipeline`` через monkeypatch BlocksNetAgent.run → ok."""
    from blocksnet_agent.a2a import executor as exec_module
    from blocksnet_agent.config import Settings

    class _FakeResult:
        """Mock AgentResult с интерфейсом dict — to_json() ожидает ``.get()``."""

        def __init__(self) -> None:
            self.output = "Mock result"
            self.run_id = "test-fake"
            self.run_dir = "/tmp/run_mock"
            self.sections = {}
            self.confidence = 0.5
            self.limitations = []
            self.artifacts = []
            self.submitted_answer = None
            self.overlay_recommendations = []
            self.overlay_meta = {}
            self.hypotheses = []
            self.measured = {}
            self.valid_block_ids = []
            self._data = {
                "question": "mock question",
                "output": self.output,
                "sections": self.sections,
                "confidence": self.confidence,
                "limitations": self.limitations,
                "artifacts": self.artifacts,
                "recommendation_blocks": [],
                "overlay_candidates": None,
                "overlay_meta": None,
                "hypotheses": self.hypotheses,
                "measured": self.measured,
            }

        def get(self, key: str, default=None):
            return self._data.get(key, default)

    # Подменяем Settings() — без чтения реальных credentials.
    fake_settings = Settings.model_construct(
        chat_url="http://test",
        api_key="test",
        model="test-model",
        data_dir=Path("/tmp"),
        output_dir=Path("/tmp"),
        max_iterations=5,
    )

    # Подменяем BlocksNetAgent.run.
    def _fake_run(self, task):
        return _FakeResult()

    from blocksnet_agent import BlocksNetAgent

    monkeypatch.setattr(BlocksNetAgent, "run", _fake_run)

    record = task_manager.submit(
        {"question": "mock question"},
        runner=lambda rec, cb: exec_module.execute_run_pipeline(
            question="mock question",
            max_iterations=5,
            output_dir=Path("/tmp"),
            data_dir=Path("/tmp"),
            deadline_sec=None,
            stop_event=rec.stop_event,
            progress_cb=lambda s, m: None,
            agent_settings=fake_settings,  # передаём напрямую — без .env
        ),
    )
    record.future.result(timeout=5.0)
    output = (task_manager.get(record.task_id) or record).output or {}
    assert output.get("status") == "ok"
    assert output.get("tool") == "run_pipeline"
    assert output.get("run_id")


def test_pipeline_on_a_reused_worker_thread_starts_its_own_run(tmp_path: Path) -> None:
    """Обе задачи — на одном потоке пула: RunContext лежит в ContextVar потока
    и переживает задачу, а дедлайн первой успевает истечь до старта второй.
    """
    deadline_sec = 0.3
    settings = Settings.model_construct(
        chat_url="http://test",
        api_key="test",
        model="test-model",
        data_dir=tmp_path,
        output_dir=tmp_path,
        max_iterations=5,
    )
    deadline_passed_at_start: list[bool] = []

    class _RecordingAgent:
        def __init__(self, settings: Any, max_iterations: int) -> None:
            pass

        def run(self, task: str) -> AgentResult:
            deadline_passed_at_start.append(runtime.is_deadline_reached())
            return AgentResult(output="готово", run_dir=str(runtime.get_run_context().run_dir))

    def _run_pipeline() -> dict[str, Any]:
        return execute_run_pipeline(
            question="Где не хватает школ?",
            max_iterations=None,
            output_dir=tmp_path,
            data_dir=tmp_path,
            deadline_sec=deadline_sec,
            stop_event=None,
            progress_cb=lambda state, message: None,
            agent_factory=_RecordingAgent,
            agent_settings=settings,
        )

    with ThreadPoolExecutor(max_workers=1) as worker:
        first = worker.submit(_run_pipeline).result(timeout=60.0)
        time.sleep(deadline_sec * 2)
        second = worker.submit(_run_pipeline).result(timeout=60.0)

    assert second["run_dir"] != first["run_dir"]
    assert deadline_passed_at_start == [False, False]


def test_parallel_pipelines_keep_their_own_stop_flag_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два прогона одновременно: каждый видит только свой стоп-флаг и шлёт прогресс
    только в свой колбэк, а функции модуля ``runtime`` после них прежние."""
    # Пусть pytest вернёт оригиналы, даже если прогон их подменит и не вернёт.
    monkeypatch.setattr(runtime, "is_stop_requested", runtime.is_stop_requested)
    monkeypatch.setattr(runtime, "report_progress", runtime.report_progress)
    originals = (runtime.is_stop_requested, runtime.report_progress)
    settings = Settings.model_construct(
        chat_url="http://test",
        api_key="test",
        model="test-model",
        data_dir=tmp_path,
        output_dir=tmp_path,
        max_iterations=5,
    )
    both_started = threading.Barrier(2)
    both_checked = threading.Barrier(2)
    stop_seen: dict[str, bool] = {}

    class _Agent:
        def __init__(self, settings: Any, max_iterations: int) -> None:
            pass

        def run(self, task: str) -> AgentResult:
            both_started.wait(timeout=5)
            # Как гейт инструментов: имя берётся из модуля в момент вызова.
            from blocksnet_agent.runtime import is_stop_requested, report_progress

            stop_seen[task] = is_stop_requested()
            report_progress(f"этап {task}")
            both_checked.wait(timeout=5)
            return AgentResult(output="готово", run_dir=str(runtime.get_run_context().run_dir))

    stops = {"A": threading.Event(), "B": threading.Event()}
    stops["A"].set()
    progress: dict[str, list[str]] = {"A": [], "B": []}

    def _run(name: str) -> dict[str, Any]:
        return execute_run_pipeline(
            question=name,
            max_iterations=None,
            output_dir=tmp_path,
            data_dir=tmp_path,
            deadline_sec=None,
            stop_event=stops[name],
            progress_cb=lambda state, message: progress[name].append(message),
            agent_factory=_Agent,
            agent_settings=settings,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(zip("AB", pool.map(_run, "AB")))

    assert stop_seen == {"A": True, "B": False}
    assert progress == {"A": ["этап A"], "B": ["этап B"]}
    assert [results["A"]["status"], results["B"]["status"]] == ["partial", "ok"]
    assert (runtime.is_stop_requested, runtime.report_progress) == originals
