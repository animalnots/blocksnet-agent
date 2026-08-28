# Индекс `tests/`

Назначение: контрактные тесты MCP-server, unit-тесты сериализации, runtime и A2A.

Тесты проверяют тонкий слой обёртки и структурные части рассуждающего ядра
(`submit_answer`, P1.2-confidence, P1.6-overlay, кэш provision, PTR-классификатор),
а **не** качество LLM-рассуждения в целом.

## Тесты

| Файл | Что проверяет |
|---|---|
| `test_serialize.py` | Преобразование `AgentResult` → JSON-контракт: P1.1 `submit_answer` приоритет, P1.2 `confidence` + `confidence_self` + `confidence_basis`, P1.6 `overlay_candidates`, regex-fallback с `salvaged: true` |
| `test_tool_contract.py` | Входные параметры и обязательные поля выхода `analyze_urban_question` |
| `test_async_mcp_contract.py` | Async-обёртка FastMCP: `notifications/progress`, корректная работа с `DEADLINE_SEC`, `status="partial"` вместо `ExceptionGroup` |
| `test_runtime.py` | `runtime.start_run`: `run_id`, deadline, прогресс, прогресс-callback |
| `test_confidence_signals.py` | P1.2-формула `confidence_basis`: какие сигналы дают какой вклад, сохранение `confidence_self` |
| `test_overlay_candidates.py` | P1.6: overlay-кандидаты из гипотез-слоёв, hard_passed/diagnostic_layers, fallback в `recommendation_blocks` |
| `test_ptr_classifier.py` | PTR-классификатор гипотез: `supported` / `refuted` / `inconclusive` |
| `test_provision_cache.py` | T1.2-мемоизация `compute_*` / `list_*` / `load_*`; `compute_scenario_provision` и `list_cached_data` намеренно не мемоизируются |
| `test_numeric_metric_resolution.py` | Резолвер числовых метрик по `state` (имена колонок, алиасы) |
| `test_provision_summaries.py` | Сводки по `compute_service_provision` (город/квартал/сервис) |
| `test_target_block_selection.py` | Логика `suggest_target_blocks` (валидные критерии, отбраковка невалидных) |
| `test_tool_failure_dedup.py` | Дедупликация повторных failed-вызовов (P0.4: invalidation по версии state) |
| `test_no_data_grounding.py` | Поведение при отсутствии данных: `NO_DATA` маркеры, явные `limitations` |
| `test_experiment_harness.py` | Локальный harness для прогона экспериментов на `examples/saint_petersburg/` |
| `test_preparation_tools.py` | Три новых preparation-tools: `build_blocks_with_services`, `prepare_accessibility_matrix`, `prepare_road_congestion_inputs` (fallback на готовый gpkg, no-op, явные ошибки) |
| `test_mcp_session.py` | SessionStore LRU+TTL |
| `test_mcp_session_scenario.py` | Привязка `scenario_id` |
| `test_mcp_tool_exposure.py` | 36 tools в каталоге, `submit_answer` не экспонирован |
| `test_image_deps.py` | MCP без LLM-зависимостей (для изоляции образа) |
| `test_a2a_card.py` | Agent Card имеет 2 skill-а |
| `test_a2a_tasks.py` | TaskManager concurrent + TTL |
| `test_a2a_skills.py` | SKILLS контракт |
| `test_auth.py` | authcore + middleware |
| `test_context_adapter.py` | `resolve_context` + path-traversal защита |
| `test_runtime_stop_scope.py` | Per-run stop_event изоляция |
| `test_settings_inheritance.py` | Settings inheritance |
| `test_tool_catalog.py` | Build catalog: 36 tools корректно собирается |
| `test_tool_catalog_docs.py` | Auto-generated каталог не протухает (актуальная версия в `docs/mcp_tool_catalog.md`) |
| `test_synthesis.py` | Synthesis-узел и обратная совместимость `to_json` |
| `test_codesynapse_contract.py` | Контракт с CodeSynapse-регистрацией |
| `test_codesynapse_mcp.py` | MCP-интеграция с CodeSynapse |
| `test_image_deps.py` | MCP без LLM-зависимостей (для изоляции образа) |
| `test_road_congestion_tool.py` | Контракт `compute_road_congestion` |
| `test_service_resolution.py` | Резолвер сервисов по `service_type.json` |

## Запуск

```bash
.venv/bin/python -m pytest tests
```

Все тесты должны проходить (`passed`) на Python 3.10+.

## Минимальные проверки (MCP-контракт)

- `question` обязателен и остаётся в ответе.
- `analysis_plan`, `result`, `reflection`, `recommendations`, `measured_effects`, `confidence`,
  `confidence_self`, `confidence_basis`, `limitations`, `artifacts`, `run_id`, `run_dir`, `status`
  имеют ожидаемые типы.
- При `submit_answer`: `salvaged: false`; `recommendation_blocks` извлекается из `recommendations[].block_id`.
- В fallback-пути: `salvaged: true`, в `limitations` присутствует `SALVAGED_ANSWER`,
  `overlay_candidates` / `overlay_meta` приходят из `overlay_candidates` (если слои были).
- `hypotheses[].status` (где присутствует, в fallback-пути) ограничен значениями
  `supported` / `refuted` / `inconclusive`.
- Сериализация не требует парсинга длинного нарратива потребителем.