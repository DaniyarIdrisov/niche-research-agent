# CLAUDE.md — brief для Claude Code

> Этот файл автоматически читается Claude Code при старте сессии в этом репозитории. Если ты — Claude Code и видишь это: ниже сжатый брифинг, после которого ты войдёшь в контекст. Далее погружайся в `docs/`.

---

## Что это за проект

**Niche Research Agent** — университетский проект: мультиагентная система на LangGraph для ресёрча ниши малой бытовой техники (МБТ) на Wildberries.

На входе: NL-запрос вида *«оцени нишу — электрические зубные щётки до 3000 рублей»*.
На выходе: markdown-отчёт + PRD по шаблону + вердикт go / conditional-go / no-go / no-data.

Всё локально, GTX 1050 Ti / 4 ГБ VRAM, открытые модели.

## С чего начать чтение (порядок важен)

1. **[docs/DEFENSE_NOTES.md](docs/DEFENSE_NOTES.md)** — конспект для устной защиты. Содержит elevator pitch, все архитектурные решения с обоснованием, slabые места, альтернативы. **Если читаешь только один файл — этот.**
2. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — поток данных, граф LangGraph, компоненты по слоям.
3. **[docs/TZ.md](docs/TZ.md)** — формальное ТЗ.
4. **[README.md](README.md)** — как запустить.

Дальше по необходимости:
- **[docs/MEMORY.md](docs/MEMORY.md)** — три уровня памяти, hybrid RAG, альтернативы.
- **[docs/EVALS.md](docs/EVALS.md)** — методология оценки.
- **[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)** — Loguru/OTel/Prometheus/Langfuse.
- **[docs/ISOLATION.md](docs/ISOLATION.md)** — Docker, gVisor, Firecracker, WASM.

## Карта репозитория (что где)

```
src/agents/        — 6 агентов: scout, niche_analyst, specs_miner, usp_analyst, prd_writer, reporter
                     + supervisor.py (LangGraph граф)
src/llm/           — Ollama-клиент (raw httpx, без langchain-обёрток)
src/memory/        — working (state) + episodic (SQLite) + semantic (ChromaDB + BM25)
src/tools/         — calculator, wb_parser, specs_normalizer, usp_classifier, unit_economics, prd_validator
src/schemas/       — Pydantic + TypedDict (Product, QueryFilters, AgentState, NicheMetrics, PRD, etc)
src/observability/ — tracing (OTel), metrics (Prometheus), logging (Loguru), langfuse_hook
src/main.py        — Typer CLI

prompts/           — 6 системных промптов для агентов (Markdown с few-shot)
skills/            — 6 скиллов в Anthropic-формате (SKILL.md + опц. скрипты)
knowledge_base/    — 10 .md документов: wb_rules, specs_taxonomy, compliance, templates
data/              — mock_wb_products.json (115 карточек, seed=42) + eval_queries.json (15 запросов)
evals/             — component_evals.py + system_evals.py + run_evals.ipynb
docs/              — все архитектурные документы
observability/     — prometheus.yml, otel-collector-config.yaml, grafana dashboards
docker-compose.yml — ChromaDB, Jaeger, Prometheus, Grafana, Langfuse, OTel collector. Ollama — на хосте.
pyproject.toml     — uv-проект, Python 3.11+
.env.example       — все настройки, скопировать в .env при первом запуске
```

## Текущее состояние

✅ Всё 6 фаз закрыты:
1. Структура + README + ТЗ + Docker Compose
2. Mock-датасет (115 карточек) + eval-запросы (15)
3. MVP: Ollama-клиент + Scout + WB-парсер + LangGraph скелет
4. Все 5 оставшихся агентов + 3 слоя памяти + 6 skills + промпты
5. Полный RAG (ChromaDB + BM25 + опц. реранкер) + KB-контент + evals
6. Observability стек + Grafana дашборд + все 6 doc-файлов

**Что НЕ сделано (известное):**
- End-to-end smoke-run не запускался — нужны `uv sync` + работающая Ollama на хосте.
- `find_similar_queries` в episodic — только LIKE-fallback, не embeddings. Future work.
- Размеченных `(query, golden_chunks)` пар для RAG Recall@K — нет. Future work.

Полный список future work — в DEFENSE_NOTES.md разделы 7-8.

## Ключевые архитектурные принципы (важно для последовательности)

1. **LLM — интерпретатор, не калькулятор.** Любая арифметика, нормализация, классификация — pure Python в `src/tools/`. LLM только формулирует, кластеризует, пишет нарратив. **Не вводи LLM-вычисления.**
2. **Scout парсит только query → filters, не продукты.** Продукты приходят из `wb_parser` детерминированно. Не давай LLM генерить списки товаров.
3. **Structured output через format=json + Pydantic + repair-loop** (см. `OllamaClient.chat_structured`). Не используй `langchain.with_structured_output()` — на 3B плохо работает.
4. **Параллельный fan-out из conditional router** — `return [list]`, не отдельные edges.
5. **Graceful degradation на каждом уровне:**
   - LLM упал → fallback на rule-based/threshold (см. `specs_miner._fallback_split`, `reporter._machine_fallback_report`)
   - ChromaDB недоступен → retrieve() = []
   - OTel/Langfuse недоступны → no-op
6. **Observability через декоратор `_instrument` в supervisor.** Агенты не знают про OTel/metrics напрямую.

## Стиль кода и комментариев

- **Комментарии — пишутся «для коллеги», не «для модели».** Зачем именно так, а не иначе; на что обратить внимание; почему отвергнуты альтернативы.
- **Промпты — много few-shot.** На 3B без примеров JSON разваливается.
- **Никаких эмодзи** в коде и в файлах (если только не запрошено явно).
- **Все настройки — через .env**, не зашитыми константами. Это для тюнинга без перекомпиляции.

## Запуск (как пользователю)

См. [README.md](README.md). Минимум:

```bash
uv sync
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull nomic-embed-text
cp .env.example .env
docker compose up -d
uv run python -m src.memory.semantic ingest knowledge_base/   # один раз
uv run python -m src.main check                               # health check
uv run python -m src.main research "электрические зубные щётки до 3000 рублей"
```

## Если ты — новый Claude и user попросил продолжить работу

1. Прочитай DEFENSE_NOTES.md полностью.
2. Прочитай ARCHITECTURE.md.
3. Спроси у user'а: что именно нужно сделать — баг-фикс, расширение, eval, отладка реального запуска?
4. Не вводи новые архитектурные паттерны без обсуждения. Уважай существующие решения — они задокументированы.
5. Если меняешь промпт или агента — прогони `python -m compileall src/` и обнови соответствующий док в `docs/`.
6. Перед коммитом — спроси user'а. **Никогда не коммить автоматически.**

## Контакты архитектурных решений (если что-то непонятно)

| Вопрос | Где ответ |
|---|---|
| Почему Supervisor, а не Swarm? | DEFENSE_NOTES.md §2 |
| Почему 6 агентов? | DEFENSE_NOTES.md §2 |
| Почему raw httpx, а не langchain-ollama? | docs/ARCHITECTURE.md §«Ключевые архитектурные решения» |
| Почему hybrid RAG (dense + BM25)? | docs/MEMORY.md |
| Почему реранкер выключен по умолчанию? | docs/MEMORY.md + README.md «Тонкости работы на 1050 Ti» |
| Почему Langfuse, а не LangSmith? | docs/OBSERVABILITY.md |
| Почему Docker, а не gVisor? | docs/ISOLATION.md |
| Почему LLM-as-judge той же моделью? | docs/EVALS.md «LLM-as-judge» |

---

**Версия этого брифа:** v1 (initial commit). При значительных изменениях архитектуры — обновляй эту страницу.
