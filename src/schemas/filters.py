"""
QueryFilters — то, что Scout извлекает из NL-запроса пользователя.

Это маленький, жёсткий JSON-объект — на 3B-модели чем меньше полей, тем стабильнее
parse rate. Остальное (узкие фильтры по характеристикам) — делается уже
постфильтрацией по полученным карточкам.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

KNOWN_CATEGORIES = (
    "electric_toothbrush",
    "hair_dryer",
    "epilator",
    "unknown",
)
CategorySlug = Literal[
    "electric_toothbrush", "hair_dryer", "epilator", "unknown"
]


class QueryFilters(BaseModel):
    """
    Извлечённые из NL-запроса параметры поиска.

    Поля намеренно простые — числа, строки, нет вложенности. На маленькой модели
    любая вложенность → ниже parse rate.
    """

    category: CategorySlug = Field(
        default="unknown",
        description=(
            "Слаг категории. unknown — если Scout не уверен, тогда дальше "
            "fallback на ключевые слова."
        ),
    )
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    keywords: list[str] = Field(
        default_factory=list,
        description="Дополнительные ключевые слова для текстового матча в mock/live режимах",
    )

    @model_validator(mode="after")
    def _sanitize(self) -> "QueryFilters":
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            # тихая нормализация — типичный артефакт LLM на маленьких моделях
            self.min_price, self.max_price = self.max_price, self.min_price
        return self
