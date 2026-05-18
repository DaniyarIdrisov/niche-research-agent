"""
Scout — первый агент. Парсит NL-запрос → QueryFilters → дёргает wb_parser.

Архитектурное решение: LLM-у достаётся ТОЛЬКО парсинг запроса в фильтры.
Сам список товаров мы получаем детерминированно из wb_parser.search_products —
никаких галлюцинаций sku и цен, никаких 100-объектных JSON от 3B-модели.

Это сильно повышает стабильность системы и упрощает evals (parse rate
проверяется на маленьком объекте вместо большого списка).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, StructuredOutputError
from src.schemas.filters import QueryFilters
from src.schemas.state import AgentState
from src.tools.wb_parser import search_products

PROMPT_PATH = PROJECT_ROOT / "prompts" / "scout.md"


def _load_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def scout_node(state: AgentState) -> AgentState:
    """
    LangGraph-нода.

    1. Берёт state["query"].
    2. Просит LLM извлечь QueryFilters (JSON mode + repair-loop).
    3. Зовёт wb_parser.search_products(filters).
    4. Пишет state["filters"] и state["products"].

    На любой ошибке — добавляет запись в state["errors"], валидационная нода
    решит, ретраить или падать.
    """
    query = state["query"]
    logger.info("scout.start", query=query)

    system_prompt = _load_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    errors = list(state.get("errors", []))

    try:
        with OllamaClient() as llm:
            filters = llm.chat_structured(
                messages,
                QueryFilters,
                temperature=0.1,
                max_repair_attempts=2,
            )
    except StructuredOutputError as e:
        # Fallback: считаем запрос целиком ключевыми словами без категории.
        # Дальше валидационная нода увидит filters.category == "unknown" и
        # либо ретрайнет Scout, либо отдаст пользователю мягкую ошибку.
        logger.warning("scout.llm_parse_failed_fallback", error=str(e))
        filters = QueryFilters(category="unknown", keywords=[query])
        errors.append(f"scout: LLM parse failed, fell back to keyword search ({e})")

    logger.info("scout.filters", filters=filters.model_dump())

    products = search_products(filters)
    logger.info("scout.products_found", n=len(products))

    return {
        **state,
        "filters": filters,
        "products": products,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Validation node
# ---------------------------------------------------------------------------


def _is_valid_scout_output(state: AgentState) -> bool:
    """
    Считаем Scout успешным если:
    - есть filters
    - найдено ≥1 товаров ИЛИ это явный edge-case "unknown" категория
      (тогда дальше pipeline корректно сворачивается с no-data verdict).
    """
    filters: QueryFilters | None = state.get("filters")
    if filters is None:
        return False
    products = state.get("products", [])
    if len(products) >= 1:
        return True
    # Пустая ниша — допустимое состояние, не повод ретраить.
    if filters.category == "unknown":
        return True
    return False


def validate_scout_node(state: AgentState) -> AgentState:
    """
    Помечает в state нужен ли retry. Решение о ребре — в conditional_edge ниже.
    """
    valid = _is_valid_scout_output(state)
    retries = dict(state.get("retries", {}))
    n_done = retries.get("scout", 0)
    if not valid:
        retries["scout"] = n_done + 1
        msg = (
            f"scout: validation failed (attempt {n_done + 1}). "
            f"filters={state.get('filters')}, products={len(state.get('products', []))}"
        )
        logger.warning("scout.validation_failed", attempt=n_done + 1)
        errors = [*state.get("errors", []), msg]
    else:
        errors = state.get("errors", [])
        logger.info("scout.validation_ok", products=len(state.get("products", [])))
    return {**state, "retries": retries, "errors": errors}
