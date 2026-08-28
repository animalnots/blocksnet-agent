# `data/yuzhno-sakhalinsk/` — сценарий «Южно-Сахалинск»

> Этот файл упаковывается в архив `yuzhno-sakhalinsk-data.zip` (см. `data/manifest.json`).
> Контрольные суммы и размер пересчитываются скриптом `scripts/package_data.py`.

## Что внутри

| Файл / каталог | Размер | Назначение |
|---|---|---|
| `data/blocks_with_services.gpkg` | 1.6 МБ | Кварталы (903) + population + land_use + share + `capacity_<service>` + `count_<service>` для 60+ сервисов. **Контракт `load_blocks` готов.** |
| `data/acc_mx.pickle` | 1.6 МБ | Матрица доступности 903×903, float16 |
| `data/archetypes.csv` | 11 КБ | Социально-культурные архетипы регионов РФ (для ЮСх = Сахалинская область) |
| `data/service_type.json` | 73 КБ | Каталог нормативов сервисов (98 записей) |
| `data/services/*.geojson` | 43 файла, 0.6 МБ | POI по сервисам (отдельные geojson по сервисам) |
| `data/boundaries.gpkg` | 0.1 МБ | Сырьё: bbox границ ЮСх (OSM nominatim) |
| `data/buildings.gpkg` | 7.5 МБ | Сырьё: здания |
| `data/functional_zones.geojson` | 3.0 МБ | Сырьё: функциональные зоны (EPSG:4326, 832 полигона) |
| `data/roads.geojson` | 12 МБ | Сырьё: дороги |
| `data/railways.geojson` | 0.2 МБ | Сырьё: ж/д |
| `data/water.geojson` | 1.4 МБ | Сырьё: водные объекты |

## Совместимость с инструментами

| Инструмент | Работает? | Примечание |
|---|---|---|
| `load_blocks` | ✅ | `blocks_with_services.gpkg` валиден, CRS EPSG:32654, `land_use` в формате `LandUse.SPECIAL` (нормализуется через `_parse_land_use`) |
| `load_accessibility_matrix` | ✅ | `acc_mx.pickle` 903×903 float16 |
| `list_service_types`, `list_key_services` | ✅ | 60+ `capacity_*` колонок, 43 POI в `services/` |
| `compute_service_provision`, `compute_scenario_provision` | ✅ | требует `acc_mx.pickle` |
| `recommend_blocks_for_services` | ✅ | |
| `compute_road_congestion` | ❌ | нет road_congestion inputs |
| A2A-skill `run_pipeline` | ✅ для provision | для road_congestion вернёт ошибку |

## Что НЕ нужно делать перед запуском

`blocks_with_services.gpkg` уже собран и содержит все необходимые колонки
для provision-расчётов. Дополнительная подготовка не требуется.

## Как запустить

```bash
DATA_DIR=data/yuzhno-sakhalinsk python -m blocksnet_agent
```

Пример:
```bash
DATA_DIR=data/yuzhno-sakhalinsk python -c "
import sys, json, time; sys.path.insert(0, '.')
from blocksnet_mcp.tools_mcp import analyze_urban_question
start = time.time()
result = analyze_urban_question('Какие кварталы имеют наименьшее покрытие школами?', max_iterations=4)
print(f'Elapsed: {time.time()-start:.1f}s')
print(json.loads(result))
"
```

## Опционально: подготовка road_congestion

Через MCP-инструмент `prepare_road_congestion_inputs` (Overpass-запрос через
`blocksnet.relations.get_accessibility_graph`, ~минуты):

```bash
DATA_DIR=data/yuzhno-sakhalinsk python -m blocksnet_mcp
#   → вызвать prepare_road_congestion_inputs(out_dir="data") и дождаться результата.
```

Агент сам вызовет инструмент при отсутствии road_congestion inputs и вопросе про заторы.

## Контрольные суммы

| Файл | SHA256 |
|---|---|
| `yuzhno-sakhalinsk-data.zip` | `см. data/manifest.json` |