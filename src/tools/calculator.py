"""
Calculator tool для Niche Analyst.

Зачем: 3B-модели плохо считают арифметику (особенно на длинных числах).
Поэтому Niche Analyst вызывает функции этого модуля детерминированно, а LLM-у
оставляем только содержательную интерпретацию полученных цифр.

В Phase 4 этот же модуль превратится в LangChain Tool / structured tool для
формального tool-calling, сейчас он импортируется как обычные функции.
"""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from src.schemas.product import Product


def revenue_top_n(products: list[Product], top_n: int = 30) -> int:
    """
    Оценка месячного оборота топ-N товаров в рублях.
    Прокси: sales_estimate_per_month × price.
    """
    top = sorted(products, key=lambda p: p.total_sales_proxy(), reverse=True)[:top_n]
    return sum(p.total_sales_proxy() * p.price for p in top)


def seller_concentration(products: list[Product], top_n: int = 5) -> dict[str, Any]:
    """
    Доля топ-N продавцов в обороте. Hi-конц. (>50%) — нишу держат единицы,
    зайти сложно.
    """
    if not products:
        return {"top_n": top_n, "top_share": 0.0, "top_sellers": []}
    by_seller: Counter[str] = Counter()
    for p in products:
        if not p.seller:
            continue
        by_seller[p.seller] += p.total_sales_proxy() * p.price
    total = sum(by_seller.values())
    if total == 0:
        return {"top_n": top_n, "top_share": 0.0, "top_sellers": []}
    top_sellers = by_seller.most_common(top_n)
    top_revenue = sum(v for _, v in top_sellers)
    return {
        "top_n": top_n,
        "top_share": round(top_revenue / total, 3),
        "top_sellers": [
            {"seller": s, "revenue": v, "share": round(v / total, 3)}
            for s, v in top_sellers
        ],
    }


def price_stats(products: list[Product]) -> dict[str, Any]:
    """Медиана, мин/макс, спред цены."""
    if not products:
        return {"median": 0, "min": 0, "max": 0, "spread": 0}
    prices = [p.price for p in products]
    med = int(median(prices))
    return {
        "median": med,
        "min": min(prices),
        "max": max(prices),
        "spread": max(prices) - min(prices),
        "p25": _quantile(prices, 0.25),
        "p75": _quantile(prices, 0.75),
    }


def _quantile(values: list[int], q: float) -> int:
    if not values:
        return 0
    sorted_vals = sorted(values)
    idx = int(q * (len(sorted_vals) - 1))
    return sorted_vals[idx]


def review_dynamics(products: list[Product]) -> dict[str, Any]:
    """
    Прокси на скорость спроса по объёму отзывов.

    Возвращаем суммарные отзывы, медиану на карточку, долю карточек с >100 отзывов
    (зрелые) и <10 (новые) — это говорит о темпе ротации в нише.
    """
    if not products:
        return {"total_reviews": 0, "median_reviews": 0, "share_mature": 0, "share_new": 0}
    reviews = [p.reviews_count for p in products]
    mature = sum(1 for r in reviews if r >= 100)
    new = sum(1 for r in reviews if r < 10)
    return {
        "total_reviews": sum(reviews),
        "median_reviews": int(median(reviews)),
        "share_mature": round(mature / len(reviews), 3),
        "share_new": round(new / len(reviews), 3),
    }


def summarize(products: list[Product]) -> dict[str, Any]:
    """
    Один вызов на всё — удобно, когда Niche Analyst ничего больше не нужно.
    Возвращает плоский dict, который агент уже передаёт LLM-у для нарратива.
    """
    return {
        "n_products": len(products),
        "revenue_top_30": revenue_top_n(products, 30),
        "revenue_top_5": revenue_top_n(products, 5),
        "concentration_top_5": seller_concentration(products, 5),
        "price": price_stats(products),
        "reviews": review_dynamics(products),
    }
