"""
Product schema — единое представление карточки товара после Scout.

Используется и mock-парсером, и live-парсером (мапит ответ search.wb.ru сюда).
Дальше по pipeline другие агенты работают только с этим типом.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class Product(BaseModel):
    """
    Карточка товара после Scout-нормализации.

    specs хранится как dict[str, Any], потому что:
    - ключи в живых карточках WB не унифицированы («автономность» vs «время работы»)
    - значения бывают числами, строками и булями
    - нормализация — задача Specs Miner / Specs Normalizer, не Scout
    """

    sku: int = Field(description="WB-артикул товара")
    category: str = Field(description="Внутренний слаг категории, например electric_toothbrush")
    name: str
    brand: str | None = None
    model: str | None = None
    price: int = Field(description="Цена в рублях")
    currency: str = "RUB"
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews_count: int = Field(default=0, ge=0)
    sales_estimate_per_month: int | None = Field(
        default=None,
        description="Прокси-оценка месячных продаж. В mock считается из отзывов, в live — None",
    )
    seller: str | None = None
    url: str | None = None  # не HttpUrl — search.wb.ru даёт относительные ссылки
    description: str | None = None
    specs: dict[str, Any] = Field(default_factory=dict)
    color: str | None = None

    def total_sales_proxy(self) -> int:
        """
        Если sales_estimate_per_month отсутствует, fallback — reviews_count.
        Используется в Niche Analyst для ранжирования и расчёта оборота.
        """
        return self.sales_estimate_per_month or self.reviews_count


class ProductList(BaseModel):
    """Контейнер для удобной (де)сериализации списка товаров."""

    products: list[Product]

    def __len__(self) -> int:
        return len(self.products)
