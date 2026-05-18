"""
PRD validator — stand-alone проверка структуры PRD.

Используется и в LangGraph (через validate_prd_node), и в evals
(component_evals по полю prd_sections_filled).
"""

from __future__ import annotations

from typing import Any

REQUIRED_NON_EMPTY = (
    "title",
    "goal",
    "target_audience",
    "must_have_specs",
    "compliance",
    "target_price",
)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return True
    return False


def validate_prd(prd: dict | None) -> dict:
    if prd is None:
        return {
            "valid": False,
            "missing_sections": ["whole PRD missing"],
            "warnings": [],
        }

    missing: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED_NON_EMPTY:
        if _is_empty(prd.get(key)):
            missing.append(key)

    tp = prd.get("target_price")
    if isinstance(tp, dict):
        if "min" not in tp or "max" not in tp:
            missing.append("target_price.min/max")
        elif tp.get("min") is not None and tp.get("max") is not None:
            try:
                if int(tp["min"]) > int(tp["max"]):
                    warnings.append("target_price.min > max — поменяй местами")
            except (TypeError, ValueError):
                missing.append("target_price.min_max_not_int")

    if _is_empty(prd.get("differentiation")):
        warnings.append("differentiation пуст — не указали точек отстройки")
    if _is_empty(prd.get("risks")):
        warnings.append("risks пуст — не указали рисков")

    return {
        "valid": len(missing) == 0,
        "missing_sections": missing,
        "warnings": warnings,
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m src.tools.prd_validator <path-to-prd.json>")
        raise SystemExit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        prd_obj = json.load(f)
    print(json.dumps(validate_prd(prd_obj), ensure_ascii=False, indent=2))
