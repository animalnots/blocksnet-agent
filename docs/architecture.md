# Архитектура `blocksnet-agent`

> **Актуальная архитектура проекта.** Документ описывает целевое состояние:
> два транспорта (MCP-stdio + A2A-HTTP) на одном ядре агента.

---

## 1. Главная идея

Два решения на одном ядре:

- **MCP-server** (`python -m blocksnet_mcp`) — stdio, **36 raw-инструмента + 3 session-tools**.
  Не требует LLM. Подходит для интеграции в LLM-агенты (Claude/Cursor), скрипты,
  дашборды.
- **A2A-агент** (`python -m blocksnet_agent`) — HTTP (FastAPI), **2 skill-а**:
  `run_pipeline` (основной) и `analyze_urban_question` (DEPRECATED, back-compat).
  Требует LLM (CHAT_URL/API_KEY). Подходит для MAS-оркестрации, standalone чат-агентов.

Оба опираются на общий пакет `blocksnet_agent/`: tools, runtime, auth, context,
payload, конфиг. Никакой дубликации — один источник правды.

## 2. Структура пакетов

```text
                        ┌───────────────────────────────────────────────┐
                        │  blocksnet_agent/  (общее ядро)                │
                        │                                                │
                        │  ┌──────────────┐    ┌──────────────────────┐ │
                        │  │ runtime.py   │    │ tools/ + catalog.py  │ │
                        │  │ (per-run,    │    │ 36 raw tools + RAG   │ │
                        │  │  start_run)  │    │                      │ │
                        │  └──────────────┘    └──────────────────────┘ │
                        │  ┌──────────────┐    ┌──────────────────────┐ │
                        │  │ authcore.py  │    │ context.py           │ │
                        │  │ (auth-shared)│    │ (ScenarioContext)    │ │
                        │  └──────────────┘    └──────────────────────┘ │
                        │  ┌───────────────────────────────────────────┐ │
                        │  │ a2a/                                      │ │
                        │  │ server.py, executor.py, task_manager.py, │ │
                        │  │ skills.py, agent_card.py, auth.py         │ │
                        │  └───────────────────────────────────────────┘ │
                        └─────────────────┬─────────────────────────────┘
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              │                                                       │
   ┌──────────▼────────────┐                          ┌───────────────▼──────────┐
   │ blocksnet_mcp/        │                          │ blocksnet_agent.a2a/    │
   │ (MCP-server)          │                          │ (A2A-агент)             │
   │                       │                          │                         │
   │ server.py   ───std─►  │                          │ server.py   ──HTTP──►   │
   │   FastMCP             │                          │   FastAPI               │
   │ envelope.py           │                          │                         │
   │ session.py            │                          │ 36 raw-tools не         │
   │  (LRU+TTL+isolation)  │                          │ экспонируются —         │
   │                       │                          │ только 2 skill-а:       │
   │ 36 raw-tools          │                          │ run_pipeline +          │
   │ + 3 session-tools     │                          │ analyze_urban_question  │
   └───────────────────────┘                          └─────────────────────────┘
              │                                                     │
              │ stdio (MCP-клиент)                                  │ JSON-RPC / SendMessage
              │                                                     │
   ┌──────────▼───────────┐                            ┌───────────▼──────────────┐
   │ LLM-агенты:          │                            │ MAS-оркестратор /        │
   │ Claude / Cursor /    │                            │ standalone HTTP-клиент  │
   │ LangGraph            │                            │                         │
   └──────────────────────┘                            └─────────────────────────┘
```

## 3. Граница ответственности

| Пакет | Транспорт | LLM | Размер | Кто использует |
|---|---|---|---|---|
| `blocksnet_agent.a2a` | HTTP (FastAPI) | ✅ | большой | MAS, standalone |
| `blocksnet_mcp` | stdio (MCP) | ❌ | компактный | LLM-агенты, IDE |
| `blocksnet_agent` (общее) | — | ✅ | большой | зависимость обоих |

**Главное правило:** `blocksnet_agent/` — общее ядро без знания о транспорте.
`blocksnet_mcp/` и `blocksnet_agent.a2a/` — транспортные адаптеры, оба импортируют
ядро, но не зависят друг от друга.

## 4. Поток `run_pipeline` (A2A → agent → output)

```text
JSON-RPC SendMessage (Bearer)
  │
  ▼
DefaultRequestHandler → AgentExecutor.execute()
  │  DataPart scenario_id → ScenarioContext
  ▼
TaskManager.submit() → Semaphore(MAX_CONCURRENT)
  │
  ▼ (worker thread)
execute_run_pipeline()
  │  resolve_context(scenario_id)  ← blocksnet_agent/context.py
  │  AgentSettings(data_dir=ctx.data_dir)  ← blocksnet_agent/config.py
  │  start_run(deadline_sec=...)  ← blocksnet_agent/runtime.py (per-run stop)
  ▼
BlocksNetAgent.run(question)
  │ <tool_call> tool_a → tool_b → submit_answer (terminal) OR prose fallback
  │ Refine layer (M1/M2/M3) — coherence, evidence-grounding
  │ Hypothesis classifier — supported/refuted/inconclusive
  ▼
synthesize(question, steps, ledger)   ← blocksnet_agent/synthesis.py
  │   отбирает verified obs (без failure-маркеров) + supported/refuted гипотезы
  │   LLM-вызов: 7-секционный decision memo на русском (Answer / How I read it /
  │   What it hinges on / Options weighed / Case for it / What's still uncertain /
  │   Where this reasoning could be wrong). Fallback — из сырых observations
  │   пишет run_dir/synthesis.md
  ▼
build_payload(result)  ← blocksnet_agent/payload.py (общий с MCP!)
  │   to_json(result) прикладывает synthesis/synthesis_citations/synthesis_path
  │                     + synthesis_fallback в payload (§12 tool_contract.md)
  ▼
TaskStatusUpdateEvent (streaming)
  │
  ▼
JSON-RPC result → client
```

**Ключевое:** `submit_answer` остаётся терминальной точкой для **структурных**
полей (`recommendations`, `measured_effects`). `synthesis` — **всегда
вызывается вторым слоем** после refine+classify, независимо от того, был ли
`submit_answer`. Это нужно потому что:
- `submit_answer` есть не всегда (агент может не дойти до терминала),
- `submit_answer` не даёт человеко-читаемой структуры (только JSON-плейн),
- итоговый 7-секционный memo клиенту нужен в любом случае.

## 5. Поток MCP-tool (stdio → agent → envelope)

```text
MCP-клиент (Claude/Cursor/etc)
  │
  ▼ stdio JSON-RPC
mcp.call_tool(tool_name, args)  ← blocksnet_mcp/server.py
  │ session_id → store.get_or_create()
  │ data_dir/output_dir → resolve_context(scenario_id)
  ▼
_build_catalog_tools через build_catalog()  ← blocksnet_agent/tools/catalog.py
  │ make_tools(state, data_dir, output_dir) — общая фабрика
  ▼
tool.invoke(args)
  │ start_run(deadline_sec=...) — изоляция сессии
  ▼
build_envelope(tool, session_id, text, artifacts)
  │ is_failed_observation(text) → status
  │ Артефакты из RunLogger.saved_files
  ▼
envelope dict → MCP-клиент
```

## 6. Инварианты архитектуры

1. **`blocksnet_agent/agent.py`, `hypotheses.py`, `metrics.py`, `tools/registry.py`
   не редактируются** без согласования (это инвариант 1 — терминальный `submit_answer`
   должен работать как задокументировано).
2. **`mcp` НЕ импортируется внутри `blocksnet_agent/`** — только docstring-упоминания.
3. **`blocksnet_agent.tools.catalog.build_catalog()` — единственная точка
   регистрации инструментов**; `TOOL_BLOCKLIST` хранит `submit_answer`.
4. **`blocksnet_agent.payload.build_payload()` — единственная обёртка
   результата** для MCP и A2A.
5. **`ScenarioContext` — единый провайдер `data_dir`/`output_dir`**
   для `AgentSettings`.
6. **`blocksnet_agent/synthesis.py` — единственный producer финального
   структурного ответа** для клиента (см. §12 `tool_contract.md`). Вызывается
   **всегда** после `_refine_until_coherent` + `classify_hypothesis_ledger`,
   даже если `submit_answer` не был вызван. Подключение в `agent.run()` —
   стабильная точка (P-S5.2): меняется только контракт `FinalSynthesis`,
   но не место вызова.

## 7. Разделение зависимостей (для Docker)

| Образ | Что тянет | Размер |
|---|---|---|
| `Dockerfile.mcp` | `mcp`, `pydantic-settings`, `geopandas`, `blocksnet`, `numpy`, `pandas`, `matplotlib`, `optuna` | компактный (без LLM) |
| `Dockerfile.agent` | всё из `mcp` + `a2a-sdk`, `langchain-core`, `langchain-classic`, `langchain-openai`, `langgraph`, `tiktoken` | большой (с LLM) |

`import blocksnet_mcp.server` НЕ тянет `langgraph`/`tiktoken`/`langchain_openai`
(verified `tests/test_image_deps.py`, subprocess-проверка).

## 8. См. также

| Документ | Назначение |
|---|---|
| [deployment.md](deployment.md) | Quickstart, env-таблица, Docker |
| [tool_contract.md](tool_contract.md) | Контракт: 33 MCP-tools, 2 A2A skill-а |
| [mcp_tool_catalog.md](mcp_tool_catalog.md) | Auto-generated каталог |
| [a2a_agent_card.md](a2a_agent_card.md) | Карточка A2A-агента |