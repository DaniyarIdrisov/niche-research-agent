# Архитектура системы

## Контекст

Система решает задачу из [`TZ.md`](TZ.md) — автоматизация первичного ресёрча ниши на Wildberries в категории МБТ. Эта страница — полное описание архитектуры: компоненты, потоки данных, обоснование решений и отвергнутые альтернативы.

## Высокоуровневая схема

```
                            ┌──────────────────────────────┐
   CLI (Typer) ───────────► │   Supervisor (LangGraph)     │
   user query               │                              │
                            │  ┌──────────┐                │
                            │  │  scout   │                │
                            │  └────┬─────┘                │
                            │       │ validate (retry≤2)   │
                            │       ▼                      │
                            │  ┌────────────┬──────────┐   │
                            │  │ niche      │ specs    │   │
                            │  │ analyst    │ miner    │   │ ← параллельно
                            │  └─────┬──────┴────┬─────┘   │
                            │        │  ┌────────┘         │
                            │        │  │ usp_analyst      │
                            │        ▼  ▼                  │
                            │      prd_writer              │
                            │        │ validate (retry≤2)  │
                            │        ▼                     │
                            │      reporter                │
                            └─────────┬────────────────────┘
                                      │
              ┌───────────────────────┼──────────────────────────┐
              │                       │                          │
              ▼                       ▼                          ▼
        Ollama (LLM)            Memory layers           Observability
        + embeddings            (working / episodic /    (Loguru / OTel /
        on host                  semantic)                Prometheus / Langfuse)
                                       │
                                       ▼
                                 ChromaDB + KB
                                 SQLite episodic
```

## Поток данных одного запроса

1. CLI принимает запрос пользователя, генерирует `run_id`, создаёт `AgentState` и пишет старт в episodic memory (SQLite).
2. **scout**: LLM парсит NL-запрос → `QueryFilters`. Затем детерминированный `wb_parser.search_products()` (mock или live) возвращает топ-N карточек.
3. **validate_scout**: проверяет, что либо есть товары, либо категория `unknown`. Если ниша поддерживаемая, но товаров нет — retry (≤MAX_RETRIES_PER_NODE). Если категория `unknown` или retries исчерпаны — сразу в reporter с no-data.
4. **Параллельный fan-out** через `return [list]` из conditional-router:
   - **niche_analyst**: `calculator.summarize()` → числа, LLM-insights → `NicheMetrics`.
   - **specs_miner**: `specs_normalizer.frequency_table()` → LLM раскладывает на must/nice/rare → `SpecsSummary`.
   - **usp_analyst**: `usp_classifier.baseline_classify()` (rules) → LLM корректирует/находит gaps → `UspMatrix`.
5. **prd_writer**: компактный JSON из трёх артефактов → LLM в strict mode → `PRD` (Pydantic).
6. **validate_prd**: проверяет полноту обязательных секций (`compliance`, `must_have_specs`, `target_price`). Не прошло — retry.
7. **reporter**: высокая температура (0.4), markdown-отчёт. Если LLM упал — машинный template-фоллбэк. Verdict вытаскивается из последней строки `**Verdict:** ...`, на fallback — эвристика по top_share/share_new/p75/p25.
8. CLI пишет в episodic memory, дампит state и report на диск.

## Компоненты по слоям

### Агенты (src/agents/)

| Агент | LLM-temp | Что делает | Тип выхода |
|---|---|---|---|
| scout | 0.1 | NL → фильтры, затем wb_parser | `QueryFilters` |
| niche_analyst | 0.2 | calculator + insights | `NicheMetrics` |
| specs_miner | 0.1 | normalizer + LLM split | `SpecsSummary` |
| usp_analyst | 0.2 | rules + LLM cluster | `UspMatrix` |
| prd_writer | 0.2 | compose strict PRD | `PRD` |
| reporter | 0.4 | markdown report + verdict | `str` |

Принцип: **LLM не считает, не вычисляет, не парсит — только интерпретирует и формулирует.** Всё arithmetic — детерминированно в `src/tools/`.

### Инструменты (src/tools/)

- `wb_parser.py` — mock + live режимы, rate limiter, mapping search.wb.ru → `Product`
- `calculator.py` — `revenue_top_n`, `seller_concentration`, `price_stats`, `review_dynamics`, `summarize`
- `specs_normalizer.py` — taxonomy (20 канонических ключей, ~60 синонимов), `frequency_table`
- `usp_classifier.py` — 4 типа УТП через regex, `find_gaps`
- `unit_economics.py` — комиссии WB 2025, logistics, acquiring, margin
- `prd_validator.py` — проверка полноты PRD

### Память (src/memory/)

См. [`MEMORY.md`](MEMORY.md) для полной картины. Кратко:

- **working** — state LangGraph (TypedDict) + helpers
- **episodic** — SQLite (`runs` table), история, диагностика, материал для evals
- **semantic** — ChromaDB embedded mode + BM25 in-memory + опциональный bge-reranker

### Observability (src/observability/)

См. [`OBSERVABILITY.md`](OBSERVABILITY.md). Кратко:

- **Loguru** — JSONL-логи на диск + цветной stdout
- **OpenTelemetry** — span на каждый LLM-вызов и ноду графа, экспорт в Jaeger
- **Prometheus** — counters/histograms (latency, токены, retries, retrieve outcomes), scrape с host.docker.internal:9464
- **Langfuse** — отдельный self-hosted сервис для LLM-трейсов (промпт+ответ+метаданные)

## Ключевые архитектурные решения

### 1. Supervisor pattern + LangGraph

**Что:** один граф состояний с явными нодами и edge'ами.

**Альтернативы:** ReAct loop, plan-and-execute, swarm (agents-call-agents).

**Почему именно так:**
- Предсказуемый поток управления — критично для evals и отладки.
- Явные validate-ноды между критичными шагами.
- LangGraph даёт нативный fan-out через `return [list]` из conditional router.
- Граф рисуется на бумаге за 10 секунд — защитимо на устной защите.

**Минусы:**
- Жёсткая последовательность шагов — нельзя «передумать» в середине.
- Добавление новой ниши требует добавления нод/рёбер, не plug'n'play.

### 2. LLM в роли интерпретатора, а не калькулятора

Каждый агент делит работу:
- Детерминированная часть (tools) — pure Python.
- Творческая часть (LLM) — нарратив, классификация, формулировки.

**Почему:** на 3B-модели любая арифметика — это лотерея. 37 000 × 30 даст «approximately 1 million» в трёх случаях из десяти.

**Цена:** агенты получаются «не чисто LLM» — это компромисс ради надёжности.

### 3. Structured output через format=json + Pydantic + repair-loop

**Стек:**
1. Ollama `format=json` (форсирует валидный JSON, не markdown).
2. Pydantic-валидация (типы, обязательные поля).
3. **Repair-loop:** при невалидном выходе делаем доп. LLM-вызов, показывая модели её ошибку. До `max_repair_attempts=2`.

**Альтернативы:** `langchain.with_structured_output()`, function calling, JSON schema через grammar.

**Почему так:** langchain-обёртка скрывает что происходит, function calling на маленьких моделях работает плохо. Repair-loop с явной ошибкой — наиболее эффективный приём для 3B.

### 4. Hybrid RAG с весами в env

См. [`MEMORY.md`](MEMORY.md) для детального обоснования.

### 5. Graceful degradation на всех уровнях

Каждый компонент имеет fallback:
- LLM упал → fallback на rule-based / threshold-based
- ChromaDB недоступен → retrieve() возвращает []
- Langfuse недоступен → log_llm_call() = no-op
- OTel collector недоступен → spans записываются локально, продолжаем
- Reranker не загрузился → пропускаем шаг rerank

Система **всегда** выдаёт пользователю результат, пусть и degraded.

## Что осталось за скобками (future work)

- **Кэширование частых запросов** — сейчас каждый запуск идёт с нуля.
- **Стриминг отчёта Reporter'у** — было бы UX-улучшение.
- **GraphRAG** для связных вопросов между документами KB.
- **Fine-tuning эмбеддингов** на корпусе WB-карточек.
- **Multi-marketplace** — Ozon, Я.Маркет требуют отдельных парсеров и taxonomy.

См. также [`DEFENSE_NOTES.md`](DEFENSE_NOTES.md) — там этот список оформлен под устную защиту.
