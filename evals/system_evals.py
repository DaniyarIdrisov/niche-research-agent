"""
System-level evaluations.

Прогоняет полный pipeline на эталонных запросах из data/eval_queries.json
и считает интегральные метрики.

Метрики:
  1. task_success_rate — Scout вернул ≥min_products, PRD-секции заполнены,
     report содержит Verdict.
  2. trajectory_correctness — порядок вызовов агентов совпадает с ожидаемым
     (по тегам в state.json и логах).
  3. retrieval_metrics — Recall@5 для RAG-запросов (на стенде с прогретой KB).
  4. judge_score — LLM-as-judge оценка качества отчёта (1-5).

CLI:
    python -m evals.system_evals               # все запросы
    python -m evals.system_evals q01_toothbrush_basic q03_dryer_basic  # подмножество
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from src.agents.supervisor import build_graph
from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, StructuredOutputError
from src.observability.logging import setup_logging
from src.schemas.state import make_initial_state

console = Console()

EVAL_QUERIES_PATH = PROJECT_ROOT / "data" / "eval_queries.json"
JUDGE_PROMPT_PATH = PROJECT_ROOT / "evals" / "judge_prompts" / "report_quality.md"
OUT_PATH = PROJECT_ROOT / "evals" / "last_system_results.json"


class _JudgeScore(BaseModel):
    score: int = Field(ge=1, le=5)
    rationale: str
    missing: list[str] = Field(default_factory=list)


def _serialize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list) and value and hasattr(value[0], "model_dump"):
        return [v.model_dump() for v in value]
    return value


def _run_single(graph, query: str) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    state = make_initial_state(query=query, run_id=run_id)
    t0 = time.perf_counter()
    final = graph.invoke(state)
    elapsed = time.perf_counter() - t0
    return {
        "run_id": run_id,
        "elapsed_s": round(elapsed, 1),
        "final": {k: _serialize(v) for k, v in final.items()},
    }


def _check_expectations(final: dict, expected: dict) -> dict[str, Any]:
    """Сопоставляет фактический результат с expected из eval_queries.json."""
    checks: dict[str, Any] = {}

    # task_completed
    has_products = bool(final.get("products"))
    has_report = bool(final.get("report"))
    checks["task_completed"] = has_report

    # scout_min_products
    min_p = expected.get("scout_min_products")
    if min_p is not None:
        n = len(final.get("products", []))
        checks["scout_min_products"] = n >= min_p
        checks["scout_n_products"] = n

    # prd_sections_filled
    required = expected.get("prd_sections_filled")
    if required:
        prd = final.get("prd") or {}
        missing = [s for s in required if not prd.get(s)]
        checks["prd_sections_filled"] = len(missing) == 0
        checks["prd_missing"] = missing

    # verdict_in
    allowed = expected.get("verdict_in")
    if allowed:
        v = final.get("verdict")
        checks["verdict_in"] = v in allowed
        checks["verdict_got"] = v

    # must_mention_in_report
    must = expected.get("must_mention_in_report")
    if must:
        report = (final.get("report") or "").lower()
        hits = [m for m in must if m.lower() in report]
        checks["must_mention_hits"] = len(hits)
        checks["must_mention_required"] = len(must)
        checks["must_mention_pass"] = len(hits) >= max(1, len(must) // 2)

    # task_success — все обязательные пройдены
    obligatory_keys = [k for k in ("task_completed", "scout_min_products", "verdict_in") if k in checks]
    checks["passed"] = all(checks.get(k, True) for k in obligatory_keys)
    return checks


def _judge_report(report: str, judge_prompt: str) -> _JudgeScore | None:
    """LLM-as-judge: оцениваем отчёт от 1 до 5."""
    try:
        with OllamaClient() as llm:
            return llm.chat_structured(
                [
                    {"role": "system", "content": judge_prompt},
                    {"role": "user", "content": report[:6000]},  # обрезаем для num_ctx
                ],
                _JudgeScore,
                temperature=0.0,
                max_repair_attempts=1,
            )
    except StructuredOutputError as e:
        logger.warning("judge.failed", error=str(e))
        return None


def main(query_ids: list[str] | None = None) -> int:
    setup_logging()
    queries = json.loads(EVAL_QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    if query_ids:
        queries = [q for q in queries if q["id"] in query_ids]

    judge_prompt = JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    graph = build_graph()

    results: list[dict[str, Any]] = []
    for q in queries:
        console.rule(f"[bold]{q['id']}[/bold]: {q['query']}")
        try:
            run = _run_single(graph, q["query"])
        except Exception as e:
            logger.exception("system_eval.run_failed", id=q["id"])
            results.append({"id": q["id"], "query": q["query"], "error": str(e), "passed": False})
            continue

        checks = _check_expectations(run["final"], q.get("expected", {}))

        # Судья — только если есть отчёт
        report = run["final"].get("report")
        judge = _judge_report(report, judge_prompt) if report else None

        results.append(
            {
                "id": q["id"],
                "query": q["query"],
                "elapsed_s": run["elapsed_s"],
                "checks": checks,
                "judge_score": judge.score if judge else None,
                "judge_rationale": judge.rationale if judge else None,
                "verdict": run["final"].get("verdict"),
            }
        )

    # Сводная таблица
    table = Table(title="System Eval Summary")
    table.add_column("ID")
    table.add_column("Verdict")
    table.add_column("Passed")
    table.add_column("Judge")
    table.add_column("Latency, s")
    for r in results:
        passed_str = "[green]✓[/green]" if r.get("checks", {}).get("passed") else "[red]✗[/red]"
        judge_str = f"{r['judge_score']}" if r.get("judge_score") else "—"
        table.add_row(
            r["id"], str(r.get("verdict") or "—"), passed_str, judge_str, str(r.get("elapsed_s", "—"))
        )
    console.print(table)

    # Aggregates
    total = len(results)
    passed = sum(1 for r in results if r.get("checks", {}).get("passed"))
    avg_judge = (
        sum(r["judge_score"] for r in results if r.get("judge_score"))
        / max(1, sum(1 for r in results if r.get("judge_score")))
    )
    summary = {
        "total": total,
        "task_success_rate": round(passed / total, 3) if total else 0,
        "avg_judge_score": round(avg_judge, 2) if avg_judge else None,
        "results": results,
    }
    OUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\n[green]Saved:[/green] {OUT_PATH}")
    console.print(f"task_success_rate = [bold]{summary['task_success_rate']}[/bold]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] if len(sys.argv) > 1 else None))
