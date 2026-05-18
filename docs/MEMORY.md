# Память и RAG

## Зачем три уровня

Memory в агентских системах — это не «база данных». Это разделение, какая информация **живёт сколько**:

| Уровень | Жизненный цикл | Что хранит | Что НЕ хранит |
|---|---|---|---|
| Working | 1 запрос | state между нодами графа | ничего за пределами запроса |
| Episodic | бессрочно | история запусков, run_id, query, результаты | KB-знания, эмбеддинги |
| Semantic | бессрочно | KB-чанки, эмбеддинги | информация о конкретных запусках |

Этот же раздел в [LangChain Memory docs](https://python.langchain.com/docs/modules/memory/) выделяет ещё **procedural** и **persona** memory — для нашей задачи они не нужны, поведение агентов фиксировано в промптах.

## Working memory

**Реализация:** `src/memory/working.py` + `src/schemas/state.py::AgentState` (TypedDict).

**Почему TypedDict, а не Pydantic:** LangGraph нативно мерджит state по ключам TypedDict. Pydantic подходит для строгих границ между функциями, а внутри графа keys часто отсутствуют (state эволюционирует по мере прохождения).

**Что в state:**
- input: `query`, `filters`, `run_id`
- результаты агентов: `products`, `niche_metrics`, `specs_summary`, `usp_analysis`, `prd`, `report`
- control: `errors`, `retries`, `verdict`

**Helpers** (working.py): `add_error`, `increment_retry`, `get_retry_count`, `short_dump`.

## Episodic memory

**Реализация:** `src/memory/episodic.py` — sqlite3, синхронный.

**Схема:**
```sql
runs(
  run_id      TEXT PRIMARY KEY,
  query       TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  verdict     TEXT,
  n_products  INTEGER,
  state_json  TEXT NOT NULL,
  errors_json TEXT
);
```

**Почему SQLite:**
- ноль операционных затрат, файл коммитится игнором
- хватит до 10⁴+ запусков
- легко открывается DBeaver/sqlite3 при отладке

**Почему НЕ PostgreSQL:** для университетского проекта overkill — добавляет ещё один сервис в compose, тяжелее в setup.

**Чего НЕ умеет (sознательно):**
- Поиск по похожим запросам через embeddings — есть `find_similar_queries()` через `LIKE`, в Phase 5+ можно вынести в semantic memory.
- Versioning / branching истории.

## Semantic memory (RAG)

**Реализация:** `src/memory/semantic.py`. Стек:
1. **ChromaDB** (HTTP-режим, контейнер) — векторное хранилище.
2. **Эмбеддинги:** `nomic-embed-text` через Ollama, на CPU (~512 dim).
3. **BM25:** `rank_bm25` (с TF-fallback при отсутствии библиотеки), in-memory.
4. **Hybrid fusion:** `dense × dense_weight + sparse × bm25_weight` (cosine similarity нормализована в [0,1], BM25 — делением на max).
5. **Опциональный реранкер:** `bge-reranker-v2-m3` через `sentence-transformers`. Lazy load, выключен по умолчанию.

### Chunking

`chunk_markdown()` в semantic.py:
1. Режем по заголовкам (regex по `^#{1,6}`).
2. Длинные секции (>1200 символов) — режем по абзацам с overlap=200.

**Почему так:** markdown — наш единственный формат KB. Header-aware splitter даёт человекочитаемые чанки. Overlap снижает риск разрыва смысла на границе.

### Retrieve flow

```
query
  ├─→ Ollama embed → ChromaDB.query(top_k_dense=10) → dense hits
  └─→ tokenize → BM25.search(top_k_bm25=10)         → sparse hits
                                ↓
                          fusion (weighted sum)
                                ↓
                    top-K кандидатов (default K = 5)
                                ↓
                  [optional] rerank → final K
```

### Параметры в .env

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `RAG_TOP_K_DENSE` | 10 | сколько брать из ChromaDB |
| `RAG_TOP_K_BM25` | 10 | сколько брать из BM25 |
| `RAG_FINAL_K` | 5 | финальное число чанков |
| `RAG_DENSE_WEIGHT` | 0.6 | вес dense в fusion |
| `RAG_BM25_WEIGHT` | 0.4 | вес sparse в fusion |
| `RAG_USE_RERANKER` | false | включить bge-reranker |

Тюнинг этих параметров — без перекомпиляции. На небольших KB (наш случай — ~50 чанков) выше веса BM25 часто работают лучше: точные термины «ТР ТС 004» лексически уникальны.

## Недостатки текущего подхода

1. **Latency двухэтапа retrieval.** dense + sparse прибавляют 100-200 мс на запрос. Для нашего объёма приемлемо, для prod-нагрузки нужен прекомпилированный fused index.
2. **Тюнинг весов hybrid search — ручной.** В норме нужно евальнуть на размеченных queries; у нас этого нет, веса прибиты «на глаз».
3. **Reranker не помещается в VRAM рядом с LLM.** На 4 ГБ — нужно выгружать LLM. Из-за этого по умолчанию выключен — даёт +10-15% к Recall@K ценой +3 секунд на запрос.
4. **BM25 — in-memory.** На каждом старте полностью перестраивается из ChromaDB. На 1000+ чанков это станет ощутимо.
5. **Эмбеддинги на CPU.** ~50 чанков/сек на i5. Для нашего KB ингест занимает 1-2 секунды, но если KB вырастет до 10k чанков — потребуется GPU-эмбеддер или батчинг.

## Альтернативы и почему отвергли

### Naive RAG (только cosine top-K)

**Плюсы:** проще, быстрее.

**Минусы:** теряется на синонимах и редких терминах. Запрос «декларация ТР ТС 020» по чисто dense retrieval промахнётся, если KB хранит «электромагнитная совместимость» — а нам важно собирать оба ракурса.

### GraphRAG (Microsoft)

**Плюсы:** для связных вопросов («какие документы для эпилятора с wet&dry и как они связаны с упаковкой») сильно лучше — строит локальный граф знаний и навигирует по нему.

**Минусы:** требует целого pipeline извлечения сущностей и связей — отдельные LLM-вызовы на этапе ингеста. На 50 чанков и одну поверхностную систему оверкилл, а на наших ресурсах не запустится в разумное время.

### MemGPT / Letta

**Плюсы:** долгосрочная память с автоматической архивизацией.

**Минусы:** сложнее в отладке, дополнительный сервис. Для system, где запуски независимые, episodic в SQLite достаточно.

### Только context window (большой prompt)

**Плюсы:** zero infrastructure.

**Минусы:** num_ctx=4096 (наш предел на 4 ГБ VRAM) не вмещает всю KB. Контекст-стаффинг приводит к «потерянной середине» и инфляции токенов.

## Что бы улучшил в проде

1. **Fine-tune эмбеддингов** на корпусе живых WB-карточек — даст +5-10% Recall@K.
2. **Кэширование частых запросов** — embed для query и retrieve-результаты можно закэшировать на 10-15 минут.
3. **GraphRAG поверх existing setup** для compliance-вопросов («какой документ + какая маркировка + где наносить»).
4. **Re-индексация по cron** — KB сейчас статичная, но правила WB меняются.
5. **Распределённый reranker** на отдельном GPU-инстансе — снимает trade-off с VRAM.
