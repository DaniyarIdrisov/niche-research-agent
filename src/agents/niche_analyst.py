"""
Niche Analyst.

Pipeline:
  1. calculator.summarize(products)  — детерминированные метрики
  2. LLM в JSON mode → insights (3-6 коротких утверждений)
  3. Собираем NicheMetrics, кладём в state["niche_metrics"]

LLM-у мы не доверяем счёт. Он только интерпретирует то, что ему положили на блюдце.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, StructuredOutputError
from src.memory.working import increment_retry
from src.schemas.analysis import NicheInsight, NicheMetrics
from src.schemas.state import AgentState
from src.tools.calculator import summarize

PROMPT_PATH = PROJECT_ROOT / "prompts" / "niche_analyst.md"


# Узкая схема для LLM-вызова — только insights. Остальные поля заполняем сами.
class _LLMInsights(BaseModel):
    insights: list[NicheInsight] = Field(default_factory=list, max_length=6)


def _load_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def niche_analyst_node(state: AgentState) -> AgentState:
    products = state.get("products", [])
    logger.info("niche_analyst.start", n_products=len(products))

    if not products:
        # Нет товаров — никаких метрик, дальше Reporter скажет no-data
        return {**state, "niche_metrics": None}

    raw = summarize(products)
    concentration = raw["concentration_top_5"]
    price = raw["price"]
    reviews = raw["reviews"]

    # Готовим компактный JSON для LLM — все цифры в одном объекте
    llm_input = {
        "n_products": raw["n_products"],
        "revenue_top_30": raw["revenue_top_30"],
        "revenue_top_5": raw["revenue_top_5"],
        "top_share": concentration["top_share"],
        "price_median": price["median"],
        "price_p25": price["p25"],
        "price_p75": price["p75"],
        "price_spread": price["spread"],
        "total_reviews": reviews["total_reviews"],
        "share_mature": reviews["share_mature"],
        "share_new": reviews["share_new"],
    }

    insights: list[NicheInsight] = []
    new_errors: list[str] = []

    try:
        with OllamaClient() as llm:
            resp = llm.chat_structured(
                [
                    {"role": "system", "content": _load_prompt()},
                    {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
                ],
                _LLMInsights,
                temperature=0.2,
                max_repair_attempts=2,
            )
        insights = list(resp.insights)
    except StructuredOutputError as e:
        logger.warning("niche_analyst.llm_failed", error=str(e))
        new_errors.append(f"niche_analyst: LLM insights failed ({e})")

    metrics = NicheMetrics(
        n_products=llm_input["n_products"],
        revenue_top_30=llm_input["revenue_top_30"],
        revenue_top_5=llm_input["revenue_top_5"],
        top_share=concentration["top_share"],
        top_sellers=concentration["top_sellers"],
        price_median=price["median"],
        price_p25=price["p25"],
        price_p75=price["p75"],
        price_spread=price["spread"],
        total_reviews=reviews["total_reviews"],
        share_mature=reviews["share_mature"],
        share_new=reviews["share_new"],
        insights=insights,
    )

    logger.info(
        "niche_analyst.done",
        top_share=metrics.top_share,
        median=metrics.price_median,
        insights=len(insights),
    )
    # Возвращаем ТОЛЬКО свои ключи — иначе при параллельном fan-out с
    # specs_miner/usp_analyst LangGraph падает на конфликте записи `query`.
    out: dict[str, Any] = {"niche_metrics": metrics.model_dump()}
    if new_errors:
        out["errors"] = new_errors  # reducer (add) сконкатенирует с существующими
    return out


def validate_niche_node(state: AgentState) -> AgentState:
    """Лёгкая валидация. Считаем OK, если есть числа — insights опциональны."""
    m = state.get("niche_metrics")
    if m is None and state.get("products"):
        # Метрик нет, но товары были — это поломка
        retries = increment_retry(state, "niche_analyst")
        msg = f"niche_analyst: metrics missing (attempt {retries['niche_analyst']})"
        logger.warning("niche_analyst.validation_failed")
        return {"retries": retries, "errors": [msg]}
    return {}
