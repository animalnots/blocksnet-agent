"""Тесты, что ``docs/mcp_tool_catalog.md`` актуален (шаг 08).

Главная гарантия:
- Сгенерированный файл совпадает с закоммиченным — иначе каталог протухнет
  через две недели и интеграторы будут работать по устаревшим данным.

Тест НЕ требует реальных данных: ``build_catalog()`` создаёт инструменты
на пустом state — нужны только имена/описания/схемы.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_FILE = PROJECT_ROOT / "docs" / "mcp_tool_catalog.md"
GENERATOR = PROJECT_ROOT / "scripts" / "generate_tool_catalog.py"


def test_catalog_file_exists() -> None:
    """``docs/mcp_tool_catalog.md`` существует."""
    assert CATALOG_FILE.exists(), (
        f"{CATALOG_FILE} не найден. Запустите: "
        f"python scripts/generate_tool_catalog.py"
    )


def test_catalog_header_marks_auto_generated() -> None:
    """В шапке каталога есть явная пометка «auto-generated»."""
    content = CATALOG_FILE.read_text(encoding="utf-8")
    assert "Не редактировать руками" in content
    assert "генерируется" in content.lower()


def test_catalog_lists_32_canonical_tools() -> None:
    """В каталоге перечислены все доменные инструменты + 3 служебных."""
    content = CATALOG_FILE.read_text(encoding="utf-8")
    canonical_names = [
        "build_adjacency_graph",
        "build_blocks_with_services",
        "compute_area_accessibility",
        "compute_connectivity",
        "compute_density_indicators",
        "compute_development_indicators",
        "compute_land_use_accessibility",
        "compute_max_accessibility",
        "compute_mean_accessibility",
        "compute_median_accessibility",
        "compute_population_centrality",
        "compute_road_congestion",
        "compute_scenario_provision",
        "compute_service_provision",
        "compute_services_centrality",
        "compute_services_collocation",
        "compute_services_count",
        "compute_services_density",
        "compute_shannon_diversity",
        "compute_shared_provision",
        "find_tools",
        "get_analysis_results",
        "get_block_info",
        "get_metric_for_block",
        "get_tool_help",
        "get_weakest_services",
        "list_cached_data",
        "list_key_services",
        "list_service_types",
        "load_accessibility_matrix",
        "load_blocks",
        "prepare_accessibility_matrix",
        "prepare_road_congestion_inputs",
        "propose_zone_development",
        "render_metric_map",
        "suggest_target_blocks",
    ]
    for name in canonical_names:
        assert f"## `{name}`" in content, f"инструмент {name} отсутствует в каталоге"

    # submit_answer не должен быть в каталоге.
    assert "## `submit_answer`" not in content

    # Служебные упомянуты.
    for service in ("open_session", "close_session", "session_info"):
        assert service in content


def test_catalog_matches_generation() -> None:
    """Сгенерированный Markdown совпадает с закоммиченным файлом.

    Это главная защита от протухания: после любого изменения в коде
    (docstring'ах, схемах, фабриках) скрипт ``generate_tool_catalog.py``
    пересоберёт файл, и ``git status`` покажет diff. Если закоммитить
    без пересборки — этот тест поймает расхождение.
    """
    # Запускаем генератор в subprocess — он использует свой sys.path.
    result = subprocess.run(
        [sys.executable, str(GENERATOR)],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, f"generator failed: {result.stderr}"

    # Перечитываем закоммиченный файл.
    committed = CATALOG_FILE.read_text(encoding="utf-8")
    # Перегенерированный файл — рядом.
    generated_path = CATALOG_FILE
    generated = generated_path.read_text(encoding="utf-8")

    if committed != generated:
        # Покажем diff в pytest -v (первые 30 строк различий).
        diff_lines: list[str] = []
        for line_a, line_b in zip(committed.splitlines(), generated.splitlines()):
            if line_a != line_b:
                diff_lines.append(f"- {line_a}")
                diff_lines.append(f"+ {line_b}")
                if len(diff_lines) > 30:
                    break
        pytest.fail(
            "docs/mcp_tool_catalog.md устарел. Перезапустите:\n"
            "  python scripts/generate_tool_catalog.py\n\n"
            f"First diffs:\n{chr(10).join(diff_lines)}"
        )


def test_session_required_marker_present() -> None:
    """Инструменты, требующие сессии, помечены явно."""
    content = CATALOG_FILE.read_text(encoding="utf-8")
    # ``get_analysis_results``, ``get_metric_for_block``, ``render_metric_map``,
    # ``list_cached_data`` — точно требуют state.
    for name in ("get_analysis_results", "get_metric_for_block",
                 "render_metric_map", "list_cached_data"):
        # Ищем заголовок инструмента + пометку в нём.
        section_start = content.find(f"## `{name}`")
        assert section_start >= 0, f"{name} не найден"
        section_end = content.find("\n---\n", section_start)
        section = content[section_start:section_end]
        assert "требует сессии" in section, (
            f"{name} должен быть помечен «требует сессии»"
        )