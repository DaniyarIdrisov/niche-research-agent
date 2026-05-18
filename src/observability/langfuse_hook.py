"""
Langfuse — LLM-специфичный observability.

В отличие от OTel-спанов, Langfuse красиво визуализирует промпт+ответ+метаданные
для каждого вызова и позволяет искать по содержимому промптов.

Self-hosted Langfuse поднят в docker-compose. По умолчанию приложение пытается
писать туда — если хост недоступен или ключи невалидны, делает no-op
и логирует warning.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.config import get_settings

_CLIENT = None
_TRIED_INIT = False


def get_client() -> Any | None:
    """Lazy-singleton Langfuse-клиент. Возвращает None если недоступен."""
    global _CLIENT, _TRIED_INIT
    if _TRIED_INIT:
        return _CLIENT
    _TRIED_INIT = True

    s = get_settings()
    try:
        from langfuse import Langfuse

        _CLIENT = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
        logger.info("langfuse.configured", host=s.langfuse_host)
    except Exception as e:
        logger.warning("langfuse.init_failed", error=str(e), note="will skip LLM tracing")
        _CLIENT = None
    return _CLIENT


def log_llm_call(
    *,
    name: str,
    model: str,
    messages: list[dict],
    response: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    metadata: dict | None = None,
) -> None:
    """Записывает один LLM-вызов в Langfuse. No-op если клиент недоступен."""
    client = get_client()
    if client is None:
        return
    try:
        trace = client.trace(name=name, metadata=metadata or {})
        trace.generation(
            name=name,
            model=model,
            input=messages,
            output=response,
            usage={"input": prompt_tokens or 0, "output": completion_tokens or 0},
        )
    except Exception as e:
        # Не падаем pipeline из-за телеметрии
        logger.debug("langfuse.log_failed", error=str(e))


def flush() -> None:
    """Сбрасывает буфер Langfuse. Вызывать перед exit() из CLI."""
    client = get_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as e:
        logger.debug("langfuse.flush_failed", error=str(e))
