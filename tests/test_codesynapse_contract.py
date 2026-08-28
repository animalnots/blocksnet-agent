"""Контракт интеграции с CodeSynapse (MAS-платформа Synapse).

Источник истины — исполняемые схемы принимающей стороны, а не наши
представления о спецификации A2A. Схемы лежат в snapshot их репозитория
(``docs/dev/codesynapse/docs/contracts/a2a``) и **не копируются** к нам:
копия разошлась бы с их версией молча (решение Д4 плана).

Путь переопределяется переменной ``A2A_CONTRACTS_DIR`` — так же, как их
собственный ``_contracts_dir()`` в ``src/integrations/a2a_contracts.py``.
Без snapshot тесты пропускаются: это чужой код, а не зависимость нашего
рантайма.

План: ``docs/dev/plans/codesynapse/01-a2a-contract.md``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CONTRACTS_DIR = PROJECT_ROOT / "docs" / "dev" / "codesynapse" / "docs" / "contracts" / "a2a"
CODESYNAPSE_SRC = PROJECT_ROOT / "docs" / "dev" / "codesynapse" / "src"

SCHEMA_FILE = "synapse-a2a-1.0.schema.json"

# Поля профиля 0.3. Их присутствие рядом с supportedInterfaces заставляет
# detect_agent_card_protocol() отклонить карточку (ловушка Л4).
LEGACY_03_CARD_FIELDS = (
    "protocolVersion",
    "url",
    "preferredTransport",
    "additionalInterfaces",
    "supportsAuthenticatedExtendedCard",
)

REQUIRED_CARD_FIELDS = (
    "name",
    "description",
    "supportedInterfaces",
    "version",
    "capabilities",
    "defaultInputModes",
    "defaultOutputModes",
    "skills",
)


def _contracts_dir() -> Path:
    configured = os.getenv("A2A_CONTRACTS_DIR")
    return Path(configured) if configured else DEFAULT_CONTRACTS_DIR


@pytest.fixture(scope="session")
def contracts_dir() -> Path:
    path = _contracts_dir()
    if not (path / SCHEMA_FILE).is_file():
        pytest.skip(
            f"snapshot схем CodeSynapse не найден: {path / SCHEMA_FILE}. "
            "Это чужой репозиторий, а не зависимость рантайма — положите snapshot "
            "в docs/dev/codesynapse или задайте A2A_CONTRACTS_DIR."
        )
    return path


@pytest.fixture(scope="session")
def schema(contracts_dir: Path) -> Dict[str, Any]:
    return json.loads((contracts_dir / SCHEMA_FILE).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def validator_for(schema: Dict[str, Any]):
    """Валидатор для любого ``$defs``-узла их схемы.

    Собирается так же, как ``_agent_card_validator`` у них: корневой ``$ref``
    на нужный узел плюс полный ``$defs`` для разрешения ссылок.
    """
    from jsonschema import Draft202012Validator, FormatChecker

    def _make(defs_key: str) -> "Draft202012Validator":
        sub_schema = {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{defs_key}",
        }
        Draft202012Validator.check_schema(sub_schema)
        return Draft202012Validator(sub_schema, format_checker=FormatChecker())

    return _make


def assert_valid(validator, instance: Any, label: str) -> None:
    """Провалить тест со **списком** расхождений, а не с первым найденным."""
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        lines = [f"{label}: {len(errors)} расхождений со схемой CodeSynapse:"]
        lines += [
            f"  - {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors
        ]
        pytest.fail("\n".join(lines))


@pytest.fixture(scope="session")
def served_card() -> Dict[str, Any]:
    """Карточка **как её отдаёт HTTP-эндпоинт**, а не как её видит Python.

    Именно эту форму забирает CodeSynapse, и именно в сериализации protobuf
    протекают лишние поля, которые ловит ``additionalProperties: false``.
    """
    from starlette.testclient import TestClient

    from blocksnet_agent.a2a.server import build_app

    response = TestClient(build_app()).get("/.well-known/agent-card.json")
    assert response.status_code == 200, response.text
    return response.json()


# --- A1: карточка против схемы ---------------------------------------------


def test_agent_card_validates_against_synapse_schema(validator_for, served_card) -> None:
    """Полная валидация по ``$defs.agentCard``.

    Схема закрыта (``additionalProperties: false``), поэтому этот же тест
    покрывает ловушку Л3: любое лишнее поле из protobuf-сериализации всплывёт
    здесь, а не при регистрации у них.
    """
    assert_valid(validator_for("agentCard"), served_card, "AgentCard")


def test_agent_card_has_required_fields(served_card) -> None:
    missing = [field for field in REQUIRED_CARD_FIELDS if field not in served_card]
    assert not missing, f"в карточке нет обязательных полей A2A 1.0: {missing}"


def test_agent_card_protocol_version_is_exactly_1_0(served_card) -> None:
    """Л5: ровно ``"1.0"``. ``"1.0.0"`` даёт a2a_protocol_version_unsupported."""
    versions = [
        interface.get("protocolVersion")
        for interface in served_card["supportedInterfaces"]
    ]
    assert versions, "supportedInterfaces пуст"
    assert set(versions) == {"1.0"}, (
        f"protocolVersion должен быть строго '1.0' без patch-номера, получено: {versions}"
    )


def test_agent_card_has_no_legacy_03_fields(served_card) -> None:
    """Л4: смешивание профилей 0.3 и 1.0 отклоняется их detect_agent_card_protocol."""
    present = [field for field in LEGACY_03_CARD_FIELDS if field in served_card]
    assert not present, (
        f"legacy-поля профиля 0.3 в карточке 1.0: {present} — "
        "их detect_agent_card_protocol() отклонит такую карточку"
    )


def test_agent_card_declares_no_fields_outside_schema(schema, served_card) -> None:
    """Явная формулировка Л3 — чтобы падение читалось без разбора схемы."""
    allowed = set(schema["$defs"]["agentCard"]["properties"])
    extra = sorted(set(served_card) - allowed)
    assert not extra, (
        f"поля вне схемы agentCard: {extra}. Схема закрыта "
        "(additionalProperties: false) — карточка будет отклонена"
    )


# --- A2: required AgentExtension со схемой параметров -----------------------


@pytest.fixture(scope="session")
def served_extension(served_card) -> Dict[str, Any]:
    from blocksnet_agent.a2a.extension import EXTENSION_URI

    extensions = served_card["capabilities"].get("extensions") or []
    matching = [ext for ext in extensions if ext.get("uri") == EXTENSION_URI]
    assert matching, (
        f"в карточке нет расширения {EXTENSION_URI}. Без него их "
        "required_extensions_with_schema() не запускает извлечение параметров, "
        "и DataPart до нас не доедет (ловушка Л1)"
    )
    return matching[0]


def test_parameter_extension_matches_synapse_profile(validator_for, served_extension) -> None:
    """Расширение валидно и как agentExtension, и как synapseParameterExtension."""
    assert_valid(validator_for("agentExtension"), served_extension, "AgentExtension")
    assert_valid(
        validator_for("synapseParameterExtension"),
        served_extension,
        "synapseParameterExtension",
    )


def test_parameter_extension_is_required(served_extension) -> None:
    """Гейт извлечения — ``required: true``, иначе LLM-вызова у них не будет."""
    assert served_extension.get("required") is True


def test_parameter_schema_is_a_valid_json_schema(served_extension) -> None:
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(served_extension["params"])


def test_parameter_schema_survives_protobuf_roundtrip() -> None:
    """Struct-сериализация не должна терять или искажать схему.

    protobuf Struct хранит числа как double, поэтому ``minimum: 1`` приезжает
    как ``1.0``. Для JSON Schema это тот же предел, но проверяем явно, чтобы
    подмена типа не прошла незамеченной.
    """
    from blocksnet_agent.a2a.extension import PARAMS_SCHEMA

    served = _served_params()
    assert served["type"] == PARAMS_SCHEMA["type"]
    assert served["required"] == PARAMS_SCHEMA["required"]
    assert served["additionalProperties"] == PARAMS_SCHEMA["additionalProperties"]
    assert set(served["properties"]) == set(PARAMS_SCHEMA["properties"])
    for name, spec in PARAMS_SCHEMA["properties"].items():
        assert served["properties"][name]["type"] == spec["type"], name
        if "pattern" in spec:
            assert served["properties"][name]["pattern"] == spec["pattern"], name
        for bound in ("minimum", "maximum"):
            if bound in spec:
                assert float(served["properties"][name][bound]) == float(spec[bound]), name


def _served_params() -> Dict[str, Any]:
    from starlette.testclient import TestClient

    from blocksnet_agent.a2a.extension import EXTENSION_URI
    from blocksnet_agent.a2a.server import build_app

    card = TestClient(build_app()).get("/.well-known/agent-card.json").json()
    for ext in card["capabilities"]["extensions"]:
        if ext["uri"] == EXTENSION_URI:
            return ext["params"]
    raise AssertionError("parameter extension missing")


def test_parameter_names_match_the_scenario_context_contract() -> None:
    """Схема — единственный источник имён; сверка программная, не глазами.

    Значения из схемы попадают в ``resolve_context``. Если имя или тип здесь
    разойдётся с тем, что принимает контекст, клиент пришлёт значение, которое
    наш же ``resolve_context`` отвергнет как VALIDATION_ERROR.
    """
    import inspect

    from blocksnet_agent.context import resolve_context
    from blocksnet_agent.a2a.extension import PARAMS_SCHEMA, known_parameter_names

    accepted = set(inspect.signature(resolve_context).parameters)
    for name in ("scenario_id", "project_id"):
        assert name in known_parameter_names(), f"{name} пропал из схемы расширения"
        assert name in accepted, f"resolve_context больше не принимает {name}"

    # Паттерн id должен совпадать с валидатором контекста, иначе клиент
    # пришлёт формально валидное по схеме значение, которое мы отвергнем.
    from blocksnet_agent.context import _SCENARIO_ID_PATTERN

    for name in ("scenario_id", "project_id"):
        assert PARAMS_SCHEMA["properties"][name]["pattern"] == _SCENARIO_ID_PATTERN.pattern


# --- A3/A4: DataPart доезжает и побеждает при конфликте ---------------------


def _message(text: str | None = None, data: Dict[str, Any] | None = None, metadata=None):
    """Собрать protobuf Message так, как его отдаёт SDK на входе."""
    from a2a.types import Message, Part, Role
    from google.protobuf.struct_pb2 import Value

    parts = []
    if text is not None:
        parts.append(Part(text=text))
    if data is not None:
        # Part.data — google.protobuf.Value, а не Struct: значение кладётся
        # в его struct_value. Ровно эту форму строит клиент CodeSynapse.
        value = Value()
        value.struct_value.update(data)
        parts.append(Part(data=value))
    return Message(role=Role.ROLE_USER, parts=parts, metadata=metadata or {})


def test_data_part_parameters_are_parsed() -> None:
    from blocksnet_agent.a2a.params import SOURCE_DATA_PART, parse_message_params

    msg = _message("Где разместить школы?", {"scenario_id": "spb-772"})
    parsed = parse_message_params(msg.parts, msg.metadata)

    assert parsed.scenario_id == "spb-772"
    assert parsed.sources["scenario_id"] == SOURCE_DATA_PART
    assert parsed.question == "Где разместить школы?"


def test_data_part_wins_over_metadata_on_conflict() -> None:
    """Их правило: "structure wins when both are present" (ADR-0006).

    Живой инцидент у CodeSynapse: текст нёс 772, устаревший DataPart — 987654321,
    посчитан был DataPart. Наша сторона обязана вести себя так же, иначе один и
    тот же запрос даёт разные ответы у них и у нас.
    """
    from blocksnet_agent.a2a.params import SOURCE_DATA_PART, parse_message_params

    msg = _message(
        "Проанализируй сценарий 772",
        {"scenario_id": "987654321"},
        metadata={"scenario_id": "772"},
    )
    parsed = parse_message_params(msg.parts, msg.metadata)

    assert parsed.scenario_id == "987654321"
    assert parsed.sources["scenario_id"] == SOURCE_DATA_PART


def test_metadata_still_works_without_a_data_part() -> None:
    """Обратная совместимость: локальные клиенты и smoke-скрипты."""
    from blocksnet_agent.a2a.params import SOURCE_METADATA, parse_message_params

    msg = _message("Вопрос", metadata={"scenario_id": "legacy-1"})
    parsed = parse_message_params(msg.parts, msg.metadata)

    assert parsed.scenario_id == "legacy-1"
    assert parsed.sources["scenario_id"] == SOURCE_METADATA


def test_question_text_reaches_the_agent_unchanged() -> None:
    """Клиент шлёт полный интент; вырезать параметры из текста нельзя."""
    from blocksnet_agent.a2a.params import parse_message_params

    text = "Проанализируй сценарий 772 и скажи, где дефицит школ"
    msg = _message(text, {"scenario_id": "772"})
    parsed = parse_message_params(msg.parts, msg.metadata)

    assert parsed.question == text
    assert parsed.as_input_payload()["question"] == text


def test_absent_parameters_are_not_fabricated() -> None:
    from blocksnet_agent.a2a.params import parse_message_params

    parsed = parse_message_params(_message("Вопрос без параметров").parts, None)

    assert parsed.scenario_id is None
    assert parsed.project_id is None
    assert parsed.max_iterations is None
    assert parsed.sources == {}


def test_numeric_parameter_arrives_as_string_from_llm_extraction() -> None:
    """Их извлечение — LLM: число вполне может приехать строкой."""
    from blocksnet_agent.a2a.params import parse_message_params

    parsed = parse_message_params(_message("q", {"max_iterations": "12"}).parts, None)
    assert parsed.max_iterations == 12


@pytest.mark.parametrize(
    "value",
    [0, 101, True, "не число"],
    ids=["below-minimum", "above-maximum", "boolean", "not-a-number"],
)
def test_invalid_parameter_values_are_rejected(value) -> None:
    from blocksnet_agent.a2a.params import ParamValidationError, parse_message_params

    with pytest.raises(ParamValidationError):
        parse_message_params(_message("q", {"max_iterations": value}).parts, None)


def test_execute_actually_calls_the_data_part_parser(monkeypatch) -> None:
    """Тест на подключение: парсер не только корректен, но и вызывается.

    Без него повторился бы ровно текущий дефект — "metadata читается, DataPart
    нет": разбор был бы написан, покрыт тестами и не подключён.
    """
    import blocksnet_agent.a2a.server as server_mod

    seen: Dict[str, Any] = {}
    real = server_mod.parse_message_params

    def spy(parts, metadata=None):
        parsed = real(parts, metadata)
        seen["scenario_id"] = parsed.scenario_id
        return parsed

    monkeypatch.setattr(server_mod, "parse_message_params", spy)

    _events, payloads = _invoke_execute(
        _message("Вопрос", {"scenario_id": "wired-through"}), monkeypatch=monkeypatch
    )

    assert seen.get("scenario_id") == "wired-through", (
        "execute() не вызвал parse_message_params — DataPart до агента не доедет"
    )
    assert payloads, "runner не был вызван"
    assert payloads[0]["scenario_id"] == "wired-through", (
        "значение из DataPart не доехало до skill runner"
    )


def _invoke_execute(message, *, monkeypatch, runner=None):
    """Прогнать ``_A2ATaskBridge.execute`` на голом Message.

    Настоящий skill подменяется заглушкой: нас интересует граница (разбор
    параметров, статусные события, артефакты), а не аналитика — иначе тест
    потребовал бы LLM и данные города.

    Возвращает ``(events, payloads)``: события очереди и полезные нагрузки,
    с которыми был вызван runner.
    """
    import asyncio
    from types import SimpleNamespace

    import blocksnet_agent.a2a.server as server_mod
    from blocksnet_agent.a2a.settings import A2ASettings
    from blocksnet_agent.a2a.task_manager import TaskManager

    events: list[Any] = []
    payloads: list[Dict[str, Any]] = []

    class _Queue:
        def enqueue_event(self, event):
            events.append(event)

            async def _noop():
                return None

            return _noop()

    def _default_runner(*, input_payload, **_kwargs):
        payloads.append(input_payload)
        return {"status": "ok", "tool": "run_pipeline", "output": "stub"}

    stub = SimpleNamespace(id="run_pipeline", runner=runner or _default_runner)
    monkeypatch.setattr(server_mod, "get_skill", lambda _skill_id: stub)

    bridge = server_mod._A2ATaskBridge(
        task_manager=TaskManager(max_concurrent=1, task_ttl_sec=5.0),
        settings=A2ASettings(),
    )
    context = SimpleNamespace(message=message, task_id="task-1", context_id="ctx-1")

    asyncio.run(bridge.execute(context, _Queue()))
    return events, payloads


# --- A5: статусные события соответствуют профилю 1.0 -----------------------


def _event_to_dict(event) -> Dict[str, Any]:
    """protobuf-событие в ту JSON-форму, которую увидит клиент."""
    from google.protobuf.json_format import MessageToDict

    return MessageToDict(event, preserving_proto_field_name=False)


def test_status_update_event_validates_against_schema(validator_for, monkeypatch) -> None:
    from a2a.types import TaskStatusUpdateEvent

    events, _payloads = _invoke_execute(_message("Вопрос"), monkeypatch=monkeypatch)
    updates = [e for e in events if isinstance(e, TaskStatusUpdateEvent)]
    assert updates, "не эмитировано ни одного TaskStatusUpdateEvent"

    for event in updates:
        assert_valid(validator_for("taskStatusUpdate"), _event_to_dict(event), "TaskStatusUpdateEvent")


def test_status_events_use_1_0_task_state_names(monkeypatch) -> None:
    """Значения перечисления — ``TASK_STATE_*``, коротких алиасов у SDK нет."""
    from a2a.types import TaskStatusUpdateEvent

    events, _payloads = _invoke_execute(_message("Вопрос"), monkeypatch=monkeypatch)
    states = {
        _event_to_dict(e)["status"]["state"]
        for e in events
        if isinstance(e, TaskStatusUpdateEvent)
    }
    assert states, "статусных событий нет"
    assert all(state.startswith("TASK_STATE_") for state in states), states


def test_status_events_carry_no_legacy_final_field(monkeypatch) -> None:
    """Л4: в 1.0 поля ``final`` нет — их ``$defs.taskStatusUpdate`` закрыта.

    Регрессия дорогая: SDK 1.1.1 роняет конструктор на неизвестном поле, а
    ``except`` в колбэке это глотал — поток статусов пропадал целиком, хотя
    карточка объявляет ``streaming: true``.
    """
    from a2a.types import TaskStatusUpdateEvent

    events, _payloads = _invoke_execute(_message("Вопрос"), monkeypatch=monkeypatch)
    for event in events:
        if isinstance(event, TaskStatusUpdateEvent):
            assert "final" not in _event_to_dict(event)


def test_task_state_short_aliases_do_not_exist() -> None:
    """Страж: если SDK когда-нибудь вернёт короткие алиасы, мы узнаем об этом."""
    from a2a.types import TaskState

    for alias in ("working", "submitted", "completed", "failed", "canceled"):
        assert not hasattr(TaskState, alias), (
            f"TaskState.{alias} снова существует — перепроверьте маппинг в _on_progress"
        )


# --- A6/A7/A8: сквозной обмен, артефакты, терминальные состояния ------------


def _exchange(output: Dict[str, Any], *, parts=None) -> Dict[str, Any]:
    """Настоящий JSON-RPC обмен со стаб-скиллом → возвращает Task.

    Это ближе всего к тому, что делает CodeSynapse: их клиент шлёт
    ``SendMessage`` c заголовком ``A2A-Version: 1.0`` и ждёт терминальной
    задачи (``returnImmediately: false``).
    """
    from types import SimpleNamespace

    from starlette.testclient import TestClient

    import blocksnet_agent.a2a.server as server_mod

    stub = SimpleNamespace(id="run_pipeline", runner=lambda **_kw: output)
    original = server_mod.get_skill
    server_mod.get_skill = lambda _skill_id: stub
    try:
        client = TestClient(server_mod.build_app())
        response = client.post(
            "/",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "m1",
                        "role": "ROLE_USER",
                        "parts": parts or [{"text": "Вопрос"}],
                    }
                },
            },
        )
    finally:
        server_mod.get_skill = original

    body = response.json()
    assert "error" not in body, body["error"]
    return body["result"]["task"]


def test_completed_task_validates_against_schema(validator_for) -> None:
    """A8: полный ответ проходит их ``$defs.task``."""
    task = _exchange({"status": "ok", "result": "готово"})
    assert_valid(validator_for("task"), task, "Task")
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"


def test_response_is_a_task_not_a_bare_message() -> None:
    """Регрессия: голый Message переводил SDK в message mode.

    Следующий же TaskArtifactUpdateEvent ронял весь ответ в
    INVALID_AGENT_RESPONSE, а CodeSynapse ждёт именно Task — их делегирование
    читает ``Task.artifacts`` и ``TaskStatus.message``.
    """
    task = _exchange({"status": "ok", "result": "готово"})
    assert "status" in task and "id" in task


def test_artifacts_carry_content_not_local_paths(validator_for) -> None:
    """A6: путь на нашей ФС для них бесполезен — нужен сам результат."""
    task = _exchange(
        {
            "status": "ok",
            "result": "Найдено 3 квартала",
            "recommendation_blocks": [1, 2, 3],
            "artifacts": ["/app/outputs/run-1/map.png"],
        }
    )
    artifacts = task["artifacts"]
    assert artifacts, "артефактов нет"
    for artifact in artifacts:
        assert_valid(validator_for("artifact"), artifact, "Artifact")

    main = artifacts[0]
    assert main["name"] == "analysis-result"
    kinds = [set(part) & {"text", "data", "raw", "url"} for part in main["parts"]]
    assert {"data"} in kinds and {"text"} in kinds

    serialized = json.dumps(task, ensure_ascii=False)
    assert "/app/outputs" not in serialized, (
        "в артефакт просочился локальный путь — для CodeSynapse он бесполезен"
    )


def test_unembeddable_artifacts_are_reported_not_dropped() -> None:
    """Растр не встраиваем, но и не молчим: он есть в skipped_artifacts."""
    task = _exchange(
        {"status": "ok", "result": "ok", "artifacts": ["/app/outputs/run-1/map.png"]}
    )
    data_part = next(part for part in task["artifacts"][0]["parts"] if "data" in part)
    skipped = data_part["data"]["skipped_artifacts"]
    assert [item["name"] for item in skipped] == ["map.png"]
    assert ".png" in skipped[0]["reason"]


def test_small_text_artifact_is_embedded(tmp_path) -> None:
    """A6: то, что встроить можно и осмысленно, — встраиваем."""
    report = tmp_path / "report.md"
    report.write_text("# Отчёт\nВсё хорошо", encoding="utf-8")

    task = _exchange({"status": "ok", "result": "ok", "artifacts": [str(report)]})
    embedded = [a for a in task["artifacts"] if a["name"] == "report.md"]
    assert embedded, [a["name"] for a in task["artifacts"]]
    assert "Всё хорошо" in embedded[0]["parts"][0]["text"]


def test_oversized_artifact_is_skipped(tmp_path) -> None:
    from blocksnet_agent.a2a.artifacts import MAX_EMBEDDED_BYTES

    big = tmp_path / "big.csv"
    big.write_text("x" * (MAX_EMBEDDED_BYTES + 1), encoding="utf-8")

    task = _exchange({"status": "ok", "result": "ok", "artifacts": [str(big)]})
    data_part = next(part for part in task["artifacts"][0]["parts"] if "data" in part)
    reasons = {item["name"]: item["reason"] for item in data_part["data"]["skipped_artifacts"]}
    assert "big.csv" in reasons
    assert "слишком большой" in reasons["big.csv"]


def test_failed_run_reports_reason_in_task_status_message(validator_for) -> None:
    """A7: их a2a_delegate показывает оркестратору TaskStatus.message."""
    task = _exchange(
        {"status": "failed", "error": "нет данных сценария", "error_code": "DATA_UNAVAILABLE"}
    )
    assert_valid(validator_for("task"), task, "Task")
    assert task["status"]["state"] == "TASK_STATE_FAILED"

    text = task["status"]["message"]["parts"][0]["text"]
    assert "DATA_UNAVAILABLE" in text
    assert "нет данных сценария" in text


def test_failure_message_carries_no_traceback() -> None:
    task = _exchange({"status": "failed", "error": "boom", "error_code": "AGENT_ERROR"})
    assert "Traceback" not in json.dumps(task, ensure_ascii=False)


def test_invalid_parameter_fails_before_the_run(validator_for) -> None:
    """A7: недопустимое значение — терминальный отказ, а не расчёт на дефолтах."""
    task = _exchange(
        {"status": "ok", "result": "не должно было выполниться"},
        parts=[{"text": "Вопрос"}, {"data": {"max_iterations": 0}}],
    )
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert "VALIDATION_ERROR" in task["status"]["message"]["parts"][0]["text"]


def test_declared_output_modes_are_understood_by_synapse(served_card) -> None:
    """Л6: их is_supported_output_mode принимает text/* и *json."""
    modes = served_card["defaultOutputModes"]
    assert modes
    supported = [
        m for m in modes
        if m.startswith("text/") or m == "application/json" or m.endswith("+json")
    ]
    assert supported, (
        f"ни один из {modes} не понятен CodeSynapse — регистрация упадёт "
        "с a2a_output_modes_unsupported"
    )


def test_artifact_part_modes_stay_within_declared_modes(served_card) -> None:
    """Их validate_part_output_mode сверяет каждую часть с объявленным."""
    declared = {m.lower() for m in served_card["defaultOutputModes"]}
    task = _exchange({"status": "ok", "result": "ok"})
    for artifact in task["artifacts"]:
        for part in artifact["parts"]:
            mode = (part.get("mediaType") or "").lower()
            if not mode:
                continue
            ok = mode in declared or mode.startswith("text/") or mode.endswith("+json")
            assert ok, f"{mode} не входит в объявленные {sorted(declared)}"


# --- H2: регистрационный пакет не расходится с кодом ------------------------


REGISTRATION_DOC = PROJECT_ROOT / "docs" / "dev" / "codesynapse_registration.md"


def test_registration_doc_exists() -> None:
    assert REGISTRATION_DOC.is_file(), (
        "docs/dev/codesynapse_registration.md — единственная инструкция для "
        "принимающей стороны, без неё передача не состоится"
    )


def test_documented_rpc_endpoint_matches_the_code() -> None:
    """Самая дорогая опечатка регистрации: ``rpc_endpoint``.

    Мы монтируем JSON-RPC на корень, их дефолт — ``/a2a``. Если документ
    разойдётся с кодом при рефакторинге роутов, регистрация даст 404 при
    формально валидной карточке.
    """
    import re

    server_src = (PROJECT_ROOT / "blocksnet_agent" / "a2a" / "server.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r"create_jsonrpc_routes\([^)]*rpc_url=\"([^\"]+)\"", server_src)
    assert match, "не найден rpc_url в create_jsonrpc_routes"
    actual = match.group(1)

    doc = REGISTRATION_DOC.read_text(encoding="utf-8")
    assert f'"rpc_endpoint": "{actual}"' in doc, (
        f"в коде rpc_url={actual!r}, а документ регистрации предлагает другое значение"
    )


def test_registration_doc_documents_the_extension_uri() -> None:
    from blocksnet_agent.a2a.extension import EXTENSION_URI

    assert EXTENSION_URI in REGISTRATION_DOC.read_text(encoding="utf-8")


def test_registration_doc_carries_no_secrets() -> None:
    """П5: в документ уходят имена переменных, но не значения."""
    import re

    doc = REGISTRATION_DOC.read_text(encoding="utf-8")
    leaks = re.findall(
        r"(?:token|secret|api[_-]?key|password)\"?\s*[:=]\s*\"([^\"]{8,})\"",
        doc,
        flags=re.IGNORECASE,
    )
    assert not leaks, f"похоже на секрет в документе регистрации: {leaks}"


# --- Развёртывание без UrbanDB: scenario_id = имя готового датасета ---------


def test_unknown_scenario_keeps_its_machine_readable_code(tmp_path) -> None:
    """Штатный отказ, а не авария: код нужен их агенту для ветвления.

    Без materializer'а (UrbanDB не подключён) несуществующий сценарий —
    ожидаемый исход. Раньше он приезжал как generic ``TASK_EXCEPTION``, из
    которого вызывающий агент ничего не мог понять.
    """
    from blocksnet_agent.a2a.executor import execute_run_pipeline
    from blocksnet_agent.context import ERROR_SCENARIO_NOT_MATERIALIZED

    (tmp_path / "saint_petersburg").mkdir()
    (tmp_path / "kronstadt").mkdir()

    payload = execute_run_pipeline(
        question="Где школы?",
        max_iterations=None,
        deadline_sec=None,
        stop_event=None,
        progress_cb=None,
        scenario_id="does-not-exist",
        data_dir=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "failed"
    assert payload["error_code"] == ERROR_SCENARIO_NOT_MATERIALIZED


def test_unknown_scenario_lists_what_is_available(tmp_path) -> None:
    """Подсказка превращает тупик в то, из чего их агент может выбраться."""
    from blocksnet_agent.a2a.executor import execute_run_pipeline

    (tmp_path / "saint_petersburg").mkdir()
    (tmp_path / "kronstadt").mkdir()

    payload = execute_run_pipeline(
        question="Где школы?",
        max_iterations=None,
        deadline_sec=None,
        stop_event=None,
        progress_cb=None,
        scenario_id="does-not-exist",
        data_dir=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert "kronstadt" in payload["error"]
    assert "saint_petersburg" in payload["error"]


def test_scenario_error_does_not_leak_absolute_paths(tmp_path) -> None:
    """Раскладка нашей ФС — не дело чужого тенанта."""
    from blocksnet_agent.a2a.executor import execute_run_pipeline

    (tmp_path / "saint_petersburg").mkdir()
    payload = execute_run_pipeline(
        question="Где школы?",
        max_iterations=None,
        deadline_sec=None,
        stop_event=None,
        progress_cb=None,
        scenario_id="nope",
        data_dir=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert str(tmp_path) not in payload["error"]


def test_available_scenarios_ignores_noise(tmp_path) -> None:
    from blocksnet_agent.context import available_scenarios

    (tmp_path / "kronstadt").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "outputs").mkdir()
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")

    assert available_scenarios(tmp_path) == ["kronstadt"]
