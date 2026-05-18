"""
Тонкая обёртка над Ollama HTTP API.

Зачем своя обёртка, а не langchain-ollama:
- меньше абстракций → проще трейсить и защищать на устной защите;
- можно точечно навесить tenacity-retry, метрики Prometheus и Langfuse-callback
  без копания в чужих интерфейсах;
- на 3B-моделях структурный вывод работает нестабильно через .with_structured_output —
  тут мы сами форсим format="json" + парсим Pydantic вручную с явной retry-петлёй.

Никакой стриминг здесь сознательно не реализован — нам нужен whole-response для
парсинга и валидации.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any, TypeVar

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.observability.metrics import record_llm_request, record_parse_failure
from src.observability.tracing import trace_span

T = TypeVar("T", bound=BaseModel)


class OllamaError(RuntimeError):
    """Любая ошибка от Ollama — таймаут, 5xx, невалидный ответ."""


class StructuredOutputError(RuntimeError):
    """Не удалось распарсить ответ модели в требуемую схему даже после retry."""


class OllamaClient:
    """
    Синхронный клиент к Ollama. Async-вариант не нужен: внутри одной ноды графа
    мы делаем 1 LLM-вызов, параллелизм — на уровне LangGraph (несколько нод
    могут идти параллельно, но каждая — синхронный вызов).
    """

    def __init__(
        self,
        base_url: str | None = None,
        llm_model: str | None = None,
        embed_model: str | None = None,
        num_ctx: int | None = None,
        timeout: int | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.ollama_base_url).rstrip("/")
        self.llm_model = llm_model or s.ollama_llm_model
        self.embed_model = embed_model or s.ollama_embed_model
        self.num_ctx = num_ctx or s.ollama_num_ctx
        self._client = httpx.Client(timeout=timeout or s.ollama_request_timeout)

    # ------------------------------------------------------------------ chat

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, OllamaError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        json_mode: bool = False,
        extra_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Низкоуровневый chat-вызов. Возвращает сырой response Ollama:
            {
              "message": {"role": "assistant", "content": "..."},
              "prompt_eval_count": N, "eval_count": M, "total_duration": ns
            }
        """
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": self.num_ctx,
        }
        if extra_options:
            options.update(extra_options)

        payload: dict[str, Any] = {
            "model": self.llm_model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"

        t0 = time.perf_counter()
        outcome = "success"
        prompt_tokens = None
        completion_tokens = None
        with trace_span(
            "ollama.chat",
            **{
                "llm.model": self.llm_model,
                "llm.json_mode": json_mode,
                "llm.temperature": temperature,
            },
        ) as span:
            try:
                resp = self._client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                outcome = f"http_{e.response.status_code}"
                record_llm_request(
                    model=self.llm_model,
                    json_mode=json_mode,
                    outcome=outcome,
                    latency_s=time.perf_counter() - t0,
                    prompt_tokens=None,
                    completion_tokens=None,
                )
                raise OllamaError(
                    f"Ollama returned {e.response.status_code}: {e.response.text[:200]}"
                ) from e
            except httpx.HTTPError as e:
                outcome = "network_error"
                record_llm_request(
                    model=self.llm_model,
                    json_mode=json_mode,
                    outcome=outcome,
                    latency_s=time.perf_counter() - t0,
                    prompt_tokens=None,
                    completion_tokens=None,
                )
                raise

            data = resp.json()
            elapsed = time.perf_counter() - t0
            prompt_tokens = data.get("prompt_eval_count")
            completion_tokens = data.get("eval_count")
            span.set_attribute("llm.prompt_tokens", prompt_tokens or 0)
            span.set_attribute("llm.completion_tokens", completion_tokens or 0)
            span.set_attribute("llm.latency_s", round(elapsed, 3))
            logger.debug(
                "ollama.chat",
                model=self.llm_model,
                json_mode=json_mode,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_s=round(elapsed, 2),
            )
            record_llm_request(
                model=self.llm_model,
                json_mode=json_mode,
                outcome=outcome,
                latency_s=elapsed,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            return data

    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        """Удобная обёртка: вернуть только content ответа."""
        resp = self.chat(messages, temperature=temperature, json_mode=False)
        return resp.get("message", {}).get("content", "")

    # ------------------------------------------------------------ structured

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        temperature: float = 0.1,
        max_repair_attempts: int = 2,
    ) -> T:
        """
        Получить ответ и распарсить в Pydantic-модель.

        На 3B-моделях даже с format=json иногда:
          - вылетают комментарии в JSON,
          - перепутаны типы (строка вместо числа),
          - отсутствуют поля.
        Поэтому делаем до `max_repair_attempts` доп. вызовов с предъявлением
        конкретной ошибки модели. Это сильно дешевле, чем полный retry агента.
        """
        attempt_messages = list(messages)
        last_error: str | None = None

        for attempt in range(max_repair_attempts + 1):
            resp = self.chat(attempt_messages, temperature=temperature, json_mode=True)
            content = resp.get("message", {}).get("content", "").strip()
            try:
                obj = json.loads(content)
                return schema.model_validate(obj)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)
                record_parse_failure(schema=schema.__name__)
                logger.warning(
                    "ollama.structured.parse_failed",
                    attempt=attempt + 1,
                    schema=schema.__name__,
                    error=last_error[:300],
                    raw=content[:300],
                )
                # repair-промпт: явно показываем модели её косяк
                attempt_messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            f"Твой ответ не прошёл валидацию. Ошибка:\n{last_error}\n\n"
                            f"Верни строго валидный JSON по той же схеме. "
                            f"Никаких комментариев, никакого текста до и после JSON."
                        ),
                    },
                ]

        raise StructuredOutputError(
            f"Failed to parse structured output after {max_repair_attempts + 1} attempts. "
            f"Last error: {last_error}"
        )

    # -------------------------------------------------------------- embeddings

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, OllamaError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        """
        Векторизация списка текстов через embed-модель.
        Ollama embeddings endpoint берёт строки по одной — батчим вручную.
        """
        out: list[list[float]] = []
        for text in texts:
            resp = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.embed_model, "prompt": text},
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise OllamaError(
                    f"Embeddings {e.response.status_code}: {e.response.text[:200]}"
                ) from e
            out.append(resp.json()["embedding"])
        return out

    # ------------------------------------------------------------------ misc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
