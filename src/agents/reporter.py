"""
Reporter.

Финальная нода. Берёт все артефакты и пишет markdown-отчёт + вытаскивает
verdict из последней строки. Температура чуть выше — это единственное место,
где нам нужна читабельность, а не структура.

Если данных нет (n_products == 0) — генерируем no-data заглушку без LLM-вызова.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from src.config import PROJECT_ROOT
from src.llm.ollama_client import OllamaClient, OllamaError
from src.memory.working import add_error
from src.schemas.state import AgentState

PROMPT_PATH = PROJECT_ROOT / "prompts" / "reporter.md"
VERDICT_RE = re.compile(r"\*\*Verdict:\*\*\s*(go|conditional-go|no-go|no-data)", re.IGNORECASE)
ALLOWED_VERDICTS = {"go", "conditional-go", "no-go", "no-data"}


def _load_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def _no_data_report(state: AgentState) -> tuple[str, str]:
    """Жёсткий шаблон когда товаров вообще нет."""
    query = state.get("query", "")
    filters = state.get("filters")
    category = filters.category if filters is not None else "unknown"
    body = (
        f"# Ресёрч ниши: {query}\n\n"
        f"## Резюме\n"
        f"По заданным параметрам не найдено ни одной карточки. "
        f"Категория, которую распознал Scout: `{category}`. "
        f"Возможные причины: запрос вне покрытия системы, слишком узкий фильтр, "
        f"или live-режим вернул пустой результат. Дальше анализировать нечего.\n\n"
        f"## Что делать дальше\n"
        f"- Уточнить категорию (electric_toothbrush, hair_dryer, epilator).\n"
        f"- Расширить ценовой диапазон.\n"
        f"- Если запрос про другую категорию МБТ — это out of scope текущей версии системы.\n\n"
        f"**Verdict:** no-data\n"
    )
    return body, "no-data"


def reporter_node(state: AgentState) -> AgentState:
    products = state.get("products", [])
    if not products:
        report, verdict = _no_data_report(state)
        logger.info("reporter.no_data")
        return {**state, "report": report, "verdict": verdict}

    llm_input = {
        "query": state.get("query"),
        "category": state.get("filters").category if state.get("filters") else "unknown",
        "niche_metrics": state.get("niche_metrics"),
        "specs_summary": state.get("specs_summary"),
        "usp_analysis": state.get("usp_analysis"),
        "prd": state.get("prd"),
    }

    errors = list(state.get("errors", []))
    try:
        with OllamaClient() as llm:
            report = llm.chat_text(
                [
                    {"role": "system", "content": _load_prompt()},
                    {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False, default=str)},
                ],
                temperature=0.4,
            )
    except OllamaError as e:
        logger.warning("reporter.llm_failed", error=str(e))
        errors = add_error(state, f"reporter: LLM failed ({e})")
        # Минимальный фоллбэк — машинный отчёт из метрик
        report = _machine_fallback_report(state)

    # Достаём verdict из последней Verdict-строки
    m = VERDICT_RE.search(report)
    if m:
        verdict = m.group(1).lower()
        if verdict not in ALLOWED_VERDICTS:
            verdict = "conditional-go"
    else:
        # Если модель забыла дописать — выводим из метрик детерминированно
        verdict = _heuristic_verdict(state)
        report = report.rstrip() + f"\n\n**Verdict:** {verdict}\n"

    logger.info("reporter.done", verdict=verdict, length=len(report))
    return {**state, "report": report, "verdict": verdict, "errors": errors}


def _heuristic_verdict(state: AgentState) -> str:
    """Backup-эвристика когда LLM не положил вердикт."""
    m = state.get("niche_metrics") or {}
    n = m.get("n_products", 0)
    if n == 0:
        return "no-data"
    top_share = m.get("top_share", 0.0)
    share_new = m.get("share_new", 0.0)
    p25 = m.get("price_p25") or 1
    p75 = m.get("price_p75") or 1
    if top_share >= 0.7 and share_new < 0.2:
        return "no-go"
    if top_share >= 0.5 or n < 10 or (p75 / p25) < 1.3:
        return "conditional-go"
    return "go"


def _machine_fallback_report(state: AgentState) -> str:
    """Минимальный отчёт когда LLM Reporter упал. Чтобы не отдавать пользователю пустоту."""
    m = state.get("niche_metrics") or {}
    prd = state.get("prd") or {}
    query = state.get("query", "")
    verdict = _heuristic_verdict(state)

    lines = [
        f"# Ресёрч ниши: {query}",
        "",
        "## Резюме",
        "Машинный отчёт (LLM Reporter упал, восстановили из артефактов агентов выше).",
        "",
        "## Метрики ниши",
        f"- Объём топ-30: {m.get('revenue_top_30', 0):,} ₽".replace(",", " "),
        f"- Концентрация топ-5: {round((m.get('top_share') or 0) * 100, 1)}%",
        f"- Медианная цена: {m.get('price_median', 0)} ₽ ({m.get('price_p25', 0)}–{m.get('price_p75', 0)} ₽)",
        f"- Доля зрелых (>=100 отзывов): {round((m.get('share_mature') or 0) * 100, 1)}%",
        f"- Доля новых (<10 отзывов): {round((m.get('share_new') or 0) * 100, 1)}%",
        "",
    ]
    if prd:
        lines += [
            "## Что предлагаем (PRD)",
            f"- Цель: {prd.get('goal', '—')}",
            f"- ЦА: {prd.get('target_audience', '—')}",
            f"- Целевая цена: {prd.get('target_price', {}).get('min', '—')}–{prd.get('target_price', {}).get('max', '—')} ₽",
            "",
        ]
    lines += [f"**Verdict:** {verdict}", ""]
    return "\n".join(lines)
