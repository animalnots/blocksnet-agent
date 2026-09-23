"""Реестр A2A-skills: ``run_pipeline`` и ``analyze_urban_question``.

Шаг 05 a2a-рефакторинга. Контракт:
- ``run_pipeline`` — основной skill. Создаёт задачу, стримит статусы,
  отдаёт артефакты.
- ``analyze_urban_question`` — back-compat обёртка: вызывает ``run_pipeline``
  и **блокирующе** ждёт терминального статуса, отдаёт финальный JSON.
  Никакой второй реализации pipeline (см. Q2 в open_questions.md).

Реестр возвращает ``list[SkillSpec]`` — плоский список для ``agent_card.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from blocksnet_agent.a2a.schemas import (
    AnalyzeUrbanQuestionInput,
    RunPipelineInput,
)


@dataclass(frozen=True)
class SkillSpec:
    """Описание одного skill для Agent Card + реестра."""

    id: str
    name: str
    description: str
    tags: tuple[str, ...]
    examples: tuple[str, ...]
    input_model: type
    # Сервер вызывает runner только по именам: input_payload, output_dir, data_dir,
    # deadline_sec, progress_cb, stop_event -> dict со status ("ok"|"partial"|"failed").
    runner: Any  # callable — тип намеренно Any, чтобы не возиться с Callable[...]


def _run_run_pipeline(
    input_payload: dict[str, Any],
    output_dir: Any,
    data_dir: Any,
    deadline_sec: int | None,
    progress_cb: Any,
    stop_event: Any,
) -> dict[str, Any]:
    """Реализация skill-а ``run_pipeline``.

    Считает прямо в потоке задачи TaskManager, которую завёл сервер. Если завести
    здесь вторую задачу в тот же пул и ждать её, запрос держит два потока, и пул
    встаёт навсегда, как только все его потоки заняты таким ожиданием.
    """
    from blocksnet_agent.a2a.executor import execute_run_pipeline

    inp = RunPipelineInput.model_validate(input_payload)
    return execute_run_pipeline(
        question=inp.question,
        max_iterations=inp.max_iterations,
        output_dir=output_dir,
        data_dir=data_dir,
        deadline_sec=deadline_sec,
        stop_event=stop_event,
        progress_cb=progress_cb,
        scenario_id=inp.scenario_id,
        project_id=inp.project_id,
    )


def _run_analyze_urban_question(
    input_payload: dict[str, Any],
    output_dir: Any,
    data_dir: Any,
    deadline_sec: int | None,
    progress_cb: Any,
    stop_event: Any,
) -> dict[str, Any]:
    """Реализация ``analyze_urban_question`` (back-compat).

    По сути — прокси для ``run_pipeline``. Никакой своей реализации pipeline
    (см. Q2): оба skill-а проходят через один ``execute_run_pipeline``.
    """
    inp = AnalyzeUrbanQuestionInput.model_validate(input_payload)
    # Преобразуем в ``RunPipelineInput`` (поля совпадают).
    return _run_run_pipeline(
        {
            "question": inp.question,
            "max_iterations": inp.max_iterations,
            "scenario_id": inp.scenario_id,
            "project_id": inp.project_id,
        },
        output_dir,
        data_dir,
        deadline_sec,
        progress_cb,
        stop_event,
    )


SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        id="run_pipeline",
        name="run_pipeline",
        description=(
            "Запускает полный аналитический конвейер BlocksNetAgent по "
            "городскому вопросу. Стримит статусы (submitted → working → "
            "completed/partial/failed), артефакты (карты, CSV), финальный "
            "JSON с гипотезами и рекомендациями."
        ),
        tags=("urban", "pipeline", "agent"),
        examples=(
            "Где в Кронштадте разместить новые спортивные площадки?",
            "Какие кварталы СПб имеют дефицит школ?",
        ),
        input_model=RunPipelineInput,
        runner=_run_run_pipeline,
    ),
    SkillSpec(
        id="analyze_urban_question",
        name="analyze_urban_question",
        description=(
            "[DEPRECATED] Back-compat обёртка над run_pipeline. "
            "Блокирующе ждёт терминального статуса. Используйте run_pipeline."
        ),
        tags=("urban", "legacy"),
        examples=("Где разместить новые школы?",),
        input_model=AnalyzeUrbanQuestionInput,
        runner=_run_analyze_urban_question,
    ),
)


def get_skill(skill_id: str) -> SkillSpec | None:
    for spec in SKILLS:
        if spec.id == skill_id:
            return spec
    return None


__all__ = ["SKILLS", "SkillSpec", "get_skill"]