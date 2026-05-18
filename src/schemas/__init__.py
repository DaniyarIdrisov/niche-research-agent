"""Pydantic / TypedDict schemas, общие для всех агентов."""

from src.schemas.analysis import (
    PRD,
    NicheInsight,
    NicheMetrics,
    SpecFrequency,
    SpecsSummary,
    UspItem,
    UspMatrix,
    UspType,
    Verdict,
)
from src.schemas.filters import KNOWN_CATEGORIES, QueryFilters
from src.schemas.product import Product, ProductList
from src.schemas.state import AgentState, make_initial_state

__all__ = [
    "PRD",
    "AgentState",
    "KNOWN_CATEGORIES",
    "NicheInsight",
    "NicheMetrics",
    "Product",
    "ProductList",
    "QueryFilters",
    "SpecFrequency",
    "SpecsSummary",
    "UspItem",
    "UspMatrix",
    "UspType",
    "Verdict",
    "make_initial_state",
]
