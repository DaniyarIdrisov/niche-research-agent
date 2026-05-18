---
name: wb_search
description: Поиск товаров на Wildberries в категории МБТ. Два режима — mock (локальный датасет) и live (search.wb.ru без авторизации). Возвращает топ карточек по продажам.
---

# Skill: wb_search

## Когда использовать

Используй этот скилл, когда нужно получить список карточек по нише с Wildberries — будь то начальный сбор данных для ресёрча или повторный запрос для свежих метрик.

Конкретные триггеры:
- запрос пользователя «оцени нишу», «топ по категории», «какие сейчас цены на …»
- агент Scout инициирует первый шаг pipeline
- evals прогоняет эталонный запрос

Не используй для:
- ручного скачивания одной карточки по URL (это отдельный skill, ещё не написан)
- работы с другими маркетплейсами

## Входы

| Поле | Тип | Описание |
|---|---|---|
| `category` | enum | `electric_toothbrush` / `hair_dryer` / `epilator` / `unknown` |
| `min_price` | int? | мин. цена в ₽ |
| `max_price` | int? | макс. цена в ₽ |
| `keywords` | list[str] | дополнительные ключевые слова для текстового матча |
| `top_n` | int? | сколько вернуть (по умолчанию `DEFAULT_TOP_N` = 30) |

## Выходы

`list[Product]` (см. `src/schemas/product.py`). Полей в живых WB-карточках меньше, чем в mock — это нормально:

| Поле | Mock | Live |
|---|---|---|
| sku, name, price, rating, reviews_count | да | да |
| seller, brand | да | да |
| description, specs, color | да | нет (потребовало бы доп. запросы к карточкам) |
| sales_estimate_per_month | да (прокси) | None |

## Пример

```python
from src.schemas.filters import QueryFilters
from src.tools.wb_parser import search_products

filters = QueryFilters(category="hair_dryer", max_price=5000)
products = search_products(filters, top_n=30)
# products: list[Product]
```

## Edge cases

- **Категория `unknown` в mock-режиме** → вернём пустой список (не угадываем).
- **Категория `unknown` в live-режиме** → используем `keywords` как поисковую строку напрямую, фильтра по категории нет.
- **`search.wb.ru` сменил формат** → live-режим вернёт пустой/обрезанный результат, mock остаётся работоспособным.
- **Очень узкий ценовой фильтр** → возможен пустой результат; вызывающий код должен обработать `len(products) == 0` явно.
- **Rate-limit** → встроенный token-bucket (по умолчанию 2 req/s), повторные вызовы дросселируются.

## Связанные скиллы

- `specs_extractor` — следующий шаг: достать характеристики из карточек
- `unit_economics` — посчитать юнит-экономику по цене+комиссии WB
