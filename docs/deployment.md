# Deployment

> **Актуальный документ.** Описывает запуск **обоих решений**: MCP-server и
> A2A-HTTP. Подробный разбор — в `RUN.md` и `README.md`.

---

## 1. Quickstart — локальный запуск

### 1.1. Установка

```bash
# Создать venv и установить зависимости
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[agent,dev]"
```

### 1.2. Переменные окружения

Скопировать `.env.example` → `.env` и заполнить:

```env
CHAT_URL=https://ollama.com/v1          # OpenAI-совместимый endpoint
API_KEY=...                              # Bearer-токен LLM
DATA_DIR=./data
OUTPUT_DIR=./outputs
MAX_ITERATIONS=24
DEADLINE_SEC=480
```

Полный список переменных — в §5 ниже.

### 1.3. Запуск сервисов

```bash
# 1) MCP-server (stdio) — для MCP-клиентов (Claude/Cursor/etc)
python -m blocksnet_mcp

# 2) A2A-агент (HTTP) — для standalone / MAS
python -m blocksnet_agent
# → http://0.0.0.0:8080/
#    .well-known/agent-card.json — карточка A2A
#    / — JSON-RPC SendMessage
#    /health — liveness
```

**MCP-server работает без LLM** (CHAT_URL/API_KEY не обязательны). Это
главное отличие от A2A — `import blocksnet_mcp.server` НЕ тянет
`langgraph`/`tiktoken` (verified `tests/test_image_deps.py`).

### 1.4. Пример MCP-клиентской конфигурации

```jsonc
{
  "mcpServers": {
    "blocksnet": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "blocksnet_mcp"],
      "cwd": "/path/to/blocksnet-agent",
      "env": {
        "DATA_DIR": "./data",
        "OUTPUT_DIR": "./outputs",
        "SESSION_TTL_SEC": "1800",
        "MAX_SESSIONS": "8"
      }
    }
  }
}
```

## 2. Quickstart — Docker

```bash
# Сборка и запуск обоих сервисов
docker compose build
docker compose up -d

# Проверка
curl -fsS http://localhost:8080/health
curl -fsS http://localhost:8080/.well-known/agent-card.json | jq
docker compose exec -T mcp python -c "import blocksnet_mcp.server; print('OK')"
```

Образы:

| Образ | Размер | Описание |
|---|---|---|
| `blocksnet-agent/mcp` | компактный (без LLM) | stdio MCP-server |
| `blocksnet-agent/a2a-agent` | большой (с LLM) | A2A-агент, 8080:8080 |

Healthcheck:
- **agent**: HTTP GET `/health` (curl в образе)
- **mcp**: `python -c "import blocksnet_mcp.server"` (stdio не проверить через HTTP)

Полный скрипт smoke — `scripts/smoke_docker.sh`.

## 3. Архитектура развёртывания

```text
┌──────────────────┐    stdio    ┌─────────────────────────┐
│ LLM-агент        │◄───────────►│ blocksnet-agent/mcp     │
│ (Claude, Cursor) │             │ (raw-tools, без LLM)    │
└──────────────────┘             └─────────────────────────┘
                                            │
                                            │ общий volume ./data
                                            ▼
┌──────────────────┐   HTTP/JSON-RPC  ┌─────────────────────┐
│ MAS-оркестратор   │◄───────────────►│ blocksnet-agent/a2a-agent │
│                  │   Bearer auth   │ (LLM-зависимый)      │
└──────────────────┘                 └─────────────────────┘
```

**Volumes:**
- `./data` — локальная модель города (gpkg/pickle). Read-write для обоих.
- `./outputs` — runtime-артефакты (run_*). Read-write для обоих.

**Лимиты ресурсов:**
- `agent`: mem_limit 4g, cpus 2.0 (GeoDataFrame + accessibility matrix)
- `mcp`: mem_limit 2g, cpus 1.0 (без LLM, только raw-tools + sessions)

## 4. Структура `data/` и `outputs/`

```text
data/
├── service_type.json        # версионируется
├── archetypes.csv            # версионируется
├── service_aliases.json      # версионируется
├── blocks_with_services.gpkg # gitignored (city-specific)
├── acc_mx.pickle             # gitignored (city-specific)
└── <scenario_id>/            # gitignored (UrbanDB-материализованные сценарии)

outputs/
└── run_<timestamp>_<id>/     # runtime-артефакты (карты, CSV, hypothesis.json)
```

Для MAS-сценариев с `scenario_id` подкаталог `data/<scenario_id>/` материализуется
через `blocksnet_agent.context.materializer`. Сейчас — in-process factory;
для production-MAS потребуется HTTP-клиент к UrbanDB.

## 5. Переменные окружения — единая таблица

| Переменная | Агент | MCP | Обязательна | По умолчанию | Описание |
|---|---|---|---|---|---|
| `CHAT_URL` | ✅ | ❌¹ | для агента | — | OpenAI-совместимый endpoint |
| `API_KEY` | ✅ | ❌¹ | для агента | — | Bearer-токен LLM |
| `MODEL` | ✅ | ❌ | нет | `gpt-4o-mini` | Модель агента |
| `DATA_DIR` | ✅ | ✅ | нет | `./data` | Каталог данных города |
| `OUTPUT_DIR` | ✅ | ✅ | нет | `./outputs` | Каталог артефактов |
| `MAX_ITERATIONS` | ✅ | ❌ | нет | `24` | Лимит итераций агента |
| `DEADLINE_SEC` | ✅ | ✅ | нет | `480` | Дедлайн одного прогона |
| `PROGRESS_INTERVAL_SEC` | ✅ | ✅ | нет | `10.0` | Интервал progress-уведомлений |
| `SESSION_TTL_SEC` | ❌ | ✅ | нет | `1800` | TTL MCP-сессии (сек) |
| `MAX_SESSIONS` | ❌ | ✅ | нет | `8` | LRU лимит MCP-сессий |
| `ENABLE_AGENT_TOOL` | ❌ | ✅ | нет | `true` | Регистрировать ли legacy `analyze_urban_question` |
| `A2A_HOST` | ✅ | ❌ | нет | `0.0.0.0` | A2A-сервер host |
| `A2A_PORT` | ✅ | ❌ | нет | `8080` | A2A-сервер port |
| `A2A_PUBLIC_URL` | ✅ | ❌ | нет | — | URL за reverse-proxy (Agent Card) |
| `A2A_MAX_CONCURRENT_TASKS` | ✅ | ❌ | нет | `2` | Лимит параллельных A2A-задач |
| `A2A_TASK_TTL_SEC` | ✅ | ❌ | нет | `3600` | TTL завершённых A2A-задач |
| `A2A_PROGRESS_INTERVAL_SEC` | ✅ | ❌ | нет | `10.0` | Интервал TaskStatusUpdateEvent |
| `A2A_AUTH_ENABLED` | ✅ | ❌ | нет | `false` | Включить Bearer auth для A2A |
| `A2A_MAS_BEARER_TOKEN` | ✅ | ❌ | при `A2A_AUTH_ENABLED=true` | — | Статический токен A2A |
| `URBANDB_URL` / `URBANDB_TOKEN` | ✅ | ✅ | при `scenario_id` | — | Материализация сценариев |

¹ Только для deprecated `analyze_urban_question` (legacy LLM-агент). Raw-tools работают без LLM.

## 6. Проверка работоспособности

### Локально

```bash
# MCP без LLM (raw-tools only)
CHAT_URL= API_KEY= python -c "import blocksnet_mcp.server; print('OK')"

# Smoke-скрипты
python scripts/smoke_mcp_tools.py   # SMOKE OK
python scripts/smoke_a2a_agent.py   # SMOKE OK

# pytest
pytest
```

### Docker

```bash
bash scripts/smoke_docker.sh  # требует docker + curl
```

## 7. Устранение неполадок

| Симптом | Причина | Решение |
|---|---|---|
| `RuntimeError: AUTH_ENABLED=true but MAS_BEARER_TOKEN is not set` | Не задан A2A-токен при включённом auth | Задать `A2A_MAS_BEARER_TOKEN` |
| `SessionScenarioMismatch` | Смена `scenario_id` в существующей сессии | Открыть новую сессию (`open_session` с другим id) |
| `SCENARIO_NOT_MATERIALIZED` | `scenario_id` задан, но `data_dir/<scenario_id>/` не существует | Создать каталог или использовать `materializer` |
| `DeadlineExceeded` | Прогон не успел за `DEADLINE_SEC` | Увеличить deadline или уменьшить `MAX_ITERATIONS` |
| MCP-server не стартует | Не установлены системные пакеты | Проверить `libgdal`, `libgeos`, `libproj` |
| A2A возвращает 401 | Токен невалиден или отсутствует | Проверить `Authorization: Bearer <token>` |

## 8. См. также

| Документ | Назначение |
|---|---|
| [architecture.md](architecture.md) | Архитектура двух решений |
| [tool_contract.md](tool_contract.md) | Контракт: 32 MCP-tools, 2 A2A skill-а |
| [a2a_agent_card.md](a2a_agent_card.md) | Карточка A2A-агента |
| [mcp_tool_catalog.md](mcp_tool_catalog.md) | Каталог MCP-инструментов |