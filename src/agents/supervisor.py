"""
LangGraph Supervisor — полный граф.

Поток:

    START
      ↓
    scout ──→ validate_scout ──(retry, ≤MAX_RETRIES)──┐
                  ↓                                    │
            (ok)  ↓                                    │
                  ├─→ niche_analyst ──┐                │
                  ├─→ specs_miner    ─┤                │
                  └─→ usp_analyst    ─┤                │
                                      ↓                │
                                  prd_writer           │
                                      ↓                │
                              validate_prd ──(retry)───┘
                                      ↓ (ok)
                                  reporter
                                      ↓
                                    END

Параллельный fan-out (niche/specs/usp) — стандартный LangGraph-паттерн: три ребра
от одной ноды на три разные → они исполняются параллельно (по умолчанию thread-
pool). Слияние происходит на prd_writer: LangGraph дожидается всех трёх
предшественников, прежде чем запустить его.

Каждая retry-петля ограничена settings.max_retries_per_node, чтобы не было
бесконечного цикла на стабильно невалидном выходе.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph
from loguru import logger

from functools import wraps
from typing import Callable

from src.agents.niche_analyst import niche_analyst_node, validate_niche_node
from src.agents.prd_writer import prd_writer_node, validate_prd_node
from src.agents.reporter import reporter_node
from src.agents.scout import scout_node, validate_scout_node
from src.agents.specs_miner import specs_miner_node
from src.agents.usp_analyst import usp_analyst_node
from src.config import get_settings
from src.observability.metrics import measure_node, record_retry
from src.observability.tracing import trace_span
from src.schemas.state import AgentState


def _instrument(name: str, fn: Callable) -> Callable:
    """
    Декоратор для нод графа: OTel span + Prometheus duration + retry counter.

    Применяется централизованно в build_graph() — агенты не знают про observability.
    """

    @wraps(fn)
    def wrapper(state: AgentState) -> AgentState:
        prev_retries = state.get("retries", {}).get(name, 0)
        with trace_span(f"node.{name}", **{"node.name": name, "run_id": state.get("run_id", "")}):
            with measure_node(name) as m:
                result = fn(state)
                # Если внутри ноды счётчик retries увеличился — это эквивалент retry
                new_retries = result.get("retries", {}).get(name, prev_retries)
                if new_retries > prev_retries:
                    record_retry(name)
                    m["outcome"] = "retry"
                return result

    return wrapper

# ---------------------------------------------------------------------------
# Routers (conditional edges)
# ---------------------------------------------------------------------------


def _scout_router(state: AgentState):
    """
    Решает, что делать после validate_scout. Возвращает ИМЕНА нод напрямую —
    LangGraph принимает либо строку (target), либо список строк (fan-out).

    Варианты:
      - "scout"                        → ретрай (≤MAX_RETRIES)
      - "reporter"                     → no_data / give_up — сразу финал
      - ["niche_analyst", "specs_miner", "usp_analyst"]  → параллельный fan-out
    """
    s = get_settings()
    retries = state.get("retries", {}).get("scout", 0)
    filters = state.get("filters")
    products = state.get("products", [])

    if filters is None:
        return "reporter"

    if products:
        return ["niche_analyst", "specs_miner", "usp_analyst"]

    # products пусто
    if filters.category == "unknown":
        logger.info("supervisor.scout_router", decision="reporter", reason="unknown_category")
        return "reporter"

    if retries < s.max_retries_per_node:
        logger.info("supervisor.scout_router", decision="scout", attempt=retries)
        return "scout"

    logger.warning("supervisor.scout_router", decision="reporter", reason="give_up")
    return "reporter"


def _prd_router(state: AgentState) -> Literal["retry", "ok"]:
    """После validate_prd: ретрайнуть PRD-Writer или идти в Reporter."""
    s = get_settings()
    retries = state.get("retries", {}).get("prd_writer", 0)
    prd = state.get("prd")
    if prd is None and state.get("products"):
        if retries < s.max_retries_per_node:
            logger.info("supervisor.prd_router", decision="retry", attempt=retries)
            return "retry"
    return "ok"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_graph():
    """Возвращает скомпилированный LangGraph-граф."""
    graph = StateGraph(AgentState)

    # Ноды (все обёрнуты в observability через _instrument)
    graph.add_node("scout", _instrument("scout", scout_node))
    graph.add_node("validate_scout", _instrument("validate_scout", validate_scout_node))
    graph.add_node("niche_analyst", _instrument("niche_analyst", niche_analyst_node))
    graph.add_node("validate_niche", _instrument("validate_niche", validate_niche_node))
    graph.add_node("specs_miner", _instrument("specs_miner", specs_miner_node))
    graph.add_node("usp_analyst", _instrument("usp_analyst", usp_analyst_node))
    graph.add_node("prd_writer", _instrument("prd_writer", prd_writer_node))
    graph.add_node("validate_prd", _instrument("validate_prd", validate_prd_node))
    graph.add_node("reporter", _instrument("reporter", reporter_node))

    # Точка входа
    graph.add_edge(START, "scout")
    graph.add_edge("scout", "validate_scout")

    # Router возвращает напрямую имена нод (строка или список), LangGraph
    # принимает их как targets. Параметр-список — это «возможные targets»,
    # нужен LangGraph чтобы построить статический граф для валидации.
    graph.add_conditional_edges(
        "validate_scout",
        _scout_router,
        ["scout", "reporter", "niche_analyst", "specs_miner", "usp_analyst"],
    )

    # niche → валидация → join в prd_writer
    graph.add_edge("niche_analyst", "validate_niche")
    graph.add_edge("validate_niche", "prd_writer")
    graph.add_edge("specs_miner", "prd_writer")
    graph.add_edge("usp_analyst", "prd_writer")

    # PRD → валидация → retry/ok
    graph.add_edge("prd_writer", "validate_prd")
    graph.add_conditional_edges(
        "validate_prd",
        _prd_router,
        {"retry": "prd_writer", "ok": "reporter"},
    )

    graph.add_edge("reporter", END)

    return graph.compile()
