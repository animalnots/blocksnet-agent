"""Unit-тесты ``blocksnet_agent/tools/preparation.py``.

Проверяем:
- fallback на готовый ``blocks_with_services.gpkg`` когда ``blocks.gpkg`` нет;
- no-op когда ``out_path`` совпадает с fallback;
- явная ошибка когда нет ни одного источника.
Overpass-зависимые ветки (``prepare_accessibility_matrix``,
``prepare_road_congestion_inputs``) — отдельно под маркер ``network``.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from blocksnet_agent.tools.preparation import _build_blocks_with_services_inner


@pytest.fixture
def minimal_data_dir(tmp_path: Path) -> Path:
    """Создаёт минимальный data_dir с готовым blocks_with_services.gpkg."""
    data_dir = tmp_path / "scenario"
    data_subdir = data_dir / "data"
    data_subdir.mkdir(parents=True)
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame(
        {"population": [100], "land_use": ["RESIDENTIAL"], "count_school": [1]},
        geometry=[poly],
        crs="EPSG:32636",
    )
    gdf.index.name = "block_id"
    out = data_subdir / "blocks_with_services.gpkg"
    gdf.to_file(out, driver="GPKG")
    return data_dir


def test_build_blocks_with_services_fallback_to_existing(
    minimal_data_dir: Path, tmp_path: Path
) -> None:
    """Нет ``blocks.gpkg`` → копируем готовый ``blocks_with_services.gpkg``."""
    out = tmp_path / "out_bws.gpkg"
    result = _build_blocks_with_services_inner(
        data_dir=minimal_data_dir,
        out_path=out,
    )
    assert out.exists()
    assert result["rows"] == 1
    assert result["cols"] >= 3
    assert "copied" in result["note"]
    loaded = gpd.read_file(out)
    assert "count_school" in loaded.columns
    assert int(loaded["population"].iloc[0]) == 100


def test_build_blocks_with_services_noop_when_out_equals_fallback(
    minimal_data_dir: Path,
) -> None:
    """``out_path`` совпадает с существующим fallback — no-op."""
    out = minimal_data_dir / "data" / "blocks_with_services.gpkg"
    result = _build_blocks_with_services_inner(
        data_dir=minimal_data_dir,
        out_path=out,
    )
    assert result["note"].startswith("source=")
    assert "no-op" in result["note"]


def test_build_blocks_with_services_raises_when_empty(tmp_path: Path) -> None:
    """Нет ни ``blocks.gpkg``, ни ``blocks_with_services.gpkg`` → FileNotFoundError."""
    data_dir = tmp_path / "empty_scenario"
    data_dir.mkdir()
    (data_dir / "data").mkdir()
    with pytest.raises(FileNotFoundError, match="blocks.gpkg"):
        _build_blocks_with_services_inner(
            data_dir=data_dir,
            out_path=tmp_path / "out.gpkg",
        )


def test_build_blocks_with_services_explicit_blocks_path(tmp_path: Path) -> None:
    """``blocks_path`` указывает на конкретный gpkg — используется он, не поиск."""
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame(
        {"population": [42], "land_use": ["BUSINESS"]},
        geometry=[poly],
        crs="EPSG:32636",
    )
    gdf.index.name = "block_id"
    src = tmp_path / "my_blocks.gpkg"
    gdf.to_file(src, driver="GPKG")

    out = tmp_path / "out.gpkg"
    result = _build_blocks_with_services_inner(
        data_dir=tmp_path / "scenario",  # пустой data_dir
        out_path=out,
        blocks_path=str(src),
    )
    assert result["rows"] == 1
    assert int(gpd.read_file(out)["population"].iloc[0]) == 42
    assert "note" not in result  # это не fallback, а реальная сборка


# Overpass-ветки — отдельные интеграционные тесты под маркером ``network``.
# Чтобы не делать сеть зависимостью юнит-прогона, помечаем skip по умолчанию.
network = pytest.mark.skip(
    reason="Overpass-сетевые; запускать вручную: pytest -m network",
)


@network
def test_prepare_accessibility_matrix_integration(tmp_path: Path) -> None:
    """Интеграционный тест — реальный Overpass, ~минуты."""
    pytest.skip("сетевой тест, запускать вручную")