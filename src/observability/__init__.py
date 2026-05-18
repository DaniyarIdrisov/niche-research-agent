"""
Логи (Loguru), трейсы (OpenTelemetry), метрики (Prometheus), LLM-трейсы (Langfuse).

Единая точка инициализации — setup_observability(). Идемпотентна.
"""

from src.observability.logging import setup_logging
from src.observability.metrics import setup_metrics
from src.observability.tracing import setup_tracing


def setup_observability() -> None:
    """Один вызов — поднимаем всё. Каждый компонент сам себя защищает от ошибок."""
    setup_logging()
    setup_metrics()
    setup_tracing()


__all__ = ["setup_logging", "setup_metrics", "setup_tracing", "setup_observability"]
