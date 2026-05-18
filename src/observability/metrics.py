"""
Prometheus-метрики.

Поднимаем HTTP-endpoint на METRICS_PORT (по умолчанию 9464). Prometheus в
docker-compose скрейпит его через host.docker.internal:9464 (см.
observability/prometheus.yml).

Что инструментируем:
  - llm_request_total{model,json_mode,outcome}        — счётчик LLM-вызовов
  - llm_request_latency_seconds{model,json_mode}      — гистограмма latency
  - llm_tokens_total{model,kind=prompt|completion}    — счётчик токенов
  - llm_structured_parse_failures_total{schema}       — счётчик невалидного JSON
  - agent_node_duration_seconds{node,outcome}         — гистограмма по нодам
  - agent_retry_total{node}                           — счётчик ретраев
  - rag_retrieve_total{outcome}                       — счётчик retrieve-вызовов
  - rag_chunks_returned                               — гистограмма числа чанков

Если prometheus_client не установлен — все wrappers становятся no-op.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from loguru import logger

from src.config import get_settings

_INITIALIZED = False

try:
    from prometheus_client import Counter, Histogram, start_http_server

    LLM_REQUESTS = Counter(
        "llm_request_total",
        "Total LLM chat requests",
        ["model", "json_mode", "outcome"],
    )
    LLM_LATENCY = Histogram(
        "llm_request_latency_seconds",
        "LLM chat latency seconds",
        ["model", "json_mode"],
        buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    )
    LLM_TOKENS = Counter(
        "llm_tokens_total",
        "Total tokens by kind",
        ["model", "kind"],
    )
    LLM_PARSE_FAIL = Counter(
        "llm_structured_parse_failures_total",
        "Pydantic-validation failures from LLM JSON output",
        ["schema"],
    )
    AGENT_DURATION = Histogram(
        "agent_node_duration_seconds",
        "Duration of agent node execution",
        ["node", "outcome"],
        buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    )
    AGENT_RETRY = Counter(
        "agent_retry_total",
        "Number of agent retries",
        ["node"],
    )
    RAG_RETRIEVE = Counter(
        "rag_retrieve_total",
        "RAG retrieve calls",
        ["outcome"],
    )
    RAG_CHUNKS = Histogram(
        "rag_chunks_returned",
        "Number of chunks returned by retrieve",
        buckets=(0, 1, 3, 5, 10, 20),
    )

    _HAS_PROM = True
except ImportError as e:
    logger.warning("metrics.no_lib", error=str(e))
    _HAS_PROM = False


def setup_metrics() -> None:
    """Поднимает HTTP-endpoint для Prometheus. Идемпотентно."""
    global _INITIALIZED
    if _INITIALIZED or not _HAS_PROM:
        return
    port = get_settings().metrics_port
    try:
        start_http_server(port)
        _INITIALIZED = True
        logger.info("metrics.configured", port=port)
    except OSError as e:
        # Порт занят (например, второй запуск в той же сессии) — пропускаем
        logger.warning("metrics.port_busy", port=port, error=str(e))
        _INITIALIZED = True


# ---------------------------------------------------------------------------
# Public helpers — кладём по тегам как одну строку, чтобы не дублировать try/except
# ---------------------------------------------------------------------------


def record_llm_request(
    *,
    model: str,
    json_mode: bool,
    outcome: str,
    latency_s: float,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    if not _HAS_PROM:
        return
    j = "true" if json_mode else "false"
    LLM_REQUESTS.labels(model=model, json_mode=j, outcome=outcome).inc()
    LLM_LATENCY.labels(model=model, json_mode=j).observe(latency_s)
    if prompt_tokens:
        LLM_TOKENS.labels(model=model, kind="prompt").inc(prompt_tokens)
    if completion_tokens:
        LLM_TOKENS.labels(model=model, kind="completion").inc(completion_tokens)


def record_parse_failure(schema: str) -> None:
    if not _HAS_PROM:
        return
    LLM_PARSE_FAIL.labels(schema=schema).inc()


def record_retry(node: str) -> None:
    if not _HAS_PROM:
        return
    AGENT_RETRY.labels(node=node).inc()


@contextmanager
def measure_node(node: str) -> Iterator[dict[str, Any]]:
    """
    Контекст для нод графа. Записывает duration и outcome (success / failure).
        with measure_node("scout") as m:
            ...
            m["outcome"] = "success"
    """
    if not _HAS_PROM:
        yield {"outcome": "success"}
        return

    import time
    ctx: dict[str, Any] = {"outcome": "success"}
    t0 = time.perf_counter()
    try:
        yield ctx
    except Exception:
        ctx["outcome"] = "failure"
        raise
    finally:
        AGENT_DURATION.labels(node=node, outcome=ctx["outcome"]).observe(time.perf_counter() - t0)


def record_retrieve(*, n_chunks: int, outcome: str) -> None:
    if not _HAS_PROM:
        return
    RAG_RETRIEVE.labels(outcome=outcome).inc()
    RAG_CHUNKS.observe(n_chunks)
