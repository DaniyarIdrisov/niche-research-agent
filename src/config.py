"""
Application settings.

Все настройки читаются из .env (или окружения). Дефолты подобраны под GTX 1050 Ti /
16 GB RAM. Менять конкретные значения — через .env, чтобы не править код.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # --- Источник данных WB ---
    data_source: str = Field(default="mock", description="mock | live")

    # --- Ollama ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "qwen2.5:3b-instruct-q4_K_M"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_num_ctx: int = 4096
    # На single-GPU (1050 Ti) с параллельным fan-out Ollama сериализует LLM-
    # вызовы, и последний в очереди может ждать 6+ минут. 600 секунд (10 мин)
    # — безопасный потолок, который не падает на нормальной нагрузке.
    ollama_request_timeout: int = 600

    # --- ChromaDB ---
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "niche_research_kb"

    # --- Episodic memory ---
    sqlite_path: Path = PROJECT_ROOT / "data" / "episodic.db"

    # --- RAG ---
    rag_top_k_dense: int = 10
    rag_top_k_bm25: int = 10
    rag_final_k: int = 5
    rag_dense_weight: float = 0.6
    rag_bm25_weight: float = 0.4
    rag_use_reranker: bool = False
    rag_reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # --- LangGraph ---
    max_retries_per_node: int = 2
    langgraph_checkpoint_db: Path = PROJECT_ROOT / "data" / "checkpoints.sqlite"

    # --- Observability ---
    log_level: str = "INFO"
    log_file: Path = PROJECT_ROOT / "logs" / "agent.jsonl"
    otel_exporter_otlp_endpoint: str = "http://localhost:4319"  # коллектор, не jaeger напрямую
    otel_service_name: str = "niche-research-agent"
    metrics_port: int = 9464
    langfuse_host: str = "http://localhost:3001"
    langfuse_public_key: str = "pk-lf-local"
    langfuse_secret_key: str = "sk-lf-local"

    # --- WB live ---
    wb_search_url: str = "https://search.wb.ru/exactmatch/ru/common/v9/search"
    wb_request_rate_per_sec: float = 2.0
    wb_request_timeout: int = 15
    wb_user_agent: str = (
        "Mozilla/5.0 (compatible; NicheResearchAgent/0.1; +https://example.com)"
    )

    # --- CLI ---
    default_top_n: int = 30
    runs_dir: Path = PROJECT_ROOT / "runs"

    # --- Mock data path ---
    mock_data_path: Path = PROJECT_ROOT / "data" / "mock_wb_products.json"

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Lazy-singleton, чтобы .env читался один раз и тесты могли подменять."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
