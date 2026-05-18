"""
Working memory — helpers поверх state.

State сам по себе TypedDict (см. src/schemas/state.py). Эти функции — чтобы
агенты не дублировали логику инкремента retries / accumulation errors.
"""

from __future__ import annotations

from typing import Any

from src.schemas.state import AgentState


def add_error(state: AgentState, message: str) -> list[str]:
    """Возвращает новый список ошибок (не мутирует state in-place)."""
    return [*state.get("errors", []), message]


def increment_retry(state: AgentState, node: str) -> dict[str, int]:
    """Возвращает новый dict retries с увеличенным счётчиком для ноды."""
    retries = dict(state.get("retries", {}))
    retries[node] = retries.get(node, 0) + 1
    return retries


def get_retry_count(state: AgentState, node: str) -> int:
    return state.get("retries", {}).get(node, 0)


def short_dump(state: AgentState) -> dict[str, Any]:
    """
    Короткий дамп state для логов — без полных списков продуктов и текстов.
    """
    return {
        "run_id": state.get("run_id"),
        "query": state.get("query"),
        "n_products": len(state.get("products", [])),
        "has_niche": "niche_metrics" in state,
        "has_specs": "specs_summary" in state,
        "has_usp": "usp_analysis" in state,
        "has_prd": "prd" in state,
        "has_report": "report" in state,
        "verdict": state.get("verdict"),
        "errors": state.get("errors", []),
        "retries": state.get("retries", {}),
    }
