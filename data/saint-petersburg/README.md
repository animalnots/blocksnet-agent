# `data/saint-petersburg/` — сценарий «Санкт-Петербург»

> Этот файл упаковывается в архив `saint-petersburg-data.zip` (см. `data/manifest.json`).
> Контрольные суммы и размер пересчитываются скриптом `scripts/package_data.py`.

## Что внутри

| Файл / каталог | Размер | Назначение |
|---|---|---|
| `data/acc_mx.pickle` | 335 МБ | Матрица доступности (обязательна для всех `compute_*provision`) |
| `data/blocks_with_services.gpkg` | 11 МБ | Кварталы + population + land_use + `count_<service>` (готовый артефакт) |
| `data/blocks.geojson` | 13 МБ | Сырьё: границы кварталов |
| `data/buildings.geojson` | 77 МБ | Сырьё: здания (для пересборки `blocks_with_services`) |
| `data/functional_zones.geojson` | 33 МБ | Сырьё: функциональные зоны (для `land_use`) |
| `data/land_use_rules.json` | 1 КБ | Маппинг кода ФЗ → категория |
| `data/service_type.json` | 67 КБ | Каталог нормативов сервисов |
| `data/services/*.geojson` | 60 файлов, 26 МБ | POI по сервисам |

## Совместимость с инструментами

| Инструмент | Работает? | Примечание |
|---|---|---|
| `load_blocks`, `load_acc_mx`, `load_services` | ✅ | |
| `compute_service_provision`, `compute_scenario_provision` | ✅ | требует `acc_mx.pickle` |
| `recommend_blocks_for_services` | ✅ | |
| `compute_road_congestion` | ❌ | нужен `graph_drive.graphml` + `blocks_to_nodes.pickle` + `nodes_to_nodes.pickle` — **в этом архиве отсутствуют** |
| A2A-skill `run_pipeline` | ✅ | для вопросов про обеспеченность |
| A2A-skill `run_pipeline` (про заторы) | ❌ | нет road_congestion inputs |

## Как запустить

```bash
DATA_DIR=data/saint-petersburg python -m blocksnet_agent
# → http://localhost:8080/
```

Пример запроса:
```bash
curl -X POST http://localhost:8080/ \
  -H "A2A-Version: 1.0" -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": "1", "method": "SendMessage",
    "params": {"message": {"role": 1, "parts": [{"text": "Какие кварталы СПб имеют наименьшее покрытие школами?"}], "messageId": "m1"}}
  }'
```

## Подготовка road_congestion (опционально)

Через MCP-инструмент `prepare_road_congestion_inputs` (Overpass через
`blocksnet.relations.get_accessibility_graph`, ~минуты):

```bash
DATA_DIR=data/saint-petersburg python -m blocksnet_mcp
#   → вызвать prepare_road_congestion_inputs(out_dir="data")
```

Агент вызовет инструмент сам, если в вопросе про заторы и входов нет.

## Контрольные суммы

| Файл | SHA256 |
|---|---|
| `saint-petersburg-data.zip` | `см. data/manifest.json` |