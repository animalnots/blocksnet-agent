"""Сборка A2A-артефактов из результата прогона.

Раньше артефакт нёс строку локального пути (``parts=[{"text": "/app/outputs/…"}]``).
Для принимающей стороны это бесполезно: CodeSynapse превращает части артефакта
в файлы (``a2a_artifacts.a2a_artifact_to_files``), и путь на **нашей** файловой
системе там не открыть. Поле со статусом ``supported`` тратилось впустую.

Здесь артефакты несут **содержимое**:

* ``analysis-result`` — структурированный результат ``data``-частью
  (у ``Part.data`` статус ``supported``) плюс человекочитаемая сводка
  ``text``-частью;
* по файлу на текстовый/табличный артефакт прогона, если он укладывается в
  лимит размера.

Решение по крупным бинарям (карты ``render_metric_map``) принято явно: они
**не встраиваются**. ``Part.raw`` у них помечен как ``partial`` («не всегда
восстанавливается как исходный файл»), а многомегабайтный base64 в MAS-ответе
— это молчаливое раздувание трафика. Такие файлы перечисляются в
``analysis-result`` списком ``skipped_artifacts`` с причиной, чтобы аналитик
знал, что они существуют, и мог забрать их из ``run_dir``.

План: ``docs/dev/plans/codesynapse/01-a2a-contract.md`` (A6).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from a2a.types import Artifact, Part
from google.protobuf.struct_pb2 import Value

log = logging.getLogger("blocksnet_agent.a2a.artifacts")

#: Максимальный размер встраиваемого файла. Выше — только упоминание.
MAX_EMBEDDED_BYTES = 256 * 1024

#: Что умеем встроить осмысленно, и с каким MIME.
_EMBEDDABLE: Dict[str, str] = {
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".txt": "text/plain",
}

#: Ключи результата, которые не несут аналитики и только раздувают DataPart.
_RESULT_NOISE = frozenset({"artifacts", "run_dir"})


def _summary_text(output: Dict[str, Any]) -> str:
    """Короткая человекочитаемая сводка — ``text``-часть основного артефакта."""
    status = output.get("status", "ok")
    lines = [f"Статус: {status}"]

    if output.get("error"):
        lines.append(f"Ошибка: {output['error']}")
    for key, label in (("result", "Результат"), ("output", "Результат")):
        value = output.get(key)
        if value:
            lines.append(f"{label}: {value}")
            break
    if output.get("recommendation_blocks"):
        blocks = output["recommendation_blocks"]
        lines.append(f"Рекомендованные кварталы ({len(blocks)}): {blocks[:20]}")
    if output.get("confidence") is not None:
        lines.append(f"Уверенность: {output['confidence']}")
    for key, label in (("limitations", "Ограничения"), ("warnings", "Предупреждения")):
        values = output.get(key)
        if values:
            lines.append(f"{label}: {'; '.join(str(v) for v in values)}")
    return "\n".join(lines)


def _classify(path: Path) -> Tuple[str | None, str | None]:
    """``(mime, None)`` если файл встраиваем, иначе ``(None, причина)``."""
    mime = _EMBEDDABLE.get(path.suffix.lower())
    if mime is None:
        return None, f"неподдерживаемый тип {path.suffix or '<без расширения>'}"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"недоступен: {exc}"
    if size > MAX_EMBEDDED_BYTES:
        return None, f"слишком большой ({size} B > {MAX_EMBEDDED_BYTES} B)"
    return mime, None


def _file_part(path: Path, mime: str) -> Dict[str, Any] | None:
    """Часть артефакта с содержимым файла: ``data`` для JSON, иначе ``text``."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("artifact %s is not readable as utf-8: %s", path, exc)
        return None

    if mime in ("application/json", "application/geo+json"):
        try:
            return {
                "data": json.loads(raw),
                "filename": path.name,
                "media_type": mime,
            }
        except json.JSONDecodeError as exc:
            log.warning("artifact %s is not valid JSON: %s", path, exc)
            # Не теряем содержимое: отдаём текстом, честно пометив тип.
            return {"text": raw, "filename": path.name, "media_type": "text/plain"}

    return {"text": raw, "filename": path.name, "media_type": mime}


def _data_part(payload: Any, *, filename: str | None = None, media_type: str) -> Part:
    """``Part`` с JSON-содержимым.

    ``Part.data`` — это ``google.protobuf.Value``, а не ``Struct``, поэтому
    словарь кладётся в ``struct_value``. Конструктор ``Part(data=<dict>)``
    молча не сработает: protobuf попытается собрать ``Value`` из словаря и
    упадёт на первом же ключе.
    """
    value = Value()
    if isinstance(payload, dict):
        value.struct_value.update(payload)
    else:
        value.list_value.extend(payload if isinstance(payload, list) else [payload])
    part = Part(data=value, media_type=media_type)
    if filename:
        part.filename = filename
    return part


def _text_part(text: str, *, filename: str | None = None, media_type: str) -> Part:
    part = Part(text=text, media_type=media_type)
    if filename:
        part.filename = filename
    return part


def _to_part(spec: Dict[str, Any]) -> Part:
    if "data" in spec:
        return _data_part(
            spec["data"], filename=spec.get("filename"), media_type=spec["media_type"]
        )
    return _text_part(
        spec["text"], filename=spec.get("filename"), media_type=spec["media_type"]
    )


def build_artifacts(output: Dict[str, Any]) -> List[Artifact]:
    """Собрать protobuf-артефакты для ``TaskArtifactUpdateEvent``.

    Первый артефакт — всегда ``analysis-result``: он несёт разбор результата и
    перечень файлов, которые встроить не удалось. Дальше — по артефакту на
    встраиваемый файл.
    """
    # ``to_json`` перечисляет файлы относительно ``run_dir`` (формат MCP-ответа);
    # без привязки они искались бы в cwd сервера и отмечались как «недоступен».
    run_dir = str(output.get("run_dir") or "")
    paths: List[Path] = []
    for raw in output.get("artifacts") or []:
        path = Path(str(raw))
        if run_dir and not path.is_absolute():
            path = Path(run_dir) / path
        paths.append(path)

    embedded: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for path in paths:
        mime, reason = _classify(path)
        if mime is None:
            skipped.append({"name": path.name, "reason": reason or "неизвестно"})
            continue
        part = _file_part(path, mime)
        if part is None:
            skipped.append({"name": path.name, "reason": "не читается как utf-8"})
            continue
        embedded.append(
            {
                "artifact_id": f"file-{len(embedded) + 1}",
                "name": path.name,
                "description": f"Файл прогона: {path.name}",
                "parts": [part],
            }
        )

    result_data = {k: v for k, v in output.items() if k not in _RESULT_NOISE}
    if skipped:
        # Не молча: аналитик должен знать, что файлы есть, но не приехали.
        result_data["skipped_artifacts"] = skipped

    artifacts: List[Artifact] = [
        Artifact(
            artifact_id="analysis-result",
            name="analysis-result",
            description="Структурированный результат анализа и краткая сводка",
            parts=[
                _data_part(result_data, media_type="application/json"),
                _text_part(_summary_text(output), media_type="text/markdown"),
            ],
        )
    ]
    artifacts.extend(
        Artifact(
            artifact_id=spec["artifact_id"],
            name=spec["name"],
            description=spec["description"],
            parts=[_to_part(part) for part in spec["parts"]],
        )
        for spec in embedded
    )
    return artifacts


__all__ = ["MAX_EMBEDDED_BYTES", "build_artifacts"]
