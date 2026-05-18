"""
PRD Writer.

Pipeline:
  1. Собираем компактный JSON со всем нужным: niche metrics, specs summary, USP.
  2. LLM возвращает PRD в strict Pydantic schema.
  3. Через src.tools.prd_validator (Phase 4 skill) можно дополнительно
     проверить полноту секций — в Phase 5 будет навешано на валидационную ноду.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, StructuredOutputError
from src.memory.working import increment_retry
from src.schemas.analysis import PRD
from src.schemas.state import AgentState
from src.tools.prd_validator import validate_prd

PROMPT_PATH = PROJECT_ROOT / "prompts" / "prd_writer.md"


def _load_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def _summarize_niche(metrics: dict | None) -> dict:
    if not metrics:
        return {}
    return {
        "n_products": metrics["n_products"],
        "revenue_top_30": metrics["revenue_top_30"],
        "top_share": metrics["top_share"],
        "price_median": metrics["price_median"],
        "price_p25": metrics["price_p25"],
        "price_p75": metrics["price_p75"],
        "insights": metrics.get("insights", []),
    }


def _summarize_specs(summary: dict | None) -> dict:
    if not summary:
        return {"must_have": [], "nice_to_have": [], "rare": []}
    keep = lambda lst: [  # noqa: E731
        {"display_name": x["display_name"], "typical_values": x["typical_values"]}
        for x in lst
    ]
    return {
        "must_have": keep(summary.get("must_have", [])),
        "nice_to_have": keep(summary.get("nice_to_have", [])),
        "rare": keep(summary.get("rare", [])),
    }


def _summarize_usp(usp: dict | None) -> dict:
    if not usp:
        return {"type_distribution": {}, "gaps": []}
    return {
        "type_distribution": usp.get("type_distribution", {}),
        "gaps": usp.get("gaps", []),
    }


def prd_writer_node(state: AgentState) -> AgentState:
    if not state.get("products"):
        # Нет данных — пропускаем PRD
        return {"prd": None}

    filters = state.get("filters")
    category = filters.category if filters is not None else "unknown"

    llm_input = {
        "category": category,
        "niche": _summarize_niche(state.get("niche_metrics")),
        "specs": _summarize_specs(state.get("specs_summary")),
        "usp": _summarize_usp(state.get("usp_analysis")),
    }
    logger.info("prd_writer.start", category=category)

    try:
        with OllamaClient() as llm:
            prd = llm.chat_structured(
                [
                    {"role": "system", "content": _load_prompt()},
                    {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
                ],
                PRD,
                temperature=0.2,
                max_repair_attempts=2,
            )
    except StructuredOutputError as e:
        logger.warning("prd_writer.failed", error=str(e))
        return {"prd": None, "errors": [f"prd_writer: LLM failed ({e})"]}

    logger.info(
        "prd_writer.done",
        must=len(prd.must_have_specs),
        nice=len(prd.nice_to_have_specs),
        diff=len(prd.differentiation),
    )
    return {"prd": prd.model_dump()}


def validate_prd_node(state: AgentState) -> AgentState:
    """
    Проверяем, что в PRD заполнены все обязательные секции.
    Если нет — инкрементируем retry; сам retry-edge живёт в supervisor.
    """
    prd = state.get("prd")
    if prd is None and state.get("products"):
        retries = increment_retry(state, "prd_writer")
        msg = f"prd_writer: PRD missing (attempt {retries['prd_writer']})"
        logger.warning("prd_writer.validation_failed")
        return {"retries": retries, "errors": [msg]}

    if prd:
        report = validate_prd(prd)
        if not report["valid"]:
            retries = increment_retry(state, "prd_writer")
            msg = (
                f"prd_writer: missing sections {report['missing_sections']} "
                f"(attempt {retries['prd_writer']})"
            )
            logger.warning("prd_writer.validation_failed", missing=report["missing_sections"])
            return {"retries": retries, "errors": [msg]}
        for w in report["warnings"]:
            logger.info("prd_writer.validation_warning", message=w)
    return {}
