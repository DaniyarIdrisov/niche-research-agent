---
name: usp_parser
description: Извлечение и базовая классификация маркетинговых УТП из заголовков и описаний карточек. Делит на 4 типа — technological / value / emotional / social. Гибрид rule-based + LLM.
---

# Skill: usp_parser

## Когда использовать

Когда нужно понять, **на чём именно давит конкурент** в карточке — на технологии, комплектации, эмоциях или социальных пруфах. Применяется к топ-5 карточкам.

## Входы

`text: str` — обычно `f"{product.name}. {product.description}"`
`seller: str | None` — продавец (для последующей атрибуции в матрице УТП).

## Выходы

`list[UspItem]` (см. `src/schemas/analysis.py`):

```python
UspItem(seller="...", phrase="звуковая технология", usp_type="technological")
```

## Алгоритм

1. **Сегментация:** разделяем текст по точкам/запятым/переводам строк на «УТП-куски» (≥4 символа).
2. **Rule-based классификация:** прогоняем каждую фразу через регулярные выражения для 4 типов (см. `src/tools/usp_classifier.py::_TECH_PATTERNS` и аналоги).
3. **Fallback:** если ни одна rule не сматчилась — ставим `value` как наименее провокационный дефолт. Дальше USP Analyst (LLM) пересматривает.

## Типы УТП

| Тип | Что входит |
|---|---|
| `technological` | Технологические фишки, числа, специфические термины (ионизация, AC-мотор, IPX7) |
| `value` | Комплектация, гарантия, доставка, сервис |
| `emotional` | Обещания эмоций или результата («белоснежная улыбка», «тихая работа») |
| `social` | Социальное доказательство («хит продаж», «рекомендуют стоматологи») |

## Примеры

```python
from src.tools.usp_classifier import baseline_classify

items = baseline_classify(
    "Soocas X3U со звуковой технологией. Хит продаж 2025. 8 насадок в комплекте.",
    seller="Soocas Official",
)
# [
#   UspItem(seller="Soocas Official", phrase="Soocas X3U со звуковой технологией", usp_type="technological"),
#   UspItem(seller="Soocas Official", phrase="Хит продаж 2025", usp_type="social"),
#   UspItem(seller="Soocas Official", phrase="8 насадок в комплекте", usp_type="value"),
# ]
```

## Edge cases

- **Пустой текст** → пустой список.
- **Фраза короче 4 символов** → отфильтровывается на этапе сегментации.
- **Тип не определён** → ставится `value` как дефолт (LLM-аналист имеет шанс поправить).
- **Несколько типов в одной фразе** → берётся первый сматчившийся (приоритет: technological > value > emotional > social).

## Связанные скиллы

Дальше — USP Analyst (агент), который кластеризует результат и находит gaps.
