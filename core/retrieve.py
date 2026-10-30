"""Retrieval: bge-large query embedding, SurrealDB KNN (numpy fallback), fitness rerank, numbered context."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import Counter, deque
from pathlib import Path

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


def content_hash(text: str, model: str = MODEL_NAME) -> str:
    """Cache key for an embedding: sha1 of (model, text), so a changed model or text never hits a stale vector."""
    return hashlib.sha1(f"{model}\n{text}".encode()).hexdigest()


class EmbeddingCache:
    """Content-hash -> embedding store persisted as one npz (hashes + matrix), loaded once, written atomically.

    Derived data, never committed: safe to delete; a cold cache just re-encodes everything.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, np.ndarray] | None = None

    def _read_disk(self) -> dict[str, np.ndarray]:
        """Fresh read of the on-disk cache; {} when absent or unreadable (derived data, safe to re-encode)."""
        if not self.path.exists():
            return {}
        try:
            with np.load(self.path) as z:
                return {str(h): np.asarray(vec, dtype=np.float32) for h, vec in zip(z["hashes"].tolist(), z["matrix"])}
        except Exception as e:  # noqa: BLE001
            log.warning("embedding cache unreadable at %s, re-encoding: %s", self.path, e)
            return {}

    def _load(self) -> dict[str, np.ndarray]:
        with self._lock:
            if self._data is None:
                self._data = self._read_disk()
            return self._data

    def get_many(self, hashes: list[str]) -> dict[str, np.ndarray]:
        """Subset of `hashes` present in the cache."""
        data = self._load()
        with self._lock:
            return {h: data[h] for h in hashes if h in data}

    def put(self, entries: dict[str, np.ndarray | list[float]]) -> None:
        """Merge entries and persist atomically (write temp file, then rename).

        Tolerant of concurrent cache instances on the same path: the on-disk file is
        re-read and merged before persisting, so entries another instance appended
        after our last load survive. Without that, one instance's write clobbers the
        other's keys and forces extra re-encodes — never stale vectors, since keys
        are content hashes. Unique tmp names keep concurrent writers from eating
        each other's temp files.
        """
        if not entries:
            return
        with self._lock:
            data = dict(self._data or {})
            data.update({h: np.asarray(v, dtype=np.float32) for h, v in entries.items()})
            for h, v in self._read_disk().items():  # another instance may have appended
                data.setdefault(h, v)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
            keys = sorted(data)
            with tmp.open("wb") as fh:
                np.savez(fh, hashes=np.array(keys), matrix=np.stack([data[k] for k in keys]).astype(np.float32))
            tmp.replace(self.path)
            self._data = data

    def __len__(self) -> int:
        return len(self._load())


def embed_query(text: str) -> list[float]:
    """1024-d normalized bge-large query embedding (model loaded lazily on first call)."""
    vec = _get_model().encode(QUERY_PREFIX + text, normalize_embeddings=True)
    return [float(x) for x in vec]


_KNN_FIELDS = "id, document, title, source_link, alpha, beta"


# ---- hybrid scoring (feature 14): fitness-weighted cosine blended with BM25 ----
# BM25 rescues exact product terms: a keyword-only query like "HNSW" scores zero
# semantic signal in bge space but is unambiguous lexically.
TOKEN_RE = re.compile(r"[a-z0-9]+")
# Domain terms (product names, acronyms) are exactly what keyword queries target —
# the stoplist only drops function words.
BM25_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from",
    "how", "i", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was",
    "what", "when", "where", "which", "who", "why",
})
BM25_K1 = 1.5
BM25_B = 0.75
# Blend weight for the min-max-normalized BM25 component in hybrid_rerank.
# Overridable per environment via CONFIG["bm25_weight"] without touching contracts.py.
BM25_WEIGHT = 0.35


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


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens minus function words; product terms survive intact."""
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in BM25_STOPWORDS]


def bm25_scores(query: str, docs: list[str]) -> list[float]:
    """Okapi BM25 of each doc against the query; IDF and avgdl come from the candidate set.

    Pure and model-free. The candidate set (the KNN pool, tens of rows) is the corpus:
    IDF from a same-domain candidate pool separates matching from non-matching chunks
    exactly where it matters — inside the rerank cut.
    """
    q = tokenize(query)
    n = len(docs)
    if not q or n == 0:
        return [0.0] * n
    doc_tokens = [tokenize(d) for d in docs]
    avgdl = (sum(len(dt) for dt in doc_tokens) / n) or 1.0
    df: Counter[str] = Counter()
    for dt in doc_tokens:
        df.update(set(dt))
    scores = []
    for dt in doc_tokens:
        tf = Counter(dt)
        dl = len(dt) or 1
        s = 0.0
        for t in q:
            f = tf[t]
            if not f:
                continue
            idf = math.log(1.0 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * f * (BM25_K1 + 1.0) / (f + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl))
        scores.append(s)
    return scores


def hybrid_rerank(rows: list[dict], query: str, floor: float = 0.5, weight: float = BM25_WEIGHT) -> list[dict]:
    """rerank (fitness-weighted cosine) blended with normalized BM25; sorted desc; sets row['score'], row['bm25'].

    score = (1 - weight) * cosine_fit + weight * bm25_norm, with bm25_norm min-max scaled
    across the candidate set. All-zero BM25 leaves the cosine-fit ordering untouched, so
    semantically-driven queries behave exactly as before.
    """
    if not rows:
        return []
    rows = rerank(rows, floor)
    bm = bm25_scores(query, [r.get("document") or "" for r in rows])
    lo, hi = min(bm), max(bm)
    rng = hi - lo
    for row, b in zip(rows, bm):
        row["bm25"] = float(b)
        row["bm25_norm"] = (b - lo) / rng if rng > 0 else 0.0
        row["score"] = (1.0 - weight) * row["score"] + weight * row["bm25_norm"]
    return sorted(rows, key=lambda r: r["score"], reverse=True)


# ---- per-shape top-k (feature 16) ----
# Comparative shapes need wider candidate sets (both sides of a comparison must survive
# the rerank cut); narrow factual shapes trade recall for precision so off-topic chunks
# don't crowd the numbered context. Absent shape -> CONFIG["knn_k"].
SHAPE_TOP_K = {"who-owns": 6, "how-to": 10, "policy": 10, "when": 8, "definition": 6, "comparison": 12, "factual": 6}


# ---- query expansion (feature 15) ----
def expand_query(query: str, reading: dict | None = None) -> str:
    """Fold the taken reading's role-specific signal into the retrieval query.

    Each reading carries a paraphrase, an assumption, and its own query; role decides
    what helps retrieval. An intent reading's assumption states what the asker actually
    means — appending it pulls the chunk that answers the need, not the wording. A
    scope reading's paraphrase bounds the domain. A literal reading IS the question,
    so the query stands. Returns `query` unchanged when there is nothing to add.
    """
    if not reading:
        return query
    role = reading.get("role")
    if role == "intent":
        extra = str(reading.get("assumption") or "")
    elif role == "scope":
        extra = str(reading.get("paraphrase") or "")
    else:
        return query
    extra = " ".join(extra.split())
    if not extra or extra == query.strip():
        return query
    return f"{query} {extra}"


# ---- context-pack de-dup (feature 17) ----
# Two chunks whose embeddings agree this closely are near-duplicates (overlapping
# splits, scraped-twice pages); both in one pack wastes a context slot and double-cites
# the same sentence. Rank order decides the survivor.
DEDUP_THRESHOLD = 0.9


async def chunk_embeddings(chunk_ids: list[str]) -> list[np.ndarray] | None:
    """Embeddings for chunk ids, aligned to input order; None when the DB can't serve them.

    None is the skip signal: de-dup must never block retrieval (graceful degradation).
    """
    if not chunk_ids:
        return None
    try:
        rows = await db.q("SELECT id, embedding FROM chunk WHERE <string> id IN $ids;", {"ids": chunk_ids})
    except Exception as e:  # noqa: BLE001
        log.warning("de-dup embedding fetch failed: %s", e)
        return None
    by_id = {str(row.get("id")): row.get("embedding") for row in rows if isinstance(row, dict)}
    vecs = []
    for cid in chunk_ids:
        emb = by_id.get(cid)
        if emb is None:  # a lost chunk would silently misalign the pack: skip, don't guess
            return None
        vecs.append(np.asarray(emb, dtype=np.float32))
    return vecs


def dedup_chunks(rows: list[dict], embeddings: list[np.ndarray] | None,
                 threshold: float = DEDUP_THRESHOLD) -> list[dict]:
    """Greedy near-duplicate removal in rank order: drop a row >threshold cosine to a kept row.

    Returns rows in input (rank) order minus dropped duplicates. embeddings=None or a
    length mismatch skips the check — the pack may then hold near-duplicates, never crash.
    """
    if not rows or embeddings is None or len(embeddings) != len(rows):
        return list(rows)
    m = np.stack(embeddings).astype(np.float32)
    norms = np.linalg.norm(m, axis=1)
    norms[norms == 0] = 1.0  # zero vectors are comparable to nothing: never duplicates
    sim = (m / norms[:, None]) @ (m / norms[:, None]).T
    kept_idx: list[int] = []
    for i in range(len(rows)):
        if kept_idx and float(sim[i, kept_idx].max()) > threshold:
            log.debug("de-dup dropped %s (>%.2f cosine to a kept chunk)", rows[i].get("id"), threshold)
            continue
        kept_idx.append(i)
    return [rows[i] for i in kept_idx]


# ---- retrieval latency histogram (feature 18) ----
# Bounded recent window, recorded on every retrieve(); summarized at /vitals.
LATENCY_MAX_SAMPLES = 1000


def pctl(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile (numpy's 'linear' method) at q in [0,1]; None when empty."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    h = (len(s) - 1) * q
    lo, hi = math.floor(h), math.ceil(h)
    if lo == hi:
        return float(s[lo])
    return float(s[lo] + (s[hi] - s[lo]) * (h - lo))


class LatencyHistogram:
    """Thread-safe bounded window of observed latencies with p50/p95 summaries."""

    def __init__(self, max_samples: int = LATENCY_MAX_SAMPLES):
        self._lock = threading.Lock()
        self._samples: deque[float] = deque(maxlen=max_samples)

    def record(self, ms: float) -> None:
        with self._lock:
            self._samples.append(float(ms))

    def summary(self) -> dict:
        with self._lock:
            samples = list(self._samples)
        return {
            "count": len(samples),
            "p50_ms": round(pctl(samples, 0.5), 2) if samples else None,
            "p95_ms": round(pctl(samples, 0.95), 2) if samples else None,
        }


latency = LatencyHistogram()  # the vitals store for retrieval timing; read at /vitals


def effective_k(shape: str | None, k: int | None = None) -> int:
    """Explicit k wins; else the shape override; else the CONFIG base."""
    if k is not None:
        return int(k)
    return SHAPE_TOP_K.get(shape or "") or CONFIG["knn_k"]


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


async def retrieve(text: str, k: int | None = None, shape: str | None = None,
                   reading: dict | None = None) -> tuple[str, list[Citation], list[dict]]:
    """embed -> knn -> hybrid rerank[:k] -> numbered_context; bumps retrieved_count on the chosen chunks.

    `shape` (feature 16) tunes k through SHAPE_TOP_K when the caller has read the question's
    shape; an explicit `k` wins over the override. `reading` (feature 15) is the taken
    reading; its role-specific signal is expanded into the query before embedding.
    """
    k = effective_k(shape, k)
    t0 = time.perf_counter()
    query = expand_query(text, reading)
    vec = embed_query(query)
    rows = await knn(vec, k)
    ranked = hybrid_rerank(rows, query, CONFIG["retrieval_fitness_floor"], CONFIG.get("bm25_weight", BM25_WEIGHT))
    # feature 17: de-dup the full ranked candidate list, so a near-duplicate doesn't
    # burn one of the k context slots.
    embeddings = await chunk_embeddings([str(row.get("id", "")) for row in ranked])
    top = dedup_chunks(ranked, embeddings)[:k]
    latency.record((time.perf_counter() - t0) * 1000.0)  # feature 18: p50/p95 at /vitals
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
