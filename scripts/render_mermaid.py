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

DIAGRAM_1A_DOMAIN = """classDiagram
    direction LR

    class Product {
      <<Pydantic>>
      +int sku
      +str category
      +str name
      +int price
      +float rating
      +int reviews_count
      +str seller
      +dict specs
      +total_sales_proxy() int
    }

    class QueryFilters {
      <<Pydantic>>
      +str category
      +int min_price
      +int max_price
      +list keywords
    }

    class NicheInsight {
      <<Pydantic>>
      +str category
      +str statement
    }

    class NicheMetrics {
      <<Pydantic>>
      +int n_products
      +int revenue_top_30
      +float top_share
      +int price_median
      +int price_p25
      +int price_p75
      +float share_mature
      +float share_new
      +list insights
    }

    class SpecFrequency {
      <<Pydantic>>
      +str canonical_key
      +str display_name
      +int frequency
      +int top_n
      +list typical_values
    }

    class SpecsSummary {
      <<Pydantic>>
      +int top_n
      +list must_have
      +list nice_to_have
      +list rare
    }

    class UspItem {
      <<Pydantic>>
      +str seller
      +str phrase
      +UspType usp_type
    }

    class UspMatrix {
      <<Pydantic>>
      +list items
      +dict type_distribution
      +list gaps
    }

    class PRD {
      <<Pydantic>>
      +str title
      +str goal
      +str target_audience
      +list must_have_specs
      +list nice_to_have_specs
      +list differentiation
      +dict target_price
      +list compliance
      +list risks
    }

    NicheMetrics o-- NicheInsight
    SpecsSummary o-- SpecFrequency
    UspMatrix o-- UspItem
"""


DIAGRAM_1B_INFRA = """classDiagram
    direction TB

    class AgentState {
      <<TypedDict>>
      +str query
      +str run_id
      +QueryFilters filters
      +list products
      +dict niche_metrics
      +dict specs_summary
      +dict usp_analysis
      +dict prd
      +str report
      +str verdict
      +list errors
      +dict retries
    }

    class OllamaClient {
      +str llm_model
      +str embed_model
      +int num_ctx
      +chat(msgs, temp, json_mode) dict
      +chat_text(msgs, temp) str
      +chat_structured(msgs, schema) T
      +embed(texts) list
    }

    class SemanticMemory {
      -collection
      -_BM25Store bm25
      -_Reranker reranker
      +ingest(kb_dir) int
      +retrieve(query, k) list
      +health() dict
    }

    class RetrievedChunk {
      <<dataclass>>
      +str text
      +str source
      +float score
      +dict metadata
    }

    class Settings {
      <<pydantic_settings>>
      +str data_source
      +str ollama_llm_model
      +int max_retries_per_node
      +float rag_dense_weight
      +bool rag_use_reranker
    }

    class QueryFilters
    class Product
    class NicheMetrics
    class SpecsSummary
    class UspMatrix
    class PRD

    AgentState ..> QueryFilters
    AgentState ..> Product
    AgentState ..> NicheMetrics
    AgentState ..> SpecsSummary
    AgentState ..> UspMatrix
    AgentState ..> PRD

    SemanticMemory ..> RetrievedChunk
    SemanticMemory ..> OllamaClient
    OllamaClient ..> Settings
    SemanticMemory ..> Settings
"""

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
    ("01a", "Диаграмма классов: доменные типы (Pydantic-схемы)", DIAGRAM_1A_DOMAIN),
    ("01b", "Диаграмма классов: state + инфраструктурные клиенты", DIAGRAM_1B_INFRA),
    ("02", "Архитектура мультиагентной системы", DIAGRAM_2_ARCHITECTURE),
    ("04", "Трёхуровневая система памяти", DIAGRAM_4_MEMORY),
    ("05", "Гибридный retrieval: dense + sparse + rerank", DIAGRAM_5_RAG),
    ("07", "Поток данных observability", DIAGRAM_7_OBSERVABILITY),
]


# ---------------------------------------------------------------------------
# Рендер через mermaid.ink
# ---------------------------------------------------------------------------


def _encode(source: str) -> str:
    """mermaid.ink хочет URL-safe base64 без padding."""
    raw = base64.urlsafe_b64encode(source.strip().encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def _render(source: str, *, dest: Path, width: int = 1800) -> int:
    """
    Возвращает размер записанного PNG в байтах. Бросает на сетевых ошибках.

    `width` — целевая ширина PNG в пикселях. По умолчанию 1800 — комфортно
    для печати/PDF и читабельно даже на большой диаграмме классов с десятком
    блоков. Mermaid.ink требует width (или height) если используется scale,
    поэтому всегда задаём width явно.
    """
    encoded = _encode(source)
    # bgColor=FFFFFF — чистый белый фон, лучше встаёт в PDF.
    url = f"https://mermaid.ink/img/{encoded}?type=png&bgColor=FFFFFF&width={width}"

    req = urllib.request.Request(url, headers={"User-Agent": "niche-research-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        # Тело ошибки от mermaid.ink обычно содержит конкретный parse-error
        raise RuntimeError(f"HTTP {e.code}: {body[:500].decode('utf-8', errors='replace')}") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    print(f"Output dir: {OUT_DIR}")
    failed: list[tuple[str, str]] = []
    # Подчищаем устаревший единый screen-01.png если остался от прежней версии
    legacy = OUT_DIR / "screen-01.png"
    if legacy.exists():
        legacy.unlink()
        print(f"  removed legacy {legacy.name}")
    for num, title, source in DIAGRAMS:
        dest = OUT_DIR / f"screen-{num}.png"
        try:
            n = _render(source, dest=dest)
            print(f"  screen-{num}.png OK   {n:>7} bytes   ({title})")
        except urllib.error.URLError as e:
            print(f"  screen-{num}.png FAIL ({e})", file=sys.stderr)
            failed.append((str(num), str(e)))
        except Exception as e:
            print(f"  screen-{num}.png FAIL ({type(e).__name__}: {e})", file=sys.stderr)
            failed.append((str(num), str(e)))

    if failed:
        print(f"\n{len(failed)} diagrams failed:", file=sys.stderr)
        for num, err in failed:
            print(f"  - screen-{num}: {err}", file=sys.stderr)
        return 1
    print(f"\nAll {len(DIAGRAMS)} diagrams rendered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
