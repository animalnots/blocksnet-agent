"""Preparation tools — собирают ``blocks_with_services.gpkg``, ``acc_mx.pickle`` и
road_congestion inputs из сырья.

Эти три операции раньше жили в ``scripts/`` (CLI), теперь — инструменты MCP.
Под капотом:

- ``blocksnet.relations.accessibility.graph.get_accessibility_graph`` для walk/drive графов;
- ``blocksnet.relations.accessibility.matrix.calculate_accessibility_matrix`` для матриц;
- ``iduedu.get_adj_matrix_gdf_to_gdf`` для ``blocks_to_nodes``/``nodes_to_nodes``
  (эту функциональность ``blocksnet`` делегирует ``iduedu``, см. ``network.py``);
- прямые geopandas-spatial-join'ы для POI↔block, buildings↔block, FZ↔block —
  этих операций в публичном API ``blocksnet`` нет, делаем на месте.

Все инструменты идемпотентны по аргументам и пишут файлы только в указанный
каталог (по умолчанию ``DATA_DIR``). Повторный вызов перезаписывает артефакт.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from langchain_core.tools import tool
from loguru import logger


REQUIRED_BLOCK_COLUMNS = ("population", "land_use", "site_area")
REQUIRED_COUNT_PREFIX = "count_"


def _project_to_utm(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if blocks.crs is None:
        raise ValueError("blocks CRS is None; невозможно проецировать в метрику")
    if not blocks.crs.is_projected:
        blocks = blocks.to_crs(blocks.estimate_utm_crs())
    return blocks


def _attach_service_counts(
    blocks: gpd.GeoDataFrame, services_dir: Path, valid_slugs: set[str]
) -> gpd.GeoDataFrame:
    """Spatial join POI↔block → ``count_<slug>`` колонки."""
    if not services_dir.is_dir():
        raise FileNotFoundError(f"каталог сервисов не найден: {services_dir}")
    bs = blocks.reset_index()[[blocks.index.name or "index", "geometry"]]
    bs = bs.rename(columns={bs.columns[0]: "__block_idx"})
    for f in sorted(services_dir.glob("*.geojson")):
        slug = f.stem
        if slug not in valid_slugs:
            continue
        try:
            poi = gpd.read_file(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[services] skip {slug}: {exc}")
            continue
        if poi.empty or poi.geometry.is_empty.all():
            blocks[f"count_{slug}"] = 0
            continue
        pts = poi.copy()
        mask = poi.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
        if mask.any():
            pts.loc[mask, "geometry"] = poi.loc[mask, "geometry"].representative_point()
        joined = gpd.sjoin(pts[["geometry"]], bs, how="inner", predicate="within")
        cnt = joined.groupby("__block_idx").size()
        blocks[f"count_{slug}"] = blocks.index.map(cnt).fillna(0).astype("int64")
    return blocks


def _attach_population(blocks: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Spatial join buildings↔block → ``population`` (sum of ``population_balanced``)."""
    if "population_balanced" not in buildings.columns:
        raise ValueError("buildings.gpkg без population_balanced — нужен пересчёт")
    centroids = buildings.copy()
    centroids["geometry"] = buildings.geometry.representative_point()
    centroids = centroids.set_crs(buildings.crs, allow_override=True)
    joined = gpd.sjoin(
        centroids[["population_balanced", "geometry"]],
        blocks[["geometry"]],
        how="inner",
        predicate="within",
    )
    right_col = "index_right" if "index_right" in joined.columns else str(blocks.index.name or "index")
    if right_col not in joined.columns:
        raise RuntimeError(f"sjoin без {right_col}: {list(joined.columns)}")
    pop = joined.groupby(right_col)["population_balanced"].sum()
    blocks["population"] = blocks.index.map(lambda i: pop.get(i, 0)).astype("int64")
    return blocks


def _attach_land_use(
    blocks: gpd.GeoDataFrame, fz: gpd.GeoDataFrame, rules: dict[str, str]
) -> gpd.GeoDataFrame:
    """Spatial join functional_zones↔block → ``land_use`` через ``land_use_rules.json``."""
    from blocksnet.enums import LandUse

    fz_m = fz.to_crs(blocks.crs) if fz.crs != blocks.crs else fz
    if "index" not in fz.columns:
        raise ValueError("functional_zones.geojson без колонки index (код зоны)")
    inter = gpd.overlay(
        blocks.reset_index(),
        fz_m[["index", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    inter["overlap_area"] = inter.geometry.area
    inter = inter.sort_values("overlap_area", ascending=False)
    inter = inter.loc[~inter[inter.columns[0]].duplicated(keep="first")]
    mapping = inter.set_index(inter.columns[0])["index"].to_dict()
    blocks["functional_zone"] = blocks.index.map(lambda i: mapping.get(i))
    blocks["land_use"] = (
        blocks["functional_zone"]
        .map(lambda c: LandUse[rules[c]] if c in rules and rules[c] in LandUse.__members__ else None)
    )
    return blocks.drop(columns=["functional_zone"])


def _build_blocks_with_services_inner(
    data_dir: Path,
    out_path: Path,
    *,
    blocks_path: str | None = None,
    buildings_path: str | None = None,
    functional_zones_path: str | None = None,
    services_subdir: str = "services",
    land_use_rules_path: str | None = None,
    service_type_path: str | None = None,
) -> dict:
    """Чистая (тестируемая) сборка ``blocks_with_services.gpkg``.

    ``blocks_path``: путь к базовому gpkg с полигонами кварталов и атрибутами.
    По умолчанию ``DATA_DIR/blocks.gpkg``. Если такого файла нет — пробует
    ``DATA_DIR/blocks_with_services.gpkg`` как fallback (он самодостаточен,
    достраивать нечего — операция становится no-op copy с записью в ``out_path``).
    """
    src = Path(blocks_path) if blocks_path else data_dir / "data" / "blocks.gpkg"
    if not src.exists():
        # blocks.gpkg нет — пробуем готовый blocks_with_services.gpkg
        # (лежит в data_dir/data/ по контракту сценариев).
        fallback = data_dir / "data" / "blocks_with_services.gpkg"
        if fallback.exists():
            out_resolved = Path(out_path).resolve()
            if out_resolved == fallback.resolve():
                # Уже на месте — no-op, отдаём метаданные существующего файла.
                b = gpd.read_file(fallback)
                return {
                    "out_path": str(out_path),
                    "rows": len(b),
                    "cols": len(b.columns),
                    "count_columns": sum(1 for c in b.columns if c.startswith("count_")),
                    "note": "source=blocks_with_services.gpkg (already at out_path, no-op)",
                }
            # Уже готовый артефакт — копируем в out_path для воспроизводимости.
            import shutil
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fallback, out_path)
            b = gpd.read_file(fallback)
            return {
                "out_path": str(out_path),
                "rows": len(b),
                "cols": len(b.columns),
                "count_columns": sum(1 for c in b.columns if c.startswith("count_")),
                "note": "source=blocks_with_services.gpkg (already built, copied)",
            }
        raise FileNotFoundError(
            f"{src} не найден и {fallback} тоже отсутствует; "
            f"укажите blocks_path или положите blocks.gpkg / blocks_with_services.gpkg"
        )
    blocks = gpd.read_file(src)
    if blocks.empty:
        raise ValueError(f"{src} пуст")
    # Если gpkg содержит свой индекс (что и так бывает при чтении),
    # reset_index(drop=False) перенесёт его в колонку — нормализуем:
    blocks = blocks.reset_index(drop=False)
    if blocks.columns[0] != "block_id":
        blocks = blocks.rename(columns={blocks.columns[0]: "block_id"})
    blocks = blocks.drop(columns=["block_id"]).set_index(
        pd.RangeIndex(len(blocks), name="block_id")
    )
    blocks = _project_to_utm(blocks)
    blocks["site_area"] = blocks.geometry.area

    if buildings_path:
        b = gpd.read_file(Path(buildings_path))
        blocks = _attach_population(blocks, b)
    if functional_zones_path:
        fz = gpd.read_file(Path(functional_zones_path))
        rules_path = Path(land_use_rules_path) if land_use_rules_path else data_dir / "land_use_rules.json"
        rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else {}
        blocks = _attach_land_use(blocks, fz, rules)

    services_dir = data_dir / services_subdir
    if services_dir.is_dir():
        st_path = Path(service_type_path) if service_type_path else data_dir / "service_type.json"
        valid_slugs: set[str] = set()
        if st_path.exists():
            st = json.loads(st_path.read_text(encoding="utf-8"))
            if isinstance(st, list):
                valid_slugs = {it["name"] for it in st if "name" in it}
        # Если уже есть count_<slug> колонки в исходнике (ЮСх-кейс) — пропускаем POI join.
        already_has_counts = any(c.startswith("count_") for c in blocks.columns)
        if not already_has_counts:
            blocks = _attach_service_counts(blocks, services_dir, valid_slugs)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    blocks.to_file(out_path, driver="GPKG")
    return {
        "out_path": str(out_path),
        "rows": len(blocks),
        "cols": len(blocks.columns),
        "count_columns": sum(1 for c in blocks.columns if c.startswith("count_")),
    }


def make_preparation_tools(ctx: dict) -> list:
    state = ctx["state"]
    data_dir = ctx["data_dir"]
    output_dir = ctx["output_dir"]

    @tool
    def build_blocks_with_services(
        out_path: str = "blocks_with_services.gpkg",
        blocks_path: str = "",
        buildings_path: str = "",
        functional_zones_path: str = "",
        land_use_rules_path: str = "",
        service_type_path: str = "",
    ) -> str:
        """Собирает ``blocks_with_services.gpkg`` из сырья в ``DATA_DIR``.

        Берёт ``blocks_path`` (по умолчанию ``DATA_DIR/blocks.gpkg``) как базовые полигоны
        кварталов; если такого файла нет, пробует ``DATA_DIR/blocks_with_services.gpkg``
        как готовый артефакт (no-op копия). Опционально присоединяет: ``buildings_path``
        (population), ``functional_zones_path`` + ``land_use_rules_path`` (land_use),
        ``DATA_DIR/services/*.geojson`` (count_<slug>; пропускается, если колонки
        уже есть в исходнике). Каталог сервисов валидируется против ``service_type_path``.

        Когда вызывать: один раз перед ``load_blocks``, если в ``DATA_DIR`` ещё нет
        готового ``blocks_with_services.gpkg``. После сборки ``load_blocks`` подхватит
        файл автоматически.

        Не путать с: ``load_blocks`` — он только читает, не собирает.
        """
        try:
            out = Path(out_path)
            if not out.is_absolute():
                # По умолчанию пишем в data_dir/data/ — там же, где лежит исходник.
                out = data_dir / "data" / out_path
            result = _build_blocks_with_services_inner(
                data_dir=data_dir,
                out_path=out,
                blocks_path=blocks_path or None,
                buildings_path=buildings_path or None,
                functional_zones_path=functional_zones_path or None,
                land_use_rules_path=land_use_rules_path or None,
                service_type_path=service_type_path or None,
            )
            state["_prepared_blocks_path"] = str(out)
            note = f"\n  примечание: {result['note']}" if result.get("note") else ""
            return (
                f"blocks_with_services собран: {result['out_path']}\n"
                f"  rows={result['rows']}, cols={result['cols']}, "
                f"count_* колонок={result['count_columns']}.{note}\n"
                f"Теперь можно вызвать load_blocks()."
            )
        except Exception as exc:
            return f"Ошибка сборки blocks_with_services: {exc}"

    @tool
    def prepare_accessibility_matrix(
        out_path: str = "acc_mx.pickle",
        graph_type: str = "walk",
        dtype: str = "float32",
    ) -> str:
        """Готовит ``acc_mx.pickle`` (матрица доступности block→block) через Overpass.

        Использует ``blocksnet.relations.accessibility``:
          - ``get_accessibility_graph(blocks, graph_type)`` — walk/drive/intermodal граф;
          - ``calculate_accessibility_matrix(blocks, graph)`` — pandas-матрица.

        Сохраняет в ``out_path`` (по умолчанию ``DATA_DIR/acc_mx.pickle``). ``load_accessibility_matrix``
        подхватит файл автоматически.

        Когда вызывать: один раз перед ``load_accessibility_matrix``, если pickle отсутствует
        или устарел. **Overpass-запрос занимает минуты**, используйте ``graph_type='walk'``
        для самого дешёвого варианта.

        Не путать с: ``load_accessibility_matrix`` — только читает; ``compute_*_accessibility`` —
        считает производные метрики (mean/median/max) из уже загруженной матрицы.
        """
        try:
            from blocksnet.relations import get_accessibility_graph
            from blocksnet.relations.accessibility.matrix import calculate_accessibility_matrix

            out = Path(out_path)
            if not out.is_absolute():
                out = data_dir / "data" / out_path
            blocks = gpd.read_file(data_dir / "data" / "blocks_with_services.gpkg")
            if blocks.empty:
                raise ValueError("blocks_with_services.gpkg пуст")
            blocks = _project_to_utm(blocks)
            logger.info(f"[prepare] Overpass {graph_type} graph query …")
            graph = get_accessibility_graph(blocks, graph_type)
            logger.info(f"[prepare] graph: {graph.number_of_nodes()} узлов")
            mx = calculate_accessibility_matrix(blocks, graph, dtype=dtype)
            out.parent.mkdir(parents=True, exist_ok=True)
            mx.to_pickle(out)
            state["_prepared_acc_mx_path"] = str(out)
            return (
                f"acc_mx собран: {out}\n"
                f"  shape={mx.shape}, dtype={mx.dtypes.iloc[0]}.\n"
                f"Теперь можно вызвать load_accessibility_matrix()."
            )
        except Exception as exc:
            return f"Ошибка prepare_accessibility_matrix: {exc}"

    @tool
    def prepare_road_congestion_inputs(
        out_dir: str = "",
        additional_edgedata: str = "lanes",
        buffer_m: int = 0,
    ) -> str:
        """Готовит три файла, нужные ``compute_road_congestion``:
          - ``graph_drive.graphml`` — drive MultiDiGraph (Overpass, ``additional_edgedata``);
          - ``blocks_to_nodes.pickle`` — block → drive-узел (через walk-граф);
          - ``nodes_to_nodes.pickle`` — drive-узел × drive-узел (время, мин).

        Использует ``blocksnet.relations.get_accessibility_graph`` (drive/walk)
        и ``iduedu.get_adj_matrix_gdf_to_gdf`` для матриц. ``buffer_m>0`` —
        обрезает кварталы буфером вокруг центроида (полезно для очень больших
        датасетов, чтобы Overpass не таймаутил).

        Когда вызывать: один раз перед ``compute_road_congestion``, если файлов
        нет или они устарели. **Overpass-запрос занимает минуты.**

        Не путать с: ``compute_road_congestion`` — только считает, не готовит входы.
        """
        try:
            from iduedu import get_adj_matrix_gdf_to_gdf
            from blocksnet.relations import get_accessibility_graph, accessibility_graph_to_gdfs
            import networkx as nx

            target = Path(out_dir) if out_dir else data_dir / "data"
            target.mkdir(parents=True, exist_ok=True)

            blocks = gpd.read_file(data_dir / "data" / "blocks_with_services.gpkg")
            if blocks.empty:
                raise ValueError("blocks_with_services.gpkg пуст")
            if buffer_m:
                projected = _project_to_utm(blocks)
                projected["geometry"] = projected.geometry.buffer(buffer_m)
                clipped = gpd.clip(blocks, projected)
                blocks = clipped if not clipped.empty else blocks

            extra = [a.strip() for a in additional_edgedata.split(",") if a.strip()]
            logger.info("[prepare] Overpass walk …")
            graph_walk = get_accessibility_graph(blocks, "walk")
            logger.info(f"[prepare] walk: {graph_walk.number_of_nodes()} узлов")

            logger.info("[prepare] Overpass drive …")
            graph_drive = get_accessibility_graph(blocks, "drive", additional_edgedata=extra)
            crs_int = int(blocks.estimate_utm_crs().to_epsg()) if blocks.crs.is_projected else 32636
            graph_drive.graph["crs"] = crs_int
            for _, data in graph_drive.nodes(data=True):
                for k in ("x", "y"):
                    if k in data:
                        data[k] = float(data[k])
            for _, _, data in graph_drive.edges(data=True):
                if "time_min" in data:
                    data["time_min"] = float(data["time_min"])
                raw = data.get("lanes", 1)
                try:
                    ln = int(float(raw))
                except (TypeError, ValueError):
                    ln = 1
                data["lanes"] = max(1, min(8, ln)) if 1 <= ln <= 8 else 1
                data.pop("geometry", None)

            nodes_gdf, _ = accessibility_graph_to_gdfs(graph_drive)
            if not isinstance(nodes_gdf, gpd.GeoDataFrame):
                nodes_gdf = gpd.GeoDataFrame(nodes_gdf, geometry="geometry", crs=graph_drive.graph["crs"])

            b2n = get_adj_matrix_gdf_to_gdf(blocks, nodes_gdf, graph_walk, weight="time_min", dtype="float32")
            n2n = get_adj_matrix_gdf_to_gdf(nodes_gdf, nodes_gdf, graph_drive, weight="time_min", dtype="float32")

            assert set(blocks.index) == set(b2n.index), "blocks.index != b2n.index"
            assert n2n.index.equals(n2n.columns), "n2n не квадратная"
            assert isinstance(graph_drive.graph["crs"], int), "graph.crs must be int"

            out_g = target / "graph_drive.graphml"
            out_b2n = target / "blocks_to_nodes.pickle"
            out_n2n = target / "nodes_to_nodes.pickle"
            nx.write_graphml(graph_drive, out_g)
            b2n.to_pickle(out_b2n)
            n2n.to_pickle(out_n2n)
            state["_prepared_road_inputs"] = {
                "graph": str(out_g),
                "blocks_to_nodes": str(out_b2n),
                "nodes_to_nodes": str(out_n2n),
            }
            return (
                f"road_congestion inputs готовы в {target}:\n"
                f"  graph_drive.graphml: {graph_drive.number_of_nodes()} узлов, "
                f"{graph_drive.number_of_edges()} рёбер (crs={crs_int})\n"
                f"  blocks_to_nodes.pickle: {b2n.shape}\n"
                f"  nodes_to_nodes.pickle: {n2n.shape}\n"
                f"Теперь можно вызвать compute_road_congestion()."
            )
        except Exception as exc:
            return f"Ошибка prepare_road_congestion_inputs: {exc}"

    return [
        build_blocks_with_services,
        prepare_accessibility_matrix,
        prepare_road_congestion_inputs,
    ]