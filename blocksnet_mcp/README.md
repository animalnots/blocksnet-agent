# Индекс `blocksnet_mcp/`

Назначение: реализация MCP-server поверх ядра агента.

Эта папка содержит код локального MCP-server. Обертка не реализует городскую
аналитику и не переписывает рассуждение агента; она принимает MCP-вызов, запускает
`BlocksNetAgent.run(...)`, сериализует результат в JSON и оборачивает его в
P0.2-transport envelope (`status` / `run_id` / `run_dir` / `error` / `error_code`).

## Файлы

| Файл | Ответственность |
|---|---|
| `__init__.py` | Объявление пакета |
| `__main__.py` | `python -m blocksnet_mcp` → запуск stdio-сервера |
| `server.py` | FastMCP-приложение, транспорт `stdio`, async-обёртка + `notifications/progress`, каталог 36+3 инструментов |
| `settings.py` | Настройки окружения: `DATA_DIR`, `CHAT_URL`, `API_KEY`, `MODEL`, `OUTPUT_DIR`, `MAX_ITERATIONS`, `DEADLINE_SEC`, `PROGRESS_INTERVAL_SEC` |
| `session.py` | `SessionStore`: LRU+TTL, изоляция state между сессиями |
| `envelope.py` | P0.2 envelope (`status` / `tool` / `session_id` / `text` / `artifacts` / `error_code` / `error`) + локальная `is_failed_observation` |
| `tools_mcp.py` | Back-compat shim: `from blocksnet_mcp.tools_mcp import analyze_urban_question` |
| `agent_tool.py` | Legacy `analyze_urban_question` для back-compat; ленивые LLM-импорты |
| `serialize.py` | `AgentResult -> JSON` по контракту `docs/tool_contract.md`; P1.1 `submit_answer` / P1.2 `confidence` / P1.6 `overlay_candidates` |

## Минимальный поток

```text
server.py
  -> tools_mcp.analyze_urban_question
  -> BlocksNetAgent(model, data_dir=DATA_DIR).run(question)
       (внутри: ReAct-цикл -> submit_answer -> state["_submitted_answer"])
  -> serialize.to_json(AgentResult)
       (P1.1: если submitted_answer задан -> отдать как есть; P1.2: переписать confidence
        авторитетной формулой, сохранить confidence_self; P1.6: если submitted_answer нет,
        использовать overlay_candidates как fallback для recommendation_blocks)
  -> tools_mcp._build_payload(...)
       (P0.2: добавить status / run_id / run_dir / error / error_code)
  -> JSON-ответ MCP-клиенту (structuredContent)
```

## Локальный запуск

```powershell
.\.venv\Scripts\python.exe -m blocksnet_mcp
```

Для MCP-клиента указывать `command` на `.venv/Scripts/python.exe`, чтобы не зависеть
от системного `PATH` и случайной версии Python.

## Границы

- Не добавлять сюда доменные расчёты `blocksnet`; они остаются в `blocksnet_agent/tools/`.
- Не добавлять UrbanDB-адаптер в локальный MVP; это future-слой.
- Не возвращать только текстовый ответ агента: контракт MCP требует отдельные JSON-поля для гипотез, измеренных эффектов, блоков-рекомендаций, confidence (с self-оценкой и basis), limitations и artifacts.
- Приоритетный путь — структурный (`submit_answer`, P1.1); regex-парсинг — только
  fallback с `salvaged: true`.