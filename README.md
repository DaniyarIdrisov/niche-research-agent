# Niche Research Agent

Мультиагентная система для ресёрча ниши **малой бытовой техники (МБТ)** на Wildberries.
Локальный запуск на слабом железе (GTX 1050 Ti, 4 ГБ VRAM), полностью на открытых моделях.

На входе — запрос вида *«оцени нишу — электрические зубные щётки до 3000 рублей»*.
На выходе — аналитический отчёт по нише, PRD на новый товар, анализ УТП конкурентов и вердикт go/no-go.

---

## TL;DR

```bash
# 1. Установить Ollama и скачать модели
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull nomic-embed-text

# 2. Поднять инфраструктуру
docker compose up -d

# 3. Установить зависимости проекта (uv)
uv sync

# 4. Прогнать первый запрос
uv run python -m src.main research "электрические зубные щётки до 3000 рублей"
```

Подробности — ниже.

---

## Содержание

1. [Требования](#требования)
2. [Быстрый старт](#быстрый-старт)
3. [Что внутри](#что-внутри)
4. [Конфигурация](#конфигурация)
5. [Запуск запроса](#запуск-запроса)
6. [Observability — где смотреть](#observability--где-смотреть)
7. [Evals](#evals)
8. [Тонкости работы на 1050 Ti](#тонкости-работы-на-1050-ti)
9. [Troubleshooting](#troubleshooting)
10. [Документация](#документация)

---

## Требования

**Железо (минимум):**
- GPU: NVIDIA с **≥4 ГБ VRAM** (тестировалось на GTX 1050 Ti)
- CPU: 4 ядра
- RAM: 16 ГБ (на 8 ГБ можно запустить, но эмбеддер придётся гонять в swap — см. Troubleshooting)
- Диск: ~15 ГБ под модели и образы Docker

**ПО:**
- Docker Desktop / Docker Engine + Docker Compose v2
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) — менеджер пакетов (`pip install uv` или `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [Ollama](https://ollama.com) — рантайм LLM
- (Опционально) NVIDIA Container Toolkit, если хочется отдать GPU в Docker. По умолчанию Ollama запускается **на хосте**, чтобы избежать проблем с прокидыванием CUDA в контейнер.

**ОС:** Linux, macOS, Windows 11 + WSL2.

---

## Быстрый старт

### 1. Клонируем и заходим в проект

```bash
git clone <repo-url> niche-research-agent
cd niche-research-agent
cp .env.example .env
```

### 2. Ставим Ollama и скачиваем модели

```bash
# Установка Ollama (Linux/macOS — см. ollama.com для других ОС)
curl -fsSL https://ollama.com/install.sh | sh

# Основная LLM — Qwen2.5 3B Instruct в Q4_K_M (~2.0 GB VRAM)
ollama pull qwen2.5:3b-instruct-q4_K_M

# Fallback (если Qwen не справляется с русским JSON в твоём кейсе)
ollama pull llama3.2:3b-instruct-q4_K_M

# Эмбеддинг-модель — гоняем на CPU
ollama pull nomic-embed-text

# (Опционально, по флагу RAG_USE_RERANKER=true) ререйнкер
# bge-reranker-v2-m3 запускается отдельно, не через Ollama — см. docs/MEMORY.md
```

### 3. Поднимаем инфраструктуру

```bash
docker compose up -d
```

Поднимутся:
- `chromadb` — векторная БД (порт 8001)
- `jaeger` — трейсы (UI: http://localhost:16686)
- `prometheus` — метрики (http://localhost:9090)
- `grafana` — дашборды (http://localhost:3000, логин/пароль `admin/admin`)
- `langfuse` + `langfuse-db` — LLM-трейсинг (http://localhost:3001)
- `otel-collector` — сборщик OpenTelemetry

Ollama запускается **на хосте**, не в Docker — так проще с GPU.

### 4. Устанавливаем зависимости проекта

```bash
uv sync
```

### 5. Прогреваем knowledge base (один раз)

```bash
uv run python -m src.memory.semantic ingest knowledge_base/
```

Команда читает все `.md` в `knowledge_base/`, разбивает на чанки и кладёт в ChromaDB.

---

## Что внутри

**Архитектура — Supervisor + 6 воркеров на LangGraph:**

```
                            START
                              │
                              ▼
                            Scout
                              │
                ┌─────────────┼─────────────┐
                ▼             ▼             ▼
         Niche Analyst  Specs Miner  USP Analyst
                └─────────────┬─────────────┘
                              ▼
                          PRD Writer
                              │
                              ▼
                           Reporter
                              │
                              ▼
                             END
```

Между критичными узлами — валидационные ноды с retry (≤2 попыток).

| Агент | Что делает | Температура |
|---|---|---|
| **Scout** | Собирает топ-100 карточек WB (mock или live), отдаёт структурированный JSON | 0.1 |
| **Niche Analyst** | Метрики ниши (оборот, доля топ-5, медиана, спред, динамика) через Calculator tool | 0.2 |
| **Specs Miner** | Извлечение и нормализация характеристик из топа | 0.2 |
| **USP Analyst** | Кластеризация УТП топ-5 по типам, поиск пустот | 0.3 |
| **PRD Writer** | PRD по шаблону через Pydantic structured output | 0.2 |
| **Reporter** | Финальный отчёт для пользователя | 0.4 |

**Память — три уровня:**
1. **Working** — state LangGraph, передаётся между нодами.
2. **Episodic** — SQLite, история запусков.
3. **Semantic** — ChromaDB + hybrid search (dense + BM25) + опциональный реранкер.

Подробнее — в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) и [`docs/MEMORY.md`](docs/MEMORY.md).

---

## Конфигурация

Все настройки — через `.env`. Ключевые переменные:

| Переменная | Дефолт | Назначение |
|---|---|---|
| `DATA_SOURCE` | `mock` | `mock` — фейковый датасет из `data/mock_wb_products.json`. `live` — реальный WB через `search.wb.ru` (может перестать работать) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Адрес Ollama |
| `OLLAMA_LLM_MODEL` | `qwen2.5:3b-instruct-q4_K_M` | Основная LLM |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Эмбеддер (CPU) |
| `CHROMA_HOST` / `CHROMA_PORT` | `localhost` / `8001` | ChromaDB |
| `SQLITE_PATH` | `./data/episodic.db` | Episodic memory |
| `RAG_USE_RERANKER` | `false` | Включить bge-reranker (требует выгрузки LLM из VRAM на время) |
| `LOG_LEVEL` | `INFO` | Уровень логов |
| `LANGFUSE_HOST` | `http://localhost:3001` | LLM-трейсинг |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTel collector |
| `MAX_RETRIES_PER_NODE` | `2` | Сколько раз ретраить агента при невалидном выходе |

Полный список — в [`.env.example`](.env.example).

---

## Запуск запроса

```bash
# Базовый кейс
uv run python -m src.main research "электрические зубные щётки до 3000 рублей"

# С указанием категории и ценового потолка
uv run python -m src.main research "фены для волос" --max-price 5000 --category beauty

# Получить результаты прошлого запуска по id
uv run python -m src.main history --run-id <uuid>

# Список всех прошлых запусков
uv run python -m src.main history list
```

Результат пишется и в stdout (markdown-отчёт), и в `runs/<run-id>/` (JSON + markdown).

---

## Observability — где смотреть

| Что | Где | URL |
|---|---|---|
| Трейсы агентов (OTel spans) | Jaeger | http://localhost:16686 |
| Метрики (latency, токены, success rate) | Grafana | http://localhost:3000 → дашборд **Niche Research Agent** |
| Сырые метрики | Prometheus | http://localhost:9090 |
| Промпты/ответы LLM | Langfuse | http://localhost:3001 |
| Логи | stdout + `logs/agent.jsonl` | tail -f |

Подробности и сравнение с альтернативами — в [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md).

---

## Evals

```bash
# Component-level: отдельные агенты
uv run python -m evals.component_evals

# System-level: end-to-end на эталонных запросах
uv run python -m evals.system_evals

# Интерактивно с визуализацией
uv run jupyter lab evals/run_evals.ipynb
```

Методология и пороги приёмки — [`docs/EVALS.md`](docs/EVALS.md).

---

## Тонкости работы на 1050 Ti

**Главная проблема: 4 ГБ VRAM не вмещает LLM + эмбеддер + реранкер одновременно.**

Что делаем:
1. **Эмбеддер — на CPU.** `nomic-embed-text` через Ollama по умолчанию идёт на CPU (Ollama сама решает). Скорость ~50 чанков/сек на i5 — для нашего KB хватает.
2. **LLM — в VRAM.** Qwen2.5 3B Q4_K_M занимает ~2.0 GB, остаётся ~1.5 GB на KV-cache. Контекст ограничен 4096 токенов в `.env` — этого хватает.
3. **Реранкер — выключен по умолчанию.** Если включить (`RAG_USE_RERANKER=true`), на время вызова LLM выгружается, ререйнкер загружается, после — обратно. Это медленно (~3 секунды overhead на запрос), но даёт +10-15% к качеству retrieval.
4. **Не запускайте Ollama в Docker** на этой машине — пробрасывать CUDA в контейнер ради экономии VRAM нерентабельно.

Если работаешь без CUDA вообще — выставь `OLLAMA_LLM_MODEL=qwen2.5:1.5b-instruct-q4_K_M` (поместится в RAM и пойдёт на CPU). Качество просядет, но система останется работоспособной.

---

## Troubleshooting

**`ollama: command not found`**
Ollama не установлена или не в PATH. Поставь по инструкции с [ollama.com](https://ollama.com), затем `ollama serve` в отдельном терминале.

**`CUDA out of memory` при первом запросе**
LLM + что-то ещё пытается влезть в VRAM. Проверь, что выключен реранкер (`RAG_USE_RERANKER=false`), закрой браузер и другие GPU-приложения. В крайнем случае — `OLLAMA_LLM_MODEL=qwen2.5:1.5b-instruct-q4_K_M`.

**`Connection refused: localhost:8001` (ChromaDB)**
Контейнер не поднялся. `docker compose ps` — посмотри статус. `docker compose logs chromadb` — посмотри ошибку.

**Scout возвращает 0 карточек в `live`-режиме**
`search.wb.ru` отдал пустой ответ или сменил формат. Переключись на `DATA_SOURCE=mock`, открой issue.

**Невалидный JSON от агента**
3B-модель иногда сбоит на длинных промптах. Система автоматически ретраит до `MAX_RETRIES_PER_NODE` раз. Если падает стабильно — попробуй `OLLAMA_LLM_MODEL=llama3.2:3b-instruct-q4_K_M`.

**`OOM Killed` у ChromaDB на больших коллекциях**
В `docker-compose.yml` подкрути лимиты памяти контейнера. На 16 ГБ RAM лимит 2 ГБ — нормально.

**Windows + WSL2: Ollama не видит GPU из WSL**
Поставь Ollama в Windows (нативно), а не в WSL. Из WSL обращайся к ней через `OLLAMA_BASE_URL=http://host.docker.internal:11434`.

---

## Документация

- [`docs/TZ.md`](docs/TZ.md) — техническое задание
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — архитектура системы целиком
- [`docs/MEMORY.md`](docs/MEMORY.md) — память и RAG, альтернативы, недостатки
- [`docs/EVALS.md`](docs/EVALS.md) — методология оценки
- [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) — observability стек и альтернативы
- [`docs/ISOLATION.md`](docs/ISOLATION.md) — варианты изоляции и обоснование выбора
- [`docs/PRD_TEMPLATE.md`](docs/PRD_TEMPLATE.md) — шаблон выходного PRD
- [`docs/DEFENSE_NOTES.md`](docs/DEFENSE_NOTES.md) — тезисы для устной защиты проекта

---

## Лицензия

Учебный проект. Использует открытые модели (Qwen, Llama 3.2, nomic-embed-text), все зависимости — OSS.
