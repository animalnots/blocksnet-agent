# Индекс `scripts/`

Назначение: локальные вспомогательные CLI, **необходимые для работы `blocksnet-agent`**.

> **Принцип включения:** в `scripts/` лежит только то, что прямо влияет на MCP-сервер,
> A2A-агента или интеграцию с CodeSynapse. Исследовательские ad-hoc скрипты — в
> `scripts/dev/` (вне репозитория, см. `.gitignore`).

## Что попадает в репозиторий

`scripts/` защищён whitelist'ом в `.gitignore`: по умолчанию **все новые файлы**
в этой папке игнорируются. Чтобы добавить скрипт в репозиторий:

1. Создайте `scripts/<имя>.py`
2. Добавьте строку `!scripts/<имя>.py` в whitelist в `.gitignore`
3. Проверьте: `git check-ignore -v scripts/<имя>.py` (должен вернуть пустой stdout = **не** ignored)

Это защищает от случайного коммита research/ad-hoc скриптов.

## Скрипты в репозитории

| Файл | Роль |
|---|---|
| `smoke_client.py` | Базовый MCP stdio-клиент: запускает `blocksnet_mcp.server`, проверяет `list_tools()` |
| `smoke_mcp_tools.py` | Прогон реальных MCP-инструментов (`compute_service_provision`, `compute_scenario_provision`, …) на текущем `DATA_DIR` |
| `smoke_a2a_agent.py` | Smoke-проверка A2A-агента через `SendMessage` end-to-end |
| `smoke_mcp_docker.py` | Smoke-проверка Docker-образа stdio MCP (для регистрации в CodeSynapse) |
| `smoke_docker.sh` | end-to-end smoke docker-compose |
| `validate_agent_card.py` | Проверка Agent Card против схемы CodeSynapse (A2A 1.0); обязательно при регистрации в MAS |
| `generate_tool_catalog.py` | Регенерация `docs/mcp_tool_catalog.md` из живого кода (`build_catalog()`); запускать после изменения tools |
| `package_data.py` | Упаковка сценариев из `data/<scenario>/data/` в иммутабельные zip + запись `data/manifest.json` (SHA256, размеры, списки файлов) |
| `fetch_data.py` | Скачивание/распаковка архива сценария по SHA256 — из GitHub Release или локального файла |

## Скрипты в `scripts/dev/` (только локально)

`scripts/dev/` — рабочее пространство для ad-hoc research-скриптов.
**Содержимое не входит в репозиторий** (`.gitignore`). Сюда кладите всё, что
не влияет на работу MCP/A2A: эксперименты с конкретными сценариями,
ad-hoc отчёты, отладочные pipeline.

Если скрипт дорос до эталона — переносите в `scripts/` и коммитьте.

## Команды

```powershell
# MCP-smoke (stdio)
.\.venv\Scripts\python.exe scripts\smoke_client.py
.\.venv\Scripts\python.exe scripts\smoke_mcp_tools.py

# A2A-smoke (HTTP)
.\.venv\Scripts\python.exe scripts\smoke_a2a_agent.py

# Регенерация каталога MCP-tools
python scripts/generate_tool_catalog.py

# Упаковка сценариев → data/_releases/ + data/manifest.json
python scripts/package_data.py
python scripts/package_data.py --only saint-petersburg
python scripts/package_data.py --dry-run

# Получение данных (после того как опубликован GitHub Release)
python scripts/fetch_data.py saint-petersburg
python scripts/fetch_data.py --all
python scripts/fetch_data.py saint-petersburg --tag v2026-data-spb --repo <owner>/blocksnet-agent

# Проверка A2A Agent Card (для CodeSynapse-регистрации)
python scripts/validate_agent_card.py --url http://localhost:8080

# Заполнить release_url в manifest.json после создания GitHub-релизов
python scripts/inject_release_urls.py
# (по умолчанию — Eynor-K/blocksnet-agent, теги v2026-data-spb, v2026-data-yuzhno-sakhalinsk)
```

## Что было удалено (логика переехала в MCP-tools)

| Удалено | Где теперь |
|---|---|
| `prepare_road_congestion_inputs.py` | MCP-tool `prepare_road_congestion_inputs` (`blocksnet_agent/tools/preparation.py`) |
| `build_vo_blocks_with_services.py` | MCP-tool `build_blocks_with_services` (тот же модуль) |
| `build_vo_road_congestion_inputs.py` | дубликат `prepare_road_congestion_inputs` |
| `run_vo_road_congestion.py` | legacy + сломан (путь `data/vo/data` не существует) |
| `smoke_after_p05.py` | разовый regression-check после P0.5 |
| `run_full_experiment.py` | research notebook; не нужен для публикации |
| `yuzhno_sakhalinsk_school_provision.py` | research pipeline; не нужен для публикации |