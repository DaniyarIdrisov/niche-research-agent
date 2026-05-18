"""
OpenTelemetry-инструментация.

Архитектура:
  приложение → OTLP gRPC → otel-collector (docker) → Jaeger
                                                 └→ Prometheus (метрики из коллектора)

Использование:
    from src.observability.tracing import setup_tracing, trace_span

    setup_tracing()
    with trace_span("scout.parse_query", query=user_query) as span:
        result = ...
        span.set_attribute("scout.parse_ok", True)

Зачем своя обёртка `trace_span` вместо прямого `tracer.start_as_current_span`:
- единая точка для добавления стандартных атрибутов (service.name, run_id);
- graceful degradation: если OTel не сконфигурен, обёртка работает как no-op,
  тесты не падают;
- меньше copy-paste по агентам.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator

from loguru import logger

from src.config import get_settings

_INITIALIZED = False
_TRACER = None


def setup_tracing() -> None:
    """Идемпотентная инициализация. При отсутствии opentelemetry — no-op."""
    global _INITIALIZED, _TRACER
    if _INITIALIZED:
        return

    s = get_settings()
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        logger.warning("tracing.no_lib", error=str(e), note="OTel disabled")
        _INITIALIZED = True
        return

    resource = Resource.create({SERVICE_NAME: s.otel_service_name})
    provider = TracerProvider(resource=resource)
    try:
        exporter = OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception as e:
        # Коллектор недоступен — продолжаем без export, чтобы не ломать pipeline
        logger.warning("tracing.exporter_failed", endpoint=s.otel_exporter_otlp_endpoint, error=str(e))

    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer(__name__)
    _INITIALIZED = True
    logger.info("tracing.configured", endpoint=s.otel_exporter_otlp_endpoint)


@contextlib.contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[Any]:
    """
    Контекст-менеджер для спана. No-op если OTel не инициализирован или нет
    библиотеки.
    """
    if _TRACER is None:
        # No-op span — даём минимальный API .set_attribute, .record_exception
        yield _NoopSpan()
        return

    with _TRACER.start_as_current_span(name) as span:
        for k, v in attributes.items():
            if v is None:
                continue
            try:
                span.set_attribute(k, v)
            except Exception:
                # OTel не любит сложные типы — игнорим
                pass
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            raise


class _NoopSpan:
    """No-op span с минимальным API, чтобы код в агентах работал без OTel."""

    def set_attribute(self, *_args, **_kwargs) -> None:
        pass

    def record_exception(self, _e: Exception) -> None:
        pass

    def add_event(self, *_args, **_kwargs) -> None:
        pass
