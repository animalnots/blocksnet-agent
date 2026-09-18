"""Agent Card для A2A-сервиса.

Шаг 05 a2a-рефакторинга. Все имена классов/полей — из ``spike-a2a.md``
(фактическая версия SDK 1.1.1), а не из старой документации:

- ``AgentCard`` — protobuf (``a2a.types.a2a_pb2.AgentCard``), без поля ``url``.
  Есть ``supported_interfaces: list[AgentInterface]`` с полями ``url``/
  ``protocol_binding``/``protocol_version``/``tenant``.
- ``AgentSkill`` — protobuf. Поля: ``id``/``name``/``description``/``tags``/
  ``examples``.
- ``AgentCapabilities`` — protobuf. ``streaming``/``push_notifications``.
- ``AgentInterface.protocol_binding`` — строка ``"JSONRPC"`` или ``"GRPC"``.

Путь Agent Card endpoint: ``/.well-known/agent-card.json`` (по умолчанию).
"""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
)
from google.protobuf.struct_pb2 import Struct

from blocksnet_agent.a2a.artifacts import OUTPUT_MODES
from blocksnet_agent.a2a.extension import build_parameter_extension
from blocksnet_agent.a2a.skills import SKILLS


def _build_extensions() -> list[AgentExtension]:
    """Profile Extension со схемой параметров запуска.

    ``AgentExtension.params`` — ``google.protobuf.Struct``, поэтому JSON Schema
    кладётся через ``update``: у protobuf нет конструктора из dict для Struct.
    Без ``required=True`` клиент CodeSynapse не запускает извлечение параметров
    вообще (их ``required_extensions_with_schema``), и DataPart до нас не
    доедет — см. ``blocksnet_agent/a2a/extension.py``.
    """
    spec = build_parameter_extension()
    params = Struct()
    params.update(spec["params"])
    return [
        AgentExtension(
            uri=spec["uri"],
            description=spec["description"],
            required=spec["required"],
            params=params,
        )
    ]


def build_agent_card(
    *,
    host: str,
    port: int,
    public_url: str | None,
    version: str = "0.2.0",
) -> AgentCard:
    """Собирает Agent Card из настроек сервиса и реестра skills.

    Args:
        host: ``A2A_HOST`` — адрес, на котором слушает uvicorn.
        port: ``A2A_PORT`` — порт.
        public_url: ``A2A_PUBLIC_URL`` (если задан) — приоритет над host:port.
            Используется в MAS-сценариях, когда сервис за reverse-proxy.
        version: версия сервиса (по умолчанию из pyproject.toml).

    Returns:
        ``a2a_pb2.AgentCard`` — готовая protobuf-карточка.
    """
    base_url = public_url or f"http://{host}:{port}/"
    interface = AgentInterface(
        url=base_url,
        protocol_binding="JSONRPC",
        protocol_version="1.0",
    )
    skills = [
        AgentSkill(
            id=spec.id,
            name=spec.name,
            description=spec.description,
            tags=list(spec.tags),
            examples=list(spec.examples),
        )
        for spec in SKILLS
    ]
    return AgentCard(
        name="blocksnet-mcp-a2a",
        version=version,
        description=(
            "A2A-сервис для городской аналитики на базе BlocksNetAgent. "
            "Загружает данные кварталов, считает метрики, рассчитывает "
            "предложения по размещению сервисов."
        ),
        supported_interfaces=[interface],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,  # отложено (09-deferred.md)
            extensions=_build_extensions(),
        ),
        default_input_modes=["text/plain"],
        default_output_modes=list(OUTPUT_MODES),
        skills=skills,
    )


__all__ = ["build_agent_card"]