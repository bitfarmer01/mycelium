"""Retrieval: bge-large query embedding, SurrealDB KNN (numpy fallback), fitness rerank, numbered context."""
from __future__ import annotations

import logging
import threading

import numpy as np

from core import db
from core.contracts import CONFIG, Citation

log = logging.getLogger(__name__)

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MODEL_NAME = "BAAI/bge-large-en-v1.5"

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_query(text: str) -> list[float]:
    """1024-d normalized bge-large query embedding (model loaded lazily on first call)."""
    vec = _get_model().encode(QUERY_PREFIX + text, normalize_embeddings=True)
    return [float(x) for x in vec]


_KNN_FIELDS = "id, document, title, source_link, alpha, beta"


async def knn(vec: list[float], k: int = 8) -> list[dict]:
    """Top k*5 chunks by cosine. HNSW KNN first; on error, full scan with numpy cosine."""
    n = int(k) * 5
    sql = (
        f"SELECT {_KNN_FIELDS}, vector::similarity::cosine(embedding, $v) AS cosine "
        f"FROM chunk WHERE embedding <|{n},40|> $v ORDER BY cosine DESC LIMIT {n};"
    )
    try:
        return await db.q(sql, {"v": vec})
    except Exception as e:  # noqa: BLE001
        log.warning("knn query failed, falling back to numpy scan: %s", e)
    rows = await db.q(f"SELECT {_KNN_FIELDS}, embedding FROM chunk;")
    rows = [r for r in rows if r.get("embedding")]
    if not rows:
        return []
    m = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
    v = np.asarray(vec, dtype=np.float32)
    denom = np.linalg.norm(m, axis=1) * (np.linalg.norm(v) or 1.0)
    denom[denom == 0] = 1.0
    sims = (m @ v) / denom
    order = np.argsort(-sims)[:n]
    out = []
    for i in order:
        row = {key: val for key, val in rows[i].items() if key != "embedding"}
        row["cosine"] = float(sims[i])
        out.append(row)
    return out


def rerank(rows: list[dict], floor: float = 0.5) -> list[dict]:
    """score = cosine * (floor + (1-floor) * alpha/(alpha+beta)); sorted desc; sets row['score']."""
    for row in rows:
        a = float(row.get("alpha") or 1.0)
        b = float(row.get("beta") or 1.0)
        fit = a / (a + b) if (a + b) > 0 else 0.5
        row["score"] = float(row.get("cosine") or 0.0) * (floor + (1.0 - floor) * fit)
    return sorted(rows, key=lambda r: r["score"], reverse=True)


def numbered_context(rows: list[dict]) -> tuple[str, list[Citation]]:
    """'[Document N (Source: <title>)]\\n<document>' blocks joined by blank lines, plus Citations (n from 1)."""
    parts, cites = [], []
    for n, row in enumerate(rows, start=1):
        title = row.get("title") or "untitled"
        doc = row.get("document") or ""
        parts.append(f"[Document {n} (Source: {title})]\n{doc}")
        cites.append(
            Citation(
                n=n,
                chunk_id=str(row.get("id", "")),
                title=title,
                link=row.get("source_link") or "",
                snippet=doc[:200],
                cosine=float(row.get("cosine") or 0.0),
            )
        )
    return "\n\n".join(parts), cites


async def retrieve(text: str, k: int = 8) -> tuple[str, list[Citation], list[dict]]:
    """embed -> knn -> rerank[:k] -> numbered_context; bumps retrieved_count on the chosen chunks."""
    vec = embed_query(text)
    rows = await knn(vec, k)
    top = rerank(rows, CONFIG["retrieval_fitness_floor"])[:k]
    ctx, cites = numbered_context(top)
    ids = [c.chunk_id for c in cites if c.chunk_id]
    if ids:
        try:
            await db.q(
                "UPDATE chunk SET retrieved_count = (retrieved_count OR 0) + 1 "
                "WHERE <string> id IN $ids;",
                {"ids": ids},
            )
        except Exception as e:  # noqa: BLE001
            log.warning("retrieved_count bump failed: %s", e)
    return ctx, cites, top


def evidence_pack(question_id: str, text: str, context: str, citations: list[Citation]) -> dict:
    return {
        "question_id": question_id,
        "text": text,
        "context": context,
        "n_docs": len(citations),
        "chunk_ids": [c.chunk_id for c in citations],
    }
