# Документация `blocksnet-agent`

**MCP-server + A2A-агент городской аналитики** поверх `BlocksNetAgent`.
MCP-server экспонирует 36 raw-tools + 3 session-tools через MCP-stdio, а A2A-агент — 2 skill-а
(`run_pipeline`, `analyze_urban_question`) через A2A-HTTP.

Три новых preparation-tools покрывают сборку `blocks_with_services.gpkg`, `acc_mx.pickle`
и road_congestion inputs через MCP-инструменты (раньше это были CLI-скрипты в `scripts/`).

| Документ | О чём |
|---|---|
| [../README.md](../README.md) | Главная спецификация: концепция, quickstart (MCP+A2A+Docker), структура |
| [WIKI-LLM.md](WIKI-LLM.md) | LLM-индекс проекта: карта директорий, маршруты чтения |
| [architecture.md](architecture.md) | Целевая архитектура: два транспорта (MCP + A2A), поток `run_pipeline`, граница ответственности |
| [tool_contract.md](tool_contract.md) | Контракт: 33 MCP-tools, 2 A2A skill-а, сессии, auth |
| [deployment.md](deployment.md) | Quickstart (локальный + Docker), единая таблица env-переменных |
| [mcp_tool_catalog.md](mcp_tool_catalog.md) | Auto-generated каталог 33 raw-инструментов + 3 session-tools |
| [a2a_agent_card.md](a2a_agent_card.md) | Реальная карточка A2A-агента, описание полей |

## Категории

### Актуальная документация продукта (эта папка)

| Документ | Назначение |
|---|---|
| `architecture.md` | Целевая архитектура: двух-транспортная диаграмма, поток `run_pipeline`, граница ответственности |
| `tool_contract.md` | Контракт: 33 MCP-tools, 2 A2A skill-а, сессии, auth |
| `deployment.md` | Локальный запуск + Docker compose + единая env-таблица |
| `mcp_tool_catalog.md` | Auto-generated из живого кода (`scripts/generate_tool_catalog.py`). 36 raw-tools + 3 session |
| `a2a_agent_card.md` | Реальная карточка A2A-агента с описанием полей и SDK-версией |
| `WIKI-LLM.md` | LLM-карта: что где лежит, маршруты чтения |

## Когда что читать

| Задача | Начать с |
|---|---|
| Подключиться к проекту впервые | [../README.md](../README.md) → [architecture.md](architecture.md) |
| Найти нужный файл для правки | [WIKI-LLM.md](WIKI-LLM.md) |
| Понять контракт инструмента | [tool_contract.md](tool_contract.md) или [mcp_tool_catalog.md](mcp_tool_catalog.md) |
| Развернуть локально или в Docker | [deployment.md](deployment.md) |
