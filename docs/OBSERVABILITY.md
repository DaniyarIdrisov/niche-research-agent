# Observability

## Стек

| Слой | Технология | Где |
|---|---|---|
| Логи | Loguru → JSONL файл + stdout | `src/observability/logging.py` |
| Трейсы | OpenTelemetry SDK → OTel collector → Jaeger | `src/observability/tracing.py` |
| Метрики | prometheus_client → HTTP endpoint → Prometheus → Grafana | `src/observability/metrics.py` |
| LLM-специфика | Langfuse (self-hosted) | `src/observability/langfuse_hook.py` |

Все подсистемы — **необязательные** в смысле работоспособности pipeline. Если коллектор недоступен, Langfuse не отвечает, prometheus_client не установлен — приложение работает, инструментация деградирует до no-op.

## Что инструментируем

### Spans (OTel)

| Span | Атрибуты |
|---|---|
| `node.<name>` | node.name, run_id |
| `ollama.chat` | llm.model, llm.json_mode, llm.temperature, llm.prompt_tokens, llm.completion_tokens, llm.latency_s |

Все spans коррелируются по `run_id` — в Jaeger можно фильтровать по `run_id=<uuid>` и видеть весь trace одного запроса.

### Метрики (Prometheus)

| Метрика | Тип | Labels | Что показывает |
|---|---|---|---|
| `llm_request_total` | counter | model, json_mode, outcome | сколько LLM-вызовов |
| `llm_request_latency_seconds` | histogram | model, json_mode | distrib. latency |
| `llm_tokens_total` | counter | model, kind=prompt\|completion | расход токенов |
| `llm_structured_parse_failures_total` | counter | schema | сколько раз JSON не распарсился |
| `agent_node_duration_seconds` | histogram | node, outcome | latency на ноду |
| `agent_retry_total` | counter | node | retry-цикл |
| `rag_retrieve_total` | counter | outcome | retrieve calls |
| `rag_chunks_returned` | histogram | — | сколько чанков |

### Логи (Loguru)

- **stdout**: цветной короткий формат, level из `.env`.
- **`logs/agent.jsonl`**: JSON-сериализованный, серилизация со всеми полями `extra`, ротация 10 MB.

В JSON-логах есть поля: `time`, `level`, `name`, `message`, `extra.run_id`, `extra.node`, `extra.error`. Это нормально парсится `jq`, ingest'ится в Loki, и т.п.

### Langfuse

Langfuse — отдельно от OTel, потому что он специально заточен под LLM:
- Хранит **полный текст** промпта и ответа (не только токены).
- Группирует вызовы по trace_id.
- Даёт UI для поиска по содержимому промптов («покажи все вызовы, где промпт содержал "ионизация"»).

В нашем коде Langfuse-логирование — отдельный хук, который вызывается из OllamaClient опционально. По умолчанию работает на dev-credentials из `.env.example`. Для прода нужны реальные ключи.

## Поток данных observability

```
                    приложение (Python)
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   Loguru JSONL     OTel SDK         prometheus_client
        │                 │                 │
        ▼                 ▼                 ▼
   logs/*.jsonl     otel-collector     :9464/metrics
                          │                 │
                          ▼                 ▼
                       Jaeger          Prometheus
                                            │
                                            ▼
                                         Grafana
```

Langfuse идёт мимо OTel — у него собственный HTTP API в self-hosted-контейнер.

## Где смотреть

| Что | URL | Логин |
|---|---|---|
| Jaeger UI | http://localhost:16686 | — |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | admin/admin |
| Langfuse | http://localhost:3001 | первый юзер создаёт себя |
| JSONL логи | `logs/agent.jsonl` | — |

В Grafana предсозданный дашборд: **Niche Research Agent**. JSON — `observability/grafana/dashboards/niche_research_agent.json`. Подключён через провижининг.

## Дашборд (что на нём)

Панели:
1. Total LLM requests (last hour)
2. Average LLM latency p95
3. JSON parse failures (last hour)
4. Total tokens (last hour)
5. LLM latency by JSON mode (p50, p95)
6. Agent node duration p95 by node
7. LLM throughput by outcome
8. Agent retries per minute
9. RAG retrieve outcome
10. RAG chunks returned (p50, p95)
11. Tokens by kind (prompt/completion)

## Алертинг

В Grafana ставится через UI на тех же графиках. Рекомендуемые алерты для нашего стека:

| Алерт | Условие | Severity |
|---|---|---|
| LLM latency p95 высокая | p95 > 60s in 10 min | warning |
| Слишком много parse failures | rate > 1/min | warning |
| Retry rate высокий | rate > 0.5/min | warning |
| RAG возвращает пусто часто | empty-rate > 50% in 10 min | warning |
| Ollama не отвечает | request_total flat zero in 5 min | critical |

JSON-файлы алертов не пакуем — это операционная вещь, делается под локальные пороги.

## Сравнение с альтернативами

### LangSmith (LangChain)

**Плюсы:** удобный UI, специально под LangChain, можно делать prompt versioning.

**Минусы:** облачный, отправляет промпты в США (нарушает требование локальности). Платный.

### Arize Phoenix

**Плюсы:** легче Langfuse, поднимается одним контейнером, hosted версия бесплатна.

**Минусы:** не настолько полный UI, как у Langfuse. Меньше интеграций.

### Helicone

**Плюсы:** удобный для облачных LLM API (OpenAI / Anthropic / etc).

**Минусы:** заточен под proxy-режим, в нашем случае с локальной Ollama требует переписывания endpoint'ов.

### W&B Weave

**Плюсы:** хорош, если уже используешь W&B для ML-экспериментов.

**Минусы:** облачный по умолчанию, on-prem сильно сложнее.

**Почему мы выбрали Langfuse:** self-hosted из коробки (одна команда compose up), хороший UI для промптов, фокус именно на LLM-tracing, OSS-лицензия.

## Известные недостатки текущего стека

1. **Langfuse v2, а не v3.** v3 требует ClickHouse — на 16 GB RAM это уже больно. v2 стабильна, но скоро deprecated.
2. **`host.docker.internal` в `prometheus.yml`** работает на Docker Desktop (Win/Mac). На голом Linux потребует `extra_hosts` в docker-compose.yml или замены на IP хоста.
3. **OTel collector → Jaeger** идёт по OTLP, но Jaeger UI v1 не умеет показать атрибуты span очень удобно. v2 (binary, beta) лучше — можно мигрировать.
4. **Tokens-метрика учитывает только то, что Ollama отдала в response.** Для длинных промптов с repair-loop отдельные вызовы суммируются — это видно как несколько событий в Jaeger.

## Smoke-test observability

```bash
# 1. Поднять стек
docker compose up -d

# 2. Прогнать запрос
uv run python -m src.main research "электрические зубные щётки до 3000 рублей"

# 3. Проверить везде:
curl http://localhost:9464/metrics | grep llm_request_total
open http://localhost:16686                  # Jaeger
open http://localhost:3000                   # Grafana, дашборд "Niche Research Agent"
open http://localhost:3001                   # Langfuse, ищем trace по run_id
tail logs/agent.jsonl | jq
```
