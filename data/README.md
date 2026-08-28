# `data/` — сценарии для `blocksnet-agent`

Этот каталог содержит **сценарии** (по одному подкаталогу на город/территорию)
и служебные файлы, описывающие их упаковку и распространение через GitHub
Releases.

## Что внутри

```
data/
├── README.md                        ← этот файл
├── manifest.json                    ← SHA256 + размеры каждого архива
├── service_aliases.json             ← синонимы сервисов (в репо)
├── service_type.json                ← каталог нормативов (в репо, единый)
├── archetypes.csv                   ← соц.-культурные архетипы регионов (в репо)
│
├── saint-petersburg/                ← сценарий «Санкт-Петербург»
│   ├── README.md                    ← описание сценария (в архиве)
│   └── data/                        ← исходные файлы (~494 МБ)
│
├── vasilievsky-island/              ← сценарий «Васильевский остров»
│   ├── README.md                    ← описание сценария (в архиве)
│   └── data/                        ← исходные файлы + рантайм-артефакты (~140 МБ)
│
└── yuzhno-sakhalinsk/               ← сценарий «Южно-Сахалинск»
    ├── README.md                    ← описание сценария (в архиве)
    └── data/                        ← исходные файлы (~3.8 МБ)
```

## Сценарии

| Сценарий | Размер | Полный provision | road_congestion | Ссылка |
|---|---|---|---|---|
| `saint-petersburg` | 494 МБ | ✅ | ⚠️ требует подготовки | [README](saint-petersburg/README.md) |
| `vasilievsky-island` | 140 МБ | ❌ нет `acc_mx.pickle` | ✅ готов | [README](vasilievsky-island/README.md) |
| `yuzhno-sakhalinsk` | ~27 МБ | ✅ | ❌ | [README](yuzhno-sakhalinsk/README.md) |

## Где живут данные в репозитории vs. в Releases

- **В репозиторий** попадают: `manifest.json`, `README.md` каждого сценария,
  `service_aliases.json`, `service_type.json`, `archetypes.csv` — мелочь
  суммарно <100 КБ.
- **Через GitHub Releases** распространяются: `saint-petersburg-data.zip`,
  `vasilievsky-island-data.zip`, `yuzhno-sakhalinsk-data.zip` — снапшоты
  файлов сценариев на конкретную дату. Иммутабельные ссылки, привязанные
  к SHA256.

## Workflow упаковки → публикации

```bash
# 1. Упаковать (zip + manifest.json). По умолчанию всё, можно --only
python scripts/package_data.py
# → data/_releases/<scenario>-data.zip
# → data/manifest.json (с SHA256)

# 2. Создать релизы (через gh CLI)
gh release create v2026-data-spb      data/_releases/saint-petersburg-data.zip \
  --title "Saint-Petersburg data snapshot 2026" \
  --notes "Блоки, здания, сервисы, acc_mx. ~494 МБ."

gh release create v2026-data-vasilievsky-island \
  data/_releases/vasilievsky-island-data.zip \
  --title "Vasilievsky Island data snapshot 2026" \
  --notes "Road_congestion inputs готовы. acc_mx.pickle отсутствует."

gh release create v2026-data-yuzhno-sakhalinsk \
  data/_releases/yuzhno-sakhalinsk-data.zip \
  --title "Yuzhno-Sakhalinsk data snapshot 2026" \
  --notes "Только сырьё, blocks_with_services.gpkg отсутствует."

# 3. Опционально: прописать release_url в manifest.json (для удобства fetch_data.py)
python - <<'PY'
import json
from pathlib import Path
m = json.loads(Path("data/manifest.json").read_text())
m["scenarios"]["saint-petersburg"]["release_url"] = (
    "https://github.com/<owner>/blocksnet-agent/releases/download/"
    "v2026-data-spb/saint-petersburg-data.zip"
)
# ... аналогично для других сценариев
Path("data/manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False))
PY
git add data/manifest.json && git commit -m "chore(data): pin release URLs"
```

## Получение данных пользователем

```bash
# Один сценарий из GitHub Release
python scripts/fetch_data.py saint-petersburg

# Все сценарии
python scripts/fetch_data.py --all

# Локальный архив (для CI / smoke-тестов без сети)
python scripts/fetch_data.py saint-petersburg --local data/_releases/saint-petersburg-data.zip

# Конкретный релиз (переопределяет release_url из манифеста)
python scripts/fetch_data.py saint-petersburg --tag v2026-data-spb --repo <owner>/blocksnet-agent

# Перезаписать существующие данные
python scripts/fetch_data.py --all --force
```

Скрипт проверяет SHA256 перед распаковкой — повреждённый или подменённый
архив будет отвергнут.

## Сборка `acc_mx.pickle` (когда потребуется)

`acc_mx.pickle` — это **предвычисленная матрица доступности**. Для каждого
нового сценария её нужно собирать отдельно. Источник:
- `blocksnet-agent` использует её «как есть» (см. `blocksnet_agent/tools/data.py:198`).
- Сборка требует `blocks_with_services.gpkg` + Overpass-графа (`iduedu.get_walk_graph` / `osmnx`).

Подробнее — в `blocksnet_agent/tools/preparation.py` (MCP-tool `prepare_accessibility_matrix` через Overpass).

## Проверка целостности

```bash
# Перерасчёт SHA256 всех архивов
python - <<'PY'
import hashlib, json
from pathlib import Path
m = json.loads(Path("data/manifest.json").read_text())
for sid, e in m["scenarios"].items():
    p = Path("data/_releases") / e["archive"]
    if not p.exists():
        print(f"{sid}: MISSING {p}")
        continue
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    print(f"{sid}: {'OK' if actual == e['sha256'] else 'MISMATCH'}")
PY
```