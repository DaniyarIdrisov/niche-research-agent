"""
Episodic memory — SQLite-журнал прошлых запусков.

Зачем:
- быстро вернуться к результату прошлого ресёрча по run_id;
- собирать материал для evals (фактический output на реальных запросах);
- в будущем — k-NN по похожим запросам, чтобы предлагать «вы уже анализировали
  щётки до 3000 руб 2 недели назад, вот предыдущие insights».

Синхронный sqlite3 (а не aiosqlite): мы не в async-контексте на уровне графа,
LangGraph по умолчанию синхронный. Async добавится, если потребуется
multi-tenant сервер — но это out of scope для университетского проекта.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

from src.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    query         TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    verdict       TEXT,
    n_products    INTEGER,
    state_json    TEXT NOT NULL,
    errors_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_verdict    ON runs(verdict);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = path or get_settings().sqlite_path
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    """Идемпотентно создаёт таблицы. Вызывается при первом use."""
    with _conn(path) as c:
        c.executescript(SCHEMA)
    logger.debug("episodic.init_db.ok", path=str(path or get_settings().sqlite_path))


def start_run(run_id: str, query: str) -> None:
    """Регистрирует начало запуска. Финальный state допишется в finish_run."""
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO runs(run_id, query, started_at, state_json) "
            "VALUES (?, ?, ?, ?)",
            (run_id, query, _utc_now(), json.dumps({"status": "started"}, ensure_ascii=False)),
        )


def finish_run(
    run_id: str,
    state: dict[str, Any],
    verdict: str | None = None,
) -> None:
    init_db()
    products = state.get("products") or []
    errors = state.get("errors") or []
    with _conn() as c:
        c.execute(
            "UPDATE runs SET finished_at=?, verdict=?, n_products=?, state_json=?, errors_json=? "
            "WHERE run_id=?",
            (
                _utc_now(),
                verdict,
                len(products),
                json.dumps(state, ensure_ascii=False, default=str),
                json.dumps(errors, ensure_ascii=False),
                run_id,
            ),
        )


def get_run(run_id: str) -> dict[str, Any] | None:
    init_db()
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT run_id, query, started_at, finished_at, verdict, n_products "
            "FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if d.get("state_json"):
        try:
            d["state"] = json.loads(d["state_json"])
        except json.JSONDecodeError:
            d["state"] = None
    if d.get("errors_json"):
        try:
            d["errors"] = json.loads(d["errors_json"])
        except json.JSONDecodeError:
            d["errors"] = []
    return d
