"""
Рендер всех Mermaid-диаграмм для REPORT.md через публичный сервис mermaid.ink.

Никаких зависимостей: только stdlib (urllib + base64). Запуск:

    python scripts/render_mermaid.py

Результат: docs/screens/screen-02.png, screen-04.png, screen-05.png, screen-07.png.
Номера соответствуют placeholder-маркерам в docs/REPORT.md.

Сервис: https://mermaid.ink/ — берёт base64-URL-encoded Mermaid и отдаёт PNG.
Альтернатива (если mermaid.ink недоступен): npm install -g @mermaid-js/mermaid-cli
и заменить _render() на subprocess к mmdc.
"""

from __future__ import annotations

import base64
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "screens"


# ---------------------------------------------------------------------------
# Mermaid sources (1:1 копии из docs/REPORT.md)
# ---------------------------------------------------------------------------

DIAGRAM_2_ARCHITECTURE = """flowchart TD
    CLI[CLI: Typer + Rich] --> SUP{Supervisor<br/>LangGraph}
    SUP --> SCOUT[Scout]
    SCOUT --> VS{validate_scout}
    VS -->|retry ≤2| SCOUT
    VS -->|no_data| REP
    VS -->|fan-out| NA[Niche Analyst]
    VS -->|fan-out| SM[Specs Miner]
    VS -->|fan-out| UA[USP Analyst]
    NA --> VN{validate_niche}
    VN --> PRD[PRD Writer]
    SM --> PRD
    UA --> PRD
    PRD --> VP{validate_prd}
    VP -->|retry ≤2| PRD
    VP -->|ok| REP[Reporter]
    REP --> END([END])

    SUP -.-> OLLAMA[(Ollama<br/>qwen2.5:3b)]
    SUP -.-> MEM[(Memory:<br/>working /<br/>episodic /<br/>semantic)]
    SUP -.-> OBS[(Observability:<br/>OTel / Prom /<br/>Loguru / Langfuse)]
"""

DIAGRAM_4_MEMORY = """flowchart LR
    Q[Запрос пользователя] --> WM[Working memory<br/>TypedDict<br/>1 запрос]
    WM --> EM[Episodic memory<br/>SQLite<br/>история запусков]
    WM --> SM[Semantic memory<br/>ChromaDB + BM25<br/>знания о домене]
    SM -.retrieve.-> WM
    EM -.find_similar.-> WM
"""

DIAGRAM_5_RAG = """flowchart TD
    Q[query] --> EMB[Ollama embed]
    Q --> TOK[tokenize]
    EMB --> CHR[ChromaDB.query<br/>top_k_dense=10]
    TOK --> BM[BM25.search<br/>top_k_bm25=10]
    CHR --> F[fusion<br/>dense·0.6 + sparse·0.4]
    BM --> F
    F --> CAND[top-K кандидатов]
    CAND --> RR{rerank?}
    RR -->|RAG_USE_RERANKER=true| BGE[bge-reranker-v2-m3]
    RR -->|false| OUT
    BGE --> OUT[final top-K]
"""

DIAGRAM_7_OBSERVABILITY = """flowchart TD
    APP[Приложение Python] --> L[Loguru]
    APP --> OTEL[OTel SDK]
    APP --> PROM[prometheus_client]
    APP --> LF[Langfuse SDK]

    L --> LJSONL[logs/agent.jsonl]
    L --> STDOUT[stdout цветной]

    OTEL --> COL[otel-collector]
    COL --> JAEGER[Jaeger UI :16686]

    PROM --> EP[:9464/metrics]
    EP --> PR[Prometheus :9090]
    PR --> GR[Grafana :3000]

    LF --> LFAPI[Langfuse :3001]
"""

DIAGRAMS = [
    (2, "Архитектура мультиагентной системы", DIAGRAM_2_ARCHITECTURE),
    (4, "Трёхуровневая система памяти", DIAGRAM_4_MEMORY),
    (5, "Гибридный retrieval: dense + sparse + rerank", DIAGRAM_5_RAG),
    (7, "Поток данных observability", DIAGRAM_7_OBSERVABILITY),
]


# ---------------------------------------------------------------------------
# Рендер через mermaid.ink
# ---------------------------------------------------------------------------


def _encode(source: str) -> str:
    """mermaid.ink хочет URL-safe base64 без padding."""
    raw = base64.urlsafe_b64encode(source.strip().encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def _render(source: str, *, dest: Path) -> int:
    """Возвращает размер записанного PNG в байтах. Бросает на сетевых ошибках."""
    encoded = _encode(source)
    # bgColor=FFFFFF — чистый белый фон, лучше встаёт в PDF
    url = f"https://mermaid.ink/img/{encoded}?type=png&bgColor=FFFFFF"

    req = urllib.request.Request(url, headers={"User-Agent": "niche-research-agent/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    print(f"Output dir: {OUT_DIR}")
    failed: list[tuple[int, str]] = []
    for num, title, source in DIAGRAMS:
        dest = OUT_DIR / f"screen-{num:02d}.png"
        try:
            n = _render(source, dest=dest)
            print(f"  screen-{num:02d}.png OK   {n:>7} bytes   ({title})")
        except urllib.error.URLError as e:
            print(f"  screen-{num:02d}.png FAIL ({e})", file=sys.stderr)
            failed.append((num, str(e)))
        except Exception as e:
            print(f"  screen-{num:02d}.png FAIL ({type(e).__name__}: {e})", file=sys.stderr)
            failed.append((num, str(e)))

    if failed:
        print(f"\n{len(failed)} diagrams failed:", file=sys.stderr)
        for num, err in failed:
            print(f"  - screen-{num:02d}: {err}", file=sys.stderr)
        return 1
    print(f"\nAll {len(DIAGRAMS)} diagrams rendered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
