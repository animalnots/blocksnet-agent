# Agent Card для BlocksNetAgent A2A-агента

> Agent Card — это JSON-манифест, который A2A-агент отдаёт на пути
> ``/.well-known/agent-card.json``. Используется клиентами для discovery.

Версия SDK: **a2a-sdk 1.1.1**.
Версия сервиса: **0.2.0**.

## 1. Получение карточки

```bash
curl -s http://localhost:8080/.well-known/agent-card.json | jq
```

Альтернативно — через `mcp.client.A2ACardResolver` в a2a-sdk.

## 2. Пример вывода с локального запуска

> Пример проверяется smoke-тестом ``scripts/smoke_a2a_agent.py``.

```json
{
  "name": "blocksnet-mcp-a2a",
  "version": "0.2.0",
  "description": "A2A-агент для городской аналитики на базе BlocksNetAgent. Загружает данные кварталов, считает метрики, рассчитывает предложения по размещению сервисов.",
  "supportedInterfaces": [
    {
      "url": "http://0.0.0.0:8080/",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    }
  ],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "skills": [
    {
      "id": "run_pipeline",
      "name": "run_pipeline",
      "description": "Запускает полный аналитический конвейер BlocksNetAgent по городскому вопросу. Стримит статусы (submitted → working → completed/partial/failed), артефакты (карты, CSV), финальный JSON с гипотезами, рекомендациями **и 7-секционным структурным синтезом** (поля `synthesis` / `synthesis_citations` / `synthesis_path` / `synthesis_fallback` в JSON-RPC `result.parts[].text` — см. `tool_contract.md` §12).",
      "tags": ["urban", "pipeline", "agent"],
      "examples": [
        "Где в Кронштадте разместить новые спортивные площадки?",
        "Какие кварталы СПб имеют дефицит школ?"
      ]
    },
    {
      "id": "analyze_urban_question",
      "name": "analyze_urban_question",
      "description": "[DEPRECATED] Back-compat обёртка над run_pipeline. Блокирующе ждёт терминального статуса. Используйте run_pipeline.",
      "tags": ["urban", "legacy"],
      "examples": ["Где разместить новые школы?"]
    }
  ]
}
```

## 3. Описание полей

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Уникальное имя сервиса |
| `version` | string | Семантическая версия (из ``pyproject.toml``) |
| `description` | string | Короткое описание для discovery |
| `supportedInterfaces` | array | Поддерживаемые интерфейсы (HTTP, GRPC, …) |
| `supportedInterfaces[].url` | string | Базовый URL сервиса |
| `supportedInterfaces[].protocolBinding` | string | `"JSONRPC"` или `"GRPC"` |
| `supportedInterfaces[].protocolVersion` | string | Версия протокола (сейчас `"1.0"`) |
| `capabilities.streaming` | bool | Поддержка SSE/streaming ответов |
| `capabilities.pushNotifications` | bool | Push-уведомления (отложено — см. ``09-deferred.md``) |
| `defaultInputModes` | array[string] | MIME-типы входа (`text/plain`, …) |
| `defaultOutputModes` | array[string] | MIME-типы выхода (`application/json`, …) |
| `skills` | array | Доступные навыки сервиса |
| `skills[].id` | string | Уникальный id skill-а |
| `skills[].name` | string | Имя skill-а |
| `skills[].description` | string | Описание skill-а |
| `skills[].tags` | array[string] | Теги для поиска |
| `skills[].examples` | array[string] | Примеры вызовов |

## 4. Контракт протокола a2a-sdk 1.1.1

В a2a-sdk **1.1.1** (актуальная стабильная версия) используются имена полей:

- ``supportedInterfaces[]`` (с ``url`` и ``protocolBinding``) — формат protobuf.
- В ``parts`` — только ``text`` (oneof), ``kind: "text"`` **удалён**.
- Метод JSON-RPC — ``"SendMessage"`` (PascalCase, gRPC-style), не ``"message/send"``.
- Header ``A2A-Version: 1.0`` — **обязателен** (без него — ``VersionNotSupportedError``).
- Поле ``role`` в ``Message`` — это **enum** (``ROLE_USER = 1``, ``ROLE_AGENT = 2``).
  В wire JSON ожидается **число**, не строка.

## 5. Полный URL карточки

По умолчанию: ``http://{A2A_HOST}:{A2A_PORT}/.well-known/agent-card.json``

Через reverse-proxy: если задан ``A2A_PUBLIC_URL=https://blocksnet.example.com/agents/blocksnet``,
то карточка вернёт:

```json
{
  "supportedInterfaces": [
    {"url": "https://blocksnet.example.com/agents/blocksnet/", ...}
  ]
}
```

Это нужно для MAS-сценариев, где сервис скрыт за прокси.
