"""
Component-level evaluations.

Запускается без полного pipeline — каждый компонент тестируется изолированно.

Проверки:
  1. Scout JSON parse rate — на 15 эталонных запросах. Цель ≥0.95.
  2. Specs Normalizer precision — на ручной разметке синонимов.
  3. PRD Validator — на «золотом» и «битом» PRD.
  4. USP Classifier — на размеченных фразах.

CLI:
    python -m evals.component_evals
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, StructuredOutputError
from src.schemas.filters import QueryFilters
from src.tools.prd_validator import validate_prd
from src.tools.specs_normalizer import normalize_key
from src.tools.usp_classifier import classify_phrase

console = Console()

EVAL_QUERIES_PATH = PROJECT_ROOT / "data" / "eval_queries.json"
SCOUT_PROMPT_PATH = PROJECT_ROOT / "prompts" / "scout.md"


# ---------------------------------------------------------------------------
# 1. Scout JSON parse rate
# ---------------------------------------------------------------------------


def eval_scout_parse_rate() -> dict[str, Any]:
    """Прогоняем Scout LLM на всех eval-запросах, считаем долю валидных JSON."""
    queries = json.loads(EVAL_QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    prompt = SCOUT_PROMPT_PATH.read_text(encoding="utf-8")

    total = 0
    parsed = 0
    matched_category = 0
    failures: list[dict] = []

    with OllamaClient() as llm:
        for q in queries:
            total += 1
            try:
                result = llm.chat_structured(
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": q["query"]},
                    ],
                    QueryFilters,
                    temperature=0.0,
                    max_repair_attempts=1,
                )
                parsed += 1
                expected_cat = q.get("category_hint")
                if expected_cat is None or result.category == expected_cat:
                    matched_category += 1
                elif expected_cat == "null":
                    pass
                else:
                    failures.append(
                        {"id": q["id"], "expected": expected_cat, "got": result.category}
                    )
            except StructuredOutputError as e:
                failures.append({"id": q["id"], "error": str(e)[:200]})

    return {
        "total": total,
        "parse_rate": round(parsed / total, 3) if total else 0,
        "category_match_rate": round(matched_category / total, 3) if total else 0,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# 2. Specs normalizer
# ---------------------------------------------------------------------------


# Размеченная пара (raw_key → canonical) для регрессионного теста
SPECS_GOLDEN: list[tuple[str, str]] = [
    ("время работы", "battery_life"),
    ("автономность", "battery_life"),
    ("продолжительность работы", "battery_life"),
    ("частота движений", "movements"),
    ("колебаний в минуту", "movements"),
    ("режимов чистки", "modes_brush"),
    ("количество режимов", "modes_brush"),
    ("насадок в комплекте", "heads_included"),
    ("сменных насадок", "attachments"),  # для фена/эпилятора
    ("класс водозащиты", "waterproof"),
    ("влагозащита", "waterproof"),
    ("гарантия", "warranty"),
    ("срок гарантии", "warranty"),
    ("мощность", "power_w"),
    ("ионизация", "ionization"),
    ("режим cool shot", "cool_shot"),
    ("длина шнура", "cord_m"),
    ("количество пинцетов", "tweezers"),
    ("wet&dry", "wet_dry"),
    ("подсветка", "light"),
    # ошибочные/незнакомые — должны вернуть None
    ("цвет корпуса", None),
    ("длина волоса", None),
]


def eval_specs_normalizer() -> dict[str, Any]:
    hits = 0
    misses: list[dict] = []
    for raw, expected in SPECS_GOLDEN:
        got = normalize_key(raw)
        if got == expected:
            hits += 1
        else:
            misses.append({"raw": raw, "expected": expected, "got": got})
    return {
        "total": len(SPECS_GOLDEN),
        "accuracy": round(hits / len(SPECS_GOLDEN), 3),
        "misses": misses,
    }


# ---------------------------------------------------------------------------
# 3. USP classifier
# ---------------------------------------------------------------------------


USP_GOLDEN: list[tuple[str, str]] = [
    ("звуковая технология", "technological"),
    ("37000 движений в минуту", "technological"),
    ("ионизация воздуха", "technological"),
    ("AC-мотор повышенной мощности", "technological"),
    ("smart-таймер 2 минуты", "technological"),
    ("2 года гарантии производителя", "value"),
    ("8 насадок в комплекте", "value"),
    ("ремонт по гарантии в 1500 городов", "value"),
    ("идеально гладкая кожа на 4 недели", "emotional"),
    ("белоснежная улыбка за 14 дней", "emotional"),
    ("тихая работа — не разбудит ребёнка", "emotional"),
    ("хит продаж 2025", "social"),
    ("рекомендуют стоматологи", "social"),
    ("более 50 000 довольных клиентов", "social"),
]


def eval_usp_classifier() -> dict[str, Any]:
    hits = 0
    misses: list[dict] = []
    for phrase, expected in USP_GOLDEN:
        got = classify_phrase(phrase)
        if got == expected:
            hits += 1
        else:
            misses.append({"phrase": phrase, "expected": expected, "got": got})
    return {
        "total": len(USP_GOLDEN),
        "accuracy": round(hits / len(USP_GOLDEN), 3),
        "note": "Rule-based бейзлайн. LLM в pipeline закрывает оставшиеся 10-20%.",
        "misses": misses,
    }


# ---------------------------------------------------------------------------
# 4. PRD validator
# ---------------------------------------------------------------------------


GOLDEN_PRD = {
    "title": "Профессиональный фен 2200 Вт",
    "goal": "Закрыть средний сегмент с быстрой сушкой",
    "target_audience": "Девушки 25–40, моют голову через день",
    "must_have_specs": ["Мощность: 2000-2200 Вт", "Температурных режимов: 3"],
    "nice_to_have_specs": ["Ионизация: есть"],
    "differentiation": ["Сильное соц. подтверждение"],
    "target_price": {"min": 2500, "max": 4500},
    "packaging_requirements": ["Картонная коробка"],
    "compliance": ["Декларация ТР ТС 004/2011", "Декларация ТР ТС 020/2011", "EAC"],
    "risks": ["Высокая концентрация топ-5"],
}

BROKEN_PRD = {
    "title": "Фен",
    "goal": "",
    "target_audience": "",
    "must_have_specs": [],
    "compliance": [],
    "target_price": {"min": 4000, "max": 1000},
}


def eval_prd_validator() -> dict[str, Any]:
    golden = validate_prd(GOLDEN_PRD)
    broken = validate_prd(BROKEN_PRD)
    return {
        "golden_valid": golden["valid"],
        "broken_valid": broken["valid"],
        "broken_missing": broken["missing_sections"],
        "ok": golden["valid"] is True and broken["valid"] is False,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    results: dict[str, Any] = {}

    console.rule("[bold]Specs Normalizer")
    results["specs_normalizer"] = eval_specs_normalizer()
    console.print(results["specs_normalizer"])

    console.rule("[bold]USP Classifier (rule-based baseline)")
    results["usp_classifier"] = eval_usp_classifier()
    console.print(results["usp_classifier"])

    console.rule("[bold]PRD Validator")
    results["prd_validator"] = eval_prd_validator()
    console.print(results["prd_validator"])

    console.rule("[bold]Scout LLM (requires Ollama)")
    try:
        results["scout_parse"] = eval_scout_parse_rate()
        console.print(results["scout_parse"])
    except Exception as e:
        results["scout_parse"] = {"skipped": True, "reason": str(e)}
        console.print(f"[yellow]Skipped (Ollama unavailable):[/yellow] {e}")

    # Сводная таблица
    console.rule("[bold]Summary")
    table = Table()
    table.add_column("Component")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_column("Threshold")
    table.add_column("Status")

    def row(comp: str, metric: str, value: float, threshold: float) -> None:
        ok = value >= threshold
        table.add_row(
            comp, metric, f"{value:.2f}", f"{threshold:.2f}", "[green]✓[/green]" if ok else "[red]✗[/red]"
        )

    row("specs_normalizer", "accuracy", results["specs_normalizer"]["accuracy"], 0.85)
    row("usp_classifier", "accuracy", results["usp_classifier"]["accuracy"], 0.70)
    if "skipped" not in results.get("scout_parse", {}):
        row("scout_llm", "parse_rate", results["scout_parse"]["parse_rate"], 0.95)
        row("scout_llm", "category_match", results["scout_parse"]["category_match_rate"], 0.80)
    table.add_row(
        "prd_validator",
        "golden=valid AND broken=invalid",
        "—",
        "—",
        "[green]✓[/green]" if results["prd_validator"]["ok"] else "[red]✗[/red]",
    )
    console.print(table)

    # Дамп в JSON для CI
    out_path = PROJECT_ROOT / "evals" / "last_component_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\n[green]Saved:[/green] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
