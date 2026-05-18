"""
Semantic memory — ChromaDB + hybrid search (dense + BM25) + опциональный реранкер.

Архитектура (см. docs/MEMORY.md):
  1. Документы из knowledge_base/*.md разбиваются на чанки.
  2. Каждый чанк эмбеддится через Ollama (nomic-embed-text, на CPU).
  3. Чанки сохраняются в ChromaDB (HTTP-режим, контейнер из docker-compose).
  4. На retrieve:
     a) dense search по ChromaDB → top-k1 кандидатов;
     b) BM25 поверх всех чанков (в памяти) → top-k2;
     c) weighted fusion → top-N;
     d) опционально bge-reranker-v2-m3 если settings.rag_use_reranker.
  5. Результаты возвращаются как RetrievedChunk.

Падать не должно даже если ChromaDB недоступен — health() возвращает статус, retrieve()
возвращает [] и логирует warning. Это критично: агенты должны работать и без KB.

Reranker реализован отдельно (lazy load), потому что:
  - модель ~600 MB, тянуть из transformers только когда нужно;
  - на 4GB VRAM требует выгрузки LLM на время — это операционное решение,
    а не часть архитектуры памяти.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import get_settings
from src.llm.ollama_client import OllamaClient, OllamaError
from src.observability.metrics import record_retrieve


@dataclass
class RetrievedChunk:
    text: str
    source: str
    score: float
    metadata: dict


# ---------------------------------------------------------------------------
# Markdown chunking
# ---------------------------------------------------------------------------


_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _strip_frontmatter(text: str) -> tuple[str, dict[str, Any]]:
    """Парсит --- frontmatter --- блок в начале md. Возвращает (body, meta)."""
    if not text.startswith("---"):
        return text, {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text, {}
    meta_raw, body = parts[1], parts[2]
    meta: dict[str, Any] = {}
    for line in meta_raw.strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return body.lstrip("\n"), meta


def chunk_markdown(text: str, max_chars: int = 1200, overlap: int = 200) -> list[str]:
    """
    Простой markdown-сплиттер.
      1. Сначала режем по заголовкам (## и глубже).
      2. Если фрагмент длиннее max_chars — режем по абзацам с overlap.

    overlap — сколько символов забираем из конца предыдущего чанка в начало
    следующего, чтобы не разрывать смысловые границы.
    """
    sections: list[str] = []
    last_pos = 0
    for m in _HEADER_RE.finditer(text):
        if m.start() > last_pos:
            sections.append(text[last_pos:m.start()].strip())
        last_pos = m.start()
    sections.append(text[last_pos:].strip())
    sections = [s for s in sections if s]

    chunks: list[str] = []
    for sec in sections:
        if len(sec) <= max_chars:
            chunks.append(sec)
            continue
        # Длинный раздел — рубим по абзацам
        paragraphs = sec.split("\n\n")
        buf = ""
        for p in paragraphs:
            if len(buf) + len(p) + 2 <= max_chars:
                buf = f"{buf}\n\n{p}" if buf else p
            else:
                if buf:
                    chunks.append(buf.strip())
                    # overlap из хвоста предыдущего
                    buf = (buf[-overlap:] + "\n\n" + p) if overlap else p
                else:
                    buf = p
        if buf:
            chunks.append(buf.strip())
    return chunks


# ---------------------------------------------------------------------------
# BM25 (in-memory)
# ---------------------------------------------------------------------------


def _tokenize_ru(text: str) -> list[str]:
    """Простой токенизатор: lower + split по non-word. Достаточно для KB размером ≤100 чанков."""
    return [t for t in re.split(r"\W+", text.lower()) if t]


class _BM25Store:
    """Обёртка над rank_bm25.BM25Okapi с фолбэком на TF-only при отсутствии библиотеки."""

    def __init__(self) -> None:
        self._docs: list[str] = []
        self._ids: list[str] = []
        self._meta: list[dict] = []
        self._bm25 = None

    def rebuild(self, ids: list[str], docs: list[str], meta: list[dict]) -> None:
        self._ids = list(ids)
        self._docs = list(docs)
        self._meta = list(meta)
        try:
            from rank_bm25 import BM25Okapi

            tokens = [_tokenize_ru(d) for d in docs]
            self._bm25 = BM25Okapi(tokens)
        except ImportError:
            logger.warning("semantic.bm25.no_library", note="install rank_bm25")
            self._bm25 = None

    def search(self, query: str, k: int) -> list[tuple[str, float, str, dict]]:
        if not self._docs:
            return []
        if self._bm25 is None:
            # TF-fallback: считаем совпадение токенов
            q_tokens = set(_tokenize_ru(query))
            scored = [
                (i, sum(1 for t in _tokenize_ru(d) if t in q_tokens))
                for i, d in enumerate(self._docs)
            ]
        else:
            q_tokens = _tokenize_ru(query)
            scores = self._bm25.get_scores(q_tokens)
            scored = list(enumerate(scores))
        scored.sort(key=lambda x: x[1], reverse=True)
        out: list[tuple[str, float, str, dict]] = []
        for idx, score in scored[:k]:
            if score <= 0:
                continue
            out.append((self._ids[idx], float(score), self._docs[idx], self._meta[idx]))
        return out


# ---------------------------------------------------------------------------
# Reranker (lazy load)
# ---------------------------------------------------------------------------


class _Reranker:
    """bge-reranker-v2-m3. Загружается лениво, при первом вызове."""

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder

            model_name = get_settings().rag_reranker_model
            logger.info("semantic.reranker.loading", model=model_name)
            self._model = CrossEncoder(model_name, max_length=512)
        except ImportError as e:
            logger.error("semantic.reranker.no_lib", error=str(e))
            raise

    def score(self, query: str, passages: list[str]) -> list[float]:
        self._load()
        pairs = [(query, p) for p in passages]
        scores = self._model.predict(pairs, show_progress_bar=False)
        return [float(s) for s in scores]


# ---------------------------------------------------------------------------
# SemanticMemory
# ---------------------------------------------------------------------------


class SemanticMemory:
    """
    Полная реализация семантической памяти. Все Chroma/BM25 операции
    скрыты за публичным API: ingest, retrieve, health.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._chroma = None
        self._collection = None
        self._bm25 = _BM25Store()
        self._reranker: _Reranker | None = None
        self._llm = OllamaClient()
        self._connect_chroma()

    def _connect_chroma(self) -> None:
        try:
            import chromadb

            self._chroma = chromadb.HttpClient(
                host=self.settings.chroma_host,
                port=self.settings.chroma_port,
            )
            self._collection = self._chroma.get_or_create_collection(
                name=self.settings.chroma_collection,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "semantic.chroma.connected",
                host=self.settings.chroma_host,
                port=self.settings.chroma_port,
                collection=self.settings.chroma_collection,
            )
            self._rebuild_bm25_from_chroma()
        except Exception as e:
            logger.warning("semantic.chroma.connect_failed", error=str(e))
            self._chroma = None
            self._collection = None

    def _rebuild_bm25_from_chroma(self) -> None:
        """При старте подтягиваем все чанки из ChromaDB в BM25-индекс в памяти."""
        if self._collection is None:
            return
        try:
            data = self._collection.get(include=["documents", "metadatas"])
            ids = data.get("ids", []) or []
            docs = data.get("documents", []) or []
            meta = data.get("metadatas", []) or [{} for _ in docs]
            self._bm25.rebuild(ids, docs, meta)
            logger.info("semantic.bm25.rebuilt", n_chunks=len(docs))
        except Exception as e:
            logger.warning("semantic.bm25.rebuild_failed", error=str(e))

    # ---- public API ----

    def ingest(self, kb_dir: Path) -> int:
        """
        Перечитывает knowledge_base/, рубит на чанки, эмбеддит, кладёт в ChromaDB.
        Идемпотентно: коллекция очищается перед ингестом.
        """
        if self._collection is None:
            logger.error("semantic.ingest.no_chroma")
            return 0

        kb_dir = Path(kb_dir)
        if not kb_dir.exists():
            logger.error("semantic.ingest.no_kb_dir", path=str(kb_dir))
            return 0

        # Очищаем коллекцию для идемпотентного ингеста
        try:
            self._chroma.delete_collection(self.settings.chroma_collection)
            self._collection = self._chroma.get_or_create_collection(
                name=self.settings.chroma_collection,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            logger.warning("semantic.ingest.cleanup_failed", error=str(e))

        all_ids: list[str] = []
        all_docs: list[str] = []
        all_meta: list[dict] = []

        for path in sorted(kb_dir.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            body, fm = _strip_frontmatter(text)
            chunks = chunk_markdown(body)
            rel = str(path.relative_to(kb_dir))
            for i, ch in enumerate(chunks):
                cid = f"{rel}#{i}-{uuid.uuid4().hex[:6]}"
                meta = {
                    "source": rel,
                    "chunk_idx": i,
                    "title": fm.get("title", path.stem),
                    "category": fm.get("category", ""),
                    "applies_to": fm.get("applies_to", ""),
                }
                all_ids.append(cid)
                all_docs.append(ch)
                all_meta.append(meta)

        if not all_docs:
            logger.warning("semantic.ingest.empty")
            return 0

        # Эмбеддинги через Ollama. Идём батчами по 16 — чтобы не упасть на больших KB.
        logger.info("semantic.ingest.embedding", n_chunks=len(all_docs))
        try:
            embeddings = self._llm.embed(all_docs)
        except OllamaError as e:
            logger.error("semantic.ingest.embed_failed", error=str(e))
            return 0

        self._collection.add(
            ids=all_ids,
            documents=all_docs,
            embeddings=embeddings,
            metadatas=all_meta,
        )
        self._bm25.rebuild(all_ids, all_docs, all_meta)
        logger.info("semantic.ingest.done", n_chunks=len(all_docs))
        return len(all_docs)

    def retrieve(
        self,
        query: str,
        k: int | None = None,
        *,
        category_filter: str | None = None,
    ) -> list[RetrievedChunk]:
        """
        Hybrid search: dense (Chroma) + sparse (BM25) → fusion → опциональный rerank.
        Возвращает k чанков, отсортированных по итоговому score (desc).
        """
        s = self.settings
        final_k = k or s.rag_final_k

        if self._collection is None:
            logger.warning("semantic.retrieve.no_chroma")
            record_retrieve(n_chunks=0, outcome="no_chroma")
            return []

        # 1. Dense
        dense: dict[str, tuple[float, str, dict]] = {}
        try:
            q_emb = self._llm.embed([query])[0]
            where = {"category": category_filter} if category_filter else None
            results = self._collection.query(
                query_embeddings=[q_emb],
                n_results=s.rag_top_k_dense,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            ids = (results.get("ids") or [[]])[0]
            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            dists = (results.get("distances") or [[]])[0]
            for cid, doc, meta, dist in zip(ids, docs, metas, dists):
                # cosine distance ∈ [0, 2]; превращаем в similarity ∈ [0, 1]
                sim = max(0.0, 1.0 - dist / 2.0)
                dense[cid] = (sim, doc, meta or {})
        except Exception as e:
            logger.warning("semantic.retrieve.dense_failed", error=str(e))

        # 2. Sparse
        sparse: dict[str, tuple[float, str, dict]] = {}
        bm25_hits = self._bm25.search(query, k=s.rag_top_k_bm25)
        # Нормализуем BM25 в [0, 1] делением на max
        if bm25_hits:
            max_score = max(h[1] for h in bm25_hits) or 1.0
            for cid, score, doc, meta in bm25_hits:
                if category_filter and (meta or {}).get("category") != category_filter:
                    continue
                sparse[cid] = (score / max_score, doc, meta)

        # 3. Fusion (weighted sum, нормализованные score)
        merged: dict[str, tuple[float, str, dict]] = {}
        for cid, (sim, doc, meta) in dense.items():
            merged[cid] = (sim * s.rag_dense_weight, doc, meta)
        for cid, (bm25_sim, doc, meta) in sparse.items():
            prev = merged.get(cid)
            merged[cid] = (
                (prev[0] if prev else 0.0) + bm25_sim * s.rag_bm25_weight,
                doc,
                meta,
            )

        ranked = sorted(merged.items(), key=lambda kv: kv[1][0], reverse=True)
        candidates = ranked[: max(final_k * 2, final_k + 5)]

        # 4. Optional rerank
        if s.rag_use_reranker and candidates:
            if self._reranker is None:
                self._reranker = _Reranker()
            try:
                passages = [c[1][1] for c in candidates]
                rerank_scores = self._reranker.score(query, passages)
                rescored = list(zip(candidates, rerank_scores, strict=True))
                rescored.sort(key=lambda x: x[1], reverse=True)
                candidates = [c for c, _ in rescored]
                # Перезаписываем итоговый score значением реранкера, нормализуя
                if rescored:
                    rmax = max(s for _, s in rescored) or 1.0
                    candidates = [
                        (c[0], (s / rmax, c[1][1], c[1][2])) for c, s in rescored
                    ]
            except Exception as e:
                logger.warning("semantic.rerank.failed", error=str(e))

        out: list[RetrievedChunk] = []
        for cid, (score, doc, meta) in candidates[:final_k]:
            out.append(
                RetrievedChunk(
                    text=doc,
                    source=str(meta.get("source", cid)),
                    score=float(score),
                    metadata=meta,
                )
            )
        record_retrieve(n_chunks=len(out), outcome="ok" if out else "empty")
        logger.info(
            "semantic.retrieve.done",
            query=query[:60],
            dense=len(dense),
            sparse=len(sparse),
            returned=len(out),
            reranked=s.rag_use_reranker,
        )
        return out

    def health(self) -> dict:
        return {
            "chroma_connected": self._collection is not None,
            "n_chunks_in_bm25": len(self._bm25._docs),
            "reranker_enabled": self.settings.rag_use_reranker,
            "chroma_host": self.settings.chroma_host,
            "chroma_port": self.settings.chroma_port,
            "collection": self.settings.chroma_collection,
        }


_INSTANCE: SemanticMemory | None = None


def get_semantic_memory() -> SemanticMemory:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SemanticMemory()
    return _INSTANCE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m src.memory.semantic [ingest <dir> | query <text> | health]")
        return 1
    cmd = sys.argv[1]
    mem = get_semantic_memory()
    if cmd == "ingest":
        if len(sys.argv) < 3:
            print("usage: ... ingest <kb-dir>")
            return 1
        n = mem.ingest(Path(sys.argv[2]))
        print(f"ingested {n} chunks")
        return 0
    if cmd == "query":
        if len(sys.argv) < 3:
            print("usage: ... query <text>")
            return 1
        chunks = mem.retrieve(" ".join(sys.argv[2:]))
        for ch in chunks:
            print(f"\n--- {ch.source}  score={ch.score:.3f} ---")
            print(ch.text[:300])
        return 0
    if cmd == "health":
        print(json.dumps(mem.health(), indent=2, ensure_ascii=False))
        return 0
    print(f"unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
