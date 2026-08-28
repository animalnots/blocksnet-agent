# `data/vasilievsky-island/` — сценарий «Васильевский остров»

> Этот файл упаковывается в архив `vasilievsky-island-data.zip` (см. `data/manifest.json`).
> Контрольные суммы и размер пересчитываются скриптом `scripts/package_data.py`.

## Что внутри

| Файл / каталог | Размер | Назначение |
|---|---|---|
| `blocks_with_services.gpkg` | 0.3 МБ | Кварталы + population + land_use + `count_<service>` (готовый артефакт) |
| `blocks_to_nodes.pickle` | 0.7 МБ | float32: квартал → ближайший drive-узел |
| `nodes_to_nodes.pickle` | 2.0 МБ | float32: drive-узел × drive-узел (время, мин) |
| `graph_drive.graphml` | 0.3 МБ | Дорожный граф (Overpass, lanes + time_min) |
| `data/blocks.gpkg` | 0.3 МБ | Сырьё: 250 полигонов кварталов |
| `data/buildings.geojson` | 77 МБ | Сырьё: здания |
| `data/functional_zones.geojson` | 33 МБ | Сырьё: функциональные зоны |
| `data/land_use_rules.json` | 1 КБ | Маппинг кода ФЗ → категория |
| `data/service_type.json` | 67 КБ | Каталог нормативов сервисов |
| `data/services/*.geojson` | 60 файлов, 26 МБ | POI по сервисам |

## Совместимость с инструментами

| Инструмент | Работает? | Примечание |
|---|---|---|
| `load_blocks` | ✅ | есть `blocks_with_services.gpkg` |
| `compute_road_congestion` | ✅ | полный набор (graph + b2n + n2n) готов |
| `compute_service_provision`, `compute_scenario_provision` | ❌ | **нет `acc_mx.pickle`** |
| `recommend_blocks_for_services` | ❌ | **нет `acc_mx.pickle`** |
| `load_acc_mx` | ❌ | **нет `acc_mx.pickle`** |
| A2A-skill `run_pipeline` | ⚠️ частично | работает только для вопросов про заторы (`compute_road_congestion`); вопросы про обеспеченность вернут ошибку |

## Как запустить

```bash
DATA_DIR=data/vasilievsky-island python -m blocksnet_agent
# → http://localhost:8080/
```

Запуск без Docker-тома: подмените `DATA_DIR` так, чтобы он указывал на корень
этого сценария (там лежат `blocks_with_services.gpkg`, `graph_drive.graphml`,
`*.pickle` — это и есть «рантайм-артефакты» по контракту `compute_road_congestion`).

Пример запроса:
```bash
curl -X POST http://localhost:8080/ \
  -H "A2A-Version: 1.0" -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": "1", "method": "SendMessage",
    "params": {"message": {"role": 1, "parts": [{"text": "Построй матрицу корреспонденций и загрузку дорог на Васильевском острове"}], "messageId": "m1"}}
  }'
```

## Известные ограничения

- `acc_mx.pickle` отсутствует → вопросы про обеспеченность сервисами вернут ошибку. Сборка `acc_mx` требует отдельного прогона через `blocksnet` и в этот архив не входит.

## Контрольные суммы

| Файл | SHA256 |
|---|---|
| `vasilievsky-island-data.zip` | `см. data/manifest.json` |