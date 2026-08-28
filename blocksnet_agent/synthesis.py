"""
Финальный структурный синтез ответа BlocksNetAgent.

Раньше финальный блок либо писался сырым текстом по 9 секциям из
``SYSTEM_PROMPT`` (разбираемый regex), либо собирался ad-hoc в ``_finalize``
только при выходе по iteration-limit. Это давало Final Answer в стиле
«дамп графа» с ``ANALYSIS PLAN: ... HYPOTHESES: - id: H1; claim: ...; status:
inconclusive``.

Здесь ``synthesize(...)`` всегда вызывается после замены Refine+Hypothesis-
reentry, отбирает «прошедшие» факты (tool_observations без failure-маркеров +
гипотезы со статусом ``supported``) и формирует 7-секционный decision memo
через отдельный LLM-вызов. Результат — русский markdown, сохраняемый в
``run_dir/synthesis.md`` и прикладываемый к ``payload["synthesis"]``.

Структура ответа:
1. Ответ (committed conclusion)
2. Как читаю вопрос
3. На чём держится
4. Варианты, которые взвешивал
5. Аргумент «за»
6. Что осталось неопределённым
7. Где это рассуждение может быть ошибочным
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from blocksnet_agent.llm import get_chat_model
from blocksnet_agent.hypotheses import Hypothesis, HypothesisLedger

_LOG = logging.getLogger("blocksnet_agent.synthesis")

# Те же маркеры, что в ``agent.py:49``: используем для отсечения мусорных
# наблюдений (``Ошибка:``/``Traceback``/``не найден`` и т.п.).
_FAILURE_MARKERS = (
    "Ошибка:",
    "Traceback",
    "Exception",
    "not found",
    "не найден",
    "NO_DATA",
    "REPEATED_FAILED_CALL",
)

# Пороги для promotion в финальный синтез отражают уже подсчитанный
# confidence на нижних слоях: гипотезы ``supported``/``refuted`` прошли
# numerical test, ``inconclusive`` — нет.
_VERIFIED_HYPOTHESIS_STATUSES = frozenset({"supported", "refuted"})

# Минимальная длина observation, чтобы она попала в synthesis-контекст.
_MIN_OBSERVATION_LEN = 24


@dataclass
class FinalSynthesis:
    """Результат синтеза — структурный ответ для downstream-потребителей.

    Attributes:
        question: исходный вопрос пользователя.
        sections: упорядоченный список (title, body) markdown-секций.
        citations: список ссылок ``[source]``, вставленных моделью в тексте.
        limitations: список ограничений, выявленных в ходе синтеза.
        fallback_used: True, если LLM-вызов не удался и использован
            деградированный путь (структура извлечена из observations).
        verified_hypotheses: гипотезы со статусом, прошедшим проверку.
    """

    question: str
    sections: list[tuple[str, str]] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    fallback_used: bool = False
    verified_hypotheses: list[Hypothesis] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Сборка финального markdown из 7 секций."""
        parts: list[str] = []
        if self.question:
            parts.append(f"## Вопрос\n\n{self.question}\n")
        # Заголовки секций — на русском, как и весь user-facing вывод.
        section_titles = [
            "Ответ",
            "Как читаю вопрос",
            "На чём держится ответ",
            "Варианты, которые взвешивал",
            "Аргумент «за»",
            "Что осталось неопределённым",
            "Где это рассуждение может быть ошибочным",
        ]
        for (title, body), expected in zip(self.sections, section_titles):
            parts.append(f"## {expected}\n\n{body.strip()}\n")
        if self.limitations:
            parts.append("## Ограничения\n\n" + "\n".join(f"- {x}" for x in self.limitations) + "\n")
        if self.fallback_used:
            parts.append(
                "\n> ⚠ Синтез выполнен в деградированном режиме: LLM-вызов не "
                "удался, структура восстановлена из наблюдений и гипотез.\n"
            )
        return "\n".join(parts).strip()


_SYNTHESIS_SYSTEM = """\
Ты — старший аналитик городской среды, формируешь финальный decision memo по
результатам работы BlocksNetAgent. На входе: вопрос пользователя, набор
проверенных наблюдений из урбан-инструментов и статусы гипотез (supported /
refuted / inconclusive) с обоснованием. Все факты — из локальной модели города
(blocksnet). Никаких внешних источников.

Правила:
1. Output — на русском, в markdown, РОВНО с 7 секциями в этом порядке и этих
   заголовках:
   ## Ответ
   ## Как читаю вопрос
   ## На чём держится ответ
   ## Варианты, которые взвешивал
   ## Аргумент «за»
   ## Что осталось неопределённым
   ## Где это рассуждение может быть ошибочным
2. Секция «Ответ» — это committed conclusion в 1-3 предложениях: дай
   конкретный ответ на вопрос пользователя (где/какие/сколько/что делать).
   Не отвечай «необходимо проанализировать» или «нужны дополнительные данные»
   вместо ответа; даже при частичных данных выбери single best-supported
   вывод и зафиксируй его. Confidence ответа (0.0-1.0) укажи в скобках в
   конце секции «Ответ», например: «... (доверие 0.62)».
3. Цитируй конкретные числа и block_id из проверенных наблюдений в квадратных
   скобках с префиксом источника: ``[compute_service_provision]``,
   ``[get_block_info(block_id=603)]``, ``[compute_scenario_provision]`` и т.п.
   Это даёт читателю нить к наблюдению. Источник — имя инструмента.
4. В «Аргумент «за»» собери все поддерживающие наблюдения по ответам. Если
   гипотеза refuted — упомяни и это: это честно и укрепляет доверие.
5. В «Где это рассуждение может быть ошибочным» не повторяй uncertainty —
   это reasoning-level self-assessment: отвергнутые альтернативные framings,
   calibrated confidence для 2-3 нагруженных утверждений с указанием где
   каждое может быть неверным, источниковый сдвиг (какие инструменты не
   вызывались и почему это могло сместить вывод).
6. Не выдумывай чисел и block_id — бери только из проверенных наблюдений.
   Если проверенных наблюдений по нужному утверждению нет — пиши это явно
   в «Что осталось неопределённым».
7. Не добавляй секций сверх 7. Не вставляй блоки ANALYSIS PLAN/HYPOTHESES/
   RESULT — это дампы нижнего слоя, клиенту они не нужны.
"""


def _is_failed_observation(observation: str) -> bool:
    text = (observation or "").strip()
    if len(text) < _MIN_OBSERVATION_LEN:
        return True
    return any(text.startswith(m) for m in _FAILURE_MARKERS)


def collect_evidence(
    steps: list[dict[str, str]],
    hypothesis_ledger: HypothesisLedger | None = None,
) -> tuple[list[dict[str, Any]], list[Hypothesis], list[str]]:
    """Отобрать факты, прошедшие promotion-порог.

    Возвращает кортеж ``(observations, verified_hypotheses, limitations)``:
    * ``observations`` — список dict ``{tool, args, summary, citation}``:
      наблюдения без failure-маркеров, dedup по сигнатуре ``tool+args``;
    * ``verified_hypotheses`` — гипотезы со статусом ``supported``/
      ``refuted`` (прошли численный тест);
    * ``limitations`` — человекочитаемые ограничения (refuted + inconclusive
      гипотезы, плюс failed observations одной строкой).
    """
    observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    limitations: list[str] = []

    for step in steps or []:
        tool = str(step.get("tool", "")).strip()
        tool_input = str(step.get("tool_input", "")).strip()
        observation = str(step.get("observation", "")).strip()
        if not tool:
            continue
        if _is_failed_observation(observation):
            limitations.append(f"инструмент {tool} вернул ошибку или пустой результат")
            continue
        key = (tool, tool_input[:120])
        if key in seen:
            continue
        seen.add(key)
        observation_snip = observation[:600]
        citation = f"[{tool}]" if not tool_input else f"[{tool}({tool_input[:60]})]"
        observations.append(
            {
                "tool": tool,
                "args": tool_input,
                "summary": observation_snip,
                "citation": citation,
            }
        )

    verified: list[Hypothesis] = []
    if hypothesis_ledger is not None:
        for item in hypothesis_ledger.hypotheses:
            status = item.status
            if status in _VERIFIED_HYPOTHESIS_STATUSES:
                verified.append(item)
            elif status == "inconclusive":
                limitations.append(
                    f"гипотеза {item.id}: '{item.claim[:80]}' — inconclusive: {item.evidence[:120]}"
                )
    return observations, verified, limitations


def _build_synthesis_prompt(
    question: str,
    observations: list[dict[str, Any]],
    verified: list[Hypothesis],
) -> str:
    obs_lines = [
        f"- {o['citation']}: {o['summary']}" for o in observations[:40]
    ]
    hyp_lines = []
    for item in verified[:30]:
        verdict_word = "подтверждена" if item.status == "supported" else "опровергнута"
        hyp_lines.append(
            f"- {item.id} ({verdict_word}): claim='{item.claim[:140]}', evidence='{item.evidence[:160]}'"
        )
    obs_block = "\n".join(obs_lines) if obs_lines else "(нет проверенных наблюдений)"
    hyp_block = "\n".join(hyp_lines) if hyp_lines else "(гипотез, прошедших проверку, нет)"
    return (
        f"Вопрос:\n{question.strip() or '(не задан)'}\n\n"
        f"Проверенные наблюдения инструментов:\n{obs_block}\n\n"
        f"Проверенные гипотезы:\n{hyp_block}\n"
    )


_CITATION_RE = re.compile(r"\[([^\[\]]+)\]")


def _split_sections(model_text: str) -> list[tuple[str, str]]:
    """Разобрать 7 секций из ответа LLM.

    Модель может:
    * следовать точно 7 заголовкам ``## Ответ`` … ``## Где это рассуждение
      может быть ошибочным``;
    * опускать заголовки, переставлять порядок, выдавать сплошной текст.

    Подход: ищем все markdown-header'ы второго уровня; первые 7 становятся
    секциями, остаток (если заголовков меньше 7) — последней секцией. Если
    заголовков нет — текст целиком идёт в секцию «Ответ», остальные пустые.
    """
    expected_titles = [
        "Ответ",
        "Как читаю вопрос",
        "На чём держится ответ",
        "Варианты, которые взвешивал",
        "Аргумент «за»",
        "Что осталось неопределённым",
        "Где это рассуждение может быть ошибочным",
    ]
    headers = list(re.finditer(r"^##\s+(.+?)\s*$", model_text, flags=re.MULTILINE))
    if not headers:
        return [(expected_titles[0], model_text.strip())] + [
            (t, "") for t in expected_titles[1:]
        ]
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(headers[:7]):
        title = match.group(1).strip()
        start = match.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(model_text)
        body = model_text[start:end].strip()
        sections.append((title, body))
    while len(sections) < 7:
        sections.append((expected_titles[len(sections)], ""))
    return sections[:7]


def _extract_citations(model_text: str) -> list[str]:
    """Собрать уникальные ``[source]`` ссылки из текста синтеза."""
    seen: list[str] = []
    for match in _CITATION_RE.finditer(model_text):
        token = f"[{match.group(1).strip()}]"
        if token not in seen:
            seen.append(token)
    return seen


def _fallback_synthesis(
    question: str,
    observations: list[dict[str, Any]],
    verified: list[Hypothesis],
    limitations: list[str],
) -> FinalSynthesis:
    """Деградированный путь: LLM недоступен — собрать структуру из сырых данных."""
    if not observations and not verified:
        answer_body = (
            "Недостаточно данных для ответа. Все инструменты завершились "
            "ошибкой или не были вызваны. (доверие 0.30)"
        )
        sections: list[tuple[str, str]] = [(t, "") for t in [
            "Ответ",
            "Как читаю вопрос",
            "На чём держится ответ",
            "Варианты, которые взвешивал",
            "Аргумент «за»",
            "Что осталось неопределённым",
            "Где это рассуждение может быть ошибочным",
        ]]
        sections[0] = ("Ответ", answer_body)
        return FinalSynthesis(
            question=question,
            sections=sections,
            limitations=limitations + ["деградированный синтез: LLM-вызов пропущен"],
            fallback_used=True,
        )

    answer_lines = [
        f"Ответ основан на {len(observations)} наблюдениях и {len(verified)} проверенных гипотезах.",
    ]
    if verified:
        verdict_lines = [
            f"- {item.id}: {item.claim[:200]} — {item.status} (evidence: {item.evidence[:120]})"
            for item in verified[:6]
        ]
        answer_lines.append("Подтверждённые/опровергнутые гипотезы:")
        answer_lines.extend(verdict_lines)
    observations_text = "\n".join(
        f"- {o['citation']}: {o['summary']}" for o in observations[:12]
    )
    sections = [
        ("Ответ", " ".join(answer_lines) + " (доверие 0.50)"),
        (
            "Как читаю вопрос",
            "Вопрос интерпретирован строго по тексту; домен — локальная урбан-модель в data/.",
        ),
        (
            "На чём держится ответ",
            "На фактически выполненных вызовах инструментов и численных проверках гипотез.",
        ),
        (
            "Варианты, которые взвешивал",
            "Альтернативные интерпретации вопроса не рассматривались — обзор одной формулировки.",
        ),
        ("Аргумент «за»", observations_text or "Проверенных наблюдений нет."),
        (
            "Что осталось неопределённым",
            "Полный синтез не выполнен (LLM-вызов не удался). Приведены только сырые наблюдения.",
        ),
        (
            "Где это рассуждение может быть ошибочным",
            "Решение опирается на сырые наблюдения без weighing альтернатив.",
        ),
    ]
    return FinalSynthesis(
        question=question,
        sections=sections,
        citations=[o["citation"] for o in observations],
        limitations=limitations + ["деградированный синтез: LLM-вызов пропущен"],
        fallback_used=True,
        verified_hypotheses=verified,
    )


def synthesize(
    question: str,
    steps: list[dict[str, str]],
    hypothesis_ledger: HypothesisLedger | None = None,
    *,
    temperature: float = 0.0,
) -> FinalSynthesis:
    """Синтезировать финальный структурный ответ из наблюдений и гипотез.

    Всегда возвращает ``FinalSynthesis``. При сбое LLM возвращает
    ``fallback_used=True`` с деградированной структурой из сырых данных.
    """
    observations, verified, limitations = collect_evidence(steps, hypothesis_ledger)
    if not observations and not verified:
        return _fallback_synthesis(question, observations, verified, limitations)

    prompt = _build_synthesis_prompt(question, observations, verified)
    model_text = ""
    try:
        response = get_chat_model(temperature=temperature).invoke(
            _SYNTHESIS_SYSTEM + "\n\n" + prompt
        )
        model_text = (response.content if hasattr(response, "content") else str(response)).strip()
    except Exception as exc:
        _LOG.warning("synthesis LLM call failed: %s", exc, exc_info=True)
        return _fallback_synthesis(question, observations, verified, limitations)

    if not model_text or len(model_text) < 40:
        return _fallback_synthesis(question, observations, verified, limitations)

    sections = _split_sections(model_text)
    citations = _extract_citations(model_text)
    return FinalSynthesis(
        question=question,
        sections=sections,
        citations=citations,
        limitations=limitations,
        fallback_used=False,
        verified_hypotheses=verified,
    )


def write_synthesis(run_dir: str, synthesis: FinalSynthesis) -> str | None:
    """Сохранить ``synthesis.md`` в run-dir. Возвращает путь или ``None``."""
    if not run_dir:
        return None
    try:
        from pathlib import Path

        path = Path(run_dir) / "synthesis.md"
        # Путь может быть ещё не создан (агент только стартует run-dir);
        # пишем независимо от существования родительского каталога.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(synthesis.to_markdown(), encoding="utf-8")
        return str(path)
    except Exception as exc:
        _LOG.warning("write_synthesis failed: %s", exc, exc_info=True)
        return None


__all__ = [
    "FinalSynthesis",
    "collect_evidence",
    "synthesize",
    "write_synthesis",
]
