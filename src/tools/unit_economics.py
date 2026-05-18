"""
Unit economics calculator для МБТ на WB.

ВАЖНО: ставки WB регулярно меняются. Источник на момент написания —
официальная страница WB Partners, раздел «Комиссии и логистика».
Для prod-использования нужно подтягивать через WB API.
"""

from __future__ import annotations

from typing import Any

# Категория → процент комиссии WB (FBO/FBS, на момент 2025)
WB_RATES_2025: dict[str, float] = {
    "electric_toothbrush": 0.23,
    "hair_dryer": 0.23,
    "epilator": 0.23,
    # Дефолт для незнакомых — средняя по МБТ
    "_default": 0.23,
}

# Базовая ставка логистики (склад→ПВЗ), ₽ за единицу, для малогабарита
LOGISTICS_BASE_RUB = 50
LOGISTICS_PER_KG_RUB = 60

# Эквайринг
ACQUIRING_RATE = 0.02


def calc_unit_economics(
    retail_price: int,
    cost_price: int,
    weight_kg: float,
    category: str = "_default",
) -> dict[str, Any]:
    commission_rate = WB_RATES_2025.get(category, WB_RATES_2025["_default"])
    wb_commission = round(retail_price * commission_rate)

    logistics = round(LOGISTICS_BASE_RUB + LOGISTICS_PER_KG_RUB * max(0.1, weight_kg))

    acquiring = round(retail_price * ACQUIRING_RATE)

    margin = retail_price - cost_price - wb_commission - logistics - acquiring
    margin_share = margin / retail_price if retail_price else 0.0

    return {
        "retail_price": retail_price,
        "wb_commission_rub": wb_commission,
        "logistics_rub": logistics,
        "acquiring_rub": acquiring,
        "cost_price": cost_price,
        "margin_rub": margin,
        "margin_share": round(margin_share, 3),
        "category": category,
        "commission_rate_used": commission_rate,
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 4:
        print("usage: python -m src.tools.unit_economics <retail> <cost> <weight_kg> [category]")
        raise SystemExit(1)

    res = calc_unit_economics(
        retail_price=int(sys.argv[1]),
        cost_price=int(sys.argv[2]),
        weight_kg=float(sys.argv[3]),
        category=sys.argv[4] if len(sys.argv) > 4 else "_default",
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
