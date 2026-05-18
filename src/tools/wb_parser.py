"""
WB-парсер. Два режима: mock (читает локальный JSON) и live (тянет search.wb.ru).

Контракт: возвращает list[Product] — единое представление для всех агентов.

Live-режим — серый. search.wb.ru это публичный поисковый бэкенд WB, JSON без
авторизации. Они могут поменять формат — тогда mock остаётся защитной сеткой.

Категории мапим на ru-запрос для поиска:
    electric_toothbrush → "электрическая зубная щётка"
    hair_dryer          → "фен для волос"
    epilator            → "эпилятор"
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.schemas.filters import QueryFilters
from src.schemas.product import Product

# Маппинг наших слагов в реальные русские строки поиска WB.
CATEGORY_QUERY_TERMS: dict[str, str] = {
    "electric_toothbrush": "электрическая зубная щётка",
    "hair_dryer": "фен для волос",
    "epilator": "эпилятор",
}


# ---------------------------------------------------------------------------
# Mock parser
# ---------------------------------------------------------------------------


def _load_mock(path: Path) -> list[Product]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Product(**p) for p in raw["products"]]


def _filter_products(products: Iterable[Product], filters: QueryFilters) -> list[Product]:
    """
    Применяем фильтры к локальному датасету. Логика та же, что нужна и для
    постфильтрации live-результатов.
    """
    out: list[Product] = []
    keywords_lc = [k.lower() for k in filters.keywords if k.strip()]
    for p in products:
        if filters.category != "unknown" and p.category != filters.category:
            continue
        if filters.min_price is not None and p.price < filters.min_price:
            continue
        if filters.max_price is not None and p.price > filters.max_price:
            continue
        if keywords_lc:
            haystack = f"{p.name} {p.description or ''}".lower()
            # «любое слово» — щадящий матч, чтобы не отсекать всё на длинных запросах
            if not any(kw in haystack for kw in keywords_lc):
                continue
        out.append(p)
    return out


def _rank(products: list[Product]) -> list[Product]:
    """
    Сортировка для топа: по прокси-продажам ↓, тай-брейк — отзывы ↓, потом рейтинг ↓.
    """
    return sorted(
        products,
        key=lambda p: (p.total_sales_proxy(), p.reviews_count, p.rating or 0),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Live parser
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Минимальный per-process rate limiter. Token-bucket для запросов в секунду."""

    def __init__(self, per_sec: float) -> None:
        self.min_interval = 1.0 / per_sec if per_sec > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval == 0:
            return
        delta = time.perf_counter() - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.perf_counter()


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _wb_search_request(query: str, limit: int = 100) -> dict:
    s = get_settings()
    params = {
        "query": query,
        "resultset": "catalog",
        "limit": str(limit),
        "sort": "popular",
        "appType": "1",
        "curr": "rub",
        "dest": "-1257786",  # дефолтный регион (Москва) — для оценки ниши достаточно
        "spp": "30",
    }
    headers = {
        "User-Agent": s.wb_user_agent,
        "Accept": "application/json",
    }
    resp = httpx.get(s.wb_search_url, params=params, headers=headers, timeout=s.wb_request_timeout)
    resp.raise_for_status()
    return resp.json()


def _map_wb_product(raw: dict, category: str) -> Product | None:
    """
    Мапим сырой объект из search.wb.ru в наш Product.
    Поля в WB API:
      id          — sku
      name        — название
      brand       — бренд
      salePriceU  — цена в копейках*100
      rating      — рейтинг
      feedbacks   — число отзывов
      supplier    — продавец
    """
    try:
        sku = int(raw["id"])
        name = raw.get("name", "")
        price_u = raw.get("salePriceU") or raw.get("priceU") or 0
        price = int(price_u) // 100
        if price <= 0:
            return None
        return Product(
            sku=sku,
            category=category,
            name=name,
            brand=raw.get("brand"),
            price=price,
            rating=raw.get("rating") or raw.get("reviewRating"),
            reviews_count=raw.get("feedbacks") or 0,
            sales_estimate_per_month=None,  # live API эту цифру не отдаёт
            seller=raw.get("supplier") or raw.get("seller"),
            url=f"https://www.wildberries.ru/catalog/{sku}/detail.aspx",
            description=None,  # потребовало бы отдельный запрос к карточке
            specs={},
            color=raw.get("colors", [{}])[0].get("name") if raw.get("colors") else None,
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("wb_parser.live.map_failed", error=str(e), raw_id=raw.get("id"))
        return None


_RATE: _RateLimiter | None = None


def _get_rate_limiter() -> _RateLimiter:
    global _RATE
    if _RATE is None:
        _RATE = _RateLimiter(get_settings().wb_request_rate_per_sec)
    return _RATE


def _fetch_live(filters: QueryFilters, limit: int) -> list[Product]:
    if filters.category == "unknown":
        # без явной категории — используем keywords как поисковую строку
        query_term = " ".join(filters.keywords) or "малая бытовая техника"
        category_slug = "unknown"
    else:
        query_term = CATEGORY_QUERY_TERMS[filters.category]
        category_slug = filters.category
        if filters.keywords:
            query_term = f"{query_term} {' '.join(filters.keywords)}"

    _get_rate_limiter().wait()
    raw = _wb_search_request(query_term, limit=limit)
    products_raw = raw.get("data", {}).get("products", [])
    mapped = [_map_wb_product(r, category_slug) for r in products_raw]
    products = [p for p in mapped if p is not None]
    return _filter_products(products, filters)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def search_products(filters: QueryFilters, top_n: int | None = None) -> list[Product]:
    """
    Главная функция Scout-а. Возвращает топ-N карточек по фильтрам.

    Режим выбирается по settings.data_source.
    """
    s = get_settings()
    top_n = top_n or s.default_top_n

    if s.data_source == "mock":
        all_products = _load_mock(s.mock_data_path)
        filtered = _filter_products(all_products, filters)
        logger.info(
            "wb_parser.mock.search",
            total=len(all_products),
            after_filter=len(filtered),
            filters=filters.model_dump(),
        )
    elif s.data_source == "live":
        filtered = _fetch_live(filters, limit=100)
        logger.info(
            "wb_parser.live.search",
            after_filter=len(filtered),
            filters=filters.model_dump(),
        )
    else:
        raise ValueError(f"Unknown DATA_SOURCE: {s.data_source}")

    ranked = _rank(filtered)
    return ranked[:top_n]
