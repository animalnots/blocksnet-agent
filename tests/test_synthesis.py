"""Контрактные тесты финального структурного синтеза.

Покрывают 5 кейсов:

1. ``full_data`` — синтез с verified гипотезами и проверенными observation;
2. ``partial`` — есть observations, но без verified гипотез;
3. ``all_failed`` — все observation с failure-маркерами (degraded fallback);
4. ``submit_answer_override`` — ``submitted_answer`` уже есть, синтез всё равно
   прикладывается как второй слой (полезно для downstream-MAS);
5. ``llm_error_fallback`` — LLM-вызов кидает исключение, ожидаем fallback.

Тесты локальные: реальный LLM не вызывается (стаб через ``monkeypatch``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch


from blocksnet_agent.hypotheses import Hypothesis, HypothesisLedger
from blocksnet_agent.synthesis import (
    FinalSynthesis,
    collect_evidence,
    synthesize,
    write_synthesis,
)
from blocksnet_mcp.serialize import to_json


def _make_ledger(*statuses: str, claim: str = "claim") -> HypothesisLedger:
    items = [
        Hypothesis(
            id=f"H{i + 1}",
            claim=f"{claim} #{i + 1}",
            prediction="prediction",
            test="test",
            status=status,
            evidence=f"evidence for {status}",
        )
        for i, status in enumerate(statuses)
    ]
    return HypothesisLedger(hypotheses=items)


def _good_steps() -> list[dict[str, str]]:
    return [
        {
            "tool": "compute_service_provision",
            "tool_input": '{"service_type":"school"}',
            "observation": "service provision = 0.42 (city)",
        },
        {
            "tool": "get_block_info",
            "tool_input": '{"block_id":603}',
            "observation": "block 603: population 1234, services 5",
        },
    ]


def test_collect_evidence_keeps_verified_hypotheses() -> None:
    observations, verified, limitations = collect_evidence(
        _good_steps(),
        _make_ledger("supported", "refuted", "inconclusive"),
    )
    assert len(observations) == 2
    assert observations[0]["tool"] == "compute_service_provision"
    assert observations[0]["citation"].startswith("[compute_service_provision")
    # ``supported`` и ``refuted`` — обе «verified»; ``inconclusive`` — нет.
    assert len(verified) == 2
    assert {h.status for h in verified} == {"supported", "refuted"}
    assert any("inconclusive" in lim for lim in limitations)


def test_collect_evidence_dedups_failed_observations() -> None:
    steps = [
        {
            "tool": "compute_mean_accessibility",
            "tool_input": "{}",
            "observation": "Ошибка: файл не найден",
        },
        {
            "tool": "list_cached_data",
            "tool_input": "{}",
            "observation": "Кэш пуст",
        },
    ]
    observations, verified, limitations = collect_evidence(steps)
    assert observations == []
    assert verified == []
    # Один из failure-маркеров триггерит сообщение в limitations.
    assert any("compute_mean_accessibility" in lim for lim in limitations)


def test_synthesize_full_data_returns_seven_sections(tmp_path: Path) -> None:
    """Кейс 1: full data — синтез с verified гипотезами и наблюдениями."""
    fake_markdown = (
        "## Ответ\nРазместить спортплощадку в квартале 603.\n\n"
        "## Как читаю вопрос\nВопрос интерпретирован буквально.\n\n"
        "## На чём держится ответ\nНа измерениях [compute_service_provision].\n\n"
        "## Варианты, которые взвешивал\nАльтернатива X отвергнута.\n\n"
        "## Аргумент «за»\n[compute_service_provision] показал 0.42; [get_block_info(block_id=603)] population 1234.\n\n"
        "## Что осталось неопределённым\nМинимальная ёмкость спортплощадки.\n\n"
        "## Где это рассуждение может быть ошибочным\nЕсли модель блоков устарела — strong_before завышен.\n"
    )
    with patch("blocksnet_agent.synthesis.get_chat_model") as mock_llm:
        mock_llm.return_value.invoke.return_value.content = fake_markdown
        synthesis = synthesize(
            "Где разместить спортплощадку?",
            _good_steps(),
            _make_ledger("supported"),
        )
    assert synthesis.fallback_used is False
    assert len(synthesis.sections) == 7
    # Все 7 заголовков ожидаемы.
    expected_titles = [
        "Ответ",
        "Как читаю вопрос",
        "На чём держится ответ",
        "Варианты, которые взвешивал",
        "Аргумент «за»",
        "Что осталось неопределённым",
        "Где это рассуждение может быть ошибочным",
    ]
    assert [t for t, _ in synthesis.sections] == expected_titles
    citations = synthesis.citations
    assert "[compute_service_provision]" in citations
    assert any("[get_block_info" in c for c in citations)
    # Markdown сохраняется в файл.
    path = write_synthesis(str(tmp_path / "run"), synthesis)
    assert path is not None
    out = Path(path).read_text(encoding="utf-8")
    assert "## Вопрос" in out
    assert "## Ответ" in out


def test_synthesize_partial_no_verified_hypotheses(tmp_path: Path) -> None:
    """Кейс 2: partial — есть observations, но гипотезы не verified."""
    fake_markdown = (
        "## Ответ\nНет проверенных данных; модель не смогла подтвердить гипотезы.\n\n"
        "## Как читаю вопрос\nВопрос интерпретирован строго по тексту.\n\n"
        "## На чём держится ответ\nТолько на единичных observation без численных сравнений.\n\n"
        "## Варианты, которые взвешивал\nНикаких альтернатив.\n\n"
        "## Аргумент «за»\n[compute_service_provision] = 0.42.\n\n"
        "## Что осталось неопределённым\nВсе before→after сравнения.\n\n"
        "Где это рассуждение может быть ошибочным\nМалый объём наблюдений; один результат — не репрезентативен.\n"
    )
    with patch("blocksnet_agent.synthesis.get_chat_model") as mock_llm:
        mock_llm.return_value.invoke.return_value.content = fake_markdown
        synthesis = synthesize("Test partial", _good_steps(), _make_ledger("inconclusive"))
    assert synthesis.fallback_used is False
    # Нет verified гипотез.
    assert synthesis.verified_hypotheses == []
    # Ограничение должно упомянуть inconclusive.
    assert any("inconclusive" in lim for lim in synthesis.limitations)


def test_synthesize_all_failed_uses_fallback(tmp_path: Path) -> None:
    """Кейс 3: все observations failed → fallback без LLM-вызова."""
    failed_steps = [
        {"tool": "compute_mean_accessibility", "tool_input": "{}", "observation": "Ошибка: путь"},
        {"tool": "list_cached_data", "tool_input": "{}", "observation": "Exception raised"},
    ]
    synthesis = synthesize("Test fallback", failed_steps, _make_ledger())
    assert synthesis.fallback_used is True
    # Хотя бы секция «Ответ» заполнена (явный сигнал недостаточности данных).
    answer_body = synthesis.sections[0][1]
    assert "0.30" in answer_body or "недостаточно" in answer_body.lower() or "недостаточно данных" in answer_body.lower()
    # Ограничения присутствуют.
    assert any("LLM-вызов" in lim or "деградированный" in lim for lim in synthesis.limitations)


def test_synthesize_handles_llm_exception() -> None:
    """Кейс 4: LLM-вызов бросает исключение → fallback без падения."""
    with patch(
        "blocksnet_agent.synthesis.get_chat_model",
        side_effect=RuntimeError("LLM недоступен"),
    ):
        synthesis = synthesize("Test", _good_steps(), _make_ledger("supported"))
    assert synthesis.fallback_used is True
    assert synthesis.sections  # не пусто


def test_synthesize_too_short_response_triggers_fallback() -> None:
    """Слишком короткий ответ модели (<40 символов) → fallback."""
    with patch("blocksnet_agent.synthesis.get_chat_model") as mock_llm:
        mock_llm.return_value.invoke.return_value.content = "ок"
        synthesis = synthesize("Test", _good_steps(), _make_ledger("supported"))
    assert synthesis.fallback_used is True


def test_to_json_attaches_synthesis_in_salvaged_path(tmp_path: Path) -> None:
    """Кейс 5 (serialize): синтез прикладывается и в salvage-пути."""
    run_dir = tmp_path / "run_20260723"
    run_dir.mkdir()
    fake_synthesis = FinalSynthesis(
        question="Q",
        sections=[("Ответ", "...")] + [(t, "") for t in [
            "Как читаю вопрос",
            "На чём держится ответ",
            "Варианты, которые взвешивал",
            "Аргумент «за»",
            "Что осталось неопределённым",
            "Где это рассуждение может быть ошибочным",
        ]],
        citations=["[compute_service_provision]"],
        limitations=[],
        fallback_used=False,
    )
    fake_synthesis_md = fake_synthesis.to_markdown()
    result: dict[str, Any] = {
        "input": "Q",
        "output": "",
        "run_dir": str(run_dir),
        "confidence": 0.42,
        "limitations": [],
        "sections": {
            "SYNTHESIS_MARKDOWN": fake_synthesis_md,
            "SYNTHESIS_PATH": str(run_dir / "synthesis.md"),
            "SYNTHESIS_FALLBACK": "false",
        },
        "submitted_answer": None,
        "overlay_recommendations": [],
        "overlay_meta": {},
        "confidence_basis": [],
        "valid_block_ids": [],
        "synthesis": fake_synthesis,
        "synthesis_path": str(run_dir / "synthesis.md"),
    }
    payload = to_json(result)
    # Синтез присутствует в обоих полях.
    assert payload["synthesis"] == fake_synthesis_md
    assert payload["synthesis_citations"] == ["[compute_service_provision]"]
    assert payload["synthesis_fallback"] is False
    # Salvaged-флаг стоит (нет submitted_answer).
    assert payload["salvaged"] is True


def test_to_json_attach_synthesis_when_missing_does_not_crash() -> None:
    """Back-compat: если у AgentResult нет synthesis — payload получает пустые дефолты."""
    result: dict[str, Any] = {
        "input": "Q",
        "output": "",
        "run_dir": "",
        "confidence": 0.42,
        "limitations": [],
        "sections": {},
        "submitted_answer": None,
        "overlay_recommendations": [],
        "overlay_meta": {},
        "confidence_basis": [],
        "valid_block_ids": [],
        # Намеренно нет 'synthesis' и 'synthesis_path'.
    }
    payload = to_json(result)
    assert payload["synthesis"] == ""
    assert payload["synthesis_citations"] == []
    assert payload["synthesis_path"] == ""
    assert payload["synthesis_fallback"] is False
