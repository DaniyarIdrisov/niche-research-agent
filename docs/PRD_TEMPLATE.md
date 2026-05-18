# PRD Template — шаблон выходного PRD

Этот документ описывает структуру PRD, который генерит `PRD Writer`. Используется и как контракт, и как KB-документ для retrieval.

## Структура

```yaml
title: <Краткое название продукта, 3-7 слов>
goal: |
  <1-2 предложения о цели продукта. Почему он зарабатывает место в нише.>
target_audience: |
  <Целевая аудитория с гипотезой JTBD (Job-To-Be-Done) в одной фразе.>

must_have_specs:
  - "<Характеристика>: <типичное значение или диапазон>"
  # Источник: SpecsSummary.must_have (frequency ≥ 0.8 от top_n)

nice_to_have_specs:
  - "<Характеристика>: <значение>"
  # Источник: SpecsSummary.nice_to_have (0.4 ≤ frequency < 0.8)

differentiation:
  - "<Точка отстройки: как сыграть на gaps из UspMatrix.gaps>"
  # Минимум 2 пункта. Если gaps пуст — берём слабо-развитые типы УТП.

target_price:
  min: <число, ₽>
  max: <число, ₽>
  # По умолчанию: min = price_p25, max = price_p75 из ниши.

packaging_requirements:
  - "Картонная коробка с защитой от ударов"
  - "Инструкция на русском языке"
  - "Штрихкод EAN-13"
  - "Гарантийный талон"

compliance:
  - "Декларация соответствия ТР ТС 004/2011 (низковольтное оборудование)"
  - "Декларация соответствия ТР ТС 020/2011 (электромагнитная совместимость)"
  - "Маркировка EAC на упаковке и/или товаре"

risks:
  - "<Риск 1: что может пойти не так при выходе>"
  - "<Риск 2>"
```

## Жёсткие правила

1. **Все обязательные поля заполнены.** `must_have_specs`, `compliance`, `target_price` не могут быть пустыми.
2. **Числа реальные.** target_price.min < target_price.max, оба > 0.
3. **Compliance включает обязательный минимум** — ТР ТС 004 + ТР ТС 020 + EAC. Это для МБТ всегда.
4. **Differentiation — минимум 2 пункта.** Если все типы УТП представлены — берём слабый, не выдумываем.
5. **Risks — минимум 1 пункт.** Если ниша «идеальная» — пишем риск «оценка идеальной ниши обычно означает скрытый фактор».

## Источники полей

| Поле | Откуда берётся |
|---|---|
| `title`, `goal`, `target_audience` | LLM (PRD Writer) |
| `must_have_specs` | SpecsSummary.must_have, копируется |
| `nice_to_have_specs` | SpecsSummary.nice_to_have, копируется |
| `differentiation` | LLM из UspMatrix.gaps + niche.insights |
| `target_price` | price_p25 / price_p75 из NicheMetrics |
| `packaging_requirements` | Дефолтный список + категорийная специфика |
| `compliance` | Из KB (этот документ + tr_ts_004 + tr_ts_020) |
| `risks` | LLM из top_share, share_new, p75/p25 |

## Связь с другими документами

- `knowledge_base/compliance/` — источник для `compliance` секции
- `knowledge_base/wb_rules/card_requirements.md` — требования к упаковке
- `src/tools/prd_validator.py` — валидатор по этому шаблону
