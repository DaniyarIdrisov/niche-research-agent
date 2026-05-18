"""
Specs Miner.

Pipeline:
  1. Берём топ-N карточек (по умолчанию top 10) из state["products"]
  2. specs_normalizer.frequency_table — детерминированно считаем частоты
  3. LLM раскладывает по корзинам must/nice/rare (LLM именно решает, потому что
     там есть нюансы: для эпилятора и фена «насадок в комплекте» имеют разный вес).
     Альтернатива — порог по числам без LLM. Делаем гибрид: LLM получает уже
     отсортированные frequencies, но выбор финальной корзины — за ним.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, StructuredOutputError
from src.schemas.analysis import SpecFrequency, SpecsSummary
from src.schemas.state import AgentState
from src.tools.specs_normalizer import frequency_table

PROMPT_PATH = PROJECT_ROOT / "prompts" / "specs_miner.md"

TOP_N_FOR_SPECS = 10


def _load_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def _fallback_split(rows: list[SpecFrequency]) -> SpecsSummary:
    """Детерминированный fallback на пороги — если LLM упал."""
    must, nice, rare = [], [], []
    for r in rows:
        share = (r.frequency / r.top_n) if r.top_n else 0
        if share >= 0.8:
            must.append(r)
        elif share >= 0.4:
            nice.append(r)
        else:
            rare.append(r)
    return SpecsSummary(top_n=rows[0].top_n if rows else 0, must_have=must, nice_to_have=nice, rare=rare)


def specs_miner_node(state: AgentState) -> AgentState:
    products = state.get("products", [])
    top = products[:TOP_N_FOR_SPECS]
    logger.info("specs_miner.start", top_n=len(top))

    if not top:
        return {"specs_summary": None}

    raw_specs = [p.specs if hasattr(p, "specs") else p["specs"] for p in top]
    freq_dict = frequency_table(raw_specs, top_n=len(top))

    rows = [
        SpecFrequency(
            canonical_key=k,
            display_name=v["display_name"],
            frequency=v["frequency"],
            top_n=v["top_n"],
            typical_values=v["typical_values"],
        )
        for k, v in freq_dict.items()
    ]

    if not rows:
        # Все specs были не из нашей taxonomy — нечего раскладывать
        logger.warning("specs_miner.empty_frequencies")
        return {"specs_summary": SpecsSummary(top_n=len(top)).model_dump()}

    llm_input = {
        "top_n": len(top),
        "frequencies": [r.model_dump() for r in rows],
    }

    new_errors: list[str] = []
    try:
        with OllamaClient() as llm:
            summary = llm.chat_structured(
                [
                    {"role": "system", "content": _load_prompt()},
                    {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
                ],
                SpecsSummary,
                temperature=0.1,
                max_repair_attempts=2,
            )
    except StructuredOutputError as e:
        logger.warning("specs_miner.llm_failed_fallback", error=str(e))
        new_errors.append(f"specs_miner: LLM failed, used threshold fallback ({e})")
        summary = _fallback_split(rows)

    logger.info(
        "specs_miner.done",
        must=len(summary.must_have),
        nice=len(summary.nice_to_have),
        rare=len(summary.rare),
    )
    # Только свой ключ — иначе конфликт записи с niche_analyst/usp_analyst при fan-out.
    out: dict[str, Any] = {"specs_summary": summary.model_dump()}
    if new_errors:
        out["errors"] = new_errors  # reducer (add) сконкатенирует
    return out
