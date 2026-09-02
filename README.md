# blocksnet-agent

`blocksnet-agent` включает два решения для городской аналитики поверх `BlocksNetAgent`:

- **MCP-server** (`python -m blocksnet_mcp`) — stdio, 36 raw-tools + 3 session-tools.
Не требует LLM.
- **A2A-агент** (`python -m blocksnet_agent`) — HTTP, 2 skill-а: `run_pipeline`
и `analyze_urban_question`. Требует LLM через `CHAT_URL` и `API_KEY`.

MCP-server — для интеграции в LLM-агенты через Model Context Protocol.
A2A-агент — для интеграции в MAS-цепочки (Agent-to-Agent) и standalone
HTTP-клиентов.

Главный навигационный индекс: [docs/WIKI-LLM.md](docs/WIKI-LLM.md).

## Концепция

Ценность системы — в рассуждающем слое агента:

1. **PTR-цикл** `predict → test → revise`: фальсифицируемые гипотезы до расчётов.
2. **RAG по инструментам**: короткие описания + полные карточки через `find_tools`/`get_tool_help`.
3. **Инварианты M1-M3, C1/C2/C3**: заземленность, измеренность, самосогласованность.
4. **Измеренные предложения развития**: TPE-оптимизация + сценарная проверка.
5. **Структурный финальный синтез**: 7-секционный decision memo на русском, всегда
  возвращается клиенту (поля `synthesis` / `synthesis_citations` / `synthesis_path` /
   `synthesis_fallback` в payload).

Два транспорта, два потребителя, **одна кодовая база**:

- **MCP-tool path** — клиент вызывает конкретный `compute_`*/`load_*` напрямую
(без LLM-цикла). Подходит для: интеграции в Claude/Cursor/etc, скриптов, дашбордов.
- **A2A-skill path** — клиент отправляет `run_pipeline(question)` и получает
стриминг статусов + финальный JSON. Подходит для: MAS-оркестрации, чат-агентов.



## Quickstart



### Локальный запуск (без Docker)

```bash
# Создать venv и установить зависимости
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[agent,dev]"

# Скопировать и заполнить .env
cp .env.example .env
# CHAT_URL=https://ollama.com/v1
# API_KEY=...
# DATA_DIR=./data
# OUTPUT_DIR=./outputs

# 1) MCP-server (stdio) — для MCP-клиентов
python -m blocksnet_mcp

# 1b) MCP-server по HTTP (streamable-http) — для сетевых MCP-клиентов (Synapse и т.п.)
TRANSPORT=http HOST=0.0.0.0 PORT=8001 python -m blocksnet_mcp
# → http://0.0.0.0:8001/mcp (MCP), http://0.0.0.0:8001/health (data_ready, datasets)

# 2) A2A-агент (HTTP) — для standalone / MAS
python -m blocksnet_agent
# → http://0.0.0.0:8080/ (Agent Card, JSON-RPC /, /health)
```



### Docker

```bash
docker compose build
docker compose up -d
# agent    — http://localhost:8080
# mcp      — stdio (для MCP-клиента: docker compose exec mcp python -m blocksnet_mcp)
# mcp-http — http://localhost:8001/mcp (streamable-http) + /health
```

`mcp-http` — тот же MCP-сервер, но по streamable-http: `TRANSPORT=http`,
`HOST`/`PORT`/`MCP_PATH` (или `MCP_HOST`/`MCP_PORT`) задают адрес. Собирается из
`Dockerfile.agent`, потому что `analyze_urban_question` требует LLM-стек агента.
Если `data/` лежит на сетевом экспорте (NFS/GPFS), оставьте `SQLITE_USE_OGR_VFS=YES`:
без него SQLite не получает POSIX-лок на `.gpkg` и GDAL отвечает `disk I/O error`.

Подробнее — [docs/deployment.md](docs/deployment.md).

## Каталог инструментов и контракт

- **MCP**: [docs/mcp_tool_catalog.md](docs/mcp_tool_catalog.md) — auto-generated из
живого кода через `build_catalog()`. 36 raw-инструмента + 3 session-tools.
- **A2A**: [docs/a2a_agent_card.md](docs/a2a_agent_card.md) — карточка сервиса
(реальный вывод), описание skill-ов.
- **Контракт**: [docs/tool_contract.md](docs/tool_contract.md) — формат
ответа (поля `synthesis` / `synthesis_citations` / `synthesis_path` /
`synthesis_fallback` в §12), коды ошибок, поведение по сценариям.



## Структура репозитория

```text
blocksnet-agent/
├── README.md
├── pyproject.toml           # extras: mcp / agent / dev
├── requirements.txt         # back-compat alias для pip install -r
├── Dockerfile.mcp           # raw-tools образ, без LLM
├── Dockerfile.agent         # A2A-агент, с langgraph + a2a-sdk
├── docker-compose.yml
├── .dockerignore
├── blocksnet_mcp/           # MCP-обёртка
│   ├── server.py            # lazy singleton (get_mcp())
│   ├── envelope.py          # P0.2 envelope + локальная is_failed_observation
│   ├── session.py           # SessionStore (LRU+TTL)
│   ├── settings.py
│   ├── agent_tool.py        # legacy LLM (LLM_NOT_CONFIGURED)
│   ├── serialize.py         # P1.1/P1.2/P1.6 + P-S5.3 _attach_synthesis()
│   └── tools_mcp.py         # back-compat shim
├── blocksnet_agent/         # общее ядро
│   ├── a2a/                 # A2A-агент (FastAPI)
│   ├── authcore.py          # StaticTokenVerifier
│   ├── context.py           # ScenarioContext + resolve_context
│   ├── payload.py           # build_payload (общий для A2A и MCP)
│   ├── runtime.py           # per-run stop_event
│   ├── synthesis.py         # P-S5.x: финальный структурный синтез (7-секционный decision memo)
│   ├── tools/               # raw tools + catalog
│   └── ...
├── tests/                   # unit-, contract- и integration-тесты
├── scripts/                 # smoke_mcp_tools, smoke_a2a_agent, generate_tool_catalog, smoke_docker
└── docs/                    # tool_contract.md, mcp_tool_catalog.md (auto-gen), A2A Agent Card, ...
```



## Контракт инструмента

`analyze_urban_question(question, max_iterations=None)` — DEPRECATED legacy MCP-инструмент
для back-compat. Сохранён, но новые интеграции используют A2A `run_pipeline`.
Полная спецификация контракта: [docs/tool_contract.md](docs/tool_contract.md).

## Финальный ответ

После рефакторинга P-S5.x клиент **всегда** получает структурный ответ — агент больше не выдаёт «9-секционный дамп графа» в prose. Финальный синтез собирается отдельным шагом в `blocksnet_agent/synthesis.py.`

### Жизненный цикл одного вызова

```text
ReAct (AgentExecutor)
  └─ submit_answer(question, result, recommendations, measured_effects, ...) — optional
     └─ Refine layer (M1/M2/M3), hypothesis classifier
        └─ synthesize(question, steps, ledger)             ← blocksnet_agent/synthesis.py
           ├─ collect_evidence: фильтр observations + supported/refuted гипотезы
           ├─ LLM-вызов: 7-секционный decision memo на русском
           └─ write_synthesis → run_dir/synthesis.md (mkdir -p parents)
              └─ to_json(result)                            ← blocksnet_mcp/serialize.py
                 ├─ payload["recommendation_blocks"]   (если submit_answer / overlay)
                 ├─ payload["measured"]                 (если submit_answer)
                 └─ _attach_synthesis(payload, result)  ← P-S5.3
                       └─ payload["synthesis"] / ["synthesis_citations"]
                          / ["synthesis_path"] / ["synthesis_fallback"]
```



### Что отдаёт `payload` пользователю (фрагмент JSON-RPC result)

```jsonc
{
  "question": "Где в СПб разместить новые спортивные площадки?",
  "result": "…числа модели…",                 // legacy back-compat для regex-парсера
  "analysis_plan": "…",
  "hypotheses": [{"claim": "...", "status": "supported", "evidence": "..."}],
  "recommendation_blocks": [603, 712, 845],
  "measured": {"sports_grounds": {"strong_before": 0.42, "strong_after": 0.78}},
  "confidence": 0.62,
  "limitations": ["Some PTR hypotheses are inconclusive"],
  "salvaged": false,

  // — P-S5.x: структурный синтез, всегда —
  "synthesis": "## Ответ\n\nВ кварталах 603, 712 и 845…\n\n## Как читаю вопрос\n…",
  "synthesis_citations": ["[compute_service_provision]", "[compute_scenario_provision]"],
  "synthesis_path": "/root/.../outputs/run_20260723-135209-25fab0/synthesis.md",
  "synthesis_fallback": false
}
```



### 7 секций `synthesis` (порядок фиксирован)


| №   | Заголовок                                     | Что внутри                                                                  |
| --- | --------------------------------------------- | --------------------------------------------------------------------------- |
| 0   | `## Вопрос`                                   | Эхо исходного вопроса                                                       |
| 1   | `## Ответ`                                    | Committed conclusion в 1–3 предложения + `(доверие 0.NN)`                   |
| 2   | `## Как читаю вопрос`                         | Проблемная рамка задачи                                                     |
| 3   | `## На чём держится ответ`                    | Главные компоненты: инструменты, гипотезы                                   |
| 4   | `## Варианты, которые взвешивал`              | Реальные альтернативы + почему выбранный путь выигрывает                    |
| 5   | `## Аргумент «за»`                            | Подтверждающие наблюдения **с** `[source]` citations                        |
| 6   | `## Что осталось неопределённым`              | Material uncertainty + confidence каждого утверждения                       |
| 7   | `## Где это рассуждение может быть ошибочным` | Reasoning-level critique: rejected framings, calibrated claims, source bias |
| +   | `## Ограничения`                              | Авто-собранные limitations (failed obs + inconclusive гипотезы)             |


## Индексация WIKI-LLM


| Индекс                                                 | Назначение                                          |
| ------------------------------------------------------ | --------------------------------------------------- |
| [docs/WIKI-LLM.md](docs/WIKI-LLM.md)                   | Главная карта проекта для LLM-навигации             |
| [docs/README.md](docs/README.md)                       | Человекочитаемый индекс документации                |
| [blocksnet_mcp/README.md](blocksnet_mcp/README.md)     | Индекс MCP-слоя                                     |
| [blocksnet_agent/README.md](blocksnet_agent/README.md) | Индекс переносимого ядра агента                     |
| [tests/README.md](tests/README.md)                     | Индекс контрактных тестов                           |
| [scripts/README.md](scripts/README.md)                 | Индекс CLI-скриптов для проверки и подготовки данных |




## Документация


| Документ                                             | О чём                                                                 |
| ---------------------------------------------------- | --------------------------------------------------------------------- |
| [docs/architecture.md](docs/architecture.md)         | Целевая архитектура: два транспорта (MCP + A2A), поток `run_pipeline` |
| [docs/tool_contract.md](docs/tool_contract.md)       | Контракт MCP-инструментов и A2A-skill-ов: формат ответа, сессии, auth |
| [docs/mcp_tool_catalog.md](docs/mcp_tool_catalog.md) | Auto-generated каталог 36 raw-инструментов + 3 session-tools          |
| [docs/a2a_agent_card.md](docs/a2a_agent_card.md)     | Реальная карточка A2A-агента, описание полей                          |
| [docs/deployment.md](docs/deployment.md)             | Локальный запуск + Docker compose + единая таблица env                |
| [docs/WIKI-LLM.md](docs/WIKI-LLM.md)                 | Карта репозитория для LLM-навигации                                   |




## Статус

**Два решения готовы:** MCP-server (`python -m blocksnet_mcp`, 36 raw-tools + 3 session-tools,
без LLM) и A2A-агент (`python -m blocksnet_agent`, 2 skill-а, с LLM). Bearer auth +
scenario_id, per-run stop, Docker с разделением зависимостей. **Финальный синтез
(7-секционный decision memo)** собирается всегда, в `run_dir/synthesis.md` и в payload.
Контракты A2A и MCP покрыты профильными тестами.

**Preparation через MCP:** `build_blocks_with_services`, `prepare_accessibility_matrix`,
`prepare_road_congestion_inputs` — три новых инструмента, которые раньше жили как
CLI-скрипты в `scripts/`. Агент теперь сам собирает данные через них, без внешних команд.

> Этот README описывает только текущее состояние продукта.

