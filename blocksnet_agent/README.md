# Индекс `blocksnet_agent/`

Назначение: переносимое рассуждающее ядро агента городской аналитики.

`blocksnet-agent` состоит из двух решений на общем ядре:
- `blocksnet_mcp/` — MCP-server (stdio), оборачивает ядро, без LLM.
- `blocksnet_agent/` — A2A-агент (HTTP, FastAPI), плюс само ядро и инструменты.

Этот пакет не должен менять логику PTR-цикла, инвариантов, tool-calling и доменных
расчётов без отдельной задачи. Текущая версия дополнена слоем P1.1 (терминальный
`submit_answer` для структурного ответа), P1.2 (авторитетная `confidence` +
`confidence_self` + `confidence_basis`) и P1.6 (`overlay_candidates` как fallback
для `recommendation_blocks`); всё это инвариантно для MCP-server.

## Состав

| Файл или папка | Роль |
|---|---|
| `__init__.py` | Публичный API: `BlocksNetAgent`, `AgentResult` (с полями `submitted_answer`, `overlay_recommendations`, `overlay_meta`, `confidence_basis`) |
| `__main__.py` | `python -m blocksnet_agent` → запуск A2A-сервера |
| `agent.py` | Основной агент, запуск tool-calling, инварианты, confidence, терминальный `submit_answer` |
| `hypotheses.py` | PTR-цикл: генерация, проверка и ревизия гипотез; `overlay_candidates` для P1.6 |
| `prompts.py` | System prompt и формат ответа |
| `config.py` | Настройки и загрузка окружения |
| `llm.py` | OpenAI-compatible LLM |
| `runtime.py` | `outputs/run_*`, `run_log`, deadline, прогресс, регистрация артефактов |
| `metrics.py` | Метрики и проверки, используемые агентом |
| `payload.py` | `build_payload(result, run_dir, status, error, error_code)` — общий для A2A и MCP |
| `synthesis.py` | Финальный структурный синтез (7-секционный decision memo) |
| `context.py` | `ScenarioContext` + `resolve_context(scenario_id, project_id)` |
| `authcore.py` | `StaticTokenVerifier` (constant-time compare), `Principal`, `AuthError` |
| `tools/` | Доменные инструменты BlocksNet и RAG-справка: `data`, `network`, `provision`, `services`, `indicators`, `optimize`, `preparation`, `viz`, `registry`, `demand` |
| `a2a/` | A2A-агент (см. отдельный README не существует, см. `docs/a2a_agent_card.md`) |
| `vendor/` | Сторонний код, адаптированный для проекта (например, `road_congestion/`) |

## Что не относится к этому пакету

Eval-скрипты, notebooks, HTML-отчёты и runtime-outputs не входят в состав пакета.
Скрипты для подготовки данных и smoke-проверки — в `scripts/`.