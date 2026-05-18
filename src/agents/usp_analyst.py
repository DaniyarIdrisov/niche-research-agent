"""
USP Analyst.

Pipeline:
  1. Достаём топ-5 карточек по обороту (state["products"][:5])
  2. Для каждой через usp_classifier.baseline_classify извлекаем фразы +
     первичный тип (rules)
  3. LLM валидирует/исправляет типы, считает распределение, находит gaps
  4. Сохраняем UspMatrix в state["usp_analysis"]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, StructuredOutputError
from src.schemas.analysis import UspItem, UspMatrix
from src.schemas.state import AgentState
from src.tools.usp_classifier import (
    baseline_classify,
    find_gaps,
    type_distribution,
)

PROMPT_PATH = PROJECT_ROOT / "prompts" / "usp_analyst.md"
TOP_N_FOR_USP = 5


def _load_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def _extract_baseline(products: list) -> list[UspItem]:
    items: list[UspItem] = []
    for p in products:
        seller = p.seller if hasattr(p, "seller") else p.get("seller")
        text_parts = []
        name = p.name if hasattr(p, "name") else p.get("name", "")
        desc = p.description if hasattr(p, "description") else p.get("description", "")
        text_parts.append(name or "")
        text_parts.append(desc or "")
        full = ". ".join(text_parts)
        items.extend(baseline_classify(full, seller=seller))
    return items


def usp_analyst_node(state: AgentState) -> AgentState:
    products = state.get("products", [])
    top = products[:TOP_N_FOR_USP]
    logger.info("usp_analyst.start", top_n=len(top))

    if not top:
        return {"usp_analysis": None}

    baseline = _extract_baseline(top)
    if not baseline:
        empty = UspMatrix()
        return {"usp_analysis": empty.model_dump()}

    llm_input = {"items": [it.model_dump() for it in baseline]}
    new_errors: list[str] = []

    try:
        with OllamaClient() as llm:
            matrix = llm.chat_structured(
                [
                    {"role": "system", "content": _load_prompt()},
                    {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
                ],
                UspMatrix,
                temperature=0.2,
                max_repair_attempts=2,
            )
    except StructuredOutputError as e:
        # Fallback: оставляем baseline-классификацию + считаем распределение и gaps сами
        logger.warning("usp_analyst.llm_failed_fallback", error=str(e))
        new_errors.append(f"usp_analyst: LLM failed, kept rule-based ({e})")
        dist = type_distribution(baseline)
        matrix = UspMatrix(
            items=baseline,
            type_distribution=dist,
            gaps=find_gaps(dist),
        )
    else:
        # Если LLM не положил distribution/gaps — пересчитаем
        if not matrix.type_distribution:
            matrix.type_distribution = type_distribution(matrix.items)
        if not matrix.gaps:
            matrix.gaps = find_gaps(matrix.type_distribution)

    logger.info(
        "usp_analyst.done",
        n_items=len(matrix.items),
        gaps=matrix.gaps,
    )
    # Только свой ключ — иначе конфликт записи с niche_analyst/specs_miner.
    out: dict[str, Any] = {"usp_analysis": matrix.model_dump()}
    if new_errors:
        out["errors"] = new_errors  # reducer (add) сконкатенирует
    return out
