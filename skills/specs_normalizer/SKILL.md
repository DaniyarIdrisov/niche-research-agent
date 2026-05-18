---
name: specs_normalizer
description: Нормализация синонимичных ключей характеристик к каноническим. Пример — «время работы», «автономность», «продолжительность работы» сводятся к ключу `battery_life`.
---

# Skill: specs_normalizer

## Когда использовать

Когда есть характеристики из карточки в живой форме (с разными формулировками) и нужно свести их к единому канону для агрегации/частотной таблицы.

## Входы

`dict[str, Any]` — characteristics в исходных формулировках (например `{"автономность": "60 дней", "частота движений": "37000 в минуту"}`).

## Выходы

`dict[str, Any]` — с каноническими ключами (например `{"battery_life": "60 дней", "movements": "37000 в минуту"}`).

## Источник истины

`src/tools/specs_normalizer.py::TAXONOMY` — словарь `canonical_key → [synonyms]`. В Phase 5 этот же словарь будет дублироваться в `knowledge_base/specs_taxonomy/*.md` для RAG, чтобы нечёткое матчирование шло через embeddings.

## Примеры

```python
from src.tools.specs_normalizer import normalize_specs, frequency_table

raw = {"автономность": "60 дней", "режимов чистки": 5, "влагозащита": "IPX7"}
canon = normalize_specs(raw)
# {"battery_life": "60 дней", "modes_brush": 5, "waterproof": "IPX7"}

# для топа карточек:
freqs = frequency_table([p.specs for p in top_products], top_n=len(top_products))
# {canonical_key: {"frequency": N, "typical_values": [...], "display_name": ...}}
```

## Edge cases

- **Ключ не из taxonomy** → пропускается (возвращается None). Это сознательно: чтобы не загрязнять частотную таблицу шумом.
- **Два разных raw-ключа → один canonical** → сохраняется первое встретившееся значение (порядок зависит от dict-insertion в Python 3.7+).
- **Пустой dict** → пустой dict.

## Что НЕ делает

- Не приводит **значения** к каноническому виду (т.е. "60 дней" и "2 месяца" остаются разными). Нормализация значений — отдельная задача (Phase 5+).
- Не угадывает категорию.

## Связанные скиллы

- `specs_extractor` — извлекает specs до нормализации.
