"""
Pydantic-схемы для structured output агентов после Scout.

Принцип: для каждого агента — отдельная схема, но плоская и с малым числом полей.
На 3B-моделях глубокая вложенность роняет parse rate. Где можно — числа и
короткие строки.

Verdict хранится отдельно, в финальной ноде Reporter.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["go", "conditional-go", "no-go", "no-data"]


# ---------------------------------------------------------------------------
# Niche Analyst
# ---------------------------------------------------------------------------


class NicheInsight(BaseModel):
    """Одна осмысленная находка из метрик, сформулированная LLM-ом."""

    category: Literal["volume", "concentration", "price", "demand", "risk"]
    statement: str = Field(description="Человекочитаемое утверждение в 1 предложении")


class NicheMetrics(BaseModel):
    """
    Полный результат Niche Analyst.

    Цифры (revenue, concentration, price, reviews) посчитаны детерминированно
    через src.tools.calculator. LLM добавляет только interpretations.
    """

    n_products: int
    revenue_top_30: int = Field(description="Прокси месячного оборота топ-30, ₽")
    revenue_top_5: int
    top_share: float = Field(ge=0, le=1, description="Доля топ-5 продавцов в обороте")
    top_sellers: list[dict] = Field(default_factory=list, description="[{seller, revenue, share}]")
    price_median: int
    price_p25: int
    price_p75: int
    price_spread: int
    total_reviews: int
    share_mature: float = Field(ge=0, le=1, description="Доля карточек с ≥100 отзывов")
    share_new: float = Field(ge=0, le=1, description="Доля карточек с <10 отзывов")
    insights: list[NicheInsight] = Field(default_factory=list, max_length=6)


# ---------------------------------------------------------------------------
# Specs Miner
# ---------------------------------------------------------------------------


class SpecFrequency(BaseModel):
    """Сколько раз эта характеристика встречается в топ-N + типичные значения."""

    canonical_key: str = Field(description="Канонический ключ из specs_taxonomy")
    display_name: str = Field(description="Человекочитаемое название")
    frequency: int = Field(ge=0, description="У скольких карточек из топа есть это поле")
    top_n: int = Field(description="Размер выборки топа")
    typical_values: list[str] = Field(default_factory=list, description="Топ-3 самых частых значения")


class SpecsSummary(BaseModel):
    """Полный результат Specs Miner."""

    top_n: int
    must_have: list[SpecFrequency] = Field(
        default_factory=list, description="Frequency ≥ 0.8 от top_n"
    )
    nice_to_have: list[SpecFrequency] = Field(
        default_factory=list, description="0.4 ≤ Frequency < 0.8"
    )
    rare: list[SpecFrequency] = Field(default_factory=list, description="Frequency < 0.4")


# ---------------------------------------------------------------------------
# USP Analyst
# ---------------------------------------------------------------------------


UspType = Literal["technological", "value", "emotional", "social"]


class UspItem(BaseModel):
    seller: str | None = None
    phrase: str
    usp_type: UspType


class UspMatrix(BaseModel):
    """Кто из топ-5 на что давит + пустоты."""

    items: list[UspItem] = Field(default_factory=list)
    type_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="{usp_type: count} — сколько УТП каждого типа во всём топе",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Типы УТП, представленные слабо — возможности для отстройки",
    )


# ---------------------------------------------------------------------------
# PRD Writer
# ---------------------------------------------------------------------------


class PRD(BaseModel):
    """
    Product Requirements Document.

    Жёсткая структура — на ней потом валидируется prd_validator (skill).
    Все обязательные поля строго required, опциональные — Optional.
    """

    title: str = Field(description="Краткое название продукта")
    goal: str = Field(description="Цель продукта в 1-2 предложениях")
    target_audience: str = Field(description="ЦА с гипотезой о JTBD")
    must_have_specs: list[str] = Field(
        description="Характеристики, которые ОБЯЗАТЕЛЬНО должны быть (frequency ≥ 0.8)"
    )
    nice_to_have_specs: list[str] = Field(
        description="Желательные характеристики (0.4 ≤ frequency < 0.8)"
    )
    differentiation: list[str] = Field(
        description="Точки отстройки от конкурентов — на каких УТП-пустотах сыграть"
    )
    target_price: dict[str, int] = Field(
        description="{'min': N, 'max': M} в рублях, ориентир по медианной цене ниши"
    )
    packaging_requirements: list[str] = Field(default_factory=list)
    compliance: list[str] = Field(
        description="Обязательные документы для МБТ на WB: декларация ТР ТС, EAC и т.п."
    )
    risks: list[str] = Field(default_factory=list, description="Риски выхода в нишу")
