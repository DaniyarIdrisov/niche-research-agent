---
name: prd_validator
description: Проверка полноты и структуры PRD на соответствие шаблону. Возвращает список пропущенных/пустых обязательных секций. Используется в валидационной ноде после PRD Writer.
---

# Skill: prd_validator

## Когда использовать

Сразу после генерации PRD агентом PRD Writer. Если валидация не прошла — Supervisor ретрайнет PRD Writer (≤MAX_RETRIES_PER_NODE).

## Входы

`dict` — сериализованный `PRD` (см. `src/schemas/analysis.py::PRD`).

## Выходы

```python
{
  "valid": bool,
  "missing_sections": list[str],
  "warnings": list[str],     # необязательные замечания
}
```

## Правила

Обязательные секции (PRD считается невалидным, если хоть одна пустая):
- `title`
- `goal`
- `target_audience`
- `must_have_specs` (≥1 элемент)
- `compliance` (≥1 элемент)
- `target_price` (с `min` и `max`)

Предупреждения (валидность не ломают):
- `differentiation` пуст — нет точек отстройки
- `risks` пуст — не указаны риски
- `target_price.min > target_price.max` — баг в LLM-выводе, swap делаем сами

## Запуск

```python
from src.tools.prd_validator import validate_prd

result = validate_prd(prd_dict)
if not result["valid"]:
    # retry PRD Writer
    ...
```

## Edge cases

- **`prd` is None** → invalid (missing_sections = ["whole PRD missing"])
- **`must_have_specs` = []** → invalid (must_have_specs)
- **`compliance` = []** → invalid (отсутствуют обязательные документы — это критично для МБТ)

## Связанные

- `src/agents/prd_writer.py::validate_prd_node` — валидационная нода LangGraph, использует этот скилл
