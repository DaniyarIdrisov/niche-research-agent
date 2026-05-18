"""
AgentState — central state передаваемый между нодами LangGraph.

Это TypedDict, а не Pydantic — у LangGraph нативное merging работает по ключам
TypedDict (см. add_messages / встроенный reducer). Под-объекты — Pydantic, чтобы
была валидация per-step.

Эволюция state по графу:
  scout → products
  niche_analyst → niche_metrics
  specs_miner → specs_summary
  usp_analyst → usp_analysis
  prd_writer → prd
  reporter → report
"""

from __future__ import annotations

from typing import Any, TypedDict

from src.schemas.filters import QueryFilters
from src.schemas.product import Product


class AgentState(TypedDict, total=False):
    # --- input ---
    query: str
    filters: QueryFilters  # извлекается Scout-ом из NL-запроса
    run_id: str            # uuid одного запуска, для observability

    # --- Scout ---
    products: list[Product]

    # --- Niche Analyst ---
    niche_metrics: dict[str, Any]

    # --- Specs Miner ---
    specs_summary: dict[str, Any]

    # --- USP Analyst ---
    usp_analysis: dict[str, Any]

    # --- PRD Writer ---
    prd: dict[str, Any]

    # --- Reporter ---
    report: str

    # --- control / observability ---
    errors: list[str]              # человекочитаемые ошибки от агентов / валидаторов
    retries: dict[str, int]        # счётчик попыток на ноду {"scout": 1}
    verdict: str                   # "go" | "no-go" | "conditional-go" | "no-data"


def make_initial_state(query: str, run_id: str) -> AgentState:
    """Стартовый state для нового запуска."""
    return AgentState(
        query=query,
        run_id=run_id,
        products=[],
        errors=[],
        retries={},
    )
