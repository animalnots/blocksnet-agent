# Контракт инструментов

- **MCP-server экспонирует 36 raw-инструментов** (``compute_*``, ``load_*``, ``list_*``,
  ``get_*``, ``render_metric_map``, ``suggest_target_blocks``, ``propose_zone_development``,
  ``build_adjacency_graph``, ``find_tools``, ``get_tool_help``) +
  **3 служебных** (``open_session``/``close_session``/``session_info``). Полный каталог —
  ``docs/mcp_tool_catalog.md`` (auto-generated из живого кода).
- **A2A-агент** экспонирует 2 skill-а в Agent Card: ``run_pipeline`` (новый, основной) и
  ``analyze_urban_question`` (DEPRECATED, обёртка для back-compat).
- **Сессии и scenario_id**: изоляция state между параллельными клиентами; привязка
  к ``scenario_id``/``project_id`` (whitelist `[a-zA-Z0-9_-]{1,64}`).
- **Auth**: Bearer-токен, единый текст ошибки для «нет» и «неверный», токен НЕ логируется.
- **Два сервиса — два образа** (Docker): MCP-образ без LLM-зависимостей.

## 7. A2A Skills

### 7.1. `run_pipeline` (основной)

**Вход** (``RunPipelineInput``, Pydantic):

| Поле | Тип | Описание |
|---|---|---|
| `question` | `str` (required) | Городской вопрос на естественном языке |
| `max_iterations` | `int \| None` | Переопределить лимит итераций; без значения используется `MAX_ITERATIONS` |
| `scenario_id` | `str \| None` | Id сценария MAS (whitelist `[a-zA-Z0-9_-]{1,64}`) |
| `project_id` | `str \| None` | Id проекта MAS |

**Передача:** CodeSynapse передаёт структурированные параметры через DataPart.
`message.metadata` поддерживается только как fallback для локальных клиентов.
Если задан `scenario_id`, используется `DATA_DIR/<scenario_id>`.

**Выход:** тот же dict, что отдаёт ``analyze_urban_question`` v1 (см. раздел 4).

**Жизненный цикл задачи:**
1. ``submitted`` → задача в очереди, ожидает слота конкурентности
2. ``working`` → задача исполняется в пуле потоков, ``start_run()`` уже создан
3. ``completed`` / ``partial`` (дедлайн) / ``failed`` / ``canceled``

Поток статусов эмитится через ``TaskStatusUpdateEvent`` (a2a-sdk). Дедлайн НЕ
через ``asyncio.wait_for`` — поток не убивается, агент сам финализирует.

**Лимит конкурентности:** ``A2A_MAX_CONCURRENT_TASKS`` (default 2). Превышение →
задача в ``submitted``, ждёт семафора.

### 7.2. `analyze_urban_question` (DEPRECATED, back-compat)

**Вход** (``AnalyzeUrbanQuestionInput``, Pydantic) — те же поля, что и ``run_pipeline``.

**Выход:** идентичен ``run_pipeline`` (прокси на тот же ``execute_run_pipeline``).

**Статус:** DEPRECATED. Помечен в Agent Card и skill.description. Удаление —
в v2.1 (см. таблицу совместимости).

**Причина deprecated:** v1-контракт был рассчитан на единственный MCP-tool.
A2A-skill ``run_pipeline`` предоставляет тот же формат + ``streaming`` +
``cancel`` + multi-task. Поддерживаем legacy через shim, но новые интеграции —
на ``run_pipeline``.

## 8. MCP Tool Catalog

Полный каталог — ``docs/mcp_tool_catalog.md`` (auto-generated). Формат:

```json
{
  "status": "ok" | "partial" | "failed",
  "tool": "<name>",
  "session_id": "<sid>",
  "text": "<raw tool output>",
  "artifacts": ["<path>", ...],
  "error_code": "<code>",
  "error": "<message>"
}
```

**Коды ошибок конверта** (``blocksnet_mcp/envelope.py``):

| Код | Когда |
|---|---|
| `TOOL_FAILED` | Текст инструмента содержит FAILURE_MARKERS («Ошибка:», «Traceback», и т.п.) |
| `TOOL_EXCEPTION` | Исключение внутри инструмента (НЕ транспортная ошибка — клиент получает структуру) |
| `VALIDATION_ERROR` | Неверные аргументы (например, пустой ``question``) |
| `LLM_NOT_CONFIGURED` | ``analyze_urban_question`` без ``CHAT_URL``/``API_KEY`` (legacy) |
| `SESSION_SCENARIO_MISMATCH` | Смена ``scenario_id`` в существующей сессии |
| `SCENARIO_NOT_MATERIALIZED` | ``scenario_id`` задан, но каталога нет и materializer не помог |

### 8.1. `compute_road_congestion` (экспериментальный)

Строит OD-матрицу (origin-constrained gravity, integerized) и выполняет
дискретное trip-by-trip назначение поездок через Дейкстру на дорожном графе.
Возвращает для каждого ребра `intensity` (число поездок), `capacity`
(ёмкость по полосам) и `congestion_level = intensity / capacity`.

**Статус:** экспериментальный. Реализация вендорена в
`blocksnet_agent/vendor/road_congestion/` (upstream
`aimclub/blocksnet @ feat/road_congestion @ 3a2ea5f`), пин на feature-ветку
снят, остальной стек работает на релизе `blocksnet` с PyPI.

**Ограничения по применению:**

- OD семантика: `total_trips ≈ Σ население × trip_rate(land_use)`. На
  территории > ~50 тыс. жителей OD превышает дефолт `max_trips=50_000` —
  предохранитель возвращает понятное сообщение **без** подсказки
  «увеличьте лимит»: дискретное назначение — `O(поездки × Dijkstra)`,
  поднимать `max_trips` опасно (часы работы).
- Дефолт `max_trips=50_000` — компромисс: метрика применима к району или
  малому городу. Для крупного города уменьшайте территорию или
  агрегируйте узлы; честный вывод, а не маскировка дефолтом.
- Поддерживаются `lanes ∈ 1..8`; `lanes > 8` отвергается заранее
  (иначе `KeyError` в `_get_capacity_by_lanes`). `lanes < 1` нормализуется
  к 1. Лоссовый разбор (`lanes` как список/строка с разделителями)
  отражается в сводке числом рёбер.
- OD-выгрузка: `origin_destination_matrix.csv` пишется sparse (топ-N пар,
  дефолт 200). Полная матрица остаётся в `state['origin_destination_matrix']`.

**Данные:** требует `blocks_to_nodes.pickle`, `nodes_to_nodes.pickle`,
`graph_drive.graphml`. Альтернативы `*.pkl` и `drive.graphml` поддержаны.
Входные файлы готовятся вне этого репозитория.

**Известные upstream-дефекты** (см.
`research/road_congestion_skill_basis.md`):
`# FIXME multidigraph edges split congestion`. Перенесены вместе с кодом;
правки — за рамками этого плана.

Полный план доведения до production — в `research/road_congestion_skill_basis.md`.

## 9. Сессии MCP

``session_id`` — первый аргумент каждого tool-call (default = ``"default"``).

- **Изоляция:** state одной сессии не виден другой. TTL 1800 с, LRU max 8 (настройки).
- **``open_session(session_id, scenario_id, project_id)``:** создаёт/возвращает сессию.
  Если задан ``scenario_id`` — резолвится через ``resolve_context`` (whitelist + path-traversal защита).
- **``close_session(session_id)``:** закрывает сессию, очищает state.
- **``session_info(session_id)``:** диагностика (age, idle, **имена** ключей state, не значения).

**Сценарий-контракт:** смена ``scenario_id`` в существующей сессии → ``SESSION_SCENARIO_MISMATCH``.
Клиент открывает новую сессию.

## 10. Auth и scenario_id

Bearer auth применяется к A2A-сервису. Для включения задаются
``A2A_AUTH_ENABLED=true`` и ``A2A_MAS_BEARER_TOKEN``. Проверка токена использует
``hmac.compare_digest``. MCP работает через локальный stdio-транспорт и не
выполняет HTTP-аутентификацию.

**Коды ошибок auth:**

| Код | HTTP | Когда |
|---|---|---|
| `invalid_token` | 401 | Нет токена или токен неверный (текст единый) |
| `insufficient_scope` | 403 | Токен валиден, но scope не разрешает (задел на JWT) |

``scenario_id`` приходит из DataPart A2A-запроса или аргументов MCP tool-call.
Whitelist-регулярка ``[a-zA-Z0-9_-]{1,64}``. Попытка path traversal возвращает
``VALIDATION_ERROR``.

## 12. Выходной payload `analyze_urban_question` / `run_pipeline`

> **Финальный синтез** — структурный 7-секционный decision memo, см.
> `blocksnet_agent/synthesis.py`. Контракт — общий для обоих skill-ов и для
> legacy-MCP-tool `analyze_urban_question`. Формат фиксирован в
> `blocksnet_mcp/serialize.py::to_json` и покрыт тестами
> `tests/test_serialize.py` + `tests/test_synthesis.py`.

### 12.1. Что получает клиент

Поля делятся на три группы: **back-compat** (есть в v1), **статус-обвязка** (любой
payload), и **synthesis-поля** (новые, шаг P-S5).

| Поле | Тип | Источник | Назначение |
|---|---|---|---|
| `status` | `ok` \| `partial` \| `failed` | обвязка | Статус MCP-envelope. `partial` = дедлайн, `failed` = ошибка |
| `tool` | `str` | обвязка | Имя инструмента (back-compat с envelope MCP raw-tool) |
| `run_id` | `str` | обвязка | Имя run-каталога (`<YYYYMMDD-HHMMSS-XXXXXX>`) |
| `run_dir` | `str` | обвязка | Абсолютный путь run-каталога с `synthesis.md`, `run_log.{md,json}`, `maps/` |
| `error` | `str`? | обвязка | Человекочитаемое сообщение (только при `status="failed"`) |
| `error_code` | `str`? | обвязка | Код ошибки (только при `status="failed"`) |
| **back-compat (P1.1)** | | | |
| `question` | `str` | agent | Эхо входа |
| `analysis_plan` | `str` | regex-парсинг prose | План, восстановленный из prosa агента (`ANALYSIS PLAN:` секция) |
| `result` | `str` | regex-парсинг prose | `RESULT:` + `REFLECTION:` (legacy-формат) |
| `hypotheses` | `list[dict]` | regex-парсинг | Каждая гипотеза `{id, claim, prediction, test, status, evidence}` |
| `measured` | `dict[str,dict]` | regex-парсинг before→after | `{<service>: {strong_before, strong_after, missing_before, missing_after}}` |
| `recommendation_blocks` | `list[int]` | submit_answer ∨ overlay ∨ regex | block_id из structured recommendations. Сначала — `submit_answer`, затем overlay, иначе regex |
| `overlay_candidates` | `list[dict]`? | overlay | Структурные кандидаты из `overlay_candidates()`. Только если есть overlay и **нет** submit_answer |
| `overlay_meta` | `dict`? | overlay | `{hard_passed, hard_total, diagnostic_layers, nondiagnostic_layers}` |
| `confidence` | `float` | авторитетная P1.2-формула | Число 0.0…1.0. **Не самооценка модели** |
| `confidence_self` | `float`? | agent | Самооценка агента (для аудита), если есть |
| `confidence_basis` | `list[str]`? | agent | Сигналы, объясняющие `confidence` |
| `limitations` | `list[str]` | agent + авто | Самооценка, плюс `"SALVAGED_ANSWER"`, если использовался regex-fallback |
| `artifacts` | `list[str]` | run_log + rglob | Относительные пути артефактов (`maps/foo.png`, CSV из `compute_*`) |
| `salvaged` | `bool` | serialize | `true` если ответ восстановлен regex'ом из prosa, **не** через `submit_answer`. `false` если submit или overlay |
| **synthesis-поля (P-S5.x, NEW)** | | | |
| `synthesis` | `str` | **всегда** | 7-секционный decision memo на русском (markdown). См. §12.3 ниже |
| `synthesis_citations` | `list[str]` | **всегда** | Уникальные `[source]`-токены, вставленные LLM в `synthesis` |
| `synthesis_path` | `str` | **всегда** | Путь к `run_dir/synthesis.md` (для offline-чтения). `""` если не сохранилось |
| `synthesis_fallback` | `bool` | **всегда** | `true` если LLM-вызов синтеза упал и собран деградированный fallback. `false` — нормальный путь |

> **Инвариант:** поля `synthesis`, `synthesis_citations`, `synthesis_path`,
> `synthesis_fallback` присутствуют **всегда** (даже если submitted_answer уже
> заполнил всё остальное). Это нужно для downstream-MAS: клиент читает
> `synthesis` для человека и `recommendations`/`measured` для машины.

### 12.2. Когда синтез в fallback

`synthesis_fallback=true` означает:
- LLM-вызов в `synthesize(...)` бросил исключение ИЛИ вернул <40 символов
- **ИЛИ** все observations — с failure-маркерами (`Ошибка:`, `Traceback`,
  `Exception`, `not found`, `не найден`, `NO_DATA`, `REPEATED_FAILED_CALL`).

В обоих случаях `synthesis` всё равно возвращается — структура из 7 секций
собирается из сырых observations и статусов гипотез, но без LLM-саммари.
Limitations получают `"деградированный синтез: LLM-вызов пропущен"`.

### 12.3. Структура `synthesis` (7 секций)

Гарантированный порядок заголовков (`## <title>`):

| # | Заголовок | Содержание |
|---|---|---|
| - | `## Вопрос` | Эхо исходного вопроса |
| 1 | `## Ответ` | Committed conclusion в 1–3 предл. с `(доверие 0.NN)` |
| 2 | `## Как читаю вопрос` | Проблемная рамка и почему она подходит |
| 3 | `## На чём держится ответ` | Главные компоненты: инструменты, гипотезы, результаты |
| 4 | `## Варианты, которые взвешивал` | Реальные альтернативы, не strawmen + почему выбранный путь выигрывает |
| 5 | `## Аргумент «за»` | Подтверждающие наблюдения **с `[source]`** citations и числами |
| 6 | `## Что осталось неопределённым` | Material uncertainty + confidence каждого утверждения |
| 7 | `## Где это рассуждение может быть ошибочным` | Reasoning-level critique: rejected framings, calibrated claims, source bias |
| + | `## Ограничения` | Авто-собранные limitations (failed obs + inconclusive гипотезы) |

Citations в `synthesis` имеют форму `[<tool>]` или `[<tool>(args)]` и
дублируются в массиве `synthesis_citations` без самих квадратных скобок.

### 12.4. Артефакты на диске

`run_dir/<run_id>/`:

```
synthesis.md         # всегда — см. §12.3
run_log.md           # всегда или нет (warning-логируется, не silent pass)
run_log.json         # машино-читаемая трасса tool-calls
maps/                # PNG/CSV из compute_*
```

`synthesis.md` создаётся `mkdir -p parents` (если каталога ещё нет).
Файл-структура `run_dir` всегда согласована с `payload["artifacts"]` —
клиент может открыть `run_dir/synthesis.md` напрямую.

## 13. Совместимость v1 → v2

| v1 | v2 | Статус |
|---|---|---|
| `analyze_urban_question` (MCP-tool, v1) | `analyze_urban_question` (MCP-tool, v2) | **DEPRECATED** — legacy LLM-tool, убрать в v2.1 |
| `analyze_urban_question` (MCP-tool) | `analyze_urban_question` (A2A skill) | **DEPRECATED** — прокси на ``run_pipeline``, убрать в v2.1 |
| (нет) | `run_pipeline` (A2A skill) | **NEW** — основной A2A skill |
| (нет) | 36 raw-инструмента + 3 session-tools (MCP) | **NEW** — включает `compute_road_congestion` и 3 preparation-tools (`build_blocks_with_services`, `prepare_accessibility_matrix`, `prepare_road_congestion_inputs`) |
| (нет) | сессии с ``session_id`` + изоляция | **NEW** |
| (нет) | ``scenario_id`` / ``project_id`` | **NEW** |
| (нет) | Bearer auth | **NEW** (опционально) |

**План удаления deprecated:** v2.1 — убрать ``analyze_urban_question`` из MCP и A2A,
обновить downstream-агентов на ``run_pipeline``.
