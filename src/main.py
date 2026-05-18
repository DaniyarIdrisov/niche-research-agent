"""
CLI entry point.

Usage:
    uv run python -m src.main research "электрические зубные щётки до 3000 рублей"
    uv run python -m src.main history list
    uv run python -m src.main history show <run-id>
    uv run python -m src.main check       # быстрый health-check Ollama
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from src.agents.supervisor import build_graph
from src.config import get_settings
from src.llm.ollama_client import OllamaClient, OllamaError
from src.memory import episodic
from src.observability import setup_observability
from src.observability.langfuse_hook import flush as flush_langfuse
from src.schemas.state import make_initial_state

app = typer.Typer(
    add_completion=False,
    help="Niche Research Agent — мультиагентная система ресёрча ниши на Wildberries.",
)
history_app = typer.Typer(help="История прошлых запусков.")
app.add_typer(history_app, name="history")

console = Console()


def _serialize_state(state: dict) -> dict:
    """Готовит state к записи в JSON: Pydantic-объекты → dict."""
    out = {}
    for k, v in state.items():
        if hasattr(v, "model_dump"):
            out[k] = v.model_dump()
        elif isinstance(v, list) and v and hasattr(v[0], "model_dump"):
            out[k] = [item.model_dump() for item in v]
        else:
            out[k] = v
    return out


def _render_products_table(products: list[dict], limit: int = 10) -> None:
    table = Table(title=f"Top-{min(limit, len(products))} карточек по выдаче Scout")
    table.add_column("SKU", style="dim")
    table.add_column("Название", overflow="fold")
    table.add_column("Цена", justify="right")
    table.add_column("⭐", justify="right")
    table.add_column("Отзывы", justify="right")
    table.add_column("Продавец", overflow="fold")
    for p in products[:limit]:
        table.add_row(
            str(p["sku"]),
            (p["name"] or "")[:70],
            f"{p['price']} ₽",
            f"{p.get('rating', '?')}",
            str(p.get("reviews_count", 0)),
            (p.get("seller") or "")[:30],
        )
    console.print(table)


@app.command()
def research(
    query: str = typer.Argument(..., help="Запрос на естественном языке"),
    top_n: Optional[int] = typer.Option(None, "--top-n", help="Сколько товаров оставить в топе"),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Куда сохранить артефакты (по умолчанию ./runs/<run-id>/)"
    ),
) -> None:
    """Запустить полный pipeline на запрос пользователя."""
    setup_observability()
    settings = get_settings()
    run_id = uuid.uuid4().hex[:12]
    out_dir = output_dir or (settings.runs_dir / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]Run ID:[/bold] {run_id}")
    console.print(f"[dim]Data source:[/dim] {settings.data_source}")
    console.print(f"[dim]Query:[/dim] {query}\n")

    initial = make_initial_state(query=query, run_id=run_id)
    graph = build_graph()

    # Регистрируем запуск в episodic memory ДО invoke — чтобы упавшие тоже сохранялись
    episodic.start_run(run_id=run_id, query=query)

    try:
        final_state = graph.invoke(initial)
    except Exception as e:
        logger.exception("research.failed")
        console.print(f"[red]Запуск упал:[/red] {e}")
        episodic.finish_run(run_id, {"error": str(e), "errors": [str(e)]}, verdict="error")
        raise typer.Exit(code=1) from e

    products = final_state.get("products", [])
    products_dump = [p.model_dump() if hasattr(p, "model_dump") else p for p in products]

    # Вывод
    if products_dump:
        _render_products_table(products_dump, limit=10)

    report = final_state.get("report")
    if report:
        console.rule("[bold]Отчёт[/bold]")
        console.print(report)

    # Дамп state на диск (rich-обёртки → dict)
    serialized = _serialize_state(final_state)
    state_path = out_dir / "state.json"
    state_path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")

    # Отдельно сохраняем report.md (удобно ссылаться)
    if report:
        (out_dir / "report.md").write_text(report, encoding="utf-8")

    # Запись в episodic memory
    verdict = final_state.get("verdict")
    episodic.finish_run(run_id, serialized, verdict=verdict)

    console.print(f"\n[green]State:[/green] {state_path}")
    if verdict:
        console.print(f"[bold]Verdict:[/bold] {verdict}")

    errors = final_state.get("errors", [])
    if errors:
        console.print("[yellow]Предупреждения:[/yellow]")
        for e in errors:
            console.print(f"  • {e}")

    flush_langfuse()


@app.command()
def check() -> None:
    """Быстрая проверка: Ollama жив, модель доступна, mock-датасет читается."""
    setup_observability()
    s = get_settings()
    console.print(f"[bold]Ollama:[/bold] {s.ollama_base_url}")
    console.print(f"[bold]Model:[/bold] {s.ollama_llm_model}")
    console.print(f"[bold]Embed:[/bold] {s.ollama_embed_model}")
    console.print(f"[bold]Data source:[/bold] {s.data_source}")

    if s.data_source == "mock":
        if not s.mock_data_path.exists():
            console.print(f"[red]Mock-датасет не найден:[/red] {s.mock_data_path}")
            raise typer.Exit(code=1)
        console.print(f"[green]Mock OK:[/green] {s.mock_data_path}")

    try:
        with OllamaClient() as llm:
            resp = llm.chat_text(
                [{"role": "user", "content": "ответь одним словом: ping"}],
                temperature=0.0,
            )
        console.print(f"[green]LLM OK:[/green] {resp.strip()[:80]}")
    except OllamaError as e:
        console.print(f"[red]Ollama недоступна:[/red] {e}")
        raise typer.Exit(code=1) from e


@history_app.command("list")
def history_list() -> None:
    """Показать список прошлых запусков."""
    s = get_settings()
    if not s.runs_dir.exists():
        console.print("[yellow]Пока нет ни одного запуска.[/yellow]")
        return
    runs = sorted(s.runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        console.print("[yellow]Пока нет ни одного запуска.[/yellow]")
        return
    table = Table(title="История запусков")
    table.add_column("Run ID")
    table.add_column("Время")
    table.add_column("Запрос", overflow="fold")
    for r in runs[:50]:
        state_file = r / "state.json"
        query = "?"
        if state_file.exists():
            try:
                query = json.loads(state_file.read_text(encoding="utf-8")).get("query", "?")
            except json.JSONDecodeError:
                pass
        from datetime import datetime
        ts = datetime.fromtimestamp(r.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(r.name, ts, query[:80])
    console.print(table)


@history_app.command("show")
def history_show(run_id: str = typer.Argument(...)) -> None:
    """Показать дамп state для заданного run-id."""
    s = get_settings()
    path = s.runs_dir / run_id / "state.json"
    if not path.exists():
        console.print(f"[red]Run не найден:[/red] {run_id}")
        raise typer.Exit(code=1)
    console.print_json(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
