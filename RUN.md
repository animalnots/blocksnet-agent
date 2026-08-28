# RUN.md — быстрый запуск для получателя проекта

> `RUN.md` показывает как развернуть и проверить систему за 10 минут. Подробности — в `README.md`, `docs/deployment.md`,
> и `docs/tool_contract.md`.

---

## Что это за проект

`blocksnet-agent` включает два решения для городской аналитики поверх `BlocksNetAgent`:

- **MCP-server** (`python -m blocksnet_mcp`) — stdio, 36 raw-tools + 3 session-tools.
**Не требует LLM** — может работать на чистых данных.
- **A2A-агент** (`python -m blocksnet_agent`) — HTTP, 2 skill-а: `run_pipeline`
и `analyze_urban_question` DEPRECATED. Требует LLM через OpenAI-compatible endpoint.

---



> **Интеграция в CodeSynapse (MAS):** значения регистрации и процедура подключения — в `docs/dev/codesynapse_registration.md` (developer-документ, нужен только при регистрации в MAS).

## Способ 1 — Локальный запуск без Docker (самый быстрый)

```bash
# 1. Создать venv и установить зависимости
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[agent,dev]"

# 2. Заполнить .env (или скопировать .env.example → .env и отредактировать)
cp .env.example .env
# Обязательно: CHAT_URL, API_KEY, MODEL

# 3. Положить данные (пример для СПб — 336 MB, не в репо)
mkdir -p data/saint_petersburg
# Должно быть: data/saint_petersburg/blocks_with_services.gpkg
#             data/saint_petersburg/acc_mx.pickle
#
# Для ``compute_road_congestion`` дополнительно нужны три заранее
# подготовленных файла:
#
#   data/saint_petersburg/blocks_to_nodes.pickle
#   data/saint_petersburg/nodes_to_nodes.pickle
#   data/saint_petersburg/graph_drive.graphml
#


# 4. Подготовка данных через MCP-инструменты (если что-то отсутствует)
#    Агент сам вызовет их при необходимости; можно и вручную через MCP-stdio:
DATA_DIR=data/saint_petersburg python -m blocksnet_mcp
#   → вызвать build_blocks_with_services() / prepare_accessibility_matrix() /
#     prepare_road_congestion_inputs() при отсутствии соответствующих файлов.

# 5. Запустить A2A-агента (HTTP, FastAPI)
DATA_DIR=data/saint_petersburg python -m blocksnet_agent
# → http://0.0.0.0:8080/ (Agent Card, JSON-RPC /, /health)

# В другом терминале — MCP-server (stdio, без LLM)
DATA_DIR=data/saint_petersburg python -m blocksnet_mcp
# → stdio MCP, подключай через Claude Desktop / Cursor
```

**Проверка работоспособности:**

```bash
# pytest
pytest -q

# Health endpoint
curl http://localhost:8080/health

# Agent Card
curl http://localhost:8080/.well-known/agent-card.json | jq

# Реальный вопрос агенту
DATA_DIR=data/saint_petersburg python -c "
import sys, json, time; sys.path.insert(0, '.')
from blocksnet_mcp.tools_mcp import analyze_urban_question
start = time.time()
result = analyze_urban_question('Какие кварталы имеют наименьшее покрытие школами?', max_iterations=4)
print(f'Elapsed: {time.time()-start:.1f}s')
print(json.loads(result))
"
```

---



## Способ 2 — Docker Compose (для проверки контейнеризации)

```bash
# 1. Заполнить .env
cp .env.example .env
# Обязательно: CHAT_URL, API_KEY, MODEL
# DATA_DIR и OUTPUT_DIR — относительно корня проекта

# 2. Положить данные в ./data/saint_petersburg/

# 3. Собрать и запустить
docker compose build
docker compose up -d

# 4. Проверить
sleep 30
docker compose ps                    # оба healthy
curl http://localhost:8080/health
curl http://localhost:8080/.well-known/agent-card.json | jq

# 5. Остановить
docker compose down
```

---



## Что попробовать после запуска

1. **Health:** `curl http://localhost:8080/health` → `{"status": "ok", ...}`
2. **Agent Card:** `curl http://localhost:8080/.well-known/agent-card.json`
3. **A2A-протокол:** отправь `SendMessage` через curl:
  ```bash
   curl -X POST http://localhost:8080/ \
     -H "A2A-Version: 1.0" \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0", "id": "1", "method": "SendMessage",
       "params": {"message": {"role": 1, "parts": [{"text": "Какие сервисы есть?"}], "messageId": "m1"}}
     }'
  ```
4. **MCP-каталог:** см. [docs/mcp_tool_catalog.md](docs/mcp_tool_catalog.md) —
  33 инструмента с описаниями.

---



## Что НЕ включено в передачу


| Файл/каталог                    | Почему                                                                         |
| ------------------------------- | ------------------------------------------------------------------------------ |
| `.env`                          | В `.gitignore`. **Содержит боевой Ollama Cloud API-ключ** — нужно завести свой |
| `data/` (city-specific geodata) | 336 MB, в `.gitignore`. Положить отдельно                                      |
| `outputs/`                      | Runtime-артефакты, в `.gitignore`                                              |
| `.venv/`, `.venv-spike/`        | Восстанавливается через `pip install -e ".[agent,dev]"`                        |
| `__pycache__/`, `*.pyc`         | Кеш, в `.gitignore`                                                            |
| `.git/`                         | Восстанавливается через `git clone`                                            |


---



## Документация (порядок чтения)

1. **Этот файл** — 5 минут
2. [README.md](README.md) — концепция, quickstart, статус — 10 минут
3. [docs/deployment.md](docs/deployment.md) — подробный deployment, env, troubleshooting — 15 минут
4. [docs/tool_contract.md](docs/tool_contract.md) — контракт, коды ошибок, сессии — 20 минут
5. [docs/architecture.md](docs/architecture.md) — целевая архитектура MCP+A2A — 10 минут
6. [docs/mcp_tool_catalog.md](docs/mcp_tool_catalog.md) — каталог 33 инструментов — 5 минут
7. [docs/a2a_agent_card.md](docs/a2a_agent_card.md) — реальная Agent Card — 5 минут

**Итого: ~70 минут на полное погружение.**

Для "просто запустить" хватит первых 3 документов.

---



## Если что-то непонятно

Эта документация покрывает текущее состояние системы.