# WIKI-LLM индекс проекта

Назначение: единая карта `blocksnet-agent` для навигации. Индекс помогает быстро
выбрать нужные файлы, не загружая весь репозиторий в контекст.

## Как пользоваться

1. Для общего понимания — `README.md`, затем `docs/architecture.md`.
2. Для понимания контракта — `docs/tool_contract.md`, `docs/mcp_tool_catalog.md`.
3. Для развёртывания — `docs/deployment.md` + `RUN.md`.
4. Для правок MCP-слоя — открыть `blocksnet_mcp/`, начать с `server.py`.
5. Для правок A2A-слоя — открыть `blocksnet_agent/a2a/`, начать с `server.py`.
6. Для правок ядра агента — открыть `blocksnet_agent/`, начать с `__init__.py`.

## Верхний уровень

| Путь | Роль | Когда открывать |
|---|---|---|
| `README.md` | Главная спецификация репозитория | Первый файл для понимания |
| `RUN.md` | Быстрый запуск для получателя (5 мин до работающего запуска) | При первом знакомстве с проектом |
| `project.yaml` | Маркер для `/root/workflow` индексатора | При индексации |
| `.gitattributes` | Правила Git | При настройке репозитория |
| `.gitignore` | Игнор локального окружения, outputs, кэшей, данных | При добавлении файлов |
| `.env.example` | Шаблон конфигурации (Ollama Cloud / OpenRouter / локальный) | При настройке `.env` |
| `requirements.txt` | Зависимости (legacy alias `pip install -r`) | При пересборке окружения |
| `pyproject.toml` | Зависимости + extras `mcp` / `agent` / `dev` | При настройке окружения |
| `.venv/` | Виртуальное окружение | gitignored |
| `.git/` | Git-метаданные | Не источник |
| `docs/` | Документация | При анализе проекта |
| `blocksnet_mcp/` | MCP-обертка + сессии + envelope | При правках MCP-слоя |
| `blocksnet_agent/` | Ядро агента (ReAct, PTR, confidence) | При правках агента |
| `blocksnet_agent/a2a/` | A2A-агент (FastAPI, agent_card, skills, auth) | При правках A2A-слоя |
| `data/` | Локальная модель города и нормативы | При настройке данных |
| `tests/` | Набор unit-, contract- и integration-тестов | При проверке контракта |
| `scripts/` | smoke + generate_tool_catalog + validate_agent_card + package_data + fetch_data | При ручной проверке |
| `examples/` | Notebook + локальные city-sandboxes | При визуальном анализе |

## Inventory папок

| Папка | Состав | Роль |
|---|---|---|
| `docs/` | 8 актуальных .md | Документация |
| `blocksnet_mcp/` | `server.py`, `__init__.py`, `__main__.py`, `envelope.py`, `session.py`, `settings.py`, `agent_tool.py`, `serialize.py`, `tools_mcp.py` | MCP-обёртка |
| `blocksnet_agent/` | Пакет агента и `tools/` (data/network/provision/services/indicators/optimize/preparation/viz/registry/demand) | Ядро агента |
| `blocksnet_agent/a2a/` | `server.py`, `agent_card.py`, `auth.py`, `executor.py`, `schemas.py`, `settings.py`, `skills.py`, `task_manager.py`, `__main__.py` | A2A-агент |
| `data/` | `service_type.json`, `archetypes.csv`, `service_aliases.json` (версионируются); gpkg/pickle — gitignored | Локальная модель города |
| `tests/` | Контракт, сериализация, runtime, PTR, isolation и A2A | Проверка реализации |
| `scripts/` | `smoke_mcp_tools.py`, `smoke_a2a_agent.py`, `generate_tool_catalog.py`, `validate_agent_card.py`, `package_data.py`, `fetch_data.py`, `smoke_docker.sh`, `smoke_mcp_docker.py`, `smoke_client.py` | Ручная проверка |
| `examples/` | `test_visualization.ipynb`, `saint_petersburg/`, `yuzhno-sakhalinsk/` (gitignored) | Визуализация + preprocessing |

## Документация (актуальная)

| Путь | Роль |
|---|---|
| `docs/README.md` | Человекочитаемый индекс документации |
| `docs/WIKI-LLM.md` | Этот LLM-навигационный индекс |
| `docs/architecture.md` | Целевая архитектура: два транспорта (MCP + A2A), поток `run_pipeline` |
| `docs/tool_contract.md` | Контракт: 36 MCP-tools, 2 A2A skill-а, сессии, auth |
| `docs/deployment.md` | Quickstart (локальный + Docker), env-таблица |
| `docs/mcp_tool_catalog.md` | Auto-generated каталог 36 raw-инструментов |
| `docs/a2a_agent_card.md` | Реальная карточка A2A-агента, описание полей |

## Целевой MCP-слой

Папка `blocksnet_mcp/` — тонкая обертка. Не изменяет логику агента.

| Путь | Роль |
|---|---|
| `blocksnet_mcp/README.md` | Индекс и правила ответственности MCP-слоя |
| `blocksnet_mcp/__init__.py` | Lazy re-export через `__getattr__` — `import blocksnet_mcp` не тянет LLM-стек |
| `blocksnet_mcp/__main__.py` | `python -m blocksnet_mcp` → stdio |
| `blocksnet_mcp/server.py` | FastMCP, lazy singleton (`get_mcp()`), stdio-транспорт, каталог 36+3 инструментов, сессии с `scenario_id`, per-request tools (изоляция) |
| `blocksnet_mcp/envelope.py` | Envelope (`status`/`tool`/`session_id`/`text`/`artifacts`/`error_code`/`error`) + локальная копия `is_failed_observation` |
| `blocksnet_mcp/session.py` | SessionStore: LRU+TTL, изоляция state, `scenario_id` привязка |
| `blocksnet_mcp/settings.py` | `MCPSettings`: pydantic-settings, env-алиасы, `reset_mcp_settings()`. Все LLM-поля optional |
| `blocksnet_mcp/agent_tool.py` | Legacy `analyze_urban_question` для back-compat; ленивые LLM-импорты |
| `blocksnet_mcp/tools_mcp.py` | Back-compat shim: `from blocksnet_mcp.tools_mcp import analyze_urban_question` работает |
| `blocksnet_mcp/serialize.py` | Преобразование `AgentResult` в строгий JSON. `AgentResult` через `TYPE_CHECKING`. **P-S5.3:** `_attach_synthesis()` прикладывает `synthesis` / `synthesis_citations` / `synthesis_path` / `synthesis_fallback` в payload (см. `tool_contract.md` §12) |

## Переносимое ядро агента

| Путь | Роль |
|---|---|
| `blocksnet_agent/__init__.py` | Публичный API: `BlocksNetAgent`, `AgentResult` |
| `blocksnet_agent/agent.py` | Основной ReAct/tool-calling агент, инварианты, confidence, **терминальный `submit_answer`** — **инвариант 1: не редактируется без согласования**. **P-S5.1:** `write_run_log` теперь логирует ошибку, не проглатывает. **P-S5.2:** после refine+classify всегда вызывает `synthesize(...)` и пишет `run_dir/synthesis.md`. **P-S5.3 fix:** `settings` → `self._settings` (F821 ruff) |
| `blocksnet_agent/runtime.py` | `start_run(deadline_sec=...)` — per-run контекст, per-run stop_event. `stop_run(all_runs=True)` — shutdown |
| `blocksnet_agent/payload.py` | `build_payload(result, run_dir, status, error, error_code)` — общий для A2A и MCP |
| `blocksnet_agent/authcore.py` | `StaticTokenVerifier` (constant-time compare), `Principal`, `AuthError` |
| `blocksnet_agent/context.py` | `ScenarioContext` + `resolve_context(scenario_id, project_id, ...)`. Whitelist `[a-zA-Z0-9_-]{1,64}`. Path traversal защита |
| `blocksnet_agent/a2a/` | A2A-агент: см. отдельную таблицу |
| `blocksnet_agent/hypotheses.py` | PTR-цикл гипотез; `overlay_candidates` |
| `blocksnet_agent/prompts.py` | System prompt и формат ответа |
| `blocksnet_agent/config.py` | Настройки и корень проекта |
| `blocksnet_agent/llm.py` | OpenAI-compatible LLM |
| `blocksnet_agent/metrics.py` | Метрики и инварианты C1/C2/C3 |
| `blocksnet_agent/tools/` | Доменные инструменты BlocksNet и RAG-справка по tools |
| `blocksnet_agent/synthesis.py` | Финальный структурный синтез ответа (7-секционный decision memo): `FinalSynthesis` + `synthesize()` + `write_synthesis()` + `collect_evidence()`. **Инвариант 6 в `architecture.md` §6 — стабильная точка вызова** |

## A2A-агент

| Путь | Роль |
|---|---|
| `blocksnet_agent/a2a/__init__.py` | Пакетный индекс: re-exports `A2ASettings`, `TaskManager`, `execute_run_pipeline`, `SKILLS`, `build_agent_card`, `build_app`, `main` |
| `blocksnet_agent/a2a/server.py` | FastAPI-приложение: Agent Card, JSON-RPC `/`, `/health` + auth middleware |
| `blocksnet_agent/a2a/agent_card.py` | `build_agent_card(host, port, public_url)` — protobuf-карточка a2a-sdk 1.1.1 с 2 skill-ами и supportedInterfaces |
| `blocksnet_agent/a2a/executor.py` | `execute_run_pipeline(...)` — мост A2A ↔ BlocksNetAgent, per-run stop_event |
| `blocksnet_agent/a2a/schemas.py` | Pydantic: `RunPipelineInput`, `AnalyzeUrbanQuestionInput`, `SkillOutput` |
| `blocksnet_agent/a2a/settings.py` | `A2ASettings(Settings)`: LLM + transport + auth + concurrency |
| `blocksnet_agent/a2a/skills.py` | `SKILLS` реестр: `run_pipeline` + `analyze_urban_question` (DEPRECATED) |
| `blocksnet_agent/a2a/task_manager.py` | `TaskManager`: Semaphore, per-run stop_event, TTL cleanup |
| `blocksnet_agent/a2a/auth.py` | FastAPI middleware для Bearer-токена; fail-fast при `A2A_AUTH_ENABLED=true` без `A2A_MAS_BEARER_TOKEN` |
| `blocksnet_agent/a2a/__main__.py` | `python -m blocksnet_agent.a2a` |

## Данные

| Путь | Формат | Роль | Git |
|---|---|---|---|
| `data/service_type.json` | JSON | Нормативы сервисов | да |
| `data/archetypes.csv` | CSV | Веса архетипов для TPE | да |
| `data/service_aliases.json` | JSON | Алиасы сервисов (RU/EN/synonyms) | да |
| `data/blocks_with_services.gpkg` | GeoPackage | Кварталы, сервисы, геометрия, население | gitignored |
| `data/acc_mx.pickle` | Pickle | Предвычисленная матрица доступности | gitignored |
| `data/<local-city>/` | mixed | Локальные city-sandbox'ы | gitignored |

## Окружение

| Команда | Роль |
|---|---|
| `.venv/bin/python -m pytest` | Все тесты |
| `.venv/bin/python -m pip check` | Проверка зависимостей |
| `DATA_DIR=data/saint_petersburg .venv/bin/python -m blocksnet_mcp` | Точка входа stdio MCP |
| `DATA_DIR=data/saint_petersburg .venv/bin/python -m blocksnet_agent` | Точка входа A2A HTTP |
| `docker compose up -d` | Запуск через Docker (на обычной машине) |
| `.venv/bin/python scripts/smoke_mcp_tools.py` | MCP smoke |
| `.venv/bin/python scripts/smoke_a2a_agent.py` | A2A smoke |

## Тесты

| Путь | Роль |
|---|---|
| `tests/test_serialize.py` | `AgentResult -> JSON` |
| `tests/test_tool_contract.py` | Схема входа/выхода `analyze_urban_question` |
| `tests/test_async_mcp_contract.py` | Async FastMCP: progress + DEADLINE_SEC |
| `tests/test_runtime.py` | `start_run`, deadline, прогресс |
| `tests/test_confidence_signals.py` | `confidence_basis` и `confidence_self` |
| `tests/test_overlay_candidates.py` | overlay-кандидаты |
| `tests/test_ptr_classifier.py` | PTR-классификатор |
| `tests/test_provision_cache.py` | Мемоизация `compute_/list_/load_` |
| `tests/test_numeric_metric_resolution.py` | Резолвер числовых метрик |
| `tests/test_provision_summaries.py` | Сводки provision |
| `tests/test_target_block_selection.py` | `suggest_target_blocks` |
| `tests/test_tool_failure_dedup.py` | Дедуп failed-вызовов |
| `tests/test_no_data_grounding.py` | NO_DATA-маркеры |
| `tests/test_mcp_session.py` | SessionStore LRU+TTL |
| `tests/test_mcp_session_scenario.py` | Привязка scenario_id |
| `tests/test_mcp_tool_exposure.py` | 36 tools в каталоге, `submit_answer` не экспонирован |
| `tests/test_image_deps.py` | MCP без LLM-зависимостей (для изоляции образа) |
| `tests/test_a2a_card.py` | Agent Card имеет 2 skill-а |
| `tests/test_a2a_tasks.py` | TaskManager concurrent + TTL |
| `tests/test_a2a_skills.py` | SKILLS контракт |
| `tests/test_auth.py` | authcore + middleware |
| `tests/test_context_adapter.py` | `resolve_context` + path-traversal защита |
| `tests/test_runtime_stop_scope.py` | Per-run stop_event изоляция |
| `tests/test_settings_inheritance.py` | Settings inheritance |
| `tests/test_tool_catalog_docs.py` | Auto-generated каталог не протухает |
| `tests/test_synthesis.py` | Synthesis-узел и обратная совместимость `to_json` |

## Потоки работ

| Задача | Минимальный контекст |
|---|---|
| Понять продукт | `README.md`, `docs/architecture.md`, `docs/WIKI-LLM.md` |
| Реализовать MCP-server | `docs/architecture.md`, `docs/tool_contract.md`, `blocksnet_mcp/README.md` |
| Реализовать A2A-агента | `docs/architecture.md`, `docs/tool_contract.md`, `blocksnet_agent/a2a/` |
| Настроить локальный запуск | `RUN.md`, `docs/deployment.md`, `.env.example` |
| Написать сериализацию | `docs/tool_contract.md`, `blocksnet_mcp/README.md`, `blocksnet_mcp/serialize.py` |
| Добавить тесты | `docs/tool_contract.md`, `tests/` |
| Изменить финальный синтез | `docs/tool_contract.md` §12, `docs/architecture.md` §6 (инвариант 6), `blocksnet_agent/synthesis.py`, `tests/test_synthesis.py` |
| Проверить MCP stdio | `scripts/smoke_mcp_tools.py` |
| Проверить A2A HTTP | `scripts/smoke_a2a_agent.py` |

## Границы источников

- Источник истины по текущему состоянию: `README.md`, `docs/architecture.md`, `docs/tool_contract.md`, `docs/deployment.md`, `docs/mcp_tool_catalog.md`, `docs/a2a_agent_card.md`.
- Источник истины по поведению агента: код `blocksnet_agent/` (файлы перечислены выше).
- Источник истины по поведению MCP: код `blocksnet_mcp/`.
- Источник истины по поведению A2A: код `blocksnet_agent/a2a/`.
| Источник истины по `.env` параметрам: `.env.example`.
- Источник истины по тестовому покрытию: `tests/`.
